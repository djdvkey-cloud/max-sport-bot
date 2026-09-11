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

ИИ — связка Tavily Search + DeepSeek. Раньше здесь был Yandex Search +
собственное скачивание страниц через BeautifulSoup — сравнительный тест
показал, что Tavily даёт заметно более чистые и точные результаты (Yandex
как-то отдал ссылку на федерацию настольного тенниса вместо футбольного
«Арсенала»), плюс сама возвращает очищенный текст, без ручного парсинга
HTML. Поле "answer" из ответа Tavily НЕ используется — в тесте оно дважды
путало часовой пояс (мск вместо екб, московское вместо местного) — весь
разбор по-прежнему делает DeepSeek по сырым "content" из источников.
Защита от галлюцинаций (проверено на реальных инцидентах в соседнем
боте того же стека): поле «Турнир» не может совпадать с именем самого
клуба, а заявленный соперник должен реально встречаться в собранных
материалах — иначе один повтор запроса с уточнением, и только потом
пропуск, а не отправка недостоверных данных.

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ:
    MAX_BOT_TOKEN, MAX_CHAT_ID,
    DEEPSEEK_API_KEY, TAVILY_API_KEY

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
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]

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
     "aliases": ["Автомобилист", "Avtomobilist"],
     "sites": ["khl.ru", "hc-avto.ru", "championat.com"]},
    {"key": "sinara", "name": "МФК «Синара»", "sport": "futsal", "icon": "🥅",
     "aliases": ["Синара", "Sinara"],
     "sites": ["superliga.rfs.ru", "mfkviz.ru", "futsal.rfs.ru"]},
    {"key": "ural", "name": "ФК «Урал»", "sport": "football", "icon": "⚽",
     "aliases": ["Урал", "Ural"],
     "sites": ["fc-ural.ru", "fnl.pro", "championat.com"]},
    {"key": "real", "name": "«Реал Мадрид»", "sport": "football", "icon": "⚽",
     "aliases": ["Реал Мадрид", "Real Madrid"],
     "sites": ["realmadrid.com", "championat.com", "soccer.ru"]},
    {"key": "arsenal", "name": "«Арсенал» Лондон", "sport": "football", "icon": "⚽",
     "aliases": ["Арсенал", "Arsenal"],
     "sites": ["arsenal.com", "championat.com", "soccer.ru"]},
    {"key": "milan", "name": "«Милан»", "sport": "football", "icon": "⚽",
     "aliases": ["Милан", "Milan", "AC Milan"],
     "sites": ["acmilan.com", "championat.com", "soccer.ru"]},
]

# Глобальный (не per-club) список доменов для Tavily include_domains — по
# выбору пользователя, вместо неограниченного поиска: тот показал точность
# выше, чем старые узкие per-club sites, но иногда всё равно промахивался
# на нерелевантные источники (например, UFC/MMA для Автомобилиста). Список
# осознанно с запасом на будущее — под расширение числа клубов и добавление
# КХЛ/НХЛ, поэтому включает источники сверх текущих 6 клубов.
#
TAVILY_INCLUDE_DOMAINS = [
    "hc-avto.ru", "khl.ru", "championat.com", "news.sportbox.ru", "sports.ru",
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


# --- Поиск (Tavily) и обращение к DeepSeek ----------------------------------

async def collect_web_context_tavily(query: str, sites: list[str]) -> str:
    """Поиск через Tavily — сразу очищенный текст, без ручного
    скачивания страниц и парсинга HTML. НЕ используем поле answer —
    оно иногда путает часовые пояса, полагаемся только на content
    из результатов и отдаём это на разбор DeepSeek, как раньше.

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
                # advanced вместо basic — 2 кредита вместо 1, но живой тест
                # показал, что basic плохо ранжирует официальный сайт клуба
                # среди похожих страниц (пропустил кубковый матч Урала,
                # хотя fc-ural.ru был в белом списке). При 6 клубах и
                # редких проверках результата это остаётся далеко в рамках
                # бесплатного лимита Tavily.
                "search_depth": "advanced",
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


async def deepseek_completion(messages: list[dict]) -> str:
    """Вызов DeepSeek через OpenAI-совместимый API с постоянным ключом."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-v4-flash",
                    "messages": messages,
                    "stream": False,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                raw = await resp.text()
                status = resp.status
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        raise RuntimeError(f"DeepSeek: сетевая ошибка {type(e).__name__}: {e}") from e

    try:
        data = json.loads(raw)
    except ValueError as e:
        raise RuntimeError(
            f"DeepSeek вернул не-JSON ответ (статус {status}): {raw[:200]}"
        ) from e
    if status != 200:
        error = data.get("error", {})
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(message or f"DeepSeek: ошибка запроса, статус {status}")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"DeepSeek вернул неожиданный ответ: {raw[:200]}") from e
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek вернул пустой ответ")
    return content


async def ask_deepseek_with_context(question: str, context: str, system_extra: str = "") -> str:
    """Спрашивает DeepSeek по уже готовому контексту, без нового похода в
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
        f"события нет, и отвечай «НЕТ», а не давай предположительный ответ.\n"
        f"Учитывай только основную взрослую команду указанного клуба. Всегда "
        f"игнорируй U-19/U-21/U-23, молодёжные, юношеские, резервные, вторые, "
        f"женские команды, дубли и академии, даже если они стоят выше в выдаче."
    )
    if system_extra:
        system_text += f"\n{system_extra}"
    if context:
        system_text += f"\n\nМатериалы из интернета по теме вопроса:\n{context}"
    else:
        system_text += "\n\nВ интернете ничего найти не удалось."
    messages = [{"role": "system", "content": system_text}, {"role": "user", "content": question}]
    return await deepseek_completion(messages)


async def ask_deepseek(question: str, sites: list[str], search_query: str | None = None) -> tuple[str, str]:
    """Ищет материалы в интернете и спрашивает DeepSeek. Возвращает
    (ответ, собранный контекст) — контекст нужен, чтобы потом проверить,
    не придумала ли модель факты, и чтобы можно было переспросить без
    нового поиска."""
    context = await collect_web_context_tavily(search_query or question, sites)
    answer = await ask_deepseek_with_context(question, context)
    return answer, context


def find_data_line(answer: str, min_pipes: int = 2) -> str | None:
    """Модель иногда оформляет ответ markdown-таблицей (шапка + разделитель +
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


FORBIDDEN_SQUAD_RE = re.compile(
    r"(?:\b(?:u|ю)\s*[-–—]?\s*\d{1,2}\b|"
    r"\b(?:молод[её]ж\w*|юнош\w*|резерв\w*|дубл\w*|академ\w*|"
    r"фарм[-\s]?клуб\w*|женск\w*|youth\w*|junior\w*|reserve\w*|"
    r"academy\w*|women\w*|ladies\w*)\b|"
    r"\b(?:вторая|резервная)\s+команд\w*\b)",
    re.IGNORECASE,
)
SECOND_TEAM_SUFFIX_RE = re.compile(r"(?:[-–—]\s*2\b|\s+ii\b|\s+b\s*$)", re.IGNORECASE)


def has_forbidden_squad_label(*texts: str) -> bool:
    """Программный запрет на матчи неосновных составов."""
    return any(
        FORBIDDEN_SQUAD_RE.search(text or "")
        or SECOND_TEAM_SUFFIX_RE.search(text or "")
        for text in texts
    )


def answer_says_no(answer: str) -> bool:
    return any(
        line.strip().upper() == "НЕТ"
        for line in answer.splitlines()
        if line.strip()
    )


def normalize_team_name(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[«»\"'().,]", " ", text)
    text = re.sub(r"[-–—_/]+", " ", text)
    words = re.findall(r"[а-яa-z0-9]+", text)
    generic = {"фк", "хк", "мфк", "fc", "hc", "cf", "afc", "club"}
    return " ".join(word for word in words if word not in generic)


def team_name_matches(reported: str, expected_names: list[str]) -> bool:
    """Сопоставляет русское/латинское имя команды и допускает клубные префиксы."""
    reported_norm = normalize_team_name(reported)
    if not reported_norm:
        return False
    reported_tokens = set(reported_norm.split())
    for expected in expected_names:
        expected_norm = normalize_team_name(expected)
        if not expected_norm:
            continue
        if reported_norm == expected_norm:
            return True
        if len(expected_norm) >= 4 and (
            reported_norm in expected_norm or expected_norm in reported_norm
        ):
            return True
        expected_tokens = {token for token in expected_norm.split() if len(token) >= 4}
        if expected_tokens and reported_tokens & expected_tokens:
            return True
    return False


def parse_goals(text: str) -> int:
    if not re.fullmatch(r"\d{1,2}", text.strip()):
        raise ValueError(f"число голов имеет неверный формат: {text!r}")
    return int(text)


def score_pair_mentioned(first: int, second: int, context: str) -> bool:
    """Проверяет счёт в исходном порядке источника, включая переносы строк."""
    if not context:
        return False
    pattern = rf"(?<!\d){first}\s*[:\-–—]\s*{second}(?!\d)"
    return bool(re.search(pattern, context))


def resolve_reported_result(
    team1: str,
    goals1_raw: str,
    team2: str,
    goals2_raw: str,
    club: dict,
    rival_hint: str,
    context: str,
) -> tuple[str, str]:
    """Проверяет участников и сам вычисляет исход с точки зрения нашего клуба."""
    if has_forbidden_squad_label(team1, team2):
        raise ValueError(f"обнаружен неосновной состав: {team1!r} — {team2!r}")

    goals1 = parse_goals(goals1_raw)
    goals2 = parse_goals(goals2_raw)
    if not score_pair_mentioned(goals1, goals2, context):
        raise ValueError(
            f"счёт {goals1}:{goals2} в указанном порядке не подтверждается материалами"
        )

    club_names = [club["name"], *club.get("aliases", [])]
    team1_is_club = team_name_matches(team1, club_names)
    team2_is_club = team_name_matches(team2, club_names)
    team1_is_rival = team_name_matches(team1, [rival_hint])
    team2_is_rival = team_name_matches(team2, [rival_hint])

    if team1_is_club and team2_is_rival and not team2_is_club:
        club_goals, rival_goals = goals1, goals2
    elif team2_is_club and team1_is_rival and not team1_is_club:
        club_goals, rival_goals = goals2, goals1
    else:
        raise ValueError(
            f"пары команд не совпадают с ожидаемыми: {team1!r} — {team2!r}"
        )

    if club_goals > rival_goals:
        outcome = "ПОБЕДА"
    elif club_goals == rival_goals:
        outcome = "НИЧЬЯ"
    else:
        outcome = "ПОРАЖЕНИЕ"
    return f"{club_goals}:{rival_goals}", outcome


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
    if FORBIDDEN_SQUAD_RE.search(tournament) or has_forbidden_squad_label(rival):
        raise ValueError("обнаружен матч молодёжного, резервного или второго состава")
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
        f"Учитывай ТОЛЬКО основную взрослую команду. Матчи U-19/U-21/U-23, "
        f"молодёжных, юношеских, резервных, вторых, женских команд, дублей "
        f"и академий считать матчами клуба НЕЛЬЗЯ.\n"
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
        answer, context = await ask_deepseek(prompt, club["sites"], search_query)
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
                f"Любой матч U-19/U-21/U-23, молодёжной, юношеской, резервной, "
                f"второй или женской команды, дубля или академии запрещён.\n"
                f"Ответь ЕЩЁ РАЗ и ТОЛЬКО одной строкой, без пояснений, строго "
                f"в виде «Турнир|ЧЧ:ММ|ПОЯС|Место|Соперник». «Турнир» — это "
                f"название соревнования (например «Лига чемпионов»), а НЕ "
                f"название клуба и не пара «команда — соперник». Указывай "
                f"только то, что явно и однозначно написано в материалах выше "
                f"именно про {club['name']}. Если нет уверенности — одним "
                f"словом НЕТ."
            )
            answer = (await ask_deepseek_with_context(prompt, context, retry_extra)).strip()
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

def parse_result_line_football(
    answer: str, club: dict, rival_hint: str, context: str
) -> tuple[str, str] | None:
    """Возвращает счёт клуба и вычисленный программой исход."""
    line = find_data_line(answer, min_pipes=3)
    if line is None:
        if answer_says_no(answer):
            return None
        raise ValueError("нет строки результата из 4 полей")
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 4:
        raise ValueError(f"меньше 4 полей: {parts!r}")
    return resolve_reported_result(*parts[:4], club, rival_hint, context)


async def check_result_football(club: dict, rival_hint: str) -> str | None:
    today = datetime.date.today()
    prompt = (
        f"Завершился ли сегодня, {today:%d.%m.%Y}, матч {club['name']} "
        f"против {rival_hint}? Если да, найди точный счёт.\n"
        f"Ответь СТРОГО последней строкой:\n"
        f"Команда 1|Голы 1|Команда 2|Голы 2\n"
        f"Команды и голы укажи в том же порядке, как в источнике. Голы — "
        f"только целые числа. Исход матча не пиши: программа вычислит его сама.\n"
        f"Если матч ещё не завершился — последней строкой напиши НЕТ."
    )
    search_query = (
        f"{club['name']} против {rival_hint} счёт результат сегодня "
        f"{today:%d.%m.%Y} основная команда, не дубль и не молодёжный состав"
    )
    try:
        answer, context = await ask_deepseek(prompt, club["sites"], search_query)
        answer = answer.strip()
        print(f"[DEBUG] {club['name']} (результат): {answer!r}")
        try:
            parsed = parse_result_line_football(answer, club, rival_hint, context)
        except ValueError as e:
            print(f"[DEBUG] {club['name']}: {e}, переспрашиваю ещё раз (тот же контекст, без нового поиска)")
            retry_extra = (
                f"Твой предыдущий ответ был в неправильном формате — не "
                f"годится: {answer!r}.\n"
                f"Ответь ЕЩЁ РАЗ и ТОЛЬКО одной строкой, без пояснений, строго "
                f"в виде «Команда 1|Голы 1|Команда 2|Голы 2». Сохрани порядок "
                f"команд и счёта из источника; голы — только числа. Не пиши "
                f"исход матча. Если матч ещё не завершился — "
                f"ответь одним словом НЕТ."
            )
            answer = (await ask_deepseek_with_context(prompt, context, retry_extra)).strip()
            print(f"[DEBUG] {club['name']} (результат, повтор): {answer!r}")
            try:
                parsed = parse_result_line_football(answer, club, rival_hint, context)
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


def parse_result_line_hockey(
    answer: str, club: dict, rival_hint: str, context: str
) -> tuple[str, str, str] | None:
    """Возвращает счёт клуба, вычисленный исход и способ завершения."""
    line = find_data_line(answer, min_pipes=4)
    if line is None:
        if answer_says_no(answer):
            return None
        raise ValueError("нет строки результата из 5 полей")
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 5:
        raise ValueError(f"меньше 5 полей: {parts!r}")
    score, outcome = resolve_reported_result(*parts[:4], club, rival_hint, context)
    if outcome == "НИЧЬЯ":
        raise ValueError("для хоккейного матча получен ничейный итоговый счёт")
    method = parts[4].upper()
    if method not in {"ОСНОВНОЕ", "ОТ", "БУЛЛИТЫ"}:
        raise ValueError(f"неизвестный способ завершения матча: {method!r}")
    return score, outcome, method


async def check_result_hockey(club: dict, rival_hint: str) -> str | None:
    today = datetime.date.today()
    prompt = (
        f"Завершился ли сегодня, {today:%d.%m.%Y}, матч {club['name']} "
        f"против {rival_hint}? Если да, найди точный счёт и способ "
        f"завершения матча.\n"
        f"Ответь СТРОГО последней строкой:\n"
        f"Команда 1|Голы 1|Команда 2|Голы 2|СПОСОБ\n"
        f"Команды и голы укажи в том же порядке, как в источнике. Голы — "
        f"только целые числа. Исход программа вычислит сама. "
        f"СПОСОБ — одно слово: ОСНОВНОЕ (решилось в основное время), ОТ "
        f"(овертайм) или БУЛЛИТЫ.\n"
        f"Если матч ещё не завершился — последней строкой напиши НЕТ."
    )
    search_query = (
        f"{club['name']} против {rival_hint} счёт результат сегодня "
        f"{today:%d.%m.%Y} основная команда, не дубль и не молодёжный состав"
    )
    try:
        answer, context = await ask_deepseek(prompt, club["sites"], search_query)
        answer = answer.strip()
        print(f"[DEBUG] {club['name']} (результат): {answer!r}")
        try:
            parsed = parse_result_line_hockey(answer, club, rival_hint, context)
        except ValueError as e:
            print(f"[DEBUG] {club['name']}: {e}, переспрашиваю ещё раз (тот же контекст, без нового поиска)")
            retry_extra = (
                f"Твой предыдущий ответ был в неправильном формате — не "
                f"годится: {answer!r}.\n"
                f"Ответь ЕЩЁ РАЗ и ТОЛЬКО одной строкой, без пояснений, строго "
                f"в виде «Команда 1|Голы 1|Команда 2|Голы 2|СПОСОБ». Сохрани "
                f"порядок команд и счёта из источника; голы — только числа. "
                f"Не пиши исход матча. Если матч ещё не "
                f"завершился — ответь одним словом НЕТ."
            )
            answer = (await ask_deepseek_with_context(prompt, context, retry_extra)).strip()
            print(f"[DEBUG] {club['name']} (результат, повтор): {answer!r}")
            try:
                parsed = parse_result_line_hockey(answer, club, rival_hint, context)
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
