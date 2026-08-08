"""
Семейный/футбольный Telegram-бот на Gemini API со встроенным веб-поиском.

Gemini сам ищет в Google и читает страницы — отдельный поисковый сервис
(как раньше Yandex Search) больше не нужен.

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (в Amvera → «Переменные»):
    BOT_TOKEN      — токен от @BotFather
    GEMINI_API_KEY — ключ с aistudio.google.com

ЗАПУСК:
    python bot.py
"""

import asyncio
import base64
import datetime
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject

BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]
YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]
GEMINI_MODEL = "gemini-3.5-flash-lite"   # 15 запросов/мин, 500 в день на бесплатном тарифе

BOT_NAME = "мяч"            # обращение без команды: «Мяч, когда тренировка?»
MAX_HISTORY_MESSAGES = 10   # сколько последних сообщений помнить (5 пар вопрос-ответ)
ALLOWED_CHAT_IDS = {-5579173684}      # где боту разрешено отвечать
TRAINING_POLL_CHAT_ID = -5579173684   # куда постить опросы и напоминания
GAME_TIME = "21:30"                   # время тренировки, попадает в текст опроса
YEKB_TZ = ZoneInfo("Asia/Yekaterinburg")
STATS_FILE = "/data/stats.json"   # статистика игроков по чатам — не удалять

# Первый опрос — 09.08.2026 в 12:00 по Екатеринбургу, дальше каждые 2 дня.
# Дата в тексте опроса всегда «завтра» относительно дня публикации:
# опрос 09.08 → тренировка 10.08, опрос 11.08 → тренировка 12.08 и т.д.
POLL_START = datetime.datetime(2026, 8, 9, 12, 0, tzinfo=YEKB_TZ)
POLL_INTERVAL_DAYS = 2   # точка отсчёта расписания опроса — не удалять

# --- Предпочитаемые источники по видам спорта -----------------------------
# Gemini ищет сам, поэтому это не жёсткий фильтр, а подсказка в промпте.

NHL_KEYWORDS = ["нхл", "nhl"]
NHL_SITES = "nhl.com, nhl.ru, hockey-reference.com"

FUTSAL_KEYWORDS = ["футзал", "мини-футбол", "синара", "мфк виз", "виз-синара"]
FUTSAL_SITES = "superliga.rfs.ru, mfkviz.ru, futsal.rfs.ru, uefa.com"

KHL_KEYWORDS = ["кхл", "khl", "автомобилист", "хоккей"]
KHL_SITES = "khl.ru, hc-avto.ru, news.sportbox.ru"

OTHER_SPORTS_SITES = (
    "premierliga.ru, uefa.com, fnl.pro, fc-ural.ru, premierleague.com, "
    "news.sportbox.ru, championat.com, sports.ru, matchtv.ru, "
    "sport-express.ru, sport24.ru, metaratings.ru, laliga.com, "
    "bundesliga.com, legaseriea.it"
)
SPORTS_KEYWORDS = [
    "матч", "игра", "играет", "команда", "чемпионат", "лига", "соперник",
    "турнир", "счёт", "футбол", "баскетбол", "теннис", "спортсмен", "спорт",
    "рпл", "апл", "лч", "премьер-лига", "урал", "нба",
] + NHL_KEYWORDS + FUTSAL_KEYWORDS + KHL_KEYWORDS

# История разговора отдельно с каждым человеком в каждом чате
conversation_history: dict[tuple[int, int], list[dict]] = {}


def is_sports_question(question: str) -> bool:
    return any(kw in question.lower() for kw in SPORTS_KEYWORDS)


def preferred_sites(question: str) -> str:
    """Какие источники предпочесть для этого вопроса.
    Порядок важен: НХЛ проверяется раньше КХЛ, т.к. слово «хоккей» есть у обоих."""
    q = question.lower()
    if any(kw in q for kw in NHL_KEYWORDS):
        return NHL_SITES
    if any(kw in q for kw in FUTSAL_KEYWORDS):
        return FUTSAL_SITES
    if any(kw in q for kw in KHL_KEYWORDS):
        return KHL_SITES
    return OTHER_SPORTS_SITES


def get_history(chat_id: int, user_id: int) -> list[dict]:
    return conversation_history.get((chat_id, user_id), [])


def update_history(chat_id: int, user_id: int, question: str, answer: str) -> None:
    key = (chat_id, user_id)
    history = conversation_history.get(key, [])
    history.append({"role": "user", "parts": [{"text": question}]})
    history.append({"role": "model", "parts": [{"text": answer}]})
    conversation_history[key] = history[-MAX_HISTORY_MESSAGES:]


# Короткий таймаут: на хостинге связь с Telegram иногда пропадает, и ждать
# ответа по минуте бессмысленно — лучше быстро упасть и повторить попытку.
# --- Статистика игроков ---------------------------------------------------

def load_stats() -> dict:
    """Статистика по чатам:
    {chat_id: {"players": {игрок: {games, goals, assists}}, "last": [записи]}}
    "last" — последняя внесённая игра, нужна для /отменить."""
    if not os.path.exists(STATS_FILE):
        return {}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[DEBUG] не удалось прочитать статистику: {e}")
        return {}
    # Совместимость со старым форматом без "players"
    for chat_key, chat_data in list(data.items()):
        if "players" not in chat_data:
            data[chat_key] = {"players": chat_data, "last": []}
    return data


def chat_stats_of(stats: dict, chat_id: int) -> dict:
    return stats.setdefault(str(chat_id), {"players": {}, "last": []})


def save_stats(stats: dict) -> None:
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def parse_match_line(text: str) -> tuple[list[tuple[str, int, int]], list[str]]:
    """Разбирает «Иванов 2+1, Петров 0+3» → [(имя, голы, передачи), ...].
    Вторым значением возвращает куски, которые не удалось разобрать."""
    players, errors = [], []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = re.match(r"^(.+?)\s+(\d+)\s*\+\s*(\d+)$", chunk)
        if match:
            name = match.group(1).strip().title()
            players.append((name, int(match.group(2)), int(match.group(3))))
        else:
            errors.append(chunk)
    return players, errors


bot = Bot(token=BOT_TOKEN, session=AiohttpSession(timeout=20))
dp = Dispatcher()
scheduler = AsyncIOScheduler()


async def yandex_search_urls(query: str) -> list[str]:
    """Ищет через Yandex Search API, возвращает список ссылок.
    Подробно логирует ответ — чтобы при пустом результате была видна причина."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://searchapi.api.cloud.yandex.net/v2/web/search",
            headers={"Authorization": f"Api-Key {YANDEX_API_KEY}"},
            json={
                "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query},
                "folderId": YANDEX_FOLDER_ID,
                "responseFormat": "FORMAT_XML",
            },
        ) as resp:
            raw = await resp.text()
            if resp.status != 200:
                print(f"[DEBUG] Yandex Search статус={resp.status}, тело={raw[:400]!r}")
                return []
    try:
        data = json.loads(raw)
    except ValueError:
        print(f"[DEBUG] Yandex Search вернул не-JSON: {raw[:300]!r}")
        return []
    if "rawData" not in data:
        print(f"[DEBUG] Yandex Search без rawData, ответ: {str(data)[:400]!r}")
        return []
    try:
        xml_text = base64.b64decode(data["rawData"]).decode("utf-8", errors="ignore")
        root = ET.fromstring(xml_text)
        error_el = root.find(".//error")
        if error_el is not None:
            print(f"[DEBUG] Yandex Search ошибка в XML: {''.join(error_el.itertext())!r}")
            return []
        urls = []
        for doc in root.iter("doc"):
            url_el = doc.find("url")
            if url_el is not None and url_el.text:
                urls.append(url_el.text)
            if len(urls) >= 4:
                break
        if not urls:
            print(f"[DEBUG] Yandex Search: ссылок нет. Начало XML: {xml_text[:400]!r}")
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


def has_own_topic(question: str) -> bool:
    """Есть ли в вопросе собственная тема: название лиги/команды или имя
    собственное. Если есть — вопрос самодостаточный, контекст ему не нужен."""
    q = question.lower()
    topic_words = NHL_KEYWORDS + FUTSAL_KEYWORDS + KHL_KEYWORDS + [
        "рпл", "апл", "лч", "премьер-лига", "урал", "нба", "чемпионат", "лига",
    ]
    if any(w in q for w in topic_words):
        return True
    # Слово с заглавной буквы не в начале предложения — скорее всего, название
    return any(w[:1].isupper() for w in question.split()[1:])


def build_search_query(question: str, history: list[dict]) -> str:
    """Для уточняющих вопросов («а во сколько?», «где играют?») добавляет
    предыдущий вопрос — иначе поиск теряет тему разговора. Самодостаточные
    вопросы (где тема названа прямо) оставляет как есть."""
    if has_own_topic(question):
        return question
    words = question.split()
    looks_like_followup = len(words) <= 6 or question.lower().startswith(("а ", "и ", "ещё", "еще"))
    if looks_like_followup and history:
        prior = [m["parts"][0]["text"] for m in history if m.get("role") == "user"]
        if prior:
            return f"{prior[-1]} {question}"
    return question


async def collect_web_context(query: str, preferred: str = "") -> str:
    """Ищет и читает страницы целиком — сниппеты часто не содержат таблиц
    с расписаниями, а сама страница обычно да.
    Фильтр site: в запрос НЕ добавляем: Яндекс не переваривает много сайтов
    через OR и возвращает «ничего не найдено». Вместо этого поднимаем
    страницы с предпочтительных сайтов выше в списке найденного."""
    urls = await yandex_search_urls(query)
    if preferred:
        pref_hosts = [s.strip() for s in preferred.split(",") if s.strip()]
        urls.sort(key=lambda u: 0 if any(h in u for h in pref_hosts) else 1)
    print(f"[DEBUG] поиск, ссылки (предпочтительные первыми): {urls}")
    collected = []
    for url in urls:
        try:
            text = await fetch_page_text(url)
            if len(text) > 500:
                print(f"[DEBUG] прочитал {url}, символов: {len(text)}")
                collected.append(f"Источник {url}:\n{text[:8000]}")
            else:
                print(f"[DEBUG] страница {url} дала мало текста ({len(text)}), пропускаю")
        except Exception as e:
            print(f"[DEBUG] не удалось открыть {url}: {e}")
        if len(collected) >= 2:
            break
    return "\n\n---\n\n".join(collected)


async def ask_gemini(question: str, history: list[dict] | None = None,
                     system_extra: str = "") -> str:
    """Задаёт вопрос Gemini со включённым веб-поиском Google.
    Модель сама решает, искать ли, формирует запросы и читает страницы."""
    history = history or []
    today = datetime.date.today().strftime("%d.%m.%Y")
    system_text = (
        f"Сегодня {today}. Ты — помощник в семейном чате, отвечай по-русски, "
        f"кратко и по делу.\n"
        f"Если ниже даны материалы из интернета — отвечай по ним, а не по своим "
        f"внутренним знаниям: они устаревают. Не пиши «информация не объявлена», "
        f"если в материалах есть конкретные даты и время.\n"
        f"Не пересчитывай время между часовыми поясами сам: указывай время так, "
        f"как оно дано в источнике, и пиши, какой это пояс.\n"
        f"Если вопрос подразумевает список (например, все матчи за день) — "
        f"перечисли ВСЕ подходящие пункты, а не только первый."
    )
    if system_extra:
        system_text += f"\n{system_extra}"

    payload = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": history + [{"role": "user", "parts": [{"text": question}]}],
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent")
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            raw = await resp.text()
            if resp.status != 200:
                print(f"[DEBUG] Gemini статус={resp.status}, тело={raw[:500]!r}")
                raise RuntimeError(f"Gemini вернул статус {resp.status}: {raw[:200]}")
            data = json.loads(raw)

    candidate = data["candidates"][0]
    parts = candidate.get("content", {}).get("parts", [])
    answer = "".join(p.get("text", "") for p in parts).strip()

    if not answer:
        raise RuntimeError(f"Gemini вернул пустой ответ: {raw[:300]}")
    return answer


async def answer_with_retry(message: types.Message, text: str, attempts: int = 3, delay: int = 5):
    """Отправка с повторными попытками — связь с Telegram иногда кратковременно пропадает."""
    last_error = None
    for attempt in range(attempts):
        try:
            return await message.answer(text)
        except Exception as e:
            last_error = e
            print(f"[DEBUG] попытка {attempt + 1} отправить сообщение не удалась: {e}")
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
    raise last_error


async def handle_question(message: types.Message, question: str) -> None:
    """Общая логика для /ии и обращения по имени."""
    # «Думаю...» — приятный, но не обязательный шаг. Если Telegram сейчас
    # недоступен (на хостинге это бывает), не блокируемся на нём, а работаем дальше.
    try:
        thinking_msg = await asyncio.wait_for(message.answer("Думаю..."), timeout=15)
    except Exception as e:
        print(f"[DEBUG] не удалось отправить «Думаю...», продолжаю без него: {e}")
        thinking_msg = None
    history = get_history(message.chat.id, message.from_user.id)
    system_extra = ""
    if is_sports_question(question):
        sites = preferred_sites(question)
        search_query = build_search_query(question, history)
        print(f"[DEBUG] спортивный вопрос, поисковый запрос: {search_query!r}")
        context = await collect_web_context(search_query, sites)
        if context:
            system_extra = f"Материалы из интернета по теме вопроса:\n{context}"
    try:
        answer = await ask_gemini(question, history, system_extra)
        update_history(message.chat.id, message.from_user.id, question, answer)
    except Exception as e:
        print(f"[DEBUG] ошибка запроса к Gemini: {e}")
        answer = "Не получилось получить ответ, попробуйте ещё раз чуть позже."
    try:
        if thinking_msg is not None:
            await thinking_msg.edit_text(answer)
        else:
            await answer_with_retry(message, answer)
    except Exception as e:
        print(f"[DEBUG] не удалось отредактировать сообщение, шлю новым: {e}")
        await answer_with_retry(message, answer)


# --- Плановые задачи ------------------------------------------------------

async def send_reminder(chat_id: int, text: str) -> None:
    print(f"[DEBUG] отправляю напоминание в чат {chat_id}: {text!r}")
    await bot.send_message(chat_id, f"⏰ Напоминание: {text}")


async def send_training_poll() -> None:
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    question = f"Футбол Лестех. {tomorrow.strftime('%d.%m.%y')} в {GAME_TIME.replace(':', '-')}."
    print(f"[DEBUG] отправляю автоматический опрос: {question!r}")
    await bot.send_poll(
        chat_id=TRAINING_POLL_CHAT_ID,
        question=question,
        options=["Иду", "Не иду"],
        is_anonymous=False,
    )


# Клубы, по которым бот сам присылает анонс ближайшей игры каждое утро
TRACKED_CLUBS = [
    {
        "name": "МФК «Синара» (Екатеринбург)",
        "query": "МФК Синара Екатеринбург ближайший матч расписание",
        "sites": FUTSAL_SITES,
        "icon": "🥅",
    },
    {
        "name": "ФК «Урал» (Екатеринбург)",
        "query": "ФК Урал Екатеринбург ближайший матч расписание",
        "sites": "fc-ural.ru, fnl.pro, championat.com",
        "icon": "⚽",
    },
    {
        "name": "ХК «Автомобилист» (Екатеринбург)",
        "query": "ХК Автомобилист Екатеринбург ближайший матч расписание",
        "sites": KHL_SITES,
        "icon": "🏒",
    },
]


async def next_match_for_club(club: dict) -> str:
    """Возвращает строку с анонсом ближайшей игры клуба или пустую строку."""
    today = datetime.date.today()
    try:
        context = await collect_web_context(club["query"], preferred=club["sites"])
        if not context:
            print(f"[DEBUG] {club['name']}: материалов не нашлось")
            return ""
        prompt = (
            f"Вот материалы из интернета:\n{context}\n\n"
            f"Найди БЛИЖАЙШИЙ предстоящий матч команды {club['name']}, "
            f"считая от {today.strftime('%d.%m.%Y')} включительно. "
            f"Ответь СТРОГО в одном из двух видов, без пояснений и лишних слов:\n"
            f"Турнир|ДД.ММ.ГГГГ|ЧЧ:ММ|Место проведения|Соперник\n"
            f"НЕТ — если в материалах нет данных о предстоящем матче.\n"
            f"Время указывай так, как в источнике, не пересчитывай пояса."
        )
        answer = (await ask_gemini(prompt)).strip()
        print(f"[DEBUG] {club['name']}: {answer!r}")
        if answer.upper().startswith("НЕТ"):
            return ""
        parts = [p.strip() for p in answer.split("|")]
        if len(parts) < 5:
            print(f"[DEBUG] {club['name']}: неожиданный формат, пропускаю")
            return ""
        tournament, date_str, time_str, place, rival = parts[:5]
        return (f"{club['icon']} Предстоящий матч {club['name']} проведёт:\n"
                f"   {tournament}, {date_str} в {time_str}\n"
                f"   Место: {place}\n"
                f"   Соперник: {rival}")
    except Exception as e:
        print(f"[DEBUG] ошибка при проверке {club['name']}: {e}")
        return ""


async def check_clubs_games() -> None:
    """Каждое утро присылает в чат анонсы ближайших игр отслеживаемых клубов."""
    print("[DEBUG] утренняя проверка матчей клубов")
    blocks = []
    for club in TRACKED_CLUBS:
        line = await next_match_for_club(club)
        if line:
            blocks.append(line)
    if not blocks:
        print("[DEBUG] ни по одному клубу данных не нашлось, ничего не отправляю")
        return
    text = "\n\n".join(blocks)
    for attempt in range(3):
        try:
            await bot.send_message(TRAINING_POLL_CHAT_ID, text)
            break
        except Exception as e:
            print(f"[DEBUG] попытка {attempt + 1} отправить анонсы не удалась: {e}")
            if attempt < 2:
                await asyncio.sleep(5)


# --- Команды --------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    print(f"[DEBUG] сработал /start, от {message.from_user.first_name}")
    await message.answer(
        f"Привет, {message.from_user.first_name}! Я бот «Мяч».\n"
        f"Команда /help покажет, что я умею."
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    print("[DEBUG] сработал /help")
    await message.answer(
        "Доступные команды:\n"
        "/start — приветствие\n"
        "/help — это сообщение\n"
        "/id — узнать ID этого чата\n"
        "/ии вопрос — спросить что-нибудь\n"
        "/опрос — создать опрос «Кто идёт на тренировку?»\n"
        "/напомнить ДД.ММ ЧЧ:ММ текст — запланировать напоминание\n"
        "/забыть — начать разговор заново\n"
        "\nСтатистика игр:\n"
        "/матч Иванов 2+1, Петров 0+3 — записать результаты игры\n"
        "/статистика — таблица игроков\n"
        "/отменить — убрать последнюю записанную игру\n"
        "/переименовать Старое = Новое — переименовать игрока\n"
        "/обнулить да — стереть всю статистику чата\n"
        "Или просто начните сообщение с «Мяч» — например: «Мяч, когда тренировка?»"
    )


@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    print("[DEBUG] сработал /id")
    await message.answer(f"ID этого чата: {message.chat.id}")


@dp.message(Command("ии"), F.chat.id.in_(ALLOWED_CHAT_IDS))
async def cmd_ai(message: types.Message, command: CommandObject):
    print(f"[DEBUG] сработал /ии, вопрос: {command.args!r}")
    if not command.args:
        await message.answer("Напишите вопрос после команды, например:\n/ии когда играет Урал")
        return
    await handle_question(message, command.args)


@dp.message(Command("забыть"), F.chat.id.in_(ALLOWED_CHAT_IDS))
async def cmd_forget(message: types.Message):
    print("[DEBUG] сработал /забыть")
    conversation_history.pop((message.chat.id, message.from_user.id), None)
    await message.answer("Хорошо, забыл, о чём мы говорили. Начинаем с чистого листа.")


@dp.message(Command("опрос"), F.chat.id.in_(ALLOWED_CHAT_IDS))
async def cmd_poll(message: types.Message):
    print("[DEBUG] сработал /опрос")
    await message.answer_poll(
        question="Кто идёт на тренировку?",
        options=["Иду", "Не иду", "Пока не знаю"],
        is_anonymous=False,
    )


@dp.message(Command("напомнить"), F.chat.id.in_(ALLOWED_CHAT_IDS))
async def cmd_remind(message: types.Message, command: CommandObject):
    print(f"[DEBUG] сработал /напомнить, аргументы: {command.args!r}")
    usage = "Формат: /напомнить ДД.ММ ЧЧ:ММ текст\nНапример: /напомнить 15.08 19:00 Игра с Соколом"
    if not command.args:
        await message.answer(usage)
        return
    parts = command.args.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(usage)
        return
    date_str, time_str, text = parts
    now = datetime.datetime.now()
    try:
        remind_dt = datetime.datetime.strptime(f"{date_str}.{now.year} {time_str}", "%d.%m.%Y %H:%M")
        if remind_dt < now:
            remind_dt = remind_dt.replace(year=now.year + 1)
    except ValueError:
        await message.answer(f"Не понял дату/время.\n{usage}")
        return
    scheduler.add_job(send_reminder, "date", run_date=remind_dt, args=[message.chat.id, text])
    await message.answer(f"Хорошо, напомню {remind_dt.strftime('%d.%m в %H:%M')}: «{text}»")


@dp.message(Command("матч"), F.chat.id.in_(ALLOWED_CHAT_IDS))
async def cmd_match(message: types.Message, command: CommandObject):
    print(f"[DEBUG] сработал /матч, аргументы: {command.args!r}")
    usage = ("Формат: /матч Фамилия голы+передачи, Фамилия голы+передачи\n"
             "Например: /матч Иванов 2+1, Петров 0+3, Сидоров 1+0\n"
             "Каждому перечисленному игроку засчитывается одна сыгранная игра.")
    if not command.args:
        await message.answer(usage)
        return
    players, errors = parse_match_line(command.args)
    if not players:
        await message.answer(f"Не понял ни одного игрока.\n{usage}")
        return

    stats = load_stats()
    chat = chat_stats_of(stats, message.chat.id)
    for name, goals, assists in players:
        rec = chat["players"].setdefault(name, {"games": 0, "goals": 0, "assists": 0})
        rec["games"] += 1
        rec["goals"] += goals
        rec["assists"] += assists
    chat["last"] = [list(p) for p in players]   # для /отменить
    save_stats(stats)

    lines = [f"Записал игру, участников: {len(players)}"]
    for name, goals, assists in players:
        lines.append(f"• {name}: {goals}+{assists}")
    if errors:
        lines.append(f"\nНе разобрал: {', '.join(errors)}")
    lines.append("\n/статистика — таблица, /отменить — убрать эту запись")
    await message.answer("\n".join(lines))


@dp.message(Command("статистика"), F.chat.id.in_(ALLOWED_CHAT_IDS))
async def cmd_stats(message: types.Message):
    print("[DEBUG] сработал /статистика")
    chat_stats = load_stats().get(str(message.chat.id), {}).get("players", {})
    if not chat_stats:
        await message.answer(
            "Статистики пока нет. Добавьте первую игру:\n"
            "/матч Иванов 2+1, Петров 0+3"
        )
        return
    rows = []
    for name, r in chat_stats.items():
        points = r["goals"] + r["assists"]
        koef = points / r["games"] if r["games"] else 0
        rows.append((koef, points, name, r))
    rows.sort(reverse=True)

    lines = ["📊 Статистика (И — игры, Г — голы, П — передачи, К — очки за игру)\n"]
    for i, (koef, points, name, r) in enumerate(rows, 1):
        lines.append(
            f"{i}. {name} — И:{r['games']} Г:{r['goals']} П:{r['assists']} "
            f"О:{points} К:{koef:.2f}"
        )
    await message.answer("\n".join(lines))


@dp.message(Command("обнулить"), F.chat.id.in_(ALLOWED_CHAT_IDS))
async def cmd_reset_stats(message: types.Message, command: CommandObject):
    print(f"[DEBUG] сработал /обнулить, аргументы: {command.args!r}")
    if (command.args or "").strip().lower() != "да":
        await message.answer(
            "Это сотрёт всю статистику чата без возможности восстановить.\n"
            "Если уверены — отправьте: /обнулить да"
        )
        return
    stats = load_stats()
    stats.pop(str(message.chat.id), None)
    save_stats(stats)
    await message.answer("Статистика чата очищена.")


@dp.message(Command("отменить"), F.chat.id.in_(ALLOWED_CHAT_IDS))
async def cmd_undo(message: types.Message):
    print("[DEBUG] сработал /отменить")
    stats = load_stats()
    chat = chat_stats_of(stats, message.chat.id)
    last = chat.get("last") or []
    if not last:
        await message.answer("Нечего отменять — последняя игра уже отменена или её ещё не было.")
        return
    for name, goals, assists in last:
        rec = chat["players"].get(name)
        if not rec:
            continue
        rec["games"] = max(0, rec["games"] - 1)
        rec["goals"] = max(0, rec["goals"] - goals)
        rec["assists"] = max(0, rec["assists"] - assists)
        if rec["games"] == 0 and rec["goals"] == 0 and rec["assists"] == 0:
            chat["players"].pop(name, None)   # игрок был только в этой игре
    chat["last"] = []
    save_stats(stats)
    names = ", ".join(p[0] for p in last)
    await message.answer(f"Последняя игра отменена. Затронуты: {names}")


@dp.message(Command("переименовать"), F.chat.id.in_(ALLOWED_CHAT_IDS))
async def cmd_rename(message: types.Message, command: CommandObject):
    print(f"[DEBUG] сработал /переименовать, аргументы: {command.args!r}")
    usage = ("Формат: /переименовать Старое имя = Новое имя\n"
             "Например: /переименовать Иванов = Иванов И.\n"
             "Если новое имя уже есть в таблице — статистика сложится.")
    if not command.args or "=" not in command.args:
        await message.answer(usage)
        return
    old_name, new_name = [part.strip().title() for part in command.args.split("=", 1)]
    if not old_name or not new_name:
        await message.answer(usage)
        return

    stats = load_stats()
    chat = chat_stats_of(stats, message.chat.id)
    rec = chat["players"].pop(old_name, None)
    if rec is None:
        have = ", ".join(chat["players"].keys()) or "пока никого"
        await message.answer(f"Игрока «{old_name}» в таблице нет.\nЕсть: {have}")
        return
    target = chat["players"].setdefault(new_name, {"games": 0, "goals": 0, "assists": 0})
    merged = target["games"] > 0 or target["goals"] > 0 or target["assists"] > 0
    for key in ("games", "goals", "assists"):
        target[key] += rec[key]
    # в истории последней игры тоже поправим имя, чтобы /отменить сработал верно
    for entry in chat.get("last", []):
        if entry[0] == old_name:
            entry[0] = new_name
    save_stats(stats)
    tail = " Статистика объединена." if merged else ""
    await message.answer(f"«{old_name}» теперь «{new_name}».{tail}")


@dp.message()
async def catch_all(message: types.Message):
    text = message.text or ""
    if not text.lower().startswith(BOT_NAME):
        print(f"[DEBUG] пришло сообщение, ни одна команда не подошла: {message.text!r}")
        return
    if message.chat.id not in ALLOWED_CHAT_IDS:
        print(f"[DEBUG] обращение из неразрешённого чата {message.chat.id}, игнорирую")
        return
    question = text[len(BOT_NAME):].lstrip(" ,:!?—-")
    print(f"[DEBUG] обращение по имени '{BOT_NAME}', вопрос: {question!r}")
    if not question:
        await message.answer("Да? Спросите что-нибудь после имени.")
        return
    await handle_question(message, question)


async def main():
    logging.basicConfig(level=logging.INFO)
    scheduler.add_job(
        send_training_poll,
        IntervalTrigger(days=POLL_INTERVAL_DAYS, start_date=POLL_START, timezone=YEKB_TZ),
        misfire_grace_time=1800,   # если бот был недоступен больше 30 минут — пропустить, а не слать с опозданием
        coalesce=True,             # не слать несколько опросов подряд за пропущенные дни
    )
    print(f"[DEBUG] опрос про тренировку: старт {POLL_START:%d.%m.%Y %H:%M}, "
          f"каждые {POLL_INTERVAL_DAYS} дня")
    scheduler.add_job(
        check_clubs_games,
        CronTrigger(hour=10, minute=0, timezone=YEKB_TZ),
        misfire_grace_time=1800,
        coalesce=True,
    )
    print("[DEBUG] анонсы матчей клубов: каждый день в 10:00 по Екатеринбургу")
    scheduler.start()
    print("Бот запущен (Gemini). Для остановки — Ctrl+C")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
