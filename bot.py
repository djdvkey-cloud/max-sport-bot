"""
Футбольный бот «Мяч» — опросы на тренировку, статистика игр и напоминания
о матчах трёх клубов.

ИИ используется ТОЛЬКО для утренней проверки матчей (Синара, Урал,
Автомобилист). Всё остальное — обычный код, без обращений к Claude.

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (в Amvera → «Переменные»):
    BOT_TOKEN         — токен от @BotFather
    ANTHROPIC_API_KEY — ключ с console.anthropic.com

ЗАПУСК:
    python bot.py
"""

import asyncio
import datetime
import json
import logging
import os
import re
from zoneinfo import ZoneInfo

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject

BOT_TOKEN = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
# Шаблонная задача — хватает быстрой и дешёвой модели
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# Кто может отдавать команды. 0 — ограничение выключено (как было раньше).
# Свой id узнаете командой /id — впишите сюда и перезапустите бота.
OWNER_TELEGRAM_ID = 0

ALLOWED_CHAT_IDS = {-5579173684}      # где боту разрешено отвечать
TRAINING_POLL_CHAT_ID = -5579173684   # куда постить опросы и напоминания
GAME_TIME = "21:30"                   # время тренировки, попадает в текст опроса
YEKB_TZ = ZoneInfo("Asia/Yekaterinburg")

STATS_FILE = "/data/stats.json"          # статистика игроков по чатам — не удалять
LAST_POLL_FILE = "/data/last_poll.txt"   # дата последнего опроса — не удалять

# Опрос про тренировку: отсчёт от 09.08.2026, каждые 2 дня, в 12:00.
# Дата в тексте опроса — всегда следующий день после публикации.
POLL_START_DATE = datetime.date(2026, 8, 9)
POLL_INTERVAL_DAYS = 2
POLL_HOUR = 12
CLUBS_CHECK_HOUR = 10   # во сколько проверять матчи клубов

logging.basicConfig(level=logging.INFO)
# Короткий таймаут: на хостинге связь с Telegram иногда пропадает
bot = Bot(token=BOT_TOKEN, session=AiohttpSession(timeout=20))
dp = Dispatcher()
scheduler = AsyncIOScheduler()


@dp.message.outer_middleware()
async def only_owner(handler, event, data):
    """Пропускает команды только от владельца. Пока OWNER_TELEGRAM_ID = 0
    проверка выключена — чтобы вы успели узнать свой id командой /id."""
    if not OWNER_TELEGRAM_ID:
        return await handler(event, data)
    user = getattr(event, "from_user", None)
    if user and user.id == OWNER_TELEGRAM_ID:
        return await handler(event, data)

    # Исключение: статистику и составы в футбольном чате может смотреть
    # и запускать любой участник, не только владелец
    text = (getattr(event, "text", "") or "").strip().lower()
    chat = getattr(event, "chat", None)
    if text.startswith(("/статистика", "/составы")) and chat and chat.id in ALLOWED_CHAT_IDS:
        return await handler(event, data)

    print(f"[DEBUG] команда не от владельца (id {user.id if user else '?'}), игнорирую")
    return None


# --- Статистика игроков ---------------------------------------------------

def load_stats() -> dict:
    """{chat_id: {"players": {игрок: {games, goals, assists}}, "last": [записи]}}"""
    if not os.path.exists(STATS_FILE):
        return {}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for chat_key, chat_data in list(data.items()):   # совместимость со старым форматом
            if isinstance(chat_data, dict) and "players" not in chat_data:
                data[chat_key] = {"players": chat_data, "last": []}
        return data
    except Exception as e:
        print(f"[DEBUG] не удалось прочитать статистику: {e}")
        return {}


def chat_stats_of(stats: dict, chat_id: int) -> dict:
    return stats.setdefault(str(chat_id), {"players": {}, "last": []})


def target_group_chat_id(message: types.Message) -> int:
    """К какому чату относить эту команду. Если она пришла в личку (владельцу —
    посторонних туда и так не пускает only_owner), результат всё равно
    футбольный чат: статистика, опрос и т. п. общие независимо от того,
    откуда именно их вызвали."""
    if message.chat.id in ALLOWED_CHAT_IDS:
        return message.chat.id
    return next(iter(ALLOWED_CHAT_IDS))


def save_stats(stats: dict) -> None:
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def parse_match_line(text: str) -> tuple[list[tuple[str, int, int]], list[str]]:
    """«Иванов 2+1, Соломин, Петров 0+3» → [(имя, голы, передачи), ...].
    Фамилия без цифр считается как 0+0."""
    players, errors = [], []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = re.match(r"^(.+?)\s+(\d+)\s*\+\s*(\d+)$", chunk)
        if match:
            players.append((match.group(1).strip().title(),
                            int(match.group(2)), int(match.group(3))))
        elif not any(ch.isdigit() for ch in chunk):
            players.append((chunk.title(), 0, 0))
        else:
            errors.append(chunk)
    return players, errors


# --- Обращение к Claude (только для проверки матчей) ----------------------

async def ask_claude(question: str, allowed_domains: list[str] | None = None) -> str:
    """Вопрос к Claude со встроенным веб-поиском по указанным сайтам."""
    today = datetime.date.today().strftime("%d.%m.%Y")
    system_text = (
        f"Сегодня {today}. Отвечай по-русски, строго в запрошенном формате, "
        f"без пояснений и markdown-разметки.\n"
        f"Время не пересчитывай между часовыми поясами — бери как в источнике."
    )
    search_tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
    if allowed_domains:
        search_tool["allowed_domains"] = allowed_domains

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 800,
        "system": system_text,
        "messages": [{"role": "user", "content": question}],
        "tools": [search_tool],
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            raw = await resp.text()
            if resp.status != 200:
                print(f"[DEBUG] Claude статус={resp.status}, тело={raw[:400]!r}")
                raise RuntimeError(f"Claude вернул статус {resp.status}")
            data = json.loads(raw)

    queries = [b.get("input", {}).get("query") for b in data.get("content", [])
               if b.get("type") == "server_tool_use"]
    print(f"[DEBUG] Claude искал: {queries}")
    answer = "".join(b.get("text", "") for b in data.get("content", [])
                     if b.get("type") == "text").strip()
    if not answer:
        raise RuntimeError("Claude вернул пустой ответ")
    return answer


async def send_with_retry(chat_id: int, text: str, attempts: int = 3, delay: int = 5) -> bool:
    """Отправка с повторами — связь с Telegram иногда кратковременно пропадает."""
    for attempt in range(attempts):
        try:
            await bot.send_message(chat_id, text)
            return True
        except Exception as e:
            print(f"[DEBUG] попытка {attempt + 1} отправить не удалась: {e}")
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
    return False


# --- Опрос про тренировку -------------------------------------------------

def is_poll_day(day: datetime.date) -> bool:
    """День опроса: через каждые POLL_INTERVAL_DAYS от точки отсчёта."""
    if day < POLL_START_DATE:
        return False
    return (day - POLL_START_DATE).days % POLL_INTERVAL_DAYS == 0


def last_poll_date() -> datetime.date | None:
    if not os.path.exists(LAST_POLL_FILE):
        return None
    try:
        with open(LAST_POLL_FILE, "r") as f:
            return datetime.date.fromisoformat(f.read().strip())
    except Exception:
        return None


def remember_poll_date(day: datetime.date) -> None:
    os.makedirs(os.path.dirname(LAST_POLL_FILE), exist_ok=True)
    with open(LAST_POLL_FILE, "w") as f:
        f.write(day.isoformat())


async def send_training_poll(today: datetime.date | None = None) -> None:
    today = today or datetime.datetime.now(YEKB_TZ).date()
    tomorrow = today + datetime.timedelta(days=1)
    question = f"Футбол Лестех. {tomorrow.strftime('%d.%m.%y')} в {GAME_TIME.replace(':', '-')}."
    print(f"[DEBUG] отправляю опрос: {question!r}")
    try:
        await bot.send_poll(
            chat_id=TRAINING_POLL_CHAT_ID,
            question=question,
            options=["Иду", "Не иду"],
            is_anonymous=False,
        )
        remember_poll_date(today)
    except Exception as e:
        print(f"[DEBUG] не удалось отправить опрос: {e}")


async def poll_job() -> None:
    """Ежедневная проверка в 12:00: сегодня день опроса — и он ещё не был?"""
    today = datetime.datetime.now(YEKB_TZ).date()
    if not is_poll_day(today):
        print(f"[DEBUG] {today} — не день опроса, пропускаю")
        return
    if last_poll_date() == today:
        print(f"[DEBUG] опрос за {today} уже отправлен")
        return
    await send_training_poll(today)


async def catch_up_poll() -> None:
    """При запуске: если день опроса уже наступил, время прошло, а опроса
    не было (например, бот перезапускался в полдень) — отправить сейчас."""
    now = datetime.datetime.now(YEKB_TZ)
    today = now.date()
    if is_poll_day(today) and now.hour >= POLL_HOUR and last_poll_date() != today:
        print("[DEBUG] опрос за сегодня пропущен — отправляю с опозданием")
        await send_training_poll(today)


def next_poll_date(from_day: datetime.date) -> datetime.date:
    day = from_day
    for _ in range(POLL_INTERVAL_DAYS + 1):
        if is_poll_day(day) and day >= from_day:
            return day
        day += datetime.timedelta(days=1)
    return day


# --- Матчи клубов ---------------------------------------------------------

TRACKED_CLUBS = [
    {
        "name": "МФК «Синара» (Екатеринбург)",
        "query": "ближайший матч МФК Синара Екатеринбург: турнир, дата, время, место, соперник",
        "sites": ["superliga.rfs.ru", "mfkviz.ru", "futsal.rfs.ru", "uefa.com"],
        "icon": "🥅",
    },
    {
        "name": "ФК «Урал» (Екатеринбург)",
        "query": "ближайший матч ФК Урал Екатеринбург: турнир, дата, время, место, соперник",
        "sites": ["fc-ural.ru", "fnl.pro", "championat.com"],
        "icon": "⚽",
    },
    {
        "name": "ХК «Автомобилист» (Екатеринбург)",
        "query": "ближайший матч ХК Автомобилист Екатеринбург: турнир, дата, время, место, соперник",
        "sites": ["khl.ru", "hc-avto.ru", "news.sportbox.ru"],
        "icon": "🏒",
    },
]


async def next_match_for_club(club: dict) -> str:
    """Если клуб играет СЕГОДНЯ — текст напоминания, иначе пустая строка."""
    today = datetime.date.today()
    prompt = (
        f"{club['query']}. Найди БЛИЖАЙШИЙ предстоящий матч, считая от "
        f"{today.strftime('%d.%m.%Y')} включительно.\n"
        f"ПОСЛЕДНЕЙ СТРОКОЙ ответа выдай данные строго в таком виде:\n"
        f"Турнир|ДД.ММ.ГГГГ|ЧЧ:ММ|Место проведения|Соперник\n"
        f"Дата — обязательно одним днём в формате ДД.ММ.ГГГГ. "
        f"Время НЕ пересчитывай между поясами — бери как в источнике, но "
        f"обязательно допиши пояс: «19:00 мск» или «19:00 екб». Если пояс в "
        f"источнике не указан явно, но это российский турнир — пиши «мск». "
        f"Если время неизвестно — напиши «уточняется», но строку всё равно выдай. "
        f"Если предстоящего матча нет вовсе (межсезонье, расписание не "
        f"опубликовано, известен только диапазон дат) — последней строкой напиши НЕТ."
    )
    try:
        answer = (await ask_claude(prompt, allowed_domains=club["sites"])).strip()
        print(f"[DEBUG] {club['name']}: {answer!r}")
        # Claude часто добавляет пояснение — берём последнюю строку с разделителями
        data_line = None
        for line in reversed(answer.splitlines()):
            if line.count("|") >= 4:
                data_line = line.strip()
                break
        if data_line is None:
            print(f"[DEBUG] {club['name']}: данных о матче нет, пропускаю")
            return ""
        parts = [p.strip() for p in data_line.split("|")]
        tournament, date_str, time_str, place, rival = parts[:5]

        try:
            game_date = datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
        except ValueError:
            print(f"[DEBUG] {club['name']}: дату {date_str!r} разобрать не смог")
            return ""
        if game_date != today:
            print(f"[DEBUG] {club['name']}: ближайший матч {game_date}, не сегодня — молчу")
            return ""

        return (f"{club['icon']} Сегодня играет {club['name']}\n"
                f"Турнир: {tournament}\n"
                f"Время: {time_str}\n"
                f"Место: {place}\n"
                f"Соперник: {rival}")
    except Exception as e:
        print(f"[DEBUG] ошибка при проверке {club['name']}: {e}")
        return ""


async def check_clubs_games() -> None:
    """Каждое утро проверяет клубы и пишет только о тех, кто играет сегодня."""
    print("[DEBUG] утренняя проверка матчей клубов")
    blocks = []
    for club in TRACKED_CLUBS:
        line = await next_match_for_club(club)
        if line:
            blocks.append(line)
    if not blocks:
        print("[DEBUG] сегодня никто из клубов не играет, ничего не отправляю")
        return
    await send_with_retry(TRAINING_POLL_CHAT_ID, "\n\n".join(blocks))


async def send_reminder(chat_id: int, text: str) -> None:
    print(f"[DEBUG] отправляю напоминание в чат {chat_id}: {text!r}")
    await bot.send_message(chat_id, f"⏰ Напоминание: {text}")


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
        "Что умею:\n"
        "/опрос — создать опрос «Кто идёт на тренировку?»\n"
        "/напомнить ДД.ММ ЧЧ:ММ текст — запланировать напоминание\n"
        "/id — узнать ID этого чата\n\n"
        "Статистика игр:\n"
        "/матч Иванов 2+1, Петров 0+3, Соломин — записать результаты игры\n"
        "/статистика — таблица игроков\n"
        "/составы Иванов, Петров, ... — разбить на равные по силе команды\n"
        "/отменить — убрать последнюю записанную игру\n"
        "/переименовать Старое = Новое — переименовать игрока\n"
        "/обнулить да — стереть всю статистику чата\n\n"
        "Сам присылаю:\n"
        f"• опрос на тренировку в {POLL_HOUR}:00, каждые {POLL_INTERVAL_DAYS} дня\n"
        f"• в {CLUBS_CHECK_HOUR}:00 — напоминание, если сегодня играют "
        f"Синара, Урал или Автомобилист"
    )


@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    print("[DEBUG] сработал /id")
    await message.answer(
        f"ID этого чата: {message.chat.id}\n"
        f"Ваш ID: {message.from_user.id}\n\n"
        f"Чтобы бот слушался только вас — впишите ваш ID в строку "
        f"OWNER_TELEGRAM_ID в начале файла."
    )


@dp.message(Command("опрос"), F.chat.id.in_(ALLOWED_CHAT_IDS) | (F.chat.type == "private"))
async def cmd_poll(message: types.Message):
    print("[DEBUG] сработал /опрос")
    target = target_group_chat_id(message)
    await bot.send_poll(
        chat_id=target,
        question="Кто идёт на тренировку?",
        options=["Иду", "Не иду", "Пока не знаю"],
        is_anonymous=False,
    )
    if target != message.chat.id:
        await message.answer("Опрос опубликован в футбольном чате.")


@dp.message(Command("напомнить"), F.chat.id.in_(ALLOWED_CHAT_IDS) | (F.chat.type == "private"))
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


@dp.message(Command("матч"), F.chat.id.in_(ALLOWED_CHAT_IDS) | (F.chat.type == "private"))
async def cmd_match(message: types.Message, command: CommandObject):
    print(f"[DEBUG] сработал /матч, аргументы: {command.args!r}")
    usage = ("Формат: /матч Фамилия голы+передачи, Фамилия голы+передачи\n"
             "Например: /матч Иванов 2+1, Петров 0+3, Соломин\n"
             "Кто не забивал и не отдавал — можно просто фамилией, без цифр.\n"
             "Каждому перечисленному игроку засчитывается одна сыгранная игра.")
    if not command.args:
        await message.answer(usage)
        return
    players, errors = parse_match_line(command.args)
    if not players:
        await message.answer(f"Не понял ни одного игрока.\n{usage}")
        return

    from_private = message.chat.id not in ALLOWED_CHAT_IDS
    stats = load_stats()
    chat = chat_stats_of(stats, target_group_chat_id(message))
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
    if from_private:
        lines.append("\n(ушло в общую статистику футбольного чата)")
    lines.append("\n/статистика — таблица, /отменить — убрать эту запись")
    await message.answer("\n".join(lines))


@dp.message(Command("статистика"), F.chat.id.in_(ALLOWED_CHAT_IDS) | (F.chat.type == "private"))
async def cmd_stats(message: types.Message):
    print("[DEBUG] сработал /статистика")
    chat_stats = load_stats().get(str(target_group_chat_id(message)), {}).get("players", {})
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

    lines = ["📊 Статистика (И — игры, Г — голы, П — передачи, К — коэффициент)\n"]
    for i, (koef, points, name, r) in enumerate(rows, 1):
        lines.append(
            f"{i}. {name} — И:{r['games']} Г:{r['goals']} П:{r['assists']} "
            f"О:{points} К:{koef:.2f}"
        )
    await message.answer("\n".join(lines))


@dp.message(Command("отменить"), F.chat.id.in_(ALLOWED_CHAT_IDS) | (F.chat.type == "private"))
async def cmd_undo(message: types.Message):
    print("[DEBUG] сработал /отменить")
    stats = load_stats()
    chat = chat_stats_of(stats, target_group_chat_id(message))
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


@dp.message(Command("переименовать"), F.chat.id.in_(ALLOWED_CHAT_IDS) | (F.chat.type == "private"))
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
    chat = chat_stats_of(stats, target_group_chat_id(message))
    rec = chat["players"].pop(old_name, None)
    if rec is None:
        have = ", ".join(chat["players"].keys()) or "пока никого"
        await message.answer(f"Игрока «{old_name}» в таблице нет.\nЕсть: {have}")
        return
    target = chat["players"].setdefault(new_name, {"games": 0, "goals": 0, "assists": 0})
    merged = target["games"] > 0 or target["goals"] > 0 or target["assists"] > 0
    for key in ("games", "goals", "assists"):
        target[key] += rec[key]
    for entry in chat.get("last", []):   # чтобы /отменить сработал верно
        if entry[0] == old_name:
            entry[0] = new_name
    save_stats(stats)
    tail = " Статистика объединена." if merged else ""
    await message.answer(f"«{old_name}» теперь «{new_name}».{tail}")


@dp.message(Command("обнулить"), F.chat.id.in_(ALLOWED_CHAT_IDS) | (F.chat.type == "private"))
async def cmd_reset_stats(message: types.Message, command: CommandObject):
    print(f"[DEBUG] сработал /обнулить, аргументы: {command.args!r}")
    if (command.args or "").strip().lower() != "да":
        await message.answer(
            "Это сотрёт всю статистику футбольного чата без возможности восстановить.\n"
            "Если уверены — отправьте: /обнулить да"
        )
        return
    stats = load_stats()
    stats.pop(str(target_group_chat_id(message)), None)
    save_stats(stats)
    await message.answer("Статистика футбольного чата очищена.")


def snake_teams(players: list[tuple[str, float]], team_count: int) -> list[list[tuple[str, float]]]:
    """Раскладывает players (уже отсортированы по убыванию коэффициента) по
    командам «змейкой»: 0,1,..,K-1,K-1,..,1,0,0,1,... — стандартный приём для
    честного разбора по силе (как в фэнтези-драфтах)."""
    teams: list[list[tuple[str, float]]] = [[] for _ in range(team_count)]
    i, forward = 0, True
    for p in players:
        teams[i].append(p)
        if forward:
            if i == team_count - 1:
                forward = False
            else:
                i += 1
        else:
            if i == 0:
                forward = True
            else:
                i -= 1
    return teams


def teams_and_bench_size(n: int) -> tuple[int, int]:
    """По числу игроков — (число команд по 5, число запасных).
    10→2 команды, 15→3 команды, между ними и после — с запасными.
    Меньше 10 — тоже 2 команды, просто неполные, без запасных."""
    if n == 10:
        return 2, 0
    if n == 15:
        return 3, 0
    if 10 < n < 15:
        return 2, n - 10
    if n > 15:
        return 3, n - 15
    return 2, 0


@dp.message(Command("составы"), F.chat.id.in_(ALLOWED_CHAT_IDS) | (F.chat.type == "private"))
async def cmd_lineups(message: types.Message, command: CommandObject):
    print(f"[DEBUG] сработал /составы, аргументы: {command.args!r}")
    usage = (
        "Формат: /составы Фамилия, Фамилия, Фамилия...\n"
        "Например: /составы Иванов, Петров, Сидоров, Козлов, Смирнов, "
        "Волков, Лебедев, Соколов, Новиков, Морозов\n\n"
        "10 игроков → 2 команды, 15 → 3 команды. Другое количество между "
        "10 и 15 или больше 15 — часть игроков идёт в запасные.\n"
        "Команды собираются по коэффициенту из /статистика (у кого не было "
        "игр — коэффициент 0)."
    )
    if not command.args:
        await message.answer(usage)
        return
    names = [chunk.strip().title() for chunk in command.args.split(",") if chunk.strip()]
    if not names:
        await message.answer(usage)
        return

    chat_players = load_stats().get(str(target_group_chat_id(message)), {}).get("players", {})
    players = []
    unknown = []
    for name in names:
        rec = chat_players.get(name)
        if rec and rec.get("games"):
            koef = (rec["goals"] + rec["assists"]) / rec["games"]
        else:
            koef = 0.0
            unknown.append(name)
        players.append((name, koef))
    players.sort(key=lambda p: -p[1])

    team_count, bench_size = teams_and_bench_size(len(players))
    bench = players[len(players) - bench_size:] if bench_size else []
    playing = players[:len(players) - bench_size] if bench_size else players
    teams = snake_teams(playing, team_count)

    lines = [f"⚖️ Составы — игроков {len(players)}, команд {team_count}\n"]
    for i, team in enumerate(teams, 1):
        avg = sum(k for _, k in team) / len(team) if team else 0
        lines.append(f"Команда {i} (средний коэф. {avg:.2f}):")
        lines += [f"• {name} — {koef:.2f}" for name, koef in team]
        lines.append("")
    if bench:
        lines.append("Запасные:")
        lines += [f"• {name} — {koef:.2f}" for name, koef in bench]
        lines.append("")
    if unknown:
        lines.append(f"Не было в статистике (коэффициент принят за 0): {', '.join(unknown)}")
    await message.answer("\n".join(lines).strip())


async def main():
    scheduler.add_job(
        poll_job,
        CronTrigger(hour=POLL_HOUR, minute=0, timezone=YEKB_TZ),
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.add_job(
        check_clubs_games,
        CronTrigger(hour=CLUBS_CHECK_HOUR, minute=0, timezone=YEKB_TZ),
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.start()

    now = datetime.datetime.now(YEKB_TZ)
    today = now.date()
    print(f"[DEBUG] сейчас по Екатеринбургу: {now:%d.%m.%Y %H:%M}")
    print(f"[DEBUG] сегодня день опроса: {is_poll_day(today)}, "
          f"последний опрос: {last_poll_date()}")
    print(f"[DEBUG] ближайший опрос: {next_poll_date(today):%d.%m.%Y} в {POLL_HOUR}:00")
    print(f"[DEBUG] проверка матчей клубов: каждый день в {CLUBS_CHECK_HOUR}:00")

    await catch_up_poll()

    print("Бот запущен («Мяч»). Для остановки — Ctrl+C")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
