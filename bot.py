"""
Семейный Telegram-бот — отвечает на команды, включая вопросы к ИИ (GigaChat).
Библиотеки: aiogram 3.x, aiohttp (ставится вместе с aiogram)

НАСТРОЙКА:
    1. BOT_TOKEN — токен от @BotFather
    2. GIGACHAT_AUTH_KEY — «Ключ авторизации» из личного кабинета
       developers.sber.ru / giga.chat (проект с доступом GIGACHAT_API_PERS —
       для физлиц, бесплатно до 1 млн токенов в месяц)

ЗАПУСК:
    python bot.py
    Остановить — Ctrl+C
"""

import asyncio
import base64
import datetime
import json
import logging
import os
import uuid
import xml.etree.ElementTree as ET
import aiohttp
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject

BOT_TOKEN = os.environ["BOT_TOKEN"]
GIGACHAT_AUTH_KEY = os.environ["GIGACHAT_AUTH_KEY"]
YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]
API_SPORTS_KEY = os.environ.get("API_SPORTS_KEY", "")   # ключ с api-sports.io, для футбола и хоккея

BOT_NAME = "мяч"    # обращение к боту без команды, например "Мяч, когда тренировка?"
MAX_HISTORY_MESSAGES = 10   # сколько последних сообщений помнить (5 пар вопрос-ответ)
ALLOWED_CHAT_IDS = {-5579173684}   # ID семейной группы. Через запятую добавите ещё чаты, например {-5579173684, -1009876543210}
TRAINING_POLL_CHAT_ID = -5579173684   # в какой чат постить автоматические опросы и спортивные напоминания
GAME_TIME = "21:30"         # время игры, попадает в текст опроса
YEKB_TZ = ZoneInfo("Asia/Yekaterinburg")

# История разговора отдельно с каждым человеком в каждом чате: {(chat_id, user_id): [сообщения]}
conversation_history: dict[tuple[int, int], list[dict]] = {}


def get_history(chat_id: int, user_id: int) -> list[dict]:
    return conversation_history.get((chat_id, user_id), [])


def update_history(chat_id: int, user_id: int, question: str, answer: str) -> None:
    key = (chat_id, user_id)
    history = conversation_history.get(key, [])
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})
    conversation_history[key] = history[-MAX_HISTORY_MESSAGES:]


ANCHOR_FILE = "/data/poll_schedule_anchor.txt"   # тут хранится точка отсчёта расписания опроса — не удалять

FOOTBALL_HOCKEY_KEYWORDS = [
    "футбол", "хоккей", "рпл", "кхл", "нхл", "апл", "лч", "премьер-лига", "урал", "автомобилист",
]
FUTSAL_KEYWORDS = ["футзал", "мфк виз", "суперлига"]
FUTSAL_SITES = "site:superliga.rfs.ru OR site:mfkviz.ru"
OTHER_SPORTS_SITES = "site:sportbox.ru OR site:sports.ru OR site:championat.com"
SPORTS_KEYWORDS = [
    "матч", "игра", "играет", "команда", "чемпионат", "лига", "соперник",
    "турнир", "счёт", "баскетбол", "теннис", "спортсмен", "нба", "спорт",
] + FOOTBALL_HOCKEY_KEYWORDS + FUTSAL_KEYWORDS


def is_futsal_question(question: str) -> bool:
    return any(kw in question.lower() for kw in FUTSAL_KEYWORDS)


def is_football_hockey_question(question: str) -> bool:
    return any(kw in question.lower() for kw in FOOTBALL_HOCKEY_KEYWORDS)


def is_sports_question(question: str) -> bool:
    return any(kw in question.lower() for kw in SPORTS_KEYWORDS)


def get_poll_anchor() -> datetime.datetime:
    """Точка отсчёта для расписания 'раз в 2 дня' — берётся из файла, если он уже есть,
    иначе создаётся один раз (сегодня в 12:00) и сохраняется, чтобы пережить перезапуски."""
    if os.path.exists(ANCHOR_FILE):
        with open(ANCHOR_FILE, "r") as f:
            return datetime.datetime.fromisoformat(f.read().strip())
    anchor = datetime.datetime.now(YEKB_TZ).replace(hour=12, minute=0, second=0, microsecond=0)
    with open(ANCHOR_FILE, "w") as f:
        f.write(anchor.isoformat())
    return anchor

print(f"[DEBUG] запущен файл: {__file__}")

# Проверка ключа при запуске — покажет, не битый ли он (лишний перенос строки/пробел/обрезан)
print(f"[DEBUG] GIGACHAT_AUTH_KEY: длина={len(GIGACHAT_AUTH_KEY)}, "
      f"есть_перенос_строки={chr(10) in GIGACHAT_AUTH_KEY}, есть_пробелы={' ' in GIGACHAT_AUTH_KEY}")
try:
    base64.b64decode(GIGACHAT_AUTH_KEY.strip())
    print("[DEBUG] GIGACHAT_AUTH_KEY похож на корректный base64")
except Exception as e:
    print(f"[DEBUG] GIGACHAT_AUTH_KEY НЕ является корректным base64: {e}")

# Секреты читаются из переменных окружения Amvera (раздел "Переменные" в проекте)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()


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


async def fetch_page_text(url: str) -> str:
    """Скачивает страницу целиком и вытаскивает из неё читаемый текст без HTML-тегов —
    в отличие от поиска, тут виден весь контент страницы, а не короткий обрывок."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            html = await resp.text()
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=" ", strip=True)


async def check_ural_game() -> None:
    today = datetime.date.today()
    try:
        page_text = await fetch_page_text("https://fc-ural.ru/sezon/obschiy-kalendar")
        idx = page_text.find("Предстоящий матч")
        excerpt = page_text[idx:idx + 500] if idx != -1 else page_text[:1500]
        print(f"[DEBUG] отрывок со страницы календаря: {excerpt[:300]!r}")
    except Exception as e:
        print(f"[DEBUG] не удалось скачать страницу календаря fc-ural.ru: {e}")
        excerpt = ""
    question = (
        f"Вот отрывок со страницы календаря ФК «Урал» (fc-ural.ru):\n{excerpt}\n\n"
        f"Считая от {today.strftime('%d.%m.%Y')} включительно, когда по этим "
        f"данным ближайшая игра «Урала»? Ответь СТРОГО в формате, без лишних "
        f"слов и пояснений:\n"
        f"ДД.ММ.ГГГГ|время начала ЧЧ:ММ|название соперника"
    )
    try:
        answer = await ask_gigachat(question, [], search_query="Урал Екатеринбург ближайший матч")
        print(f"[DEBUG] проверка игры Урала: {answer!r}")
        parts = answer.strip().split("|")
        if len(parts) >= 3:
            game_date = datetime.datetime.strptime(parts[0].strip(), "%d.%m.%Y").date()
            game_time = parts[1].strip()
            opponent = parts[2].strip()
            if game_date == today:
                message_text = f"⚽ Сегодня играет «Урал»! Начало в {game_time}, соперник — {opponent}."
                for attempt in range(3):
                    try:
                        await bot.send_message(TRAINING_POLL_CHAT_ID, message_text)
                        break
                    except Exception as send_error:
                        print(f"[DEBUG] попытка {attempt + 1} отправить напоминание про Урал не удалась: {send_error}")
                        if attempt < 2:
                            await asyncio.sleep(5)
            else:
                print(f"[DEBUG] ближайшая игра Урала не сегодня, а {game_date} — молчу")
    except Exception as e:
        print(f"[DEBUG] ошибка проверки игры Урала: {e}")


async def api_sports_next_match(team_name: str, sport: str) -> str:
    """Ищет команду по названию и её ближайший матч через api-sports.io —
    структурированные данные, без угадывания текста. sport: 'football' или 'hockey'."""
    if not API_SPORTS_KEY:
        print("[DEBUG] API_SPORTS_KEY не задан, пропускаю api-sports.io")
        return ""
    base_url = "https://v3.football.api-sports.io" if sport == "football" else "https://v1.hockey.api-sports.io"
    headers = {"x-apisports-key": API_SPORTS_KEY}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/teams", headers=headers, params={"search": team_name}) as resp:
                data = await resp.json()
                print(f"[DEBUG] api-sports.io поиск команды {team_name!r}: {str(data)[:300]!r}")
                teams = data.get("response", [])
                if not teams:
                    return ""
                team_id = teams[0]["team"]["id"]
            endpoint = "fixtures" if sport == "football" else "games"
            async with session.get(
                f"{base_url}/{endpoint}", headers=headers, params={"team": team_id, "next": "1"}
            ) as resp:
                data = await resp.json()
                print(f"[DEBUG] api-sports.io ближайший матч: {str(data)[:500]!r}")
                matches = data.get("response", [])
                if not matches:
                    return ""
                return f"Данные с api-sports.io (структурированные, точные):\n{json.dumps(matches[0], ensure_ascii=False)}"
    except Exception as e:
        print(f"[DEBUG] ошибка api-sports.io: {e}")
        return ""


async def web_search(query: str) -> str:
    """Ищет по запросу через Yandex Search API, возвращает текстовую сводку топ-результатов."""
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
                print(f"[DEBUG] ошибка Yandex Search: статус={resp.status}, тело={raw[:300]!r}")
                return ""
            try:
                data = json.loads(raw)
                xml_text = base64.b64decode(data["rawData"]).decode("utf-8", errors="ignore")
                root = ET.fromstring(xml_text)
                snippets = []
                for doc in root.iter("doc"):
                    title_el = doc.find("title")
                    passage_els = doc.findall(".//passage")
                    title = "".join(title_el.itertext()) if title_el is not None else ""
                    passage = " ".join("".join(p.itertext()) for p in passage_els)
                    if title or passage:
                        snippets.append(f"- {title}: {passage}")
                    if len(snippets) >= 8:
                        break
                result = "\n".join(snippets)
                print(f"[DEBUG] Yandex Search нашёл: {result[:300]!r}")
                return result
            except Exception as e:
                print(f"[DEBUG] не удалось разобрать ответ Yandex Search: {e}, тело={raw[:300]!r}")
                return ""


async def web_search_urls(query: str) -> list[str]:
    """Как web_search, но возвращает список ссылок из результатов вместо текста."""
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
                print(f"[DEBUG] ошибка Yandex Search (urls): статус={resp.status}")
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
                print(f"[DEBUG] не удалось разобрать ответ Yandex Search (urls): {e}")
                return []


async def web_search_deep(query: str) -> str:
    """Ищет по запросу, затем открывает и читает целиком страницы из результатов —
    сниппеты часто не содержат таблицы (расписания и т.п.), а сама страница обычно да.
    Пробует несколько ссылок и комбинирует содержимое — если первая страница
    оказалась слишком короткой (мало полезного) или не открылась, идёт дальше.
    Если в тексте нашлась сегодняшняя дата — сужает отрывок вокруг неё, а не
    отдаёт модели весь необработанный блок целиком (так надёжнее)."""
    today = datetime.date.today()
    months = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    date_patterns = [
        today.strftime("%d.%m.%Y"), today.strftime("%d.%m.%y"),
        f"{today.day} {months[today.month - 1]}",
    ]
    urls = await web_search_urls(query)
    print(f"[DEBUG] глубокий поиск, ссылки: {urls}")
    combined = []
    for url in urls:
        try:
            text = await fetch_page_text(url)
            if len(text) > 500:
                print(f"[DEBUG] прочитал страницу {url}, символов: {len(text)}")
                normalized = text.replace("\xa0", " ").replace("\u2009", " ")
                focused = None
                for pattern in date_patterns:
                    idx = normalized.find(pattern)
                    if idx != -1:
                        focused = normalized[idx:idx + 1500]
                        print(f"[DEBUG] нашёл сегодняшнюю дату ({pattern!r}), сузил отрывок рядом с ней")
                        break
                if focused is None:
                    print(f"[DEBUG] дату не нашёл ни в одном из форматов {date_patterns}, беру начало страницы")
                combined.append(f"Содержимое страницы {url}:\n{focused or text[:6000]}")
            else:
                print(f"[DEBUG] страница {url} дала мало текста ({len(text)}), пропускаю")
        except Exception as e:
            print(f"[DEBUG] не удалось открыть {url}: {e}")
        if len(combined) >= 2:
            break
    if combined:
        return "\n\n---\n\n".join(combined)
    print("[DEBUG] глубокий поиск не смог получить достаточно текста, использую обычный поиск")
    return await web_search(query)


async def gigachat_completion(messages: list[dict]) -> str:
    """Низкоуровневый вызов GigaChat — без поиска, истории и системных подсказок.
    Нужен отдельно от ask_gigachat, чтобы вспомогательные вызовы (например,
    извлечение названия команды) не запускали её же маршрутизацию по кругу."""
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
        ) as token_resp:
            raw = await token_resp.text()
            print(f"[DEBUG] GigaChat oauth статус={token_resp.status}, тело={raw[:400]!r}")
            try:
                token_data = json.loads(raw)
            except ValueError:
                raise RuntimeError(f"GigaChat вернул не-JSON ответ (статус {token_resp.status}): {raw[:200]}")
            if token_resp.status != 200:
                raise RuntimeError(token_data.get("message", f"ошибка получения токена, статус {token_resp.status}"))
            access_token = token_data["access_token"]
        async with session.post(
            "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"model": "GigaChat", "messages": messages},
            ssl=False,
        ) as resp:
            raw = await resp.text()
            print(f"[DEBUG] GigaChat completions статус={resp.status}, тело={raw[:400]!r}")
            try:
                data = json.loads(raw)
            except ValueError:
                raise RuntimeError(f"GigaChat вернул не-JSON ответ (статус {resp.status}): {raw[:200]}")
            if resp.status != 200:
                raise RuntimeError(data.get("message", f"ошибка запроса, статус {resp.status}"))
            return data["choices"][0]["message"]["content"]


async def extract_team_name_english(question: str) -> str:
    """api-sports.io принимает в поиске только латиницу — вытаскиваем название
    команды из русского вопроса и переводим отдельным простым вызовом (не через
    ask_gigachat, чтобы не зациклиться на футбольном/хоккейном вопросе)."""
    try:
        answer = await gigachat_completion([{
            "role": "user",
            "content": (
                f"Определи, о какой спортивной команде идёт речь, и ответь "
                f"СТРОГО её официальным названием на английском языке, без "
                f"пояснений и лишних слов: {question}"
            ),
        }])
        cleaned = "".join(c for c in answer if c.isalnum() or c.isspace()).strip()
        print(f"[DEBUG] извлечённое название команды (англ.): {cleaned!r}")
        return cleaned
    except Exception as e:
        print(f"[DEBUG] не удалось определить название команды: {e}")
        return ""


async def ask_gigachat(question: str, history: list[dict], search_query: str | None = None) -> str:
    query = search_query or question
    if search_query is not None:
        search_results = await web_search(query)
    elif is_football_hockey_question(question):
        hockey_indicators = ["хокке", "нхл", "кхл"]
        sport = "hockey" if any(ind in question.lower() for ind in hockey_indicators) else "football"
        search_results = ""
        team_name = await extract_team_name_english(question)
        if team_name:
            search_results = await api_sports_next_match(team_name, sport)
        if not search_results:
            print("[DEBUG] api-sports.io не нашёл команду, использую обычный глубокий поиск")
            search_results = await web_search_deep(f"{question} {OTHER_SPORTS_SITES}")
    elif is_futsal_question(question):
        search_results = await web_search_deep(f"{question} {FUTSAL_SITES}")
    elif is_sports_question(question):
        search_results = await web_search_deep(f"{question} {OTHER_SPORTS_SITES}")
        if not search_results and history:
            prior_user_msgs = [m["content"] for m in history if m["role"] == "user"]
            if prior_user_msgs:
                enriched_query = f"{prior_user_msgs[-1]} {question} {OTHER_SPORTS_SITES}"
                print("[DEBUG] обычный поиск ничего не нашёл, пробую с контекстом предыдущего вопроса")
                search_results = await web_search_deep(enriched_query)
    else:
        search_results = await web_search(query)
    system_content = (
        f"Сегодняшняя дата: {datetime.date.today().strftime('%d.%m.%Y')}. "
        f"Если вопрос касается расписаний, дат или текущих событий — "
        f"учитывай эту дату и честно предупреждай, если твои знания "
        f"могут быть устаревшими, вместо того чтобы уверенно называть "
        f"старые данные актуальными. "
        f"Не пересчитывай время между часовыми поясами самостоятельно (легко "
        f"ошибиться) — указывай время в том поясе, в котором оно дано в "
        f"источнике, и не переводи в московское или другое время, если "
        f"пользователь явно не попросил об этом. "
        f"Если вопрос подразумевает список (например, все матчи за день) — "
        f"внимательно перечисли ВСЕ подходящие пункты, которые есть в "
        f"предоставленном тексте, а не только первый найденный. Не пиши "
        f"фразы вроде «это вся информация» или «остальных нет», если не "
        f"уверен на 100%, что действительно проверил весь текст целиком."
    )
    if search_results:
        system_content += (
            f"\n\nРезультаты свежего поиска в интернете по теме вопроса:\n{search_results}\n\n"
            f"Обязательно используй эти результаты, если они относятся к вопросу — "
            f"не отвечай, что информация «пока не объявлена» или «неизвестна», если "
            f"в результатах поиска есть конкретные даты, время или другие детали. "
            f"Приводи то, что нашлось, даже если не на 100% уверен, — это точнее, "
            f"чем полагаться только на свои внутренние знания."
        )
    messages = [{"role": "system", "content": system_content}] + history + [{"role": "user", "content": question}]
    return await gigachat_completion(messages)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    print(f"[DEBUG] сработал /start, от {message.from_user.first_name}")
    await message.answer(
        f"Привет, {message.from_user.first_name}! Я семейный бот.\n"
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
        "/ии вопрос — спросить что-нибудь у ИИ\n"
        "/опрос — создать опрос «Кто идёт на тренировку?»\n"
        "/напомнить ДД.ММ ЧЧ:ММ текст — запланировать напоминание\n"
        "/забыть — начать разговор с ИИ заново\n"
        "Или просто начните сообщение с «Мяч» — например: «Мяч, когда тренировка?»"
    )


@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    print("[DEBUG] сработал /id")
    await message.answer(f"ID этого чата: {message.chat.id}")


async def answer_with_retry(message: types.Message, text: str, attempts: int = 3, delay: int = 5):
    """Отправляет сообщение с несколькими попытками — на случай кратковременного
    сбоя связи с Telegram API (такое уже случалось на Amvera, обычно проходит
    за секунды)."""
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


@dp.message(Command("ии"), F.chat.id.in_(ALLOWED_CHAT_IDS))
async def cmd_ai(message: types.Message, command: CommandObject):
    print(f"[DEBUG] сработал /ии, вопрос: {command.args!r}")
    question = command.args
    if not question:
        await message.answer("Напишите вопрос после команды, например:\n/ии сколько лет живут черепахи")
        return
    thinking_msg = await answer_with_retry(message, "Думаю...")
    history = get_history(message.chat.id, message.from_user.id)
    try:
        answer = await ask_gigachat(question, history)
        await thinking_msg.edit_text(answer)
        update_history(message.chat.id, message.from_user.id, question, answer)
    except Exception as e:
        print(f"[DEBUG] ошибка запроса к GigaChat: {e}")
        await thinking_msg.edit_text("Не получилось получить ответ. Проверьте ключ авторизации GigaChat")


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
    args = command.args
    usage = "Формат: /напомнить ДД.ММ ЧЧ:ММ текст\nНапример: /напомнить 15.08 19:00 Игра с Соколом"
    if not args:
        await message.answer(usage)
        return
    parts = args.split(maxsplit=2)
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
    if text.lower().startswith(BOT_NAME):
        if message.chat.id not in ALLOWED_CHAT_IDS:
            print(f"[DEBUG] обращение по имени из неразрешённого чата {message.chat.id}, игнорирую")
            return
        question = text[len(BOT_NAME):].lstrip(" ,:!?—-")
        print(f"[DEBUG] обращение по имени '{BOT_NAME}', вопрос: {question!r}")
        if not question:
            await message.answer("Да? Спросите что-нибудь после имени.")
            return
        thinking_msg = await answer_with_retry(message, "Думаю...")
        history = get_history(message.chat.id, message.from_user.id)
        try:
            answer = await ask_gigachat(question, history)
            await thinking_msg.edit_text(answer)
            update_history(message.chat.id, message.from_user.id, question, answer)
        except Exception as e:
            print(f"[DEBUG] ошибка запроса к GigaChat: {e}")
            await thinking_msg.edit_text("Не получилось получить ответ. Проверьте ключ авторизации GigaChat")
        return
    print(f"[DEBUG] пришло сообщение, ни одна команда не подошла: {message.text!r}")


async def main():
    logging.basicConfig(level=logging.INFO)
    today_noon = get_poll_anchor()
    scheduler.add_job(
        send_training_poll,
        IntervalTrigger(days=2, start_date=today_noon, timezone=YEKB_TZ),
    )
    scheduler.add_job(
        check_ural_game,
        CronTrigger(hour=12, minute=0, timezone=YEKB_TZ),
    )
    scheduler.start()
    print("Бот запущен. Для остановки — Ctrl+C")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
