"""«Своя Трибуна» / «Трибун» — клубный слой SPORTBOT/MAX: приветствие новых участников, личные интересы,
карта интересов компании, пульт владельца (участники, публикации, автоматика, приоритеты, состояние).

Только MAX (maxapi): события UserAdded / UserRemoved / BotStarted, личные сообщения и callback-кнопки.
Данные — JSON в /data (атомарная запись, копии). Ничего не публикуется в группу, кроме приветствия новому участнику
и явно подтверждённых владельцем публикаций (черновик → предпросмотр → «📢 Опубликовать» → успешный ответ MAX).
Персональные интересы наружу не публикуются: наружу идут только агрегаты (и только владельцу в личке)."""
import asyncio
import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

YEKB_TZ = ZoneInfo("Asia/Yekaterinburg")

# ============================================================
# Бренд и тексты
# ============================================================

GROUP_NAME = "🏟️ Своя Трибуна"
BOT_NAME = "🤖 Трибун"
SLOGAN = "Матчи. Эмоции. Разборы. Своя компания."
INVITE_PLACEHOLDER = "<ССЫЛКА НА ГРУППУ>"

WELCOME_TEXT = (
    "🏟️ Добро пожаловать на «Свою Трибуну», {name}!\n\n"
    "Здесь спорт не просто смотрят — им живут. 🔥\n\n"
    "Я Трибун 🤖 — помогаю нашей компании следить за интересными матчами, результатами и главным спортивным движением.\n\n"
    "📌 В закрепе — что здесь будет.\n\n"
    "А чтобы я учитывал именно твои интересы, настрой их у меня в личке 👇"
)
WELCOME_NO_LINK_NOTE = "\n\n(Открой личный диалог со мной в MAX и нажми «Начать».)"

INVITE_TEXT = (
    "🏟️ ЗАХОДИ НА «СВОЮ ТРИБУНУ»\n\n"
    "Собираем свою компанию людей, которые спорт не просто смотрят — им реально живут. 🔥\n\n"
    "🏒 Хоккей\n⚽ Футбол\n🎾 Теннис\n🏀 Баскетбол\n🏎️ Автоспорт\n🥊 Единоборства\n"
    "и всё большое спортивное, что действительно хочется смотреть и обсуждать.\n\n"
    "🤖 В группе живёт Трибун — наш спортивный помощник.\n\n"
    "Он следит за интересными матчами, расписанием и результатами, а дальше будет помогать с анонсами, разборами, "
    "статистикой, прогнозами и главным спортивным движением прямо в MAX.\n\n"
    "И главное — Трибун учитывает интересы нашей компании: каждый участник может в личке указать свои любимые виды спорта, "
    "команды, спортсменов и турниры.\n\n"
    "🔥 Здесь не будет новостной помойки из сотни сообщений.\n\n"
    "Идея другая:\n\n"
    "главные матчи\n+ настоящие эмоции\n+ нормальные разборы\n+ свои прогнозы\n"
    "+ компания людей, которым реально интересен спорт.\n\n"
    "Сейчас собираем первых участников.\n\n"
    "Если у тебя есть знакомый, который может смотреть матч до ночи, спорить о составе, а после финальной сирены ещё полчаса "
    "обсуждать, кто всё испортил — тащи его с собой 😄\n\n"
    "🖤💛 СВОЯ ТРИБУНА\n\n"
    "Матчи. Эмоции. Разборы. Своя компания. 🔥\n\n"
    "👉 Вступить:\n{link}"
)

ABOUT_TEXT = (
    f"{GROUP_NAME} · {BOT_NAME}\n{SLOGAN}\n\n"
    "Я Трибун — спортивный помощник нашей компании в MAX. Слежу за интересными матчами, расписанием и результатами, "
    "учитываю интересы участников и не засоряю группу лишним.\n\n"
    "⚙️ «Мои интересы» — выбери виды спорта, команды, спортсменов и турниры; менять можно в любой момент.\n"
    "🔥 «Что сегодня у меня?» — события дня с учётом твоих интересов.\n\n"
    "Твои интересы видишь только ты: в группе я их не публикую. Владельцу доступна лишь общая картина по компании."
)

# ============================================================
# Каталоги выбора (стабильные ключи; подписи можно менять)
# ============================================================

SPORTS = [("hockey", "🏒", "Хоккей"), ("football", "⚽", "Футбол"), ("tennis", "🎾", "Теннис"), ("basketball", "🏀", "Баскетбол"),
          ("motorsport", "🏎️", "Автоспорт"), ("martial", "🥊", "Единоборства"), ("winter", "⛷️", "Зимние виды")]
TEAMS = [("avto", "Автомобилист", "hockey"), ("ska", "СКА", "hockey"), ("akbars", "Ак Барс", "hockey"),
         ("sinara", "Синара", "football"), ("ural", "Урал", "football"), ("zenit", "Зенит", "football"),
         ("spartak", "Спартак", "football"), ("real", "Реал Мадрид", "football"), ("arsenal", "Арсенал", "football"),
         ("milan", "Милан", "football"), ("russia", "Сборная России", "*")]
ATHLETES = [("verstappen", "Макс Ферстаппен", "motorsport"), ("hamilton", "Льюис Хэмилтон", "motorsport"),
            ("alcaraz", "Карлос Алькарас", "tennis"), ("sinner", "Янник Синнер", "tennis"),
            ("medvedev", "Даниил Медведев", "tennis"), ("ovechkin", "Александр Овечкин", "hockey")]
COMPETITIONS = [("khl", "КХЛ", "hockey"), ("rpl", "РПЛ", "football"), ("cuprus", "Кубок России", "football"),
                ("ucl", "Лига чемпионов", "football"), ("atp", "ATP/WTA", "tennis"), ("f1", "Формула-1", "motorsport"),
                ("olymp", "Олимпиада", "winter"), ("wc", "Чемпионаты мира", "*")]
CONTENT = [("matches", "🔥", "Важные матчи"), ("results", "🏁", "Результаты"), ("analysis", "🧠", "Разборы"),
           ("stats", "📊", "Статистика"), ("news", "🗞", "Новости"), ("predictions", "🎯", "Прогнозы"), ("main", "⭐", "Только главное")]
NOTIFY = [("all", "🔔", "Всё важное"), ("main", "⭐", "Только главное"), ("off", "🔕", "Без личных уведомлений")]

CATS = {  # код категории → (поле профиля, заголовок, каталог)
    "sp": ("sports", "Мои виды спорта"),
    "tm": ("teams", "Мои команды"),
    "at": ("athletes", "Мои спортсмены"),
    "cp": ("competitions", "Мои турниры"),
    "ct": ("content_preferences", "Что мне интересно"),
}
SPORT_BY_KEY = {k: (icon, name) for k, icon, name in SPORTS}
CONTENT_BY_KEY = {k: (icon, name) for k, icon, name in CONTENT}
NOTIFY_BY_KEY = {k: (icon, name) for k, icon, name in NOTIFY}
CONTENT_NAME = {k: name for k, icon, name in CONTENT}
NOTIFY_NAME = {"all": "всё важное", "main": "только главное", "off": "без личных уведомлений"}
SPORT_FAMILY_TO_KEY = {"hockey": "hockey", "football": "football", "futsal": "football", "tennis": "tennis",
                       "basketball": "basketball", "motorsport": "motorsport"}

ONBOARDING_STEPS = ["sp", "ts", "cp", "ct", "nl"]
ADMIN_PREFIXES = ("adm", "pub", "pr", "inv")
MAX_CUSTOM_LEN = 60

HIGH_SHARE, MEDIUM_SHARE = 0.5, 0.25


# ============================================================
# Хранилище: атомарная запись, копии, строгое чтение
# ============================================================

class TribunDataError(Exception):
    def __init__(self, path, reason):
        super().__init__(f"{os.path.basename(path)}: {reason}")
        self.path, self.reason = path, reason


def atomic_write_json(path: str, data) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup_file(path: str, keep: int = 30, min_age_seconds: int = 300) -> None:
    """Копия действующего файла перед перезаписью (не чаще раза в min_age_seconds, хранится последних keep)."""
    if not os.path.exists(path):
        return
    try:
        folder = os.path.join(os.path.dirname(path), "backups")
        os.makedirs(folder, exist_ok=True)
        prefix = os.path.splitext(os.path.basename(path))[0] + "-"
        names = sorted(n for n in os.listdir(folder) if n.startswith(prefix) and n.endswith(".json"))
        if names and (datetime.datetime.now().timestamp() - os.path.getmtime(os.path.join(folder, names[-1]))) < min_age_seconds:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, os.path.join(folder, f"{prefix}{stamp}.json"))
        for old in sorted(n for n in os.listdir(folder) if n.startswith(prefix) and n.endswith(".json"))[:-keep]:
            os.unlink(os.path.join(folder, old))
    except Exception as e:
        print(f"[TRIBUN] не удалось сделать копию {path}: {e}")


def read_strict(path: str, default):
    """Нет файла → default. Файл повреждён → TribunDataError (не пустая база, ничего не перезаписывается)."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise TribunDataError(path, f"{type(e).__name__}: {e}")


def mask_secrets(text: str) -> str:
    return re.sub(r"[A-Za-z0-9_\-]{24,}", "***", text or "")


def record_tick(data_dir: str, now: datetime.datetime | None = None) -> None:
    """Планировщик жив: время последнего цикла (для «Состояния» владельца)."""
    path = os.path.join(data_dir, "tribun_jobs.json")
    try:
        data = read_strict(path, {})
    except TribunDataError:
        data = {}
    data["_tick"] = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    try:
        atomic_write_json(path, data)
    except Exception as e:
        print(f"[TRIBUN] tick: {e}")


def record_job(data_dir: str, name: str, ok: bool, error: str | None = None, now: datetime.datetime | None = None) -> None:
    path = os.path.join(data_dir, "tribun_jobs.json")
    try:
        data = read_strict(path, {})
    except TribunDataError:
        data = {}
    rec = data.setdefault(name, {})
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    if ok:
        rec["last_ok"] = stamp
    else:
        rec["last_error"] = stamp
        rec["error"] = mask_secrets(error or "")[:160]
    try:
        atomic_write_json(path, data)
    except Exception as e:
        print(f"[TRIBUN] job status: {e}")


def read_job_status(data_dir: str) -> dict:
    try:
        return read_strict(os.path.join(data_dir, "tribun_jobs.json"), {})
    except TribunDataError:
        return {}


# ============================================================
# Профиль и нормализация
# ============================================================

def norm(s: str) -> str:
    s = (s or "").casefold().replace("ё", "е")
    s = re.sub(r"[«»\"'`]", "", s)
    s = re.sub(r"\b(хк|фк|мфк|бк)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_custom(text: str) -> list:
    """Свой вариант: можно несколько через запятую / «;» / с новой строки. Ничего не «исправляется»."""
    items, seen = [], set()
    for raw in re.split(r"[,\n;]+", text or ""):
        item = re.sub(r"\s+", " ", raw.strip(" \t-•*·")).strip()
        if not item or item.startswith("/"):
            continue
        item = item[:MAX_CUSTOM_LEN]
        if norm(item) not in seen:
            seen.add(norm(item))
            items.append(item)
    return items[:10]


def item_key(text: str) -> str:
    return "c" + hashlib.sha1(norm(text).encode("utf-8")).hexdigest()[:8]


def now_default() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def new_profile(user_id: int, display_name: str, stamp: str) -> dict:
    return {"user_id": user_id, "display_name": display_name, "sports": [], "teams": [], "athletes": [], "competitions": [],
            "content_preferences": [], "notification_level": None, "onboarding_completed": False, "welcomed": False,
            "active_in_group": False, "created_at": stamp, "updated_at": stamp,
            # служебное для пульта владельца
            "ever_in_group": False, "joined_at": None, "joined_source": None, "left_at": None, "returned_at": None,
            "join_notified": False, "completion_notified": False, "interests_updated_at": None}


def strip_icon(label: str) -> str:
    return label.split(" ", 1)[1] if " " in label else label


def people(n: int) -> str:
    """6 человек, 1 человек, 2 человека, 11 человек, 22 человека."""
    if n % 10 in (2, 3, 4) and not 12 <= n % 100 <= 14:
        return f"{n} человека"
    return f"{n} человек"


OWNER_EVENTS = {  # событие → (включено по умолчанию, можно ли менять в «🔔 Уведомления»)
    "new_member": (True, True, "Новый участник"),
    "profile_completed": (True, True, "Впервые настроил профиль"),
    "left": (True, True, "Покинул группу"),
    "returned": (True, True, "Вернулся"),
    "interest_changes": (False, False, "Каждое изменение интересов"),
    "clicks": (False, False, "Каждое нажатие пользователя"),
    "menu_opens": (False, False, "Каждое открытие меню"),
}


def display_name_of(user) -> str:
    parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    name = " ".join(p for p in parts if p).strip()
    return name or (getattr(user, "username", None) or str(getattr(user, "user_id", "")))


def interest_level(count: int, active_total: int) -> str:
    """Коллективный интерес: ≥50% активных участников — high, 25–49% — medium, ниже — niche."""
    if active_total <= 0:
        return "niche"
    share = count / active_total
    return "high" if share >= HIGH_SHARE else ("medium" if share >= MEDIUM_SHARE else "niche")


LEVEL_LABEL = {"high": "высокий", "medium": "средний", "niche": "нишевый"}


def grid(buttons: list, per_row: int = 2) -> list:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


@dataclass
class Btn:
    label: str
    payload: str | None = None
    url: str | None = None


def as_btn(item) -> Btn:
    return item if isinstance(item, Btn) else Btn(item[0], item[1])


@dataclass
class Ctx:
    user_id: int
    chat_id: int
    is_private: bool
    display_name: str = ""
    text: str = ""
    payload: str = ""
    reply: Callable[..., Awaitable] = None
    edit: Callable[..., Awaitable] = None
    ack: Callable[..., Awaitable] = None


# ============================================================
# Основной класс
# ============================================================

class Tribun:
    def __init__(self, bot, *, data_dir: str, group_chat_id: int, owner_id: Callable[[], int | None], version: str,
                 dry_run: bool = False, now: Callable[[], datetime.datetime] = now_default,
                 events_for_day: Callable[[datetime.date], list] | None = None,
                 automation: Callable[[], list] | None = None, status: Callable[[], dict] | None = None,
                 bot_username: str | None = None):
        self.bot = bot
        self.data_dir = data_dir
        self.group_chat_id = group_chat_id
        self.owner_id = owner_id
        self.version = version
        self.dry_run = dry_run
        self.now = now
        self.events_for_day = events_for_day or (lambda day: [])
        self.automation = automation or (lambda: [])
        self.status = status or (lambda: {})
        self.bot_username = bot_username
        self.awaiting: dict = {}                      # ожидание текстового ввода (в памяти): user_id → состояние
        self.lock = asyncio.Lock()
        self.published_guard: set = set()             # id публикаций, уже отправленных в этом процессе
        self.members_path = os.path.join(data_dir, "tribun_members.json")
        self.pubs_path = os.path.join(data_dir, "tribun_publications.json")
        self.prios_path = os.path.join(data_dir, "tribun_priorities.json")
        self._link_cache = (None, None)

    # ---- роль -----------------------------------------------------------------

    def is_owner(self, user_id) -> bool:
        owner = self.owner_id()
        return owner is not None and user_id is not None and int(user_id) == int(owner)

    def stamp(self) -> str:
        return self.now().astimezone(YEKB_TZ).isoformat(timespec="seconds")

    # ---- участники: хранилище ----------------------------------------------------

    def load_members(self) -> dict:
        store = read_strict(self.members_path, None)
        if store is None:
            return {"version": 1, "members": {}, "meta": {}}
        if not isinstance(store, dict) or not isinstance(store.get("members"), dict):
            raise TribunDataError(self.members_path, "неверная структура")
        store.setdefault("meta", {})
        return store

    def save_members(self, store: dict) -> None:
        backup_file(self.members_path)
        atomic_write_json(self.members_path, store)

    def touch(self, store: dict, user_id: int, display_name: str | None = None, *, active: bool | None = None) -> dict:
        key = str(user_id)
        profile = store["members"].get(key)
        if profile is None:
            profile = new_profile(int(user_id), display_name or str(user_id), self.stamp())
            store["members"][key] = profile
        elif display_name and display_name != profile.get("display_name"):
            profile["display_name"] = display_name              # смена имени не создаёт новый профиль (ключ — user_id)
            profile["updated_at"] = self.stamp()
        if active is not None and profile.get("active_in_group") != active:
            profile["active_in_group"] = active
            profile["updated_at"] = self.stamp()
        return profile

    # ---- события группы -----------------------------------------------------------

    async def on_user_added(self, chat_id: int, user, inviter_id=None) -> str:
        """Вход в группу. Приветствие — один раз на человека. Владельцу — одно уведомление на одно событие входа:
        повторное техническое событие (человек уже в группе) дубля не создаёт."""
        if chat_id != self.group_chat_id or getattr(user, "is_bot", False):
            return "ignored"
        async with self.lock:
            try:
                store = self.load_members()
            except TribunDataError as e:
                print(f"[TRIBUN] {e}")
                return "data-error"
            before = store["members"].get(str(user.user_id))
            was_active = bool(before and before.get("active_in_group"))
            profile = self.touch(store, user.user_id, display_name_of(user), active=True)
            first_join = not profile.get("ever_in_group")
            if first_join:
                profile.update(ever_in_group=True, joined_at=self.stamp(), joined_source="event")
            elif not was_active:
                profile["returned_at"] = self.stamp()
            welcome = not profile["welcomed"]
            self.save_members(store)
            if not first_join and was_active and not welcome:
                return "duplicate"
            result = "returned" if not first_join and not was_active else "duplicate"
            if welcome and await self.send_welcome(user):
                store = self.load_members()
                profile = self.touch(store, user.user_id)
                profile["welcomed"] = True
                profile["welcomed_at"] = self.stamp()
                self.save_members(store)
                result = "welcomed"
            elif welcome:
                result = "welcome-failed"
            if first_join:
                await self.owner_join_notice(user.user_id)
            elif not was_active:
                store = self.load_members()
                await self.notify_owner("returned", self.returned_text(store["members"][str(user.user_id)]))
            return result

    async def on_user_removed(self, chat_id: int, user) -> str:
        if chat_id != self.group_chat_id or getattr(user, "is_bot", False):
            return "ignored"
        async with self.lock:
            try:
                store = self.load_members()
            except TribunDataError as e:
                print(f"[TRIBUN] {e}")
                return "data-error"
            profile = store["members"].get(str(user.user_id))
            if profile is None:
                return "unknown"
            if not profile.get("active_in_group"):
                return "already-left"                              # повторное событие — второго уведомления нет
            self.touch(store, user.user_id, active=False)         # профиль не удаляется, но в агрегат не входит
            profile["left_at"] = self.stamp()
            self.save_members(store)
            await self.notify_owner("left", self.left_text(profile))
            return "left"

    # ---- уведомления владельцу ------------------------------------------------------------------

    def owner_settings_path(self) -> str:
        return os.path.join(self.data_dir, "tribun_settings.json")

    def owner_settings(self) -> dict:
        """Какие события присылать владельцу. Хранится в файле (переживает рестарт); по умолчанию — первый этап."""
        try:
            saved = read_strict(self.owner_settings_path(), {}).get("owner_notifications", {})
        except TribunDataError:
            saved = {}
        return {k: bool(saved.get(k, default)) if changeable else default for k, (default, changeable, _) in OWNER_EVENTS.items()}

    def set_owner_setting(self, key: str, value: bool) -> None:
        if key not in OWNER_EVENTS or not OWNER_EVENTS[key][1]:
            raise ValueError("эта настройка не меняется")
        settings = self.owner_settings()
        settings[key] = bool(value)
        atomic_write_json(self.owner_settings_path(), {"owner_notifications": settings})

    async def notify_owner(self, kind: str, text: str) -> bool:
        """Служебное сообщение ТОЛЬКО владельцу в личку (никогда в группу). True — доставлено."""
        owner = self.owner_id()
        if owner is None or not self.owner_settings().get(kind, False):
            return False
        if self.dry_run:
            print(f"[DRY RUN] владельцу ({kind}):\n{text}")
            return False
        try:
            return (await self.bot.send_message(user_id=owner, text=text)) is not None
        except Exception as e:
            print(f"[TRIBUN] уведомление владельцу ({kind}) не доставлено: {type(e).__name__}: {mask_secrets(str(e))}")
            return False

    def active_count(self, store: dict) -> int:
        return len(self.active_profiles(store))

    def join_text(self, store: dict, profile: dict) -> str:
        interests = "✅ настроены" if profile.get("onboarding_completed") else "ещё не настроены"
        return (f"👤 Новый участник «Своей Трибуны»\n\n{profile['display_name']}\nВ группе: {people(self.active_count(store))}\n\n"
                f"⚙️ Интересы: {interests}")

    def left_text(self, profile: dict) -> str:
        return (f"👋 {profile['display_name']} покинул «Свою Трибуну»\n\nПрофиль интересов сохранён.\n"
                "В общей карте интересов больше не учитывается.")

    def returned_text(self, profile: dict) -> str:
        tail = ("Его прежние интересы восстановлены." if profile.get("onboarding_completed")
                else "Интересы он ещё не настраивал.")
        return f"🔄 {profile['display_name']} вернулся на «Свою Трибуну»\n\n{tail}"

    def completion_card(self, profile: dict) -> str:
        def lines(values, cat):
            return "\n".join(values) if values else "—"
        sports = "\n".join(self.value_label("sp", v) for v in profile["sports"]) or "—"
        favs = lines(profile["teams"] + profile["athletes"], "tm")
        comps = lines(profile["competitions"], "cp")
        content = "\n".join(CONTENT_NAME.get(v, v).lower() for v in profile["content_preferences"]) or "—"
        notify = NOTIFY_NAME.get(profile.get("notification_level"), "не выбрано")
        return (f"✅ {profile['display_name']} настроил интересы\n\n{sports}\n\n❤️ Команды / спортсмены:\n{favs}\n\n"
                f"🏆 Турниры:\n{comps}\n\n🔥 Интересует:\n{content}\n\n🔔 Личные уведомления:\n{notify}")

    async def owner_join_notice(self, user_id: int) -> None:
        store = self.load_members()
        profile = store["members"][str(user_id)]
        if self.is_owner(user_id):
            profile["join_notified"] = True
        elif await self.notify_owner("new_member", self.join_text(store, profile)):
            profile["join_notified"] = True
        else:
            return
        self.save_members(store)

    async def owner_completion_notice(self, user_id: int) -> None:
        """Карточка интересов — один раз, после ПЕРВОГО завершения профиля. Мелкие правки владельцу не присылаются."""
        store = self.load_members()
        profile = store["members"][str(user_id)]
        if profile.get("completion_notified"):
            return
        if self.is_owner(user_id) or await self.notify_owner("profile_completed", self.completion_card(profile)):
            profile["completion_notified"] = True
            self.save_members(store)

    async def flush_owner_notifications(self) -> None:
        """Доставка того, что не дошло (например, владелец ещё не открывал диалог): новые участники и первые профили недавних дней."""
        try:
            store = self.load_members()
        except TribunDataError:
            return
        now = self.now()
        for key, profile in list(store["members"].items()):
            try:
                age = now - datetime.datetime.fromisoformat(profile["created_at"])
            except (KeyError, ValueError):
                continue
            if age > datetime.timedelta(days=7):
                continue
            if profile.get("ever_in_group") and profile.get("joined_source") == "event" and not profile.get("join_notified"):
                await self.owner_join_notice(int(key))
            if profile.get("onboarding_completed") and not profile.get("completion_notified"):
                await self.owner_completion_notice(int(key))

    def start_link(self) -> str | None:
        if not self.bot_username:
            return None
        try:
            from maxapi.utils.deep_linking import create_start_link
            return create_start_link(self.bot_username, "interests")
        except Exception:
            return f"https://max.ru/{self.bot_username}?start=interests"

    async def send_welcome(self, user) -> bool:
        link = self.start_link()
        text = WELCOME_TEXT.format(name=getattr(user, "first_name", None) or display_name_of(user)) + ("" if link else WELCOME_NO_LINK_NOTE)
        rows = [[Btn("⚙️ Настроить мои интересы", url=link)]] if link else None
        if self.dry_run:
            print(f"[DRY RUN] приветствие в группу:\n{text}")
            return False
        try:
            resp = await self.bot.send_message(chat_id=self.group_chat_id, text=text, attachments=build_keyboard(rows))
            return resp is not None
        except Exception as e:
            print(f"[TRIBUN] приветствие не отправлено: {type(e).__name__}: {mask_secrets(str(e))}")
            return False

    # ---- синхронизация состава группы (на случай пропущенных событий) -------------------

    async def sync_members(self) -> bool:
        """Сверяет состав группы с профилями. Уже состоящие в группе при первом знакомстве считаются приветствованными (задним
        числом не приветствуем и владельца не уведомляем). Пропущенные события выхода/возврата досылаются владельцу.
        Если список получить не удалось — ничего не меняем."""
        ids, marker = {}, None
        try:
            for _ in range(200):
                page = await self.bot.get_chat_members(chat_id=self.group_chat_id, marker=marker, count=100)
                for m in page.members:
                    if not getattr(m, "is_bot", False):
                        ids[m.user_id] = display_name_of(m)
                marker = getattr(page, "marker", None)
                if marker is None:
                    break
            else:
                raise RuntimeError("слишком много страниц")
        except Exception as e:
            print(f"[TRIBUN] синхронизация состава не удалась: {type(e).__name__}: {mask_secrets(str(e))}")
            return False
        async with self.lock:
            try:
                store = self.load_members()
            except TribunDataError as e:
                print(f"[TRIBUN] {e}")
                return False
            events = []
            for uid, name in ids.items():
                before = store["members"].get(str(uid))
                was_active = bool(before and before.get("active_in_group"))
                profile = self.touch(store, uid, name, active=True)
                if before is None:
                    profile.update(welcomed=True, welcomed_at=self.stamp(), ever_in_group=True, joined_at=self.stamp(),
                                   joined_source="sync", join_notified=True)
                elif not was_active:
                    if not profile.get("ever_in_group"):
                        profile.update(ever_in_group=True, joined_at=self.stamp(), joined_source="sync")
                    else:
                        profile["returned_at"] = self.stamp()
                        events.append(("returned", uid))
            for key, profile in store["members"].items():
                if int(key) not in ids and profile.get("active_in_group"):
                    profile["active_in_group"] = False
                    profile["left_at"] = self.stamp()
                    profile["updated_at"] = self.stamp()
                    events.append(("left", int(key)))
            store["meta"]["last_sync"] = self.stamp()
            store["meta"]["last_sync_count"] = len(ids)
            self.save_members(store)
            for kind, uid in events:
                profile = store["members"][str(uid)]
                await self.notify_owner(kind, self.returned_text(profile) if kind == "returned" else self.left_text(profile))
        print(f"[TRIBUN] состав группы: {len(ids)} участников")
        return True

    async def sync_loop(self, interval_hours: float = 3.0):
        while True:
            try:
                await self.sync_members()
                await self.flush_owner_notifications()
            except Exception as e:
                print(f"[TRIBUN] sync_loop: {type(e).__name__}: {e}")
            await asyncio.sleep(interval_hours * 3600)

    # ---- личный диалог ---------------------------------------------------------------

    async def ensure_profile(self, user_id: int, display_name: str | None) -> dict:
        """Профиль по стабильному user_id (имя/username могут меняться). Новый профиль из личного диалога: участие в группе
        проверяется у MAX. Уже состоящего в группе в группе приветствовать не нужно; пока не вступил — приветствие дождётся входа."""
        async with self.lock:
            store = self.load_members()
            known = str(user_id) in store["members"]
            profile = self.touch(store, user_id, display_name or None)
            if not known:
                member = await self.is_group_member(user_id)
                profile["active_in_group"] = member
                if member:
                    profile.update(welcomed=True, ever_in_group=True, joined_at=self.stamp(), joined_source="dialog", join_notified=True)
            self.save_members(store)
            return dict(profile)

    async def on_bot_started(self, user, chat_id: int, payload=None, reply=None) -> None:
        """Человек открыл личный диалог с Трибуном (кнопка «Начать» / ссылка с start=interests)."""
        try:
            profile = await self.ensure_profile(user.user_id, display_name_of(user))
        except TribunDataError as e:
            print(f"[TRIBUN] {e}")
            return
        done = profile["onboarding_completed"]
        text, rows = (self.main_menu(profile, self.is_owner(user.user_id)) if done and payload != "interests"
                      else self.onboarding_intro(profile))
        await reply(text, rows)

    async def is_group_member(self, user_id: int) -> bool:
        try:
            return (await self.bot.get_chat_member(chat_id=self.group_chat_id, user_id=user_id)) is not None
        except Exception as e:
            print(f"[TRIBUN] проверка участия в группе: {type(e).__name__}: {mask_secrets(str(e))}")
            return False

    async def on_message(self, ctx: Ctx) -> None:
        """Сообщение боту. Только личный диалог; в группе Трибун молчит."""
        if not ctx.is_private:
            return
        try:
            profile = await self.ensure_profile(ctx.user_id, ctx.display_name)
            state = self.awaiting.get(ctx.user_id)
            if state:
                await self.handle_awaited(ctx, state)
                return
            text, rows = self.main_menu(profile, self.is_owner(ctx.user_id))
            if not profile["onboarding_completed"]:
                text += "\n\n👉 Настрой интересы — это занимает минуту."
                rows = [[Btn("⚙️ Настроить интересы", "ob:sp")]] + rows
            await ctx.reply(text, rows)
        except TribunDataError as e:
            print(f"[TRIBUN] {e}")
            await ctx.reply("⚠️ Данные Трибуны временно недоступны. Попробуй чуть позже.", None)

    # ---- callback --------------------------------------------------------------------

    async def on_callback(self, ctx: Ctx) -> None:
        if ctx.ack:
            try:
                await ctx.ack()
            except Exception:
                pass
        if not ctx.is_private:
            return                                                # групповые сообщения не управляют Трибуном
        payload = ctx.payload or ""
        head = payload.split(":", 1)[0]
        if head in ADMIN_PREFIXES and not self.is_owner(ctx.user_id):
            print(f"[TRIBUN] админ-действие {head!r} от не-владельца {ctx.user_id} отклонено")
            return                                                # серверная проверка роли на КАЖДОМ админ-действии
        try:
            await self.route(ctx, head, payload)
        except TribunDataError as e:
            print(f"[TRIBUN] {e}")
            await ctx.reply("⚠️ Данные Трибуны временно недоступны. Попробуй чуть позже.", None)
        except (ValueError, IndexError, KeyError) as e:
            print(f"[TRIBUN] неизвестное действие {payload!r}: {type(e).__name__}: {e}")
            profile = await self.ensure_profile(ctx.user_id, ctx.display_name)
            await ctx.edit(*self.main_menu(profile, self.is_owner(ctx.user_id)))

    async def route(self, ctx: Ctx, head: str, payload: str) -> None:
        parts = payload.split(":")
        self.awaiting.pop(ctx.user_id, None) if head not in ("add", "pr") else None
        owner = self.is_owner(ctx.user_id)
        profile = await self.ensure_profile(ctx.user_id, ctx.display_name)
        if head in ("m", ""):
            await ctx.edit(*self.main_menu(profile, owner))
        elif head == "about":
            await ctx.edit(ABOUT_TEXT, [[Btn("🏠 Меню", "m")]])
        elif head == "today":
            await ctx.edit(*self.today_view(profile))
        elif head == "me":
            await ctx.edit(*self.interests_hub(profile))
        elif head == "ob":
            await ctx.edit(*self.onboarding_screen(profile, parts[1]))
        elif head == "ed":
            await ctx.edit(*(self.nl_edit_screen(profile) if parts[1] == "nl" else self.category_screen(profile, parts[1], "e")))
        elif head == "t":                                     # t:<cat>:<key>:<mode>
            await self.toggle(ctx, profile, parts[1], parts[2], parts[3])
        elif head == "add":                                   # add:<cat>:<mode>
            self.awaiting[ctx.user_id] = {"kind": "custom", "cat": parts[1], "mode": parts[2]}
            await ctx.edit(self.add_prompt(parts[1]), [[Btn("⬅️ Отмена", self.back_payload(parts[1], parts[2]))]])
        elif head == "nl":                                    # nl:<level>:<mode>
            await self.set_notification(ctx, profile, parts[1], parts[2])
        elif head == "inv":
            await ctx.edit(*await self.invite_view())
        elif head in ADMIN_PREFIXES:
            await self.route_admin(ctx, head, parts)
        else:
            await ctx.edit(*self.main_menu(profile, owner))

    # ---- меню ---------------------------------------------------------------------------

    def main_menu(self, profile: dict, owner: bool):
        text = f"{GROUP_NAME} · {BOT_NAME}\n{SLOGAN}\n\nПривет, {profile['display_name']}! Выбирай 👇"
        rows = [[Btn("🔥 Что сегодня у меня?", "today")], [Btn("⚙️ Мои интересы", "me")], [Btn("ℹ️ О Трибуне", "about")]]
        if owner:
            rows += [[Btn("📨 Приглашение", "inv")], [Btn("🛠 Управление Трибуной", "adm")]]
        return text, rows

    def onboarding_intro(self, profile: dict):
        text = (f"{GROUP_NAME}\n\nПривет, {profile['display_name']}! Я Трибун 🤖\n"
                "Давай за минуту настроим твои интересы: виды спорта, команды, спортсмены, турниры и что тебе интересно. "
                "Потом всё можно поменять в «⚙️ Мои интересы».")
        return text, [[Btn("▶️ Начать настройку", "ob:sp")], [Btn("🏠 Меню", "m")]]

    # ---- опции категорий ---------------------------------------------------------------------

    def selected_sports(self, profile: dict) -> set:
        return set(profile["sports"])

    def catalog(self, cat: str, profile: dict) -> list:
        """[(key, label, value)] — варианты каталога, подходящие выбранным видам спорта (если не выбраны — все)."""
        chosen = self.selected_sports(profile)

        def fits(tag: str) -> bool:
            return tag == "*" or not chosen or tag in chosen
        if cat == "sp":
            return [(k, f"{icon} {name}", k) for k, icon, name in SPORTS]
        if cat == "tm":
            return [(k, name, name) for k, name, tag in TEAMS if fits(tag)]
        if cat == "at":
            return [(k, name, name) for k, name, tag in ATHLETES if fits(tag)]
        if cat == "cp":
            return [(k, name, name) for k, name, tag in COMPETITIONS if fits(tag)]
        if cat == "ct":
            return [(k, f"{icon} {name}", k) for k, icon, name in CONTENT]
        raise ValueError("неизвестная категория")

    def value_label(self, cat: str, value: str) -> str:
        if cat == "sp":
            icon, name = SPORT_BY_KEY.get(value, ("➕", value))
            return f"{icon} {name}"
        if cat == "ct":
            icon, name = CONTENT_BY_KEY.get(value, ("•", value))
            return f"{icon} {name}"
        return value

    def canonical_value(self, cat: str, text: str) -> str:
        """Известное название приводится к каноническому написанию каталога; неизвестное остаётся как написал человек."""
        for key, label, value in self.catalog_full(cat):
            names = [norm(value), norm(label)] + ([norm(SPORT_BY_KEY[key][1])] if cat == "sp" else [])
            if norm(text) in names:
                return value
        return text

    def catalog_full(self, cat: str) -> list:
        return self.catalog(cat, {"sports": []})

    def option_buttons(self, cat: str, profile: dict, mode: str) -> list:
        field = CATS[cat][0]
        selected = {norm(v) for v in profile[field]}
        buttons, shown = [], set()
        for key, label, value in self.catalog(cat, profile):
            shown.add(norm(value))
            mark = "✅ " if norm(value) in selected else ""
            buttons.append(Btn(mark + label, f"t:{cat}:{key}:{mode}"))
        for value in profile[field]:                                   # свои варианты, уже выбранные человеком
            if norm(value) not in shown and not (cat in ("sp", "ct") and value in (SPORT_BY_KEY if cat == "sp" else CONTENT_BY_KEY)):
                buttons.append(Btn("✅ " + self.value_label(cat, value), f"t:{cat}:{item_key(value)}:{mode}"))
        return buttons

    def resolve_option(self, cat: str, key: str, profile: dict):
        for k, label, value in self.catalog_full(cat):
            if k == key:
                return value
        for value in profile[CATS[cat][0]]:
            if item_key(value) == key:
                return value
        return None

    def add_prompt(self, cat: str) -> str:
        return {"sp": "✍️ Напиши, какой ещё вид спорта тебе интересен (можно несколько — через запятую):",
                "tm": "✍️ Напиши команды (по одной в строке или через запятую), например:\nАвтомобилист\nЗенит\nСборная России",
                "at": "✍️ Напиши спортсменов (по одному в строке или через запятую), например:\nМакс Ферстаппен\nКарлос Алькарас",
                "cp": "✍️ Напиши турниры (по одному в строке или через запятую):"}[cat]

    def back_payload(self, cat: str, mode: str) -> str:
        if mode == "o":
            return "ob:" + ("ts" if cat in ("tm", "at") else {"sp": "sp", "cp": "cp", "ct": "ct"}[cat])
        return f"ed:{cat}"

    # ---- экраны выбора ------------------------------------------------------------------------

    def selection_summary(self, cat: str, profile: dict) -> str:
        values = profile[CATS[cat][0]]
        return ", ".join(self.value_label(cat, v) for v in values) if values else "пока ничего"

    def category_screen(self, profile: dict, cat: str, mode: str):
        title = CATS[cat][1]
        text = f"⚙️ {title}\n\nСейчас: {self.selection_summary(cat, profile)}\n\nНажимай, чтобы выбрать или убрать."
        rows = grid(self.option_buttons(cat, profile, mode))
        if cat in ("sp", "cp"):
            rows.append([Btn("➕ Другое", f"add:{cat}:{mode}")])
        elif cat == "tm":
            rows.append([Btn("➕ Добавить команду", f"add:tm:{mode}")])
        elif cat == "at":
            rows.append([Btn("➕ Добавить спортсмена", f"add:at:{mode}")])
        rows.append([Btn("✅ Готово", "me")] if mode == "e" else [])
        return text, [r for r in rows if r]

    def onboarding_screen(self, profile: dict, step: str):
        n = ONBOARDING_STEPS.index(step) + 1
        head = f"Шаг {n} из {len(ONBOARDING_STEPS)}"
        prev_step = ONBOARDING_STEPS[n - 2] if n > 1 else None
        next_step = ONBOARDING_STEPS[n] if n < len(ONBOARDING_STEPS) else None
        nav = []
        if prev_step:
            nav.append(Btn("◀️ Назад", f"ob:{prev_step}"))
        if next_step:
            nav.append(Btn("Далее ▶️", f"ob:{next_step}"))
        if step == "ts":
            text = (f"{head} · Команды и спортсмены\n\nКоманды: {self.selection_summary('tm', profile)}\n"
                    f"Спортсмены: {self.selection_summary('at', profile)}\n\nВыбирай несколько или добавь своё.")
            rows = grid(self.option_buttons("tm", profile, "o")) + [[Btn("➕ Добавить команду", "add:tm:o")]] + \
                grid(self.option_buttons("at", profile, "o")) + [[Btn("➕ Добавить спортсмена", "add:at:o")]]
        elif step == "nl":
            level = profile.get("notification_level")
            text = f"{head} · Личные уведомления\n\nКак часто писать тебе в личку?"
            rows = [[Btn(("✅ " if level == k else "") + f"{icon} {name}", f"nl:{k}:o")] for k, icon, name in NOTIFY]
            nav = [Btn("◀️ Назад", f"ob:{prev_step}")]
        else:
            cat = {"sp": "sp", "cp": "cp", "ct": "ct"}[step]
            titles = {"sp": "Виды спорта", "cp": "Турниры", "ct": "Что тебе интересно"}
            text = f"{head} · {titles[step]}\n\nСейчас: {self.selection_summary(cat, profile)}\n\nМожно выбрать несколько."
            rows = grid(self.option_buttons(cat, profile, "o"))
            if cat in ("sp", "cp"):
                rows.append([Btn("➕ Другое", f"add:{cat}:o")])
        if nav:
            rows.append(nav)
        return text, rows

    def interests_hub(self, profile: dict):
        level = profile.get("notification_level")
        notify = " ".join(NOTIFY_BY_KEY[level]) if level in NOTIFY_BY_KEY else "не выбрано"
        text = (
            "⚙️ Мои интересы\n\n"
            f"🏅 Виды спорта: {self.selection_summary('sp', profile)}\n"
            f"👥 Команды: {self.selection_summary('tm', profile)}\n"
            f"⭐ Спортсмены: {self.selection_summary('at', profile)}\n"
            f"🏆 Турниры: {self.selection_summary('cp', profile)}\n"
            f"🎯 Что интересно: {self.selection_summary('ct', profile)}\n"
            f"🔔 Личные уведомления: {notify}\n\n"
            "Выбери, что изменить — остальное не трогаю.")
        rows = [[Btn("🏅 Мои виды спорта", "ed:sp"), Btn("👥 Мои команды", "ed:tm")],
                [Btn("⭐ Мои спортсмены", "ed:at"), Btn("🏆 Мои турниры", "ed:cp")],
                [Btn("🎯 Что мне интересно", "ed:ct"), Btn("🔔 Личные уведомления", "ed:nl")],
                [Btn("🔥 Что сегодня у меня?", "today"), Btn("🏠 Меню", "m")]]
        return text, rows

    # ---- изменения профиля ------------------------------------------------------------------------

    async def toggle(self, ctx: Ctx, profile: dict, cat: str, key: str, mode: str) -> None:
        async with self.lock:
            store = self.load_members()
            profile = self.touch(store, ctx.user_id)
            value = self.resolve_option(cat, key, profile)
            if value is None:
                await ctx.edit("Этот вариант уже недоступен. Открой настройки заново.", [[Btn("⚙️ Мои интересы", "me")]])
                return
            field = CATS[cat][0]
            kept = [v for v in profile[field] if norm(v) != norm(value)]
            profile[field] = kept if len(kept) != len(profile[field]) else profile[field] + [value]
            profile["updated_at"] = profile["interests_updated_at"] = self.stamp()
            self.save_members(store)
        if mode == "o":
            step = "ts" if cat in ("tm", "at") else cat
            await ctx.edit(*self.onboarding_screen(profile, step))
        else:
            await ctx.edit(*self.category_screen(profile, cat, "e"))

    async def set_notification(self, ctx: Ctx, profile: dict, level: str, mode: str) -> None:
        if level not in NOTIFY_BY_KEY:
            return
        async with self.lock:
            store = self.load_members()
            profile = self.touch(store, ctx.user_id)
            profile["notification_level"] = level
            profile["updated_at"] = profile["interests_updated_at"] = self.stamp()
            first_completion = mode == "o" and not profile["onboarding_completed"]
            if mode == "o":
                profile["onboarding_completed"] = True
            self.save_members(store)
        if first_completion:
            await self.owner_completion_notice(ctx.user_id)
        if mode == "o":
            await ctx.edit("✅ Запомнил!\n\nТеперь я буду учитывать твои интересы в работе «Своей Трибуны».",
                           [[Btn("⚙️ Мои интересы", "me")], [Btn("🔥 Что сегодня у меня?", "today")]])
        else:
            await ctx.edit(*self.nl_edit_screen(profile))

    def nl_edit_screen(self, profile: dict):
        level = profile.get("notification_level")
        rows = [[Btn(("✅ " if level == k else "") + f"{icon} {name}", f"nl:{k}:e")] for k, icon, name in NOTIFY]
        rows.append([Btn("✅ Готово", "me")])
        return "⚙️ Личные уведомления\n\nКак часто писать тебе в личку?", rows

    async def handle_awaited(self, ctx: Ctx, state: dict) -> None:
        kind = state["kind"]
        if kind == "custom":
            self.awaiting.pop(ctx.user_id, None)
            cat, mode = state["cat"], state["mode"]
            items = parse_custom(ctx.text)
            if not items:
                self.awaiting[ctx.user_id] = state
                await ctx.reply("Не понял. " + self.add_prompt(cat), [[Btn("⬅️ Отмена", self.back_payload(cat, mode))]])
                return
            async with self.lock:
                store = self.load_members()
                profile = self.touch(store, ctx.user_id)
                field = CATS[cat][0]
                added = []
                for item in items:
                    value = self.canonical_value(cat, item)
                    if all(norm(v) != norm(value) for v in profile[field]):
                        profile[field].append(value)
                        added.append(value)
                profile["updated_at"] = profile["interests_updated_at"] = self.stamp()
                self.save_members(store)
            note = ("Добавил: " + ", ".join(added)) if added else "Это уже в списке."
            if mode == "o":
                text, rows = self.onboarding_screen(profile, "ts" if cat in ("tm", "at") else cat)
            else:
                text, rows = self.category_screen(profile, cat, "e")
            await ctx.reply(f"✅ {note}\n\n{text}", rows)
            return
        if not self.is_owner(ctx.user_id):                      # дальше — только владелец
            self.awaiting.pop(ctx.user_id, None)
            return
        await self.handle_admin_text(ctx, state)

    # ---- «Что сегодня у меня?» -----------------------------------------------------------------------------

    def event_score(self, profile: dict, ev: dict, prios: list):
        score, reasons = 0, []
        club, rival, tour = norm(ev.get("club_name", "")), norm(ev.get("rival", "")), norm(ev.get("tournament", ""))
        aliases = [norm(a) for a in ev.get("aliases", [])]
        for team in profile["teams"]:
            t = norm(team)
            if t and (t in club or any(t == a or t in a for a in aliases) or t in rival):
                score += 3
                reasons.append(team)
                break
        for ath in profile["athletes"]:
            a = norm(ath)
            if a and (a in rival or a in club or a in tour):
                score += 3
                reasons.append(ath)
                break
        for comp in profile["competitions"]:
            c = norm(comp)
            if c and c in tour:
                score += 2
                reasons.append(comp)
                break
        if ev.get("sport_key") and ev["sport_key"] in profile["sports"]:
            score += 1
            reasons.append(SPORT_BY_KEY.get(ev["sport_key"], ("", ev["sport_key"]))[1])
        boosted = False
        for p in prios:
            v = norm(p["value"])
            if (p["type"] == "team" and (v in club or v in rival)) or (p["type"] == "competition" and v in tour) or \
                    (p["type"] == "sport" and v == norm(ev.get("sport_key", ""))) or \
                    (p["type"] == "event" and v and (v in club or v in rival or v in tour)):
                boosted = True
        return score + (1 if boosted else 0), reasons, boosted

    def fmt_event(self, ev: dict) -> str:
        line = f"{ev.get('icon', '🏟️')} {ev['club_name']} — {ev['rival']}\n   🗓 {ev.get('time_text') or 'время уточняется'}"
        if ev.get("tournament"):
            line += f" · {ev['tournament']}"
        return line

    def today_view(self, profile: dict):
        today = self.now().astimezone(YEKB_TZ).date()
        prios = self.active_priorities()
        events = self.events_for_day(today)
        has_interests = any(profile[f] for f in ("sports", "teams", "athletes", "competitions"))
        head = f"🔥 Что сегодня у меня — {today:%d.%m.%Y}"
        rows = [[Btn("⚙️ Мои интересы", "me"), Btn("🏠 Меню", "m")]]
        if not events:
            soon = []
            for offset in range(1, 8):
                day = today + datetime.timedelta(days=offset)
                for ev in self.events_for_day(day):
                    soon.append((day, ev))
            text = f"{head}\n\nСегодня по моим источникам событий нет."
            if soon:
                scored = sorted(soon, key=lambda de: (-self.event_score(profile, de[1], prios)[0], de[0]))[:3]
                text += "\n\nБлижайшее:\n" + "\n".join(f"{d:%d.%m} · " + self.fmt_event(ev).replace("\n   🗓 ", " · ") for d, ev in scored)
            if not has_interests:
                text += "\n\nНастрой интересы, чтобы я подсказывал именно твоё."
            return text, rows
        ranked = sorted(((self.event_score(profile, ev, prios), ev) for ev in events), key=lambda x: -x[0][0])
        mine = [(sc, ev) for sc, ev in ranked if sc[1]]                 # совпавшие по личным интересам
        rest = [(sc, ev) for sc, ev in ranked if not sc[1]]
        lines = [head]
        if mine:
            lines += ["", "⭐ По твоим интересам:"] + [self.fmt_event(ev) + (" 🔝" if sc[2] else "") for sc, ev in mine]
            if rest:
                lines += ["", "Ещё сегодня у Трибуны:"] + [self.fmt_event(ev) + (" 🔝" if sc[2] else "") for sc, ev in rest]
        else:
            lines += ["", ("По твоим интересам на сегодня совпадений нет — вот что есть у Трибуны:" if has_interests
                           else "Ты ещё не настроил интересы — вот что сегодня у Трибуны:")]
            lines += [self.fmt_event(ev) + (" 🔝" if sc[2] else "") for sc, ev in ranked]
        return "\n".join(lines), rows

    # ---- агрегат интересов ------------------------------------------------------------------------------------

    def active_profiles(self, store: dict) -> list:
        return [p for p in store["members"].values() if p.get("active_in_group")]

    def interest_map(self, store: dict) -> dict:
        active = self.active_profiles(store)
        total = len(active)
        configured = sum(1 for p in active if p.get("onboarding_completed"))

        def tally(field: str, label_cat: str):
            counts, labels = {}, {}
            for p in active:
                for v in {norm(x): x for x in p.get(field, [])}.values():
                    k = norm(v)
                    counts[k] = counts.get(k, 0) + 1
                    labels.setdefault(k, self.value_label(label_cat, v))
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], labels[kv[0]]))
            return [(labels[k], n, interest_level(n, total)) for k, n in ranked]
        return {"active": total, "configured": configured, "sports": tally("sports", "sp"), "teams": tally("teams", "tm"),
                "athletes": tally("athletes", "at"), "competitions": tally("competitions", "cp"),
                "content": tally("content_preferences", "ct")}

    def interest_map_text(self, store: dict) -> str:
        m = self.interest_map(store)
        total = m["active"]
        lines = ["👥 Интересы «Своей Трибуны»", "", f"Участников: {total}", f"Настроили профиль: {m['configured']}"]
        if m["sports"]:
            lines += [""] + [f"{label} — {n} из {total}" for label, n, level in m["sports"][:8]]
        for title, key in (("❤️ Команды:", "teams"), ("⭐ Спортсмены:", "athletes"), ("🏆 Турниры:", "competitions")):
            if m[key]:
                lines += ["", title] + [f"{label} — {n}" for label, n, level in m[key][:8]]
        if m["content"]:
            lines += ["", "🔥 Контент:"] + [f"{strip_icon(label)} — {n}" for label, n, level in m["content"][:8]]
        if not total:
            lines += ["", "Пока в группе нет участников с профилем."]
        elif not m["configured"]:
            lines += ["", "Никто ещё не настроил интересы."]
        ed = self.editorial_summary(store)
        for lvl, title in (("high", "Высокий"), ("medium", "Средний")):
            parts = [f"{name}: {', '.join(ed[lvl][key])}" for key, name in (("sports", "виды спорта"), ("teams", "команды"),
                                                                              ("athletes", "спортсмены"), ("competitions", "турниры"),
                                                                              ("content", "контент")) if ed[lvl][key]]
            if parts:
                lines += ["", f"🎯 {title} интерес (для редакции): " + "; ".join(parts)]
        lines += ["", "Считаются только участники в группе. От 50% — высокий, 25–49% — средний, ниже — нишевый. Карта пересчитывается сама."]
        return "\n".join(lines)

    def editorial_summary(self, store: dict) -> dict:
        """Данные для будущей редакционной логики: что вызывает высокий / средний / нишевый интерес у активных участников."""
        m = self.interest_map(store)
        out = {lvl: {key: [label for label, n, level in m[key] if level == lvl]
                     for key in ("sports", "teams", "athletes", "competitions", "content")} for lvl in ("high", "medium", "niche")}
        out["active"] = m["active"]
        return out

    def high_interest(self, store: dict | None = None) -> dict:
        """high-interest sports / teams / athletes / competitions и предпочитаемые типы контента (≥50% активных участников)."""
        ed = self.editorial_summary(store or self.load_members())["high"]
        return {"sports": ed["sports"], "teams": ed["teams"], "athletes": ed["athletes"], "competitions": ed["competitions"],
                "preferred_content": ed["content"]}

    # ---- приглашение ------------------------------------------------------------------------------------------

    async def group_link(self) -> str | None:
        link, at = self._link_cache
        if link and at and (self.now() - at).total_seconds() < 3600:
            return link
        try:
            chat = await self.bot.get_chat_by_id(self.group_chat_id)
            link = getattr(chat, "link", None)
        except Exception as e:
            print(f"[TRIBUN] ссылка на группу недоступна: {type(e).__name__}: {mask_secrets(str(e))}")
            return None
        if link:
            self._link_cache = (link, self.now())
        return link or None

    async def invite_view(self):
        link = await self.group_link()
        body = INVITE_TEXT.format(link=link or INVITE_PLACEHOLDER)
        note = ("✅ Ссылка на группу подставлена автоматически." if link else
                f"ℹ️ Ссылку на группу MAX не отдаёт боту — замени {INVITE_PLACEHOLDER} на свою invite-ссылку.")
        return f"📨 Приглашение (это текст для пересылки, в группу не публикуется)\n{note}\n\n{body}", [[Btn("🛠 Управление", "adm"), Btn("🏠 Меню", "m")]]

    # ---- приоритеты -------------------------------------------------------------------------------------------

    def load_prios(self) -> dict:
        data = read_strict(self.prios_path, {"items": []})
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise TribunDataError(self.prios_path, "неверная структура")
        return data

    def active_priorities(self) -> list:
        try:
            items = self.load_prios()["items"]
        except TribunDataError:
            return []
        now = self.now()
        out = []
        for it in items:
            try:
                if datetime.datetime.fromisoformat(it["expires_at"]) > now:
                    out.append(it)
            except (KeyError, ValueError):
                continue
        return out

    # ---- публикации (черновик → предпросмотр → публикация) ------------------------------------------------------

    def load_pubs(self) -> dict:
        data = read_strict(self.pubs_path, {"items": []})
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise TribunDataError(self.pubs_path, "неверная структура")
        return data

    def save_pubs(self, data: dict) -> None:
        backup_file(self.pubs_path)
        atomic_write_json(self.pubs_path, data)

    def pub_view(self, pub: dict):
        if pub["status"] == "published":
            head = f"✅ ОПУБЛИКОВАНО в группе ({pub.get('published_at', '')[:16].replace('T', ' ')}, message_id {pub.get('message_id')})"
            rows = [[Btn("📋 К списку", "pub:list"), Btn("🛠 Управление", "adm")]]
        else:
            head = "👁 ПРЕДПРОСМОТР — в группу НЕ отправлено (черновик)"
            rows = [[Btn("📢 Опубликовать в группу", f"pub:go:{pub['id']}")],
                    [Btn("✏️ Изменить текст", f"pub:edit:{pub['id']}"), Btn("🗑 Удалить", f"pub:del:{pub['id']}")],
                    [Btn("📋 К списку", "pub:list")]]
        return f"{head}\n\n{pub['text']}", rows

    async def publish(self, pub_id: str) -> tuple:
        """Отправка в группу. published=true / message_id фиксируются ТОЛЬКО после успешного ответа MAX."""
        async with self.lock:
            data = self.load_pubs()
            pub = next((p for p in data["items"] if p["id"] == pub_id), None)
            if pub is None:
                return "missing", None
            if pub["status"] == "published" or pub_id in self.published_guard:
                return "already", pub
            if self.dry_run:
                print(f"[DRY RUN] публикация в группу:\n{pub['text']}")
                return "dry", pub
            try:
                resp = await self.bot.send_message(chat_id=self.group_chat_id, text=pub["text"])
                mid = resp.message.body.mid if resp is not None and getattr(resp, "message", None) and resp.message.body else None
            except Exception as e:
                print(f"[TRIBUN] публикация не отправлена: {type(e).__name__}: {mask_secrets(str(e))}")
                return "error", pub
            if not mid:
                print("[TRIBUN] MAX не вернул message_id — публикация не считается подтверждённой")
                return "unconfirmed", pub
            self.published_guard.add(pub_id)
            pub.update({"status": "published", "published": True, "message_id": mid, "chat_id": self.group_chat_id,
                        "published_at": self.stamp()})
            try:
                self.save_pubs(data)
            except Exception as e:
                print(f"[TRIBUN] публикация отправлена (message_id={mid}), но запись не сохранена: {e}")
            return "ok", pub

    # ---- пульт владельца ----------------------------------------------------------------------------------------------

    def admin_hub(self):
        text = f"🛠 Управление Трибуной\n{GROUP_NAME} · {BOT_NAME}"
        rows = [[Btn("👥 Интересы Трибуны", "adm:int"), Btn("👤 Участники", "adm:mem:0")],
                [Btn("📣 Публикации", "pub:list"), Btn("🗓 Автоматика", "adm:auto")],
                [Btn("⭐ Приоритеты", "pr:list"), Btn("📊 Состояние", "adm:state")],
                [Btn("🔔 Уведомления", "adm:ns"), Btn("📨 Приглашение", "inv")], [Btn("🏠 Меню", "m")]]
        return text, rows

    async def route_admin(self, ctx: Ctx, head: str, parts: list) -> None:
        # owner уже проверен в on_callback; проверяем ещё раз прямо здесь — защита не зависит от вызывающего кода
        if not self.is_owner(ctx.user_id):
            return
        back = [[Btn("🛠 Управление", "adm"), Btn("🏠 Меню", "m")]]
        if head == "adm":
            sub = parts[1] if len(parts) > 1 else ""
            if sub == "":
                await ctx.edit(*self.admin_hub())
            elif sub == "int":
                await ctx.edit(self.interest_map_text(self.load_members()), back)
            elif sub == "mem":
                await ctx.edit(*self.members_view(int(parts[2]) if len(parts) > 2 else 0))
            elif sub == "mc":
                await ctx.edit(*self.member_card(int(parts[2])))
            elif sub == "ns":
                await ctx.edit(*self.notify_settings_view())
            elif sub == "nt":
                try:
                    self.set_owner_setting(parts[2], not self.owner_settings().get(parts[2], False))
                except ValueError:
                    pass
                await ctx.edit(*self.notify_settings_view())
            elif sub == "auto":
                await ctx.edit(self.automation_text(), back)
            elif sub == "state":
                await ctx.edit(self.state_text(), back)
        elif head == "pub":
            await self.route_pub(ctx, parts)
        elif head == "pr":
            await self.route_prio(ctx, parts)

    def members_view(self, page: int):
        store = self.load_members()
        members = sorted(store["members"].values(), key=lambda p: (not p.get("active_in_group"), norm(p["display_name"])))
        active = [p for p in members if p.get("active_in_group")]
        configured = sum(1 for p in active if p.get("onboarding_completed"))
        size = 8
        pages = max(1, (len(members) + size - 1) // size)
        page = max(0, min(page, pages - 1))
        lines = ["👤 Участники", "", f"Участников группы: {len(active)}", f"✅ Настроили интересы: {configured}",
                 f"⏳ Не настроили: {len(active) - configured}", ""]
        rows = []
        for i, p in enumerate(members[page * size:(page + 1) * size], page * size + 1):
            state = ("🚪 вышел" if not p.get("active_in_group") else "✅ профиль" if p.get("onboarding_completed") else "⏳ не настроил")
            lines.append(f"{i}. {p['display_name']} — {state}")
            rows.append([Btn(f"{i}. {p['display_name']}", f"adm:mc:{p['user_id']}")])
        if not members:
            lines.append("Пока никого нет.")
        if pages > 1:
            lines.append(f"\nСтр. {page + 1} из {pages}")
        nav = []
        if page > 0:
            nav.append(Btn("◀️", f"adm:mem:{page - 1}"))
        if page < pages - 1:
            nav.append(Btn("▶️", f"adm:mem:{page + 1}"))
        rows += ([nav] if nav else []) + [[Btn("🛠 Управление", "adm"), Btn("🏠 Меню", "m")]]
        return "\n".join(lines), rows

    def member_card(self, uid: int):
        """Карточка участника — ТОЛЬКО просмотр для владельца: редактировать чужие предпочтения нельзя."""
        profile = self.load_members()["members"].get(str(uid))
        if profile is None:
            return "Участник не найден.", [[Btn("👤 Участники", "adm:mem:0")]]

        def when(value):
            return f"{value[8:10]}.{value[5:7]}.{value[0:4]} {value[11:16]}" if value else "—"

        def names(values, cat=None):
            return ", ".join(values) if values else "—"
        sports = ", ".join(SPORT_BY_KEY[v][1] if v in SPORT_BY_KEY else v for v in profile["sports"]) or "—"
        sports = sports[:1].upper() + sports[1:].lower() if all(v in SPORT_BY_KEY for v in profile["sports"]) and sports != "—" else sports
        content = ", ".join(CONTENT_NAME.get(v, v).lower() for v in profile["content_preferences"]) or "—"
        source = {"event": "", "sync": " (обнаружен при подключении Трибуна)", "dialog": " (определён по личному диалогу)"}.get(profile.get("joined_source"), "")
        lines = [f"👤 {profile['display_name']}", "", "Статус:", "✅ В группе" if profile.get("active_in_group") else "🚪 Вышел из группы", "",
                 "Профиль:", "✅ Настроен" if profile.get("onboarding_completed") else "⏳ Не настроен", "",
                 f"🏒 Виды спорта:\n{sports}", "", f"❤️ Команды / спортсмены:\n{names(profile['teams'] + profile['athletes'])}", "",
                 f"🏆 Турниры:\n{names(profile['competitions'])}", "", f"🔥 Контент:\n{content}", "",
                 f"🔔 Уведомления:\n{NOTIFY_NAME.get(profile.get('notification_level'), '—')}", "",
                 f"Дата вступления:\n{when(profile.get('joined_at'))}{source}", "",
                 f"Последнее изменение профиля:\n{when(profile.get('interests_updated_at'))}", "", "Только просмотр: личные предпочтения участника не редактируются."]
        return "\n".join(lines), [[Btn("👤 К списку", "adm:mem:0"), Btn("🛠 Управление", "adm")]]

    def notify_settings_view(self):
        settings = self.owner_settings()
        lines = ["🔔 Уведомления владельцу", "", "Что присылать вам в личку:"]
        rows = []
        for key, (default, changeable, title) in OWNER_EVENTS.items():
            on = settings[key]
            lines.append(f"{'✅' if on else '❌'} {title}" + ("" if changeable else " (не присылается)"))
            if changeable:
                rows.append([Btn(f"{'Выключить' if on else 'Включить'}: {title}", f"adm:nt:{key}")])
        rows.append([Btn("🛠 Управление", "adm"), Btn("🏠 Меню", "m")])
        return "\n".join(lines), rows

    def next_run_text(self, dt: datetime.datetime | None) -> str:
        return dt.astimezone(YEKB_TZ).strftime("%d.%m.%Y %H:%M") if dt else "—"

    def automation_text(self) -> str:
        jobs = self.automation()
        lines = ["🗓 Автоматика (часовой пояс Asia/Yekaterinburg)", ""]
        for j in jobs:
            lines.append(f"{'🟢' if j.get('enabled', True) else '⏸'} {j['name']}")
            lines.append(f"   расписание: {j['schedule']}")
            lines.append(f"   следующий запуск: {j['next']}")
            lines.append(f"   последний успешный: {j.get('last_ok') or 'нет данных'}")
            lines.append(f"   последняя ошибка: {j.get('last_error') or 'нет'}")
        if not jobs:
            lines.append("Задач нет.")
        lines += ["", "Новых массовых автоматических публикаций Трибун не создаёт: всё, что в группе, — только существующие задачи и ваши подтверждённые публикации."]
        return "\n".join(lines)

    def state_text(self) -> str:
        st = self.status()
        try:
            m = self.interest_map(self.load_members())
            members = f"Участников в группе: {m['active']}, настроили интересы: {m['configured']}"
        except TribunDataError as e:
            members = f"⚠️ Профили не читаются: {e.reason}"
        lines = ["📊 Состояние", "", f"Трибун: {'RUNNING' if st.get('running', True) else 'ОСТАНОВЛЕН'}", f"Версия: {self.version}",
                 f"Планировщик: последний цикл {st.get('last_tick') or 'нет данных'}",
                 f"Последний успешный цикл: {st.get('last_ok') or 'нет данных'}",
                 f"Последняя ошибка: {st.get('last_error') or 'нет'}", f"Тихий режим (DRY_RUN): {'ДА' if self.dry_run else 'нет'}", members]
        sources = st.get("sources") or []
        if sources:
            lines += ["", "Источники и внешние сервисы:"] + [f"• {s}" for s in sources]
        return "\n".join(lines)

    # ---- публикации: маршруты --------------------------------------------------------------------------------------------

    async def route_pub(self, ctx: Ctx, parts: list) -> None:
        sub = parts[1] if len(parts) > 1 else "list"
        back = [[Btn("🛠 Управление", "adm"), Btn("🏠 Меню", "m")]]
        if sub == "list":
            data = self.load_pubs()
            items = sorted(data["items"], key=lambda p: p["created_at"], reverse=True)[:8]
            lines = ["📣 Публикации", "", "Создать → предпросмотр → явное «📢 Опубликовать». Предпросмотр и черновик — НЕ публикация."]
            rows = [[Btn("✍️ Новая публикация", "pub:new")]]
            for p in items:
                icon = "✅" if p["status"] == "published" else "📝"
                snippet = re.sub(r"\s+", " ", p["text"])[:28]
                rows.append([Btn(f"{icon} {snippet}", f"pub:view:{p['id']}")])
            rows.append(back[0])
            if not items:
                lines.append("\nПока нет ни черновиков, ни публикаций.")
            await ctx.edit("\n".join(lines), rows)
        elif sub == "new":
            self.awaiting[ctx.user_id] = {"kind": "pub_text"}
            await ctx.edit("✍️ Пришли текст публикации одним сообщением. Я покажу предпросмотр — в группу ничего не уйдёт, пока не нажмёшь «📢 Опубликовать».",
                           [[Btn("⬅️ Отмена", "pub:list")]])
        elif sub == "view":
            pub = self.find_pub(parts[2])
            await (ctx.edit(*self.pub_view(pub)) if pub else ctx.edit("Публикация не найдена.", back))
        elif sub == "edit":
            pub = self.find_pub(parts[2])
            if pub and pub["status"] == "draft":
                self.awaiting[ctx.user_id] = {"kind": "pub_edit", "id": pub["id"]}
                await ctx.edit("✏️ Пришли новый текст — заменю черновик.", [[Btn("⬅️ Отмена", f"pub:view:{pub['id']}")]])
            else:
                await ctx.edit("Опубликованное изменить нельзя.", back)
        elif sub == "del":
            async with self.lock:
                data = self.load_pubs()
                data["items"] = [p for p in data["items"] if not (p["id"] == parts[2] and p["status"] == "draft")]
                self.save_pubs(data)
            await ctx.edit("🗑 Черновик удалён (опубликованное не удаляется).", [[Btn("📣 Публикации", "pub:list")]])
        elif sub == "go":
            status, pub = await self.publish(parts[2])
            if status == "ok":
                await ctx.edit(*self.pub_view(pub))
            elif status == "already":
                await ctx.edit("Уже опубликовано — повторно не отправляю.\n\n" + self.pub_view(pub)[0], self.pub_view(pub)[1])
            elif status == "dry":
                await ctx.edit("🧪 Тихий режим (DRY_RUN): в группу ничего не отправлено.", back)
            elif status == "missing":
                await ctx.edit("Публикация не найдена.", back)
            else:
                text = ("⚠️ Не удалось отправить в группу — публикацией это не считается, черновик сохранён. Можно повторить."
                        if status == "error" else
                        "⚠️ MAX не подтвердил отправку (нет message_id) — публикацией не считаю, черновик сохранён.")
                await ctx.edit(text, self.pub_view(pub)[1])

    def find_pub(self, pub_id: str):
        return next((p for p in self.load_pubs()["items"] if p["id"] == pub_id), None)

    async def handle_admin_text(self, ctx: Ctx, state: dict) -> None:
        kind = state["kind"]
        text = (ctx.text or "").strip()
        if kind in ("pub_text", "pub_edit"):
            self.awaiting.pop(ctx.user_id, None)
            if not text or text.startswith("/"):
                await ctx.reply("Пусто. Пришли текст публикации.", [[Btn("⬅️ Отмена", "pub:list")]])
                return
            if len(text) > 3500:
                await ctx.reply("Слишком длинно (до 3500 символов).", [[Btn("⬅️ Отмена", "pub:list")]])
                return
            async with self.lock:
                data = self.load_pubs()
                if kind == "pub_edit":
                    pub = next((p for p in data["items"] if p["id"] == state["id"] and p["status"] == "draft"), None)
                    if pub is None:
                        await ctx.reply("Черновик не найден.", [[Btn("📣 Публикации", "pub:list")]])
                        return
                    pub["text"], pub["updated_at"] = text, self.stamp()
                else:
                    pub = {"id": uuid.uuid4().hex[:8], "text": text, "status": "draft", "published": False,
                           "created_at": self.stamp(), "updated_at": self.stamp()}
                    data["items"].append(pub)
                self.save_pubs(data)
            await ctx.reply(*self.pub_view(pub))
        elif kind == "prio_value":
            await self.prio_value(ctx, state, text)

    # ---- приоритеты: маршруты -------------------------------------------------------------------------------------------------

    PRIO_TYPES = {"team": "Команда", "sport": "Вид спорта", "competition": "Турнир", "event": "Событие"}

    async def route_prio(self, ctx: Ctx, parts: list) -> None:
        sub = parts[1] if len(parts) > 1 else "list"
        back = [Btn("🛠 Управление", "adm"), Btn("🏠 Меню", "m")]
        if sub == "list":
            items = self.active_priorities()
            lines = ["⭐ Приоритеты", "", "Временный редакционный сигнал: поднимает команду/вид спорта/турнир/событие в «Что сегодня». "
                     "Личные интересы участников не меняет.", ""]
            rows = []
            for i, it in enumerate(items):
                until = datetime.datetime.fromisoformat(it["expires_at"]).astimezone(YEKB_TZ)
                lines.append(f"{i + 1}. {self.PRIO_TYPES[it['type']]}: {it['value']} — до {until:%d.%m %H:%M}")
                rows.append([Btn(f"🗑 Убрать {i + 1}", f"pr:del:{it['id']}")])
            if not items:
                lines.append("Сейчас приоритетов нет.")
            rows += [[Btn("➕ Добавить", "pr:add")], back]
            await ctx.edit("\n".join(lines), rows)
        elif sub == "add":
            rows = [[Btn(name, f"pr:type:{t}")] for t, name in self.PRIO_TYPES.items()] + [[Btn("⬅️ Отмена", "pr:list")]]
            await ctx.edit("Что повысить в приоритете?", rows)
        elif sub == "type":
            self.awaiting[ctx.user_id] = {"kind": "prio_value", "type": parts[2]}
            hint = {"team": "название команды", "sport": "вид спорта (hockey, football, tennis, basketball, motorsport, martial, winter)",
                    "competition": "название турнира", "event": "слово из названия события (команда/соперник/турнир)"}[parts[2]]
            await ctx.edit(f"✍️ Напиши {hint}:", [[Btn("⬅️ Отмена", "pr:list")]])
        elif sub == "dur":                                      # pr:dur:<id>:<days>
            await self.prio_set_duration(ctx, parts[2], int(parts[3]))
        elif sub == "del":
            async with self.lock:
                data = self.load_prios()
                data["items"] = [it for it in data["items"] if it["id"] != parts[2]]
                backup_file(self.prios_path)
                atomic_write_json(self.prios_path, data)
            await self.route_prio(ctx, ["pr", "list"])

    async def prio_value(self, ctx: Ctx, state: dict, text: str) -> None:
        self.awaiting.pop(ctx.user_id, None)
        value = re.sub(r"\s+", " ", text)[:MAX_CUSTOM_LEN]
        if not value or value.startswith("/"):
            await ctx.reply("Пусто. Напиши значение.", [[Btn("⬅️ Отмена", "pr:list")]])
            return
        pid = uuid.uuid4().hex[:8]
        async with self.lock:
            data = self.load_prios()
            data["items"].append({"id": pid, "type": state["type"], "value": value, "created_at": self.stamp(),
                                  "expires_at": (self.now() - datetime.timedelta(seconds=1)).isoformat(), "pending": True})
            backup_file(self.prios_path)
            atomic_write_json(self.prios_path, data)
        rows = [[Btn("1 день", f"pr:dur:{pid}:1"), Btn("7 дней", f"pr:dur:{pid}:7"), Btn("30 дней", f"pr:dur:{pid}:30")], [Btn("⬅️ Отмена", f"pr:del:{pid}")]]
        await ctx.reply(f"На сколько повысить «{value}»?", rows)

    async def prio_set_duration(self, ctx: Ctx, pid: str, days: int) -> None:
        if days not in (1, 7, 30):
            return
        async with self.lock:
            data = self.load_prios()
            it = next((x for x in data["items"] if x["id"] == pid), None)
            if it is None:
                await ctx.edit("Не найдено.", [[Btn("⭐ Приоритеты", "pr:list")]])
                return
            it["expires_at"] = (self.now() + datetime.timedelta(days=days)).isoformat()
            it.pop("pending", None)
            backup_file(self.prios_path)
            atomic_write_json(self.prios_path, data)
        await self.route_prio(ctx, ["pr", "list"])

    # ---- вход из MAX -------------------------------------------------------------------------------------------------------------

    def register(self, dp) -> None:
        from maxapi.enums.chat_type import ChatType
        from maxapi.types import BotStarted, MessageCallback, MessageCreated, UserAdded, UserRemoved
        tribun = self

        @dp.bot_started()
        async def _bot_started(event: BotStarted):
            async def reply(t, rows=None):
                await tribun.bot.send_message(chat_id=event.chat_id, text=t, attachments=build_keyboard(rows))
            await tribun.on_bot_started(event.user, event.chat_id, event.payload, reply)

        @dp.user_added()
        async def _user_added(event: UserAdded):
            await tribun.on_user_added(event.chat_id, event.user, event.inviter_id)

        @dp.user_removed()
        async def _user_removed(event: UserRemoved):
            await tribun.on_user_removed(event.chat_id, event.user)

        @dp.message_created()
        async def _message(event: MessageCreated):
            msg = event.message
            sender = msg.sender
            if sender is None or getattr(sender, "is_bot", False):
                return
            private = msg.recipient.chat_type == ChatType.DIALOG

            async def reply(t, rows=None):
                await msg.answer(t, attachments=build_keyboard(rows) or None)
            ctx = Ctx(user_id=sender.user_id, chat_id=msg.recipient.chat_id, is_private=private,
                      display_name=display_name_of(sender), text=(msg.body.text if msg.body else "") or "", reply=reply)
            await tribun.on_message(ctx)

        @dp.message_callback()
        async def _callback(event: MessageCallback):
            msg = event.message
            private = msg is not None and msg.recipient.chat_type == ChatType.DIALOG
            answered = {"v": False}

            async def reply(t, rows=None):
                await event.send(t, attachments=build_keyboard(rows) or None)

            async def edit(t, rows=None):
                if answered["v"]:
                    await event.send(t, attachments=build_keyboard(rows) or None)
                    return
                answered["v"] = True
                await event.edit(text=t, attachments=build_keyboard(rows))

            ctx = Ctx(user_id=event.callback.user.user_id, chat_id=msg.recipient.chat_id if msg is not None else 0,
                      is_private=private, display_name=display_name_of(event.callback.user), payload=event.callback.payload or "",
                      reply=reply, edit=edit, ack=None)
            try:
                await tribun.on_callback(ctx)
            finally:
                if not answered["v"]:
                    answered["v"] = True
                    try:
                        await event.ack()
                    except Exception as e:
                        print(f"[TRIBUN] ack: {e}")


def build_keyboard(rows):
    """Строки Btn/кортежей → вложение клавиатуры MAX. Кнопка с url — ссылка, иначе callback."""
    if not rows:
        return []
    from maxapi.types.attachments.buttons import CallbackButton, LinkButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    for row in rows:
        buttons = []
        for item in row:
            b = as_btn(item)
            buttons.append(LinkButton(text=b.label, url=b.url) if b.url else CallbackButton(text=b.label, payload=b.payload))
        if buttons:
            builder.row(*buttons)
    return [builder.as_markup()]
