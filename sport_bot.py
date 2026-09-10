"""
Спортивный бот для группы в MAX — версия 2.

6 клубов: Автомобилист (КХЛ), Синара, Урал, Реал Мадрид, Арсенал Лондон, Милан.
Любые официальные турниры. Без турнирных таблиц — только результат.

10:00 ЕКБ — если клуб играет сегодня, подробное оповещение с временем
            начала, местом и соперником; время старта сохраняется.
Каждые 20 минут (13:00-22:00 UTC ≈ 16:00-01:00 ЕКБ) — проверка: если
с начала сохранённого матча прошло 2,5 часа и результат ещё не
отправлен, ищем счёт и шлём сообщение с нужной эмоцией.

Хоккей (Автомобилист): ничьих не бывает, отдельно отмечаем победу/
поражение в овертайме или по буллитам — это важно из-за очков.

ИИ — связка Tavily Search + Google Gemini. Раньше здесь был Yandex Search +
собственное скачивание страниц через BeautifulSoup — сравнительный тест
показал, что Tavily даёт заметно более чистые и точные результаты (Yandex
как-то отдал ссылку на федерацию настольного тенниса вместо футбольного
«Арсенала»), плюс сама возвращает очищенный текст, без ручного парсинга
HTML. Поле "answer" из ответа Tavily НЕ используется — в тесте оно дважды
путало часовой пояс (мск вместо екб, московское вместо местного) — весь
разбор по-прежнему делает LLM по сырым "content" из источников.
GigaChat заменён на Gemini: регулярно падал по сетевым таймаутам на
стороне Sber (наблюдали вживую несколько раз подряд на Урале), а раннер
GitHub Actions физически не в России — бесплатный внешний API доступен
без VPN и оказался стабильнее.
Защита от галлюцинаций (проверено на реальных инцидентах в соседнем
боте того же стека): поле «Турнир» не может совпадать с именем самого
клуба, а заявленный соперник должен реально встречаться в собранных
материалах — иначе один повтор запроса с уточнением, и только потом
пропуск, а не отправка недостоверных данных.

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ:
    MAX_BOT_TOKEN, MAX_CHAT_ID,
    GEMINI_API_KEY, TAVILY_API_KEY

ЗАПУСК:
    python sport_bot.py
"""

import asyncio
import datetime
import json
import os
import re
from zoneinfo import ZoneInfo

import aiohttp
from maxapi import Bot

MAX_BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
MAX_CHAT_ID = int(os.environ["MAX_CHAT_ID"])
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
GEMINI_MODEL = "gemini-3.6-flash"

YEKB_TZ = ZoneInfo("Asia/Yekaterinburg")
MORNING_HOUR = 10
RESULT_DELAY_HOURS = 2.5

DATA_FILE = "data/today_matches.json"   # что сегодня играет и отправлен ли результат
# Утренний прогон (10:00 ЕКБ) должен пережить проверки результатов вплоть до
# раннего утра следующих суток — поздние вечерние матчи (22:00+ мск) с учётом
# RESULT_DELAY_HOURS проверяются уже ПОСЛЕ полуночи по Екатеринбургу, то есть
# калидарные сутки по ЕКБ успевают смениться прямо посреди рабочего окна.
# Поэтому годность состояния определяем не по совпадению календарной даты
# (это стирало бы данные ровно в такой ситуации), а по давности сохранения.
STATE_MAX_AGE_HOURS = 20

TZ_OFFSET = {"мск": 3, "москва": 3, "екб": 5, "екатеринбург": 5}

# icon: 🏒 хоккей, ⚽ футбол, 🥅 футзал
CLUBS = [
    {"key": "avtomobilist", "name": "ХК «Автомобилист»", "sport": "hockey", "icon": "🏒",
     "sites": ["khl.ru", "hc-avto.ru", "championat.com"]},
    {"key": "sinara", "name": "МФК «Синара»", "sport": "futsal", "icon": "🥅",
     "sites": ["superliga.rfs.ru", "mfkviz.ru", "futsal.rfs.ru"]},
    {"key": "ural", "name": "ФК «Урал»", "sport": "football", "icon": "⚽",
     "sites": ["fc-ural.ru", "fnl.pro", "championat.com"]},
    {"key": "real", "name": "«Реал Мадрид»", "sport": "football", "icon": "⚽",
     "sites": ["realmadrid.com", "championat.com", "soccer.ru"]},
    {"key": "arsenal", "name": "«Арсенал» Лондон", "sport": "football", "icon": "⚽",
     "sites": ["arsenal.com", "championat.com", "soccer.ru"]},
    {"key": "milan", "name": "«Милан»", "sport": "football", "icon": "⚽",
     "sites": ["acmilan.com", "championat.com", "soccer.ru"]},
]

# Глобальный (не per-club) список доменов для Tavily include_domains — по
# выбору пользователя, вместо неограниченного поиска: тот показал точность
# выше, чем старые узкие per-club sites, но иногда всё равно промахивался
# на нерелевантные источники (например, UFC/MMA для Автомобилиста). Список
# осознанно с запасом на будущее — под расширение числа клубов и добавление
# КХЛ/НХЛ, поэтому включает источники сверх текущих 6 клубов.
#
# championat.com сюда намеренно НЕ входит: include_domains у Tavily —
# фильтр по домену целиком, без путей (нет способа ограничить его только
# разделами /football/ и /hockey/), а в живом тесте этот домен несколько
# раз приносил статьи не по теме (бокс, теннис, шахматы) для Арсенала
# и Милана. Остальные домены в списке уже достаточно специализированы
# по футболу/хоккею, поэтому потеря championat.com не страшна.
TAVILY_INCLUDE_DOMAINS = [
    "hc-avto.ru", "khl.ru", "news.sportbox.ru", "sports.ru",
    "mfkviz.ru", "superliga.rfs.ru", "rfs.ru", "fnl.pro", "fc-ural.ru",
    "fapl.ru", "arsenal.com", "legaseriea.it", "realmadrid.com", "acmilan.com",
    "laliga.com", "sport-express.ru", "ria.ru", "bundesliga.com", "bvb.de",
    "nhl.ru", "nhl.com", "allhockey.ru", "nhl-news.ru",
]


# --- Хранилище состояния (что сегодня играет) ------------------------------

def load_state() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[DEBUG] не удалось прочитать {DATA_FILE}: {type(e).__name__}: {e}")
        return {}
    saved_at_raw = data.get("saved_at")
    if not saved_at_raw:
        return {}
    try:
        saved_at = datetime.datetime.fromisoformat(saved_at_raw)
    except ValueError:
        return {}
    age = datetime.datetime.now(YEKB_TZ) - saved_at
    if age > datetime.timedelta(hours=STATE_MAX_AGE_HOURS):
        return {}
    return data


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    state["saved_at"] = datetime.datetime.now(YEKB_TZ).isoformat()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# --- Поиск (Tavily) и обращение к Gemini ------------------------------------

async def collect_web_context_tavily(query: str, sites: list[str]) -> str:
    """Поиск через Tavily — сразу очищенный текст, без ручного
    скачивания страниц и парсинга HTML. НЕ используем поле answer —
    оно иногда путает часовые пояса, полагаемся только на content
    из результатов и отдаём это на разбор Gemini, как раньше.

    include_domains — глобальный TAVILY_INCLUDE_DOMAINS, а не per-club
    sites (последний параметр сейчас не используется внутри функции,
    но сохранён в сигнатуре — вызывающий код по-прежнему передаёт
    club["sites"]). Полностью неограниченный поиск в живом тесте иногда
    приносил нерелевантные источники (например, UFC/MMA для Автомобилиста);
    старые узкие per-club sites — наоборот, резко ухудшали результаты.
    Этот список — осознанный выбор пользователя как компромисс."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.tavily.com/search",
            headers={
                "Authorization": f"Bearer {TAVILY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "search_depth": "basic",
                "max_results": 8,
                "include_domains": TAVILY_INCLUDE_DOMAINS,
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            raw = await resp.text()
            if resp.status != 200:
                print(f"[DEBUG] Tavily статус={resp.status}, тело={raw[:300]!r}")
                return ""
            try:
                data = json.loads(raw)
            except ValueError as e:
                print(f"[DEBUG] не удалось разобрать ответ Tavily: {type(e).__name__}: {e}")
                return ""

    results = data.get("results", [])
    print(f"[DEBUG] Tavily нашёл {len(results)} источников по «{query}»")
    print(f"[DEBUG] все URL от Tavily: {[r.get('url', '') for r in results]}")
    combined = []
    # max_results подняли с 5 до 8, и смотрим все 8, а не только первые 4 —
    # в живом тесте нужный источник (fc-ural.ru) оказался ниже 4-й позиции
    # (Tavily ставил впереди календари клуба-дубля «Урал-2» и чужих команд).
    for r in results:
        content = r.get("content", "")
        url = r.get("url", "")
        if len(content) > 150:
            print(f"[DEBUG] источник {url}, символов: {len(content)}, фрагмент: {content[:200]!r}")
            combined.append(f"Источник {url}:\n{content}")
        if len(combined) >= 3:
            break
    return "\n\n---\n\n".join(combined)


_gemini_semaphore = asyncio.Semaphore(1)


async def gemini_completion(messages: list[dict]) -> str:
    """До 3 попыток при сетевом сбое (таймаут, обрыв соединения) или
    HTTP 429/5xx — тот же паттерн ретраев, что раньше защищал от голого
    исключения без текста (str(TimeoutError()) == '') при обращении к
    GigaChat, применяем и здесь на случай нестабильности бесплатного
    тарифа Gemini.

    messages — тот же псевдо-OpenAI формат (role: system/user), что
    использовался для GigaChat, чтобы не переписывать вызывающий код;
    здесь он переводится в формат Gemini (systemInstruction + contents)."""
    system_text = next((m["content"] for m in messages if m["role"] == "system"), None)
    contents = [{"role": "user", "parts": [{"text": m["content"]}]} for m in messages if m["role"] != "system"]
    body = {"contents": contents}
    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}

    async with _gemini_semaphore:
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
                        json=body,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        raw = await resp.text()
                        status = resp.status
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                print(f"[DEBUG] Gemini: сетевая ошибка {type(e).__name__}: {e}, попытка {attempt + 1}/3")
                if attempt < 2:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                raise RuntimeError(f"Gemini: сетевая ошибка после 3 попыток: {type(e).__name__}: {e}")

            try:
                data = json.loads(raw)
            except ValueError:
                raise RuntimeError(f"Gemini вернул не-JSON ответ (статус {status}): {raw[:200]}")
            if status in (429, 503) and attempt < 2:
                print(f"[DEBUG] Gemini: статус {status}, пауза и повтор ({attempt + 1}/3)")
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if status != 200:
                raise RuntimeError(data.get("error", {}).get("message", f"ошибка запроса, статус {status}"))
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                raise RuntimeError(f"Gemini вернул ответ без текста (статус {status}): {raw[:300]}")
        raise RuntimeError("Gemini: превышены попытки после 429/503")


async def ask_gemini_with_context(question: str, context: str, system_extra: str = "") -> str:
    """Спрашивает Gemini по уже готовому контексту, без нового похода в
    поиск — используется для повторных попыток: не тратим лишний запрос
    к Tavily на то же самое, раз материалы уже собраны, и вопрос-уточнение
    может быть длинной инструкцией с цитатой прошлого плохого ответа."""
    today = datetime.datetime.now(YEKB_TZ).strftime("%d.%m.%Y")
    system_text = (
        f"Сегодня {today}. Отвечай по-русски, строго в запрошенном формате, "
        f"без пояснений и markdown-разметки.\n"
        f"Время не пересчитывай между часовыми поясами — бери как в источнике, "
        f"но обязательно указывай пояс словом «мск» или «екб».\n"
        f"Никогда не придумывай факты, которых нет в материалах ниже. Если по "
        f"материалам нельзя точно и однозначно установить ответ — считай, что "
        f"события нет, и отвечай «НЕТ», а не давай предположительный ответ."
    )
    if system_extra:
        system_text += f"\n{system_extra}"
    if context:
        system_text += f"\n\nМатериалы из интернета по теме вопроса:\n{context}"
    else:
        system_text += "\n\nВ интернете ничего найти не удалось."
    messages = [{"role": "system", "content": system_text}, {"role": "user", "content": question}]
    return await gemini_completion(messages)


async def ask_gemini(question: str, sites: list[str], search_query: str | None = None) -> tuple[str, str]:
    """Ищет материалы в интернете и спрашивает Gemini. Возвращает
    (ответ, собранный контекст) — контекст нужен, чтобы потом проверить,
    не придумала ли модель факты, и чтобы можно было переспросить без
    нового поиска."""
    context = await collect_web_context_tavily(search_query or question, sites)
    answer = await ask_gemini_with_context(question, context)
    return answer, context


def find_data_line(answer: str, min_pipes: int = 2) -> str | None:
    """Gemini иногда оформляет ответ markdown-таблицей (шапка + разделитель +
    данные, или наоборот — сначала пояснение, потом НЕТ). Поэтому: сначала
    ищем НЕТ по всему ответу целиком — если есть где угодно, данных нет,
    и не важно, что стоит рядом с шапкой таблицы. Только если НЕТ нигде
    не нашлось, ищем строку с данными, с конца — так меньше шанс попасть
    на шапку, если она вообще есть."""
    lines = [ln.strip() for ln in answer.splitlines() if ln.strip()]
    if any(ln.upper() == "НЕТ" for ln in lines):
        return None
    for line in reversed(lines):
        if re.fullmatch(r"[\s|:\-]+", line):
            continue  # разделитель markdown-таблицы вида |---|---|
        if line.count("|") >= min_pipes:
            return line
    return None


def looks_like_score(text: str) -> bool:
    return bool(re.search(r"\d+\s*[:\-]\s*\d+", text))


def looks_like_own_name(tournament: str, club_name: str) -> bool:
    """«Турнир: ХК «Автомобилист»» или «Реал Мадрид — Интер» вместо
    названия турнира («Лига чемпионов») — модель по ошибке подставила в
    поле «Турнир» название самого клуба (иногда вместе с соперником через
    тире). Название турнира не может состоять из имени своего участника.
    Проверено на реальных инцидентах в соседнем боте той же связки."""
    club_core = re.sub(r'[«»"()]', '', club_name).strip().lower()
    tournament_core = re.sub(r'[«»"()]', '', tournament).strip().lower()
    if not club_core or not tournament_core:
        return False
    return club_core in tournament_core or tournament_core in club_core


def rival_mentioned(rival: str, context: str) -> bool:
    """Грубая проверка-страховка: хотя бы одно характерное слово из имени
    соперника должно встречаться в собранном веб-контексте (по стему, без
    учёта падежных окончаний) — иначе похоже, что модель прочитала общую
    сводку с кучей других матчей и приписала клубу чужого соперника."""
    if not context:
        return False
    words = re.findall(r"[А-Яа-яЁёA-Za-z]+", rival)
    candidates = [w for w in words if len(w) >= 4] or words
    if not candidates:
        return False
    context_lower = context.lower()
    for word in candidates:
        stem = word[:max(4, len(word) - 3)].lower()
        if stem in context_lower:
            return True
    return False


def parse_time_to_utc(date_str: str, time_str: str, zone_str: str) -> datetime.datetime | None:
    """«19:00», «мск» → datetime в UTC сегодняшнего дня."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", time_str.strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    zone_key = zone_str.strip().lower()
    if zone_key not in TZ_OFFSET:
        print(f"[DEBUG] неизвестный часовой пояс {zone_str!r}, беру мск по умолчанию")
    offset_hours = TZ_OFFSET.get(zone_key, 3)  # по умолчанию мск
    tz = datetime.timezone(datetime.timedelta(hours=offset_hours))
    try:
        today = datetime.datetime.now(YEKB_TZ).date()
        local_dt = datetime.datetime(today.year, today.month, today.day, hour, minute, tzinfo=tz)
        return local_dt.astimezone(datetime.timezone.utc)
    except ValueError:
        return None


# --- Утренняя проверка расписания -------------------------------------------

def parse_morning_line(answer: str, club_name: str, context: str):
    """Возвращает (Турнир, Время, Пояс, Место, Соперник) или None (искреннее
    «НЕТ» — переспрашивать незачем). Бросает ValueError, если строка с
    данными нашлась, но не разобралась, поле «Турнир» оказалось именем
    клуба, или соперник не подтверждается собранным контекстом — такое
    стоит переспросить у модели ещё раз."""
    line = find_data_line(answer, min_pipes=4)
    if line is None:
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 5:
        raise ValueError(f"меньше 5 полей: {parts!r}")
    tournament, time_str, zone_str, place, rival = parts[:5]
    if looks_like_own_name(tournament, club_name):
        raise ValueError(f"поле «Турнир» похоже на имя клуба/матча, не турнира: {tournament!r}")
    if not rival_mentioned(rival, context):
        raise ValueError(f"соперник '{rival}' не подтверждается материалами")
    return tournament, time_str, zone_str, place, rival


async def check_morning(club: dict) -> dict | None:
    """Если клуб играет сегодня — возвращает данные матча, иначе None."""
    today = datetime.date.today()
    prompt = (
        f"Играет ли {club['name']} сегодня, {today:%d.%m.%Y}, в любом "
        f"официальном турнире? Если да, найди турнир, время начала, место "
        f"проведения и соперника.\n"
        f"Ответь СТРОГО последней строкой в таком виде:\n"
        f"Турнир|ЧЧ:ММ|ПОЯС|Место|Соперник\n"
        f"ПОЯС — слово «мск» или «екб» в зависимости от того, в каком поясе "
        f"указано время в источнике.\n"
        f"Если сегодня матча нет — последней строкой напиши НЕТ."
    )
    # Отдельный (короче и без инструкций по формату) поисковый запрос —
    # с явным уточнением «основная команда»: полнотекстовый prompt выше
    # в качестве поискового запроса путал Tavily, тот путал клуб с его
    # дублирующим составом (например, «Урал» с «Урал-2») и вместо
    # официального сайта клуба приносил чужие календари.
    search_query = (
        f"{club['name']} официальный сайт расписание ближайший матч "
        f"{today:%d.%m.%Y} основная команда, не дубль и не молодёжный состав"
    )
    try:
        answer, context = await ask_gemini(prompt, club["sites"], search_query)
        answer = answer.strip()
        print(f"[DEBUG] {club['name']} (утро): {answer!r}")
        try:
            parsed = parse_morning_line(answer, club["name"], context)
        except ValueError as e:
            print(f"[DEBUG] {club['name']}: {e}, переспрашиваю ещё раз (тот же контекст, без нового поиска)")
            retry_extra = (
                f"Твой предыдущий ответ был в неправильном формате, называл "
                f"соперника, которого нет в материалах выше, или в поле "
                f"«Турнир» указал название самого клуба вместо названия "
                f"соревнования — не годится: {answer!r}.\n"
                f"Ответь ЕЩЁ РАЗ и ТОЛЬКО одной строкой, без пояснений, строго "
                f"в виде «Турнир|ЧЧ:ММ|ПОЯС|Место|Соперник». «Турнир» — это "
                f"название соревнования (например «Лига чемпионов»), а НЕ "
                f"название клуба и не пара «команда — соперник». Указывай "
                f"только то, что явно и однозначно написано в материалах выше "
                f"именно про {club['name']}. Если нет уверенности — одним "
                f"словом НЕТ."
            )
            answer = (await ask_gemini_with_context(prompt, context, retry_extra)).strip()
            print(f"[DEBUG] {club['name']} (утро, повтор): {answer!r}")
            try:
                parsed = parse_morning_line(answer, club["name"], context)
            except ValueError as e2:
                print(f"[DEBUG] {club['name']}: после повтора всё ещё не разобралось ({e2}), пропускаю")
                return None
        if parsed is None:
            return None
        tournament, time_str, zone_str, place, rival = parsed
        start_utc = parse_time_to_utc(today.isoformat(), time_str, zone_str)
        return {
            "tournament": tournament, "time": time_str, "zone": zone_str,
            "place": place, "rival": rival,
            "start_utc": start_utc.isoformat() if start_utc else None,
            "result_sent": False,
        }
    except Exception as e:
        print(f"[DEBUG] ошибка при утренней проверке {club['name']}: {type(e).__name__}: {e}")
        return None


def format_morning(club: dict, match: dict) -> str:
    return (
        f"{club['icon']} Сегодня играет {club['name']}\n"
        f"Турнир: {match['tournament']}\n"
        f"Время: {match['time']} ({match['zone']})\n"
        f"Место: {match['place']}\n"
        f"Соперник: {match['rival']}"
    )


async def job_morning(bot: Bot) -> None:
    print("[DEBUG] === утренняя проверка расписания ===")
    state = load_state()
    for club in CLUBS:
        match = await check_morning(club)
        if not match:
            continue
        state[club["key"]] = match
        await send_to_group(bot, format_morning(club, match))
    save_state(state)


# --- Проверка результата ----------------------------------------------------

def parse_result_line_football(answer: str) -> tuple[str, str] | None:
    """Возвращает (Счёт, Исход). None — матч искренне ещё не завершился.
    Бросает ValueError, если строка нашлась, но поле счёта не похоже на
    счёт — стоит переспросить у модели ещё раз."""
    line = find_data_line(answer, min_pipes=1)
    if line is None:
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2:
        raise ValueError(f"меньше 2 полей: {parts!r}")
    score, outcome = parts[0], parts[1].upper()
    if not looks_like_score(score):
        raise ValueError(f"поле счёта не похоже на счёт: {score!r}")
    return score, outcome


async def check_result_football(club: dict, rival_hint: str) -> str | None:
    today = datetime.date.today()
    prompt = (
        f"Завершился ли сегодня, {today:%d.%m.%Y}, матч {club['name']} "
        f"против {rival_hint}? Если да, найди точный счёт.\n"
        f"Ответь СТРОГО последней строкой:\n"
        f"Счёт|ИСХОД\n"
        f"Счёт — в формате число:число (сначала {club['name']}). "
        f"ИСХОД — одно слово: ПОБЕДА, НИЧЬЯ или ПОРАЖЕНИЕ, с точки зрения "
        f"{club['name']}.\n"
        f"Если матч ещё не завершился — последней строкой напиши НЕТ."
    )
    search_query = (
        f"{club['name']} против {rival_hint} счёт результат сегодня "
        f"{today:%d.%m.%Y} основная команда, не дубль и не молодёжный состав"
    )
    try:
        answer, context = await ask_gemini(prompt, club["sites"], search_query)
        answer = answer.strip()
        print(f"[DEBUG] {club['name']} (результат): {answer!r}")
        try:
            parsed = parse_result_line_football(answer)
        except ValueError as e:
            print(f"[DEBUG] {club['name']}: {e}, переспрашиваю ещё раз (тот же контекст, без нового поиска)")
            retry_extra = (
                f"Твой предыдущий ответ был в неправильном формате — не "
                f"годится: {answer!r}.\n"
                f"Ответь ЕЩЁ РАЗ и ТОЛЬКО одной строкой, без пояснений, строго "
                f"в виде «Счёт|ИСХОД». Счёт — именно число:число (сначала "
                f"{club['name']}), а не слово. Если матч ещё не завершился — "
                f"ответь одним словом НЕТ."
            )
            answer = (await ask_gemini_with_context(prompt, context, retry_extra)).strip()
            print(f"[DEBUG] {club['name']} (результат, повтор): {answer!r}")
            try:
                parsed = parse_result_line_football(answer)
            except ValueError as e2:
                print(f"[DEBUG] {club['name']}: после повтора всё ещё не разобралось ({e2}), пропускаю")
                return None
        if parsed is None:
            return None
        score, outcome = parsed
        return format_result_football(club, score, outcome, rival_hint)
    except Exception as e:
        print(f"[DEBUG] ошибка при проверке результата {club['name']}: {type(e).__name__}: {e}")
        return None


def format_result_football(club: dict, score: str, outcome: str, rival: str) -> str:
    if "ПОБЕД" in outcome:
        head = "🎆🎆🎆 ПОБЕДА!!!"
    elif "НИЧЬ" in outcome:
        head = "🤝 НИЧЬЯ!"
    else:
        head = "😔 Увы, сегодня проиграли"
    return f"{head}\n\n{club['icon']} {club['name']} {score} {rival}"


def parse_result_line_hockey(answer: str) -> tuple[str, str, str] | None:
    """Возвращает (Счёт, Исход, Способ). None — матч искренне ещё не
    завершился. Бросает ValueError при нехватке полей или счёте не
    похожем на счёт."""
    line = find_data_line(answer, min_pipes=2)
    if line is None:
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        raise ValueError(f"меньше 3 полей: {parts!r}")
    score, outcome, method = parts[0], parts[1].upper(), parts[2].upper()
    if not looks_like_score(score):
        raise ValueError(f"поле счёта не похоже на счёт: {score!r}")
    return score, outcome, method


async def check_result_hockey(club: dict, rival_hint: str) -> str | None:
    today = datetime.date.today()
    prompt = (
        f"Завершился ли сегодня, {today:%d.%m.%Y}, матч {club['name']} "
        f"против {rival_hint}? Если да, найди точный счёт и способ "
        f"завершения матча.\n"
        f"Ответь СТРОГО последней строкой:\n"
        f"Счёт|ИСХОД|СПОСОБ\n"
        f"Счёт — число:число (сначала {club['name']}). ИСХОД — ПОБЕДА или "
        f"ПОРАЖЕНИЕ с точки зрения {club['name']} (в КХЛ ничьих не бывает). "
        f"СПОСОБ — одно слово: ОСНОВНОЕ (решилось в основное время), ОТ "
        f"(овертайм) или БУЛЛИТЫ.\n"
        f"Если матч ещё не завершился — последней строкой напиши НЕТ."
    )
    search_query = (
        f"{club['name']} против {rival_hint} счёт результат сегодня "
        f"{today:%d.%m.%Y} основная команда, не дубль и не молодёжный состав"
    )
    try:
        answer, context = await ask_gemini(prompt, club["sites"], search_query)
        answer = answer.strip()
        print(f"[DEBUG] {club['name']} (результат): {answer!r}")
        try:
            parsed = parse_result_line_hockey(answer)
        except ValueError as e:
            print(f"[DEBUG] {club['name']}: {e}, переспрашиваю ещё раз (тот же контекст, без нового поиска)")
            retry_extra = (
                f"Твой предыдущий ответ был в неправильном формате — не "
                f"годится: {answer!r}.\n"
                f"Ответь ЕЩЁ РАЗ и ТОЛЬКО одной строкой, без пояснений, строго "
                f"в виде «Счёт|ИСХОД|СПОСОБ». Счёт — именно число:число "
                f"(сначала {club['name']}), а не слово. Если матч ещё не "
                f"завершился — ответь одним словом НЕТ."
            )
            answer = (await ask_gemini_with_context(prompt, context, retry_extra)).strip()
            print(f"[DEBUG] {club['name']} (результат, повтор): {answer!r}")
            try:
                parsed = parse_result_line_hockey(answer)
            except ValueError as e2:
                print(f"[DEBUG] {club['name']}: после повтора всё ещё не разобралось ({e2}), пропускаю")
                return None
        if parsed is None:
            return None
        score, outcome, method = parsed
        return format_result_hockey(club, score, outcome, method, rival_hint)
    except Exception as e:
        print(f"[DEBUG] ошибка при проверке результата {club['name']}: {type(e).__name__}: {e}")
        return None


def format_result_hockey(club: dict, score: str, outcome: str, method: str, rival: str) -> str:
    won = "ПОБЕД" in outcome
    extra = "ОТ" in method
    shootout = "БУЛЛИТ" in method

    if won and shootout:
        head = "🎆🎆🎆 ПОБЕДА ПО БУЛЛИТАМ!!!"
    elif won and extra:
        head = "🎆🎆🎆 ПОБЕДА В ОВЕРТАЙМЕ!!!"
    elif won:
        head = "🎆🎆🎆 ПОБЕДА!!!"
    elif shootout:
        head = "😔 Увы, сегодня проиграли по буллитам"
    elif extra:
        head = "😔 Увы, сегодня проиграли в овертайме"
    else:
        head = "😔 Увы, сегодня проиграли"

    tail = " ОТ" if extra else (" Б" if shootout else "")
    return f"{head}\n\n{club['icon']} {club['name']} {score}{tail} {rival}"


async def job_check_results(bot: Bot) -> None:
    print("[DEBUG] === проверка результатов ===")
    state = load_state()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    changed = False

    for club in CLUBS:
        match = state.get(club["key"])
        if not match or match.get("result_sent") or not match.get("start_utc"):
            continue
        start_utc = datetime.datetime.fromisoformat(match["start_utc"])
        if now_utc < start_utc + datetime.timedelta(hours=RESULT_DELAY_HOURS):
            continue  # ещё рано

        print(f"[DEBUG] пора проверить результат: {club['name']}")
        if club["sport"] == "hockey":
            text = await check_result_hockey(club, match["rival"])
        else:
            text = await check_result_football(club, match["rival"])

        if text:
            await send_to_group(bot, text)
            match["result_sent"] = True
            changed = True
        else:
            print(f"[DEBUG] {club['name']}: результата пока нет, попробуем в следующий раз")

    if changed:
        save_state(state)


# --- Отправка и точка входа -------------------------------------------------

async def send_to_group(bot: Bot, text: str) -> None:
    for attempt in range(3):
        try:
            await bot.send_message(chat_id=MAX_CHAT_ID, text=text)
            return
        except Exception as e:
            print(f"[DEBUG] попытка {attempt + 1} отправить не удалась: {type(e).__name__}: {e}")
            if attempt < 2:
                await asyncio.sleep(10)
    print("[DEBUG] отправить не удалось ни с одной попытки")


async def main():
    now = datetime.datetime.now(YEKB_TZ)
    print(f"[DEBUG] сейчас по Екатеринбургу: {now:%d.%m.%Y %H:%M}")

    # FORCE_MODE — только для ручного запуска (workflow_dispatch input
    # mode=morning/results), чтобы можно было проверить конкретную ветку
    # не дожидаясь нужного часа. На расписании (schedule) эта переменная
    # всегда пустая, и режим определяется как обычно, по текущему часу.
    force_mode = os.environ.get("FORCE_MODE", "").strip().lower()
    if force_mode == "morning":
        run_morning = True
        print("[DEBUG] режим принудительно установлен: morning (ручная проверка)")
    elif force_mode == "results":
        run_morning = False
        print("[DEBUG] режим принудительно установлен: results (ручная проверка)")
    else:
        run_morning = now.hour == MORNING_HOUR

    bot = Bot(MAX_BOT_TOKEN)
    try:
        if run_morning:
            await job_morning(bot)
        else:
            await job_check_results(bot)
    finally:
        session = getattr(bot, "session", None)
        if session is not None:
            close = getattr(session, "close", None)
            if close:
                result = close()
                if asyncio.iscoroutine(result):
                    await result


if __name__ == "__main__":
    asyncio.run(main())
