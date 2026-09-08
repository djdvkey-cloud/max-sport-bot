"""
Спортивный бот для группы в MAX — оповещения о матчах.

Отдельные клубы: подробное сообщение утром (если играют) и результат вечером.
КХЛ (кроме Автомобилиста): одно общее сообщение утром и одно вечером.

Работает через GitHub Actions, два запуска в день:
    10:00 ЕКБ — расписание на сегодня
    23:30 ЕКБ — результаты за сегодня
Скрипт сам определяет, что делать, по текущему часу.

ИИ — связка Yandex Search + GigaChat, та же, что у футбольного бота в
Telegram: поиск и модель отдельными вызовами, оба сервиса российские,
работают без карты.

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (в GitHub → Settings → Secrets):
    MAX_BOT_TOKEN      — токен от @MasterBot
    MAX_CHAT_ID        — id группы в MAX
    GIGACHAT_AUTH_KEY  — ключ авторизации с developers.sber.ru
    YANDEX_API_KEY     — API-ключ Yandex Cloud (Search API)
    YANDEX_FOLDER_ID   — Folder ID каталога в Yandex Cloud

ЗАПУСК:
    python sport_bot.py
"""

import asyncio
import base64
import re
import datetime
import json
import os
import uuid
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup
from maxapi import Bot

MAX_BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
MAX_CHAT_ID = int(os.environ["MAX_CHAT_ID"])
GIGACHAT_AUTH_KEY = os.environ["GIGACHAT_AUTH_KEY"]
YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]

YEKB_TZ = ZoneInfo("Asia/Yekaterinburg")
MORNING_HOUR = 10   # расписание на сегодня
EVENING_HOUR = 23   # результаты за сегодня (запуск в 23:30, но сверяем по часу)
PARALLEL_CHECKS = 4  # сколько клубов проверять одновременно


def batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# --- Что отслеживаем -------------------------------------------------------

INDIVIDUAL_CLUBS = [
    {"name": "ХК «Автомобилист»", "icon": "🏒",
     "sites": ["khl.ru", "hc-avto.ru", "championat.com"]},
    {"name": "ФК «Урал»", "icon": "⚽",
     "sites": ["fc-ural.ru", "fnl.pro", "championat.com"]},
    {"name": "МФК «Синара»", "icon": "🥅",
     "sites": ["superliga.rfs.ru", "mfkviz.ru", "futsal.rfs.ru"]},
    {"name": "«Спартак» Москва", "icon": "⚽",
     "sites": ["spartak.com", "championat.com", "sports.ru"]},
    {"name": "«Зенит»", "icon": "⚽",
     "sites": ["fc-zenit.ru", "championat.com", "sports.ru"]},
    {"name": "«Реал Мадрид»", "icon": "⚽",
     "sites": ["realmadrid.com", "championat.com", "soccer.ru"]},
    {"name": "«Барселона»", "icon": "⚽",
     "sites": ["fcbarcelona.com", "championat.com", "soccer.ru"]},
    {"name": "«Арсенал»", "icon": "⚽",
     "sites": ["arsenal.com", "championat.com", "soccer.ru"]},
    {"name": "«Манчестер Сити»", "icon": "⚽",
     "sites": ["mancity.com", "championat.com", "soccer.ru"]},
    {"name": "«Манчестер Юнайтед»", "icon": "⚽",
     "sites": ["manutd.com", "championat.com", "soccer.ru"]},
    {"name": "«Челси»", "icon": "⚽",
     "sites": ["chelseafc.com", "championat.com", "soccer.ru"]},
    {"name": "«Милан»", "icon": "⚽",
     "sites": ["acmilan.com", "championat.com", "soccer.ru"]},
    {"name": "«Боруссия» Дортмунд", "icon": "⚽",
     "sites": ["bvb.de", "championat.com", "soccer.ru"]},
    {"name": "«Бавария»", "icon": "⚽",
     "sites": ["fcbayern.com", "championat.com", "soccer.ru"]},
    {"name": "Сборная России (футбол)", "icon": "🇷🇺",
     "sites": ["rfs.ru", "championat.com", "sports.ru"]},
]

KHL_GROUP_SITES = ["khl.ru", "championat.com", "sports.ru"]


# --- Поиск (Yandex Search) и обращение к GigaChat --------------------------

async def yandex_search_urls(query: str) -> list[str]:
    """Ищет через Yandex Search API, возвращает список ссылок."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://searchapi.api.cloud.yandex.net/v2/web/search",
            headers={"Authorization": f"Api-Key {YANDEX_API_KEY}"},
            json={
                "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query},
                "folderId": YANDEX_FOLDER_ID,
                "responseFormat": "FORMAT_XML",
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            raw = await resp.text()
            if resp.status != 200:
                print(f"[DEBUG] Yandex Search статус={resp.status}, тело={raw[:300]!r}")
                return []
    try:
        data = json.loads(raw)
        xml_text = base64.b64decode(data["rawData"]).decode("utf-8", errors="ignore")
        root = ET.fromstring(xml_text)
        urls = []
        for doc in root.iter("doc"):
            url_el = doc.find("url")
            if url_el is not None and url_el.text:
                urls.append(url_el.text)
            if len(urls) >= 4:
                break
        return urls
    except Exception as e:
        print(f"[DEBUG] не удалось разобрать ответ Yandex Search: {e}")
        return []


async def fetch_page_text(url: str) -> str:
    """Скачивает страницу и вытаскивает читаемый текст без HTML-тегов."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"},
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            html = await resp.text()
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)


async def collect_web_context(query: str, preferred: list[str]) -> str:
    """Ищет и читает страницы целиком — сниппеты часто не содержат таблиц
    и расписаний, а сама страница обычно да. Страницы с предпочтительных
    сайтов идут первыми."""
    urls = await yandex_search_urls(query)
    urls.sort(key=lambda u: 0 if any(h in u for h in preferred) else 1)
    print(f"[DEBUG] поиск «{query}», ссылки: {urls}")
    combined = []
    for url in urls:
        try:
            text = await fetch_page_text(url)
            if len(text) > 500:
                print(f"[DEBUG] прочитал {url}, символов: {len(text)}")
                combined.append(f"Источник {url}:\n{text[:6000]}")
        except Exception as e:
            print(f"[DEBUG] не удалось открыть {url}: {e}")
        if len(combined) >= 2:
            break
    return "\n\n---\n\n".join(combined)


# Токен GigaChat кэшируется на весь запуск — раньше каждый из 16 запросов
# получал СВОЙ токен, и при параллельной проверке это било по серверу
# авторизации разом (отсюда таймауты и «Connection reset by peer»).
_gigachat_token: dict = {"value": None, "expires_at": 0.0}
_gigachat_token_lock = asyncio.Lock()
# GigaChat не любит параллельные запросы (Too Many Requests) — поиск и чтение
# страниц пусть остаются параллельными, а вот сам вопрос к модели — по одному
_gigachat_semaphore = asyncio.Semaphore(1)


async def get_gigachat_token() -> str:
    """Возвращает действующий токен, обновляя его только когда истёк.
    Лок нужен, чтобы при параллельных запросах не полезли за новым
    токеном одновременно несколько корутин разом."""
    async with _gigachat_token_lock:
        now = asyncio.get_event_loop().time()
        if _gigachat_token["value"] and now < _gigachat_token["expires_at"]:
            return _gigachat_token["value"]
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "RqUID": str(uuid.uuid4()),
                    "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",
                },
                data={"scope": "GIGACHAT_API_PERS"},
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                raw = await resp.text()
                try:
                    data = json.loads(raw)
                except ValueError:
                    raise RuntimeError(f"GigaChat вернул не-JSON ответ на токен (статус {resp.status}): {raw[:200]}")
                if resp.status != 200:
                    raise RuntimeError(data.get("message", f"ошибка токена, статус {resp.status}"))
        _gigachat_token["value"] = data["access_token"]
        # Токен GigaChat живёт 30 минут — обновляем заранее, за 5 минут до истечения
        _gigachat_token["expires_at"] = now + 25 * 60
        print("[DEBUG] получен новый токен GigaChat")
        return _gigachat_token["value"]


async def gigachat_completion(messages: list[dict]) -> str:
    """Вызов GigaChat с уже готовым (кэшированным) токеном. Не больше одного
    одновременного запроса (семафор) и до двух повторов при «Too Many Requests»."""
    async with _gigachat_semaphore:
        for attempt in range(3):
            access_token = await get_gigachat_token()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                    json={"model": "GigaChat", "messages": messages},
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    raw = await resp.text()
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        raise RuntimeError(f"GigaChat вернул не-JSON ответ (статус {resp.status}): {raw[:200]}")

                    if resp.status == 429 and attempt < 2:
                        print(f"[DEBUG] GigaChat: Too Many Requests, пауза и повтор ({attempt + 1}/3)")
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    if resp.status == 401:
                        # Токен протух раньше времени — сбросим кэш и попробуем заново
                        _gigachat_token["value"] = None
                        if attempt < 2:
                            continue
                    if resp.status != 200:
                        raise RuntimeError(data.get("message", f"ошибка запроса, статус {resp.status}"))
                    return data["choices"][0]["message"]["content"]
        raise RuntimeError("GigaChat: превышены попытки после Too Many Requests")


async def ask_gigachat(question: str, sites: list[str], system_extra: str = "") -> str:
    """Ищет материалы в интернете (Yandex) и просит GigaChat ответить по ним."""
    context = await collect_web_context(question, sites)
    today = datetime.datetime.now(YEKB_TZ).strftime("%d.%m.%Y")
    system_text = (
        f"Сегодня {today}. Отвечай по-русски, строго в запрошенном формате, "
        f"без пояснений и markdown-разметки.\n"
        f"Время не пересчитывай между часовыми поясами — бери как в источнике, "
        f"но обязательно указывай пояс (мск, ЕКБ и т. п.)."
    )
    if system_extra:
        system_text += f"\n{system_extra}"
    if context:
        system_text += f"\n\nМатериалы из интернета по теме вопроса:\n{context}"
    else:
        system_text += "\n\nВ интернете ничего найти не удалось."
    messages = [{"role": "system", "content": system_text}, {"role": "user", "content": question}]
    return await gigachat_completion(messages)


# --- Отдельные клубы ---------------------------------------------------

def looks_like_score(text: str) -> bool:
    """«2:1», «3-2 ОТ» — похоже на счёт. «нет», «не указан» — нет."""
    return bool(re.search(r"\d+\s*[:\-]\s*\d+", text))


async def club_schedule_today(club: dict) -> str:
    """Если клуб играет СЕГОДНЯ — текст анонса, иначе пустая строка."""
    today = datetime.date.today()
    prompt = (
        f"Играет ли {club['name']} сегодня, {today:%d.%m.%Y}? Если да, найди "
        f"турнир, время начала (как в источнике, с указанием пояса), место "
        f"проведения и соперника.\n"
        f"Ответь СТРОГО последней строкой в таком виде:\n"
        f"Турнир|Время|Место|Соперник\n"
        f"Если сегодня матча нет — последней строкой напиши НЕТ."
    )
    try:
        answer = (await ask_gigachat(prompt, club["sites"])).strip()
        print(f"[DEBUG] {club['name']} (утро): {answer!r}")
        data_line = None
        for line in reversed(answer.splitlines()):
            if line.count("|") >= 3:
                data_line = line.strip()
                break
        if data_line is None or data_line.upper().startswith("НЕТ"):
            return ""
        parts = [p.strip() for p in data_line.split("|")]
        tournament, time_str, place, rival = parts[:4]
        return (f"{club['icon']} Сегодня играет {club['name']}\n"
                f"Турнир: {tournament}\n"
                f"Время: {time_str}\n"
                f"Место: {place}\n"
                f"Соперник: {rival}")
    except Exception as e:
        print(f"[DEBUG] ошибка при проверке {club['name']} (утро): {e}")
        return ""


async def club_result_today(club: dict) -> str:
    """Если у клуба СЕГОДНЯ был матч — счёт и место в таблице, иначе пусто."""
    today = datetime.date.today()
    prompt = (
        f"Был ли у {club['name']} матч сегодня, {today:%d.%m.%Y}, и он уже "
        f"завершился? Если да, найди счёт, соперника и текущее место команды "
        f"в турнирной таблице с очками.\n"
        f"Ответь СТРОГО последней строкой в таком виде:\n"
        f"Соперник|Счёт|Место в таблице|Очки\n"
        f"Если матча не было или он ещё не закончился — последней строкой "
        f"напиши НЕТ."
    )
    try:
        answer = (await ask_gigachat(prompt, club["sites"])).strip()
        print(f"[DEBUG] {club['name']} (вечер): {answer!r}")
        data_line = None
        for line in reversed(answer.splitlines()):
            if line.count("|") >= 3:
                data_line = line.strip()
                break
        if data_line is None or data_line.upper().startswith("НЕТ"):
            return ""
        parts = [p.strip() for p in data_line.split("|")]
        rival, score, place, points = parts[:4]
        if not looks_like_score(score):
            print(f"[DEBUG] {club['name']}: поле счёта не похоже на счёт ({score!r}), пропускаю")
            return ""
        return (f"✅ {club['name']} {score} {rival}\n"
                f"Место в таблице: {place} ({points} очков)")
    except Exception as e:
        print(f"[DEBUG] ошибка при проверке {club['name']} (вечер): {e}")
        return ""


# --- КХЛ группой (кроме Автомобилиста) -------------------------------------

async def khl_group_schedule() -> str:
    today = datetime.date.today()
    prompt = (
        f"Найди полное расписание матчей КХЛ на сегодня, {today:%d.%m.%Y}, "
        f"КРОМЕ игры «Автомобилиста» — её не включай, она идёт отдельным "
        f"сообщением.\n"
        f"Ответь построчно, каждая игра — время (как в источнике, с поясом) "
        f"и через тире команды: «19:00 мск — СКА — ЦСКА». Только сами строки "
        f"с играми, без пояснений и заголовков.\n"
        f"Если сегодня, кроме Автомобилиста, игр КХЛ нет — напиши НЕТ."
    )
    try:
        answer = (await ask_gigachat(prompt, KHL_GROUP_SITES)).strip()
        print(f"[DEBUG] КХЛ (утро): {answer!r}")
        if not answer or answer.upper().startswith("НЕТ") or "НЕТ" == answer.strip().upper():
            return ""
        return "🏒 КХЛ — расписание на сегодня\n\n" + answer
    except Exception as e:
        print(f"[DEBUG] ошибка при проверке КХЛ (утро): {e}")
        return ""


async def khl_group_results() -> str:
    today = datetime.date.today()
    prompt = (
        f"Найди результаты всех завершившихся сегодня, {today:%d.%m.%Y}, "
        f"матчей КХЛ, КРОМЕ игры «Автомобилиста» — она идёт отдельным "
        f"сообщением.\n"
        f"Ответь построчно, каждая игра — команды и счёт: «СКА 3:1 ЦСКА». "
        f"Только сами строки с результатами, без пояснений и заголовков.\n"
        f"Если сегодня, кроме Автомобилиста, игр не было — напиши НЕТ."
    )
    try:
        answer = (await ask_gigachat(prompt, KHL_GROUP_SITES)).strip()
        print(f"[DEBUG] КХЛ (вечер): {answer!r}")
        if not answer or answer.upper().startswith("НЕТ") or "НЕТ" == answer.strip().upper():
            return ""
        return "✅ КХЛ — результаты дня\n\n" + answer
    except Exception as e:
        print(f"[DEBUG] ошибка при проверке КХЛ (вечер): {e}")
        return ""


# --- Отправка и оркестрация -------------------------------------------------

async def send_to_group(bot: Bot, text: str) -> None:
    for attempt in range(3):
        try:
            await bot.send_message(chat_id=MAX_CHAT_ID, text=text)
            return
        except Exception as e:
            print(f"[DEBUG] попытка {attempt + 1} отправить не удалась: {e}")
            if attempt < 2:
                await asyncio.sleep(10)
    print("[DEBUG] отправить не удалось ни с одной попытки")


async def run_morning(bot: Bot) -> None:
    print("[DEBUG] === утренняя проверка расписания ===")
    blocks = []
    for group in batched(INDIVIDUAL_CLUBS, PARALLEL_CHECKS):
        results = await asyncio.gather(*(club_schedule_today(c) for c in group))
        blocks += [r for r in results if r]
    khl = await khl_group_schedule()
    if khl:
        blocks.append(khl)

    if not blocks:
        print("[DEBUG] сегодня никто не играет, ничего не отправляю")
        return
    for block in blocks:
        await send_to_group(bot, block)


async def run_evening(bot: Bot) -> None:
    print("[DEBUG] === вечерняя проверка результатов ===")
    blocks = []
    for group in batched(INDIVIDUAL_CLUBS, PARALLEL_CHECKS):
        results = await asyncio.gather(*(club_result_today(c) for c in group))
        blocks += [r for r in results if r]
    khl = await khl_group_results()
    if khl:
        blocks.append(khl)

    if not blocks:
        print("[DEBUG] сегодня результатов нет, ничего не отправляю")
        return
    for block in blocks:
        await send_to_group(bot, block)


async def main():
    now = datetime.datetime.now(YEKB_TZ)
    print(f"[DEBUG] сейчас по Екатеринбургу: {now:%d.%m.%Y %H:%M}")

    bot = Bot(MAX_BOT_TOKEN)
    try:
        if now.hour < 16:
            await run_morning(bot)
        else:
            await run_evening(bot)
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
