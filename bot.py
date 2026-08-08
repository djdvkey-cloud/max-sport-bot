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
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot, Dispatcher, F, types
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
ANCHOR_FILE = "/data/poll_schedule_anchor.txt"   # точка отсчёта расписания опроса — не удалять

# --- Предпочитаемые источники по видам спорта -----------------------------
# Gemini ищет сам, поэтому это не жёсткий фильтр, а подсказка в промпте.

NHL_KEYWORDS = ["нхл", "nhl"]
NHL_SITES = "nhl.com, nhl.ru, hockey-reference.com"

FUTSAL_KEYWORDS = ["футзал", "мини-футбол", "мфк виз", "виз-синара"]
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


def get_poll_anchor() -> datetime.datetime:
    """Точка отсчёта расписания «раз в 2 дня» — хранится в файле, чтобы
    переживать перезапуски и не сбиваться при каждой пересборке."""
    if os.path.exists(ANCHOR_FILE):
        with open(ANCHOR_FILE, "r") as f:
            return datetime.datetime.fromisoformat(f.read().strip())
    anchor = datetime.datetime.now(YEKB_TZ).replace(hour=12, minute=0, second=0, microsecond=0)
    os.makedirs(os.path.dirname(ANCHOR_FILE), exist_ok=True)
    with open(ANCHOR_FILE, "w") as f:
        f.write(anchor.isoformat())
    return anchor


bot = Bot(token=BOT_TOKEN)
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


async def collect_web_context(query: str) -> str:
    """Ищет и читает страницы целиком — сниппеты часто не содержат таблиц
    с расписаниями, а сама страница обычно да."""
    urls = await yandex_search_urls(query)
    print(f"[DEBUG] поиск, ссылки: {urls}")
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
    thinking_msg = await answer_with_retry(message, "Думаю...")
    history = get_history(message.chat.id, message.from_user.id)
    system_extra = ""
    if is_sports_question(question):
        sites = preferred_sites(question)
        print(f"[DEBUG] спортивный вопрос, источники: {sites}")
        site_filter = " OR ".join(f"site:{s.strip()}" for s in sites.split(","))
        context = await collect_web_context(f"{question} {site_filter}")
        if not context:
            print("[DEBUG] по указанным сайтам пусто, ищу без ограничений")
            context = await collect_web_context(question)
        if context:
            system_extra = f"Материалы из интернета по теме вопроса:\n{context}"
    try:
        answer = await ask_gemini(question, history, system_extra)
        await thinking_msg.edit_text(answer)
        update_history(message.chat.id, message.from_user.id, question, answer)
    except Exception as e:
        print(f"[DEBUG] ошибка запроса к Gemini: {e}")
        await thinking_msg.edit_text("Не получилось получить ответ, попробуйте ещё раз чуть позже.")


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


async def check_ural_game() -> None:
    """Каждый день проверяет, играет ли «Урал» сегодня. Пишет в чат только если да."""
    today = datetime.date.today()
    try:
        context = await collect_web_context(
            "ФК Урал Екатеринбург ближайший матч расписание site:fc-ural.ru OR site:premierliga.ru OR site:championat.com"
        )
        prompt = (
            f"Вот материалы из интернета:\n{context}\n\n"
            f"Найди ближайший матч футбольного клуба «Урал» (Екатеринбург), "
            f"считая от {today.strftime('%d.%m.%Y')} включительно. "
            f"Ответь СТРОГО в формате, без пояснений и лишних слов:\n"
            f"ДД.ММ.ГГГГ|ЧЧ:ММ|название соперника"
        )
        answer = await ask_gemini(prompt)
        print(f"[DEBUG] проверка игры Урала: {answer!r}")
        parts = answer.strip().split("|")
        if len(parts) < 3:
            print("[DEBUG] неожиданный формат ответа про Урал, пропускаю")
            return
        game_date = datetime.datetime.strptime(parts[0].strip(), "%d.%m.%Y").date()
        if game_date != today:
            print(f"[DEBUG] ближайшая игра Урала не сегодня, а {game_date} — молчу")
            return
        text = f"⚽ Сегодня играет «Урал»! Начало в {parts[1].strip()}, соперник — {parts[2].strip()}."
        for attempt in range(3):
            try:
                await bot.send_message(TRAINING_POLL_CHAT_ID, text)
                break
            except Exception as send_error:
                print(f"[DEBUG] попытка {attempt + 1} отправить напоминание не удалась: {send_error}")
                if attempt < 2:
                    await asyncio.sleep(5)
    except Exception as e:
        print(f"[DEBUG] ошибка проверки игры Урала: {e}")


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
        IntervalTrigger(days=2, start_date=get_poll_anchor(), timezone=YEKB_TZ),
    )
    scheduler.add_job(
        check_ural_game,
        CronTrigger(hour=12, minute=0, timezone=YEKB_TZ),
    )
    scheduler.start()
    print("Бот запущен (Gemini). Для остановки — Ctrl+C")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
