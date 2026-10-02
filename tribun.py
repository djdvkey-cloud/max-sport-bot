"""«Своя Трибуна» / «Трибун» — клубный слой SPORTBOT/MAX: приветствие новых участников, настройка интересов (три раздела),
карта интересов компании, запросы «➕ Другое», реестр источников, учёт API и пульт владельца.

Только MAX (maxapi): события UserAdded / UserRemoved / BotStarted, личные сообщения и callback-кнопки.
Данные — JSON в /data (атомарная запись, копии). Ничего не публикуется в группу, кроме приветствия новому участнику
и явно подтверждённых владельцем публикаций (черновик → предпросмотр → «📢 Опубликовать» → успешный ответ MAX).

Личка обычного участника нужна ТОЛЬКО для настройки интересов: персональных спортивных сообщений нет, вся спортивная
повестка — в группе. Интересы наружу не публикуются: только агрегаты и только владельцу в личке.
Интересы НЕ влияют на публикации шести клубов SPORTBOT (афиша, утренние анонсы, результаты живут в sport_bot.py)."""
import asyncio
import datetime
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import costs
import sources as src
import tribun_catalog as C
from tribun_io import (TribunDataError, atomic_write_json, backup_file, mask_secrets, read_strict,   # noqa: F401  (реэкспорт для sport_bot)
                       utc_now)

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
# ============================================================
# Разделы интересов и служебные константы
# ============================================================

CAT_TITLE = {"sp": "🏅 Виды спорта", "cp": "🏆 Чемпионаты", "cl": "❤️ Клубы"}
FIELD = {"sp": "sports", "cp": "championships", "cl": "clubs"}          # код раздела → поле профиля
NEXT_SECTION = {"sp": "cp", "cp": "cl"}
OTHER_PROMPT = {"sp": "✍️ Напиши, какой ещё вид спорта тебе интересен (можно несколько — через запятую):",
                "cp": "✍️ Напиши, какой ещё чемпионат тебе интересен (можно несколько — через запятую):",
                "cl": "✍️ Напиши, какой ещё клуб тебе интересен (можно несколько — через запятую):"}
SAVED_TEXT = "✅ Интересы сохранены"
CAT_WORD = {C.CAT_SPORT: "спорт", C.CAT_CHAMP: "чемпионат", C.CAT_CLUB: "клуб"}
ADMIN_PREFIXES = ("adm", "pub", "pr", "inv")
MAX_CUSTOM_LEN = 60

HIGH_SHARE, MEDIUM_SHARE = 0.5, 0.25


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


def now_default() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def new_profile(user_id: int, display_name: str, stamp: str) -> dict:
    return {"user_id": user_id, "display_name": display_name, "schema": 2, "sports": [], "championships": [], "clubs": [],
            "onboarding_completed": False, "welcomed": False, "active_in_group": False, "created_at": stamp, "updated_at": stamp,
            # служебное для пульта владельца
            "ever_in_group": False, "joined_at": None, "joined_source": None, "left_at": None, "returned_at": None,
            "join_notified": False, "completion_notified": False, "interests_updated_at": None}


def normalize_profile(profile: dict) -> dict:
    """Профиль старого формата (release 1) читается без разрушительной миграции: недостающие поля новой модели добавляются, ничего
    не удаляется (старые teams/athletes/competitions/content_preferences/notification_level остаются в файле нетронутыми и просто не
    используются). Чемпионаты и клубы берутся из старых полей, только если точно совпали с каталогом."""
    if "championships" not in profile:
        profile["championships"] = [k for k in (C.match_catalog(C.CAT_CHAMP, v) for v in profile.get("competitions", [])) if k]
    if "clubs" not in profile:
        profile["clubs"] = [k for k in (C.match_catalog(C.CAT_CLUB, v) for v in profile.get("teams", [])) if k]
    profile.setdefault("sports", [])
    profile.setdefault("schema", 2)
    return profile


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
                 automation: Callable[[], list] | None = None, status: Callable[[], dict] | None = None,
                 bot_username: str | None = None, registry: src.SourceRegistry | None = None,
                 tracker: costs.UsageTracker | None = None):
        self.bot = bot
        self.data_dir = data_dir
        self.group_chat_id = group_chat_id
        self.owner_id = owner_id
        self.version = version
        self.dry_run = dry_run
        self.now = now
        self.automation = automation or (lambda: [])
        self.status = status or (lambda: {})
        self.bot_username = bot_username
        self.registry = registry
        self.tracker = tracker
        self.awaiting: dict = {}                      # ожидание текстового ввода (в памяти): user_id → состояние
        self.lock = asyncio.Lock()
        self.published_guard: set = set()             # id публикаций, уже отправленных в этом процессе
        self.members_path = os.path.join(data_dir, "tribun_members.json")
        self.requests_path = os.path.join(data_dir, "tribun_requests.json")
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
        normalize_profile(profile)                                # профиль старого формата читается без потери данных
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
        """Карточка владельцу: 🏅 Спорт / 🏆 Чемпионаты / ❤️ Клубы / ➕ Запросы."""
        profile = normalize_profile(dict(profile))
        mine = self.user_requests(self.load_requests(), profile["user_id"])
        eff = self.effective(profile)

        def block(title, code):
            names = [self.label(code, k) for k in eff[code]] + [f"➕ {r['raw_text']}" for r in mine if r["category"] == C.CODE_TO_CAT[code]]
            return f"{title}\n" + ("\n".join(names) if names else "—")
        asked = "\n".join(f"{r['raw_text']} ({CAT_WORD[r['category']]})"
                          for r in mine) or "—"
        return (f"✅ {profile['display_name']} настроил интересы\n\n" + block("🏅 Спорт:", "sp") + "\n\n" + block("🏆 Чемпионаты:", "cp")
                + "\n\n" + block("❤️ Клубы:", "cl") + f"\n\n➕ Запросы:\n{asked}")

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
            text, rows = self.main_menu(profile, self.is_owner(user.user_id))
        except TribunDataError as e:
            print(f"[TRIBUN] {e}")
            return
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
            if self.is_owner(ctx.user_id) and (ctx.text or "").strip().casefold().startswith("/цена"):
                await ctx.reply(self.price_command(ctx.text), None)
                return
            await ctx.reply(*self.main_menu(profile, self.is_owner(ctx.user_id)))
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
        if head not in ("o", "pr"):
            self.awaiting.pop(ctx.user_id, None)
        owner = self.is_owner(ctx.user_id)
        profile = await self.ensure_profile(ctx.user_id, ctx.display_name)
        if head in ("m", ""):
            await ctx.edit(*self.main_menu(profile, owner))
        elif head == "c":                                     # c:<раздел>
            await ctx.edit(*self.category_screen(profile, self.section(parts[1])))
        elif head == "t":                                     # t:<раздел>:<ключ каталога>
            await self.toggle(ctx, self.section(parts[1]), parts[2])
        elif head == "rt":                                    # rt:<раздел>:<id запроса>
            await self.toggle_request(ctx, self.section(parts[1]), parts[2])
        elif head == "o":                                     # o:<раздел> — «➕ Другое»
            code = self.section(parts[1])
            self.awaiting[ctx.user_id] = {"kind": "other", "code": code}
            await ctx.edit(OTHER_PROMPT[code], [[Btn("⬅️ Отмена", f"c:{code}")]])
        elif head == "sv":
            await self.save_interests(ctx)
        elif head == "inv":
            await ctx.edit(*await self.invite_view())
        elif head in ADMIN_PREFIXES:
            await self.route_admin(ctx, head, parts)
        else:                                                 # старые кнопки прежнего меню и всё неизвестное → главное меню
            await ctx.edit(*self.main_menu(profile, owner))

    @staticmethod
    def section(code: str) -> str:
        if code not in CAT_TITLE:
            raise ValueError("неизвестный раздел")
        return code

    # ---- запросы «➕ Другое» -----------------------------------------------------------------------

    def load_requests(self) -> dict:
        data = read_strict(self.requests_path, {"items": []})
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise TribunDataError(self.requests_path, "неверная структура")
        return data

    def save_requests(self, data: dict) -> None:
        backup_file(self.requests_path)
        atomic_write_json(self.requests_path, data)

    def add_request(self, data: dict, user_id: int, category: str, raw: str) -> tuple:
        """Запрос участника: (category, raw_text, normalized_text, user_id, created_at, active). Повтор того же — не дубль, а возврат в active."""
        normalized = C.normalize_request(raw)
        for item in data["items"]:
            if item["user_id"] == int(user_id) and item["category"] == category and item["normalized_text"] == normalized:
                if not item["active"]:
                    item["active"], item["updated_at"] = True, self.stamp()
                    return item, True
                return item, False
        item = {"id": uuid.uuid4().hex[:8], "category": category, "raw_text": raw, "normalized_text": normalized,
                "user_id": int(user_id), "created_at": self.stamp(), "active": True}
        data["items"].append(item)
        return item, True

    def user_requests(self, data: dict, user_id: int, category: str | None = None) -> list:
        return [i for i in data["items"] if i["user_id"] == int(user_id) and i["active"] and (category is None or i["category"] == category)]

    # ---- выбор и зависимости ------------------------------------------------------------------------------

    def stored_sports(self, profile: dict) -> list:
        return [k for k, _, _, _ in C.SPORTS if k in profile.get("sports", [])]

    def options(self, code: str, profile: dict) -> list:
        """[(ключ, подпись)] — варианты раздела. Чемпионаты и клубы — только для выбранных видов спорта."""
        sports = self.stored_sports(profile)
        if code == "sp":
            return [(k, f"{icon} {name}") for k, icon, name, _ in C.SPORTS]
        if code == "cp":
            return [(c[0], c[1]) for c in C.competitions_for(sports)]
        return [(c[0], c[1]) for c in C.clubs_for(sports)]

    def effective(self, profile: dict) -> dict:
        """Что реально учитывается: убранный вид спорта скрывает свои чемпионаты/клубы (данные при этом не теряются)."""
        sports = self.stored_sports(profile)
        return {"sp": sports,
                "cp": [c[0] for c in C.competitions_for(sports) if c[0] in profile.get("championships", [])],
                "cl": [c[0] for c in C.clubs_for(sports) if c[0] in profile.get("clubs", [])]}

    def is_complete(self, profile: dict, requests: list) -> bool:
        """Профиль настроен = в каждом из трёх разделов есть выбор: вариант каталога или активный запрос «Другое»."""
        eff = self.effective(profile)
        wanted = {r["category"] for r in requests if r["active"]}
        return all(eff[code] or C.CODE_TO_CAT[code] in wanted for code in ("sp", "cp", "cl"))

    def label(self, code: str, key: str) -> str:
        if code == "sp":
            icon, name = C.SPORT_BY_KEY[key]
            return f"{icon} {name}"
        return C.COMP_BY_KEY[key][1] if code == "cp" else C.CLUB_BY_KEY[key][1]

    def chosen_text(self, profile: dict, code: str, requests: list) -> str:
        names = [self.label(code, k) for k in self.effective(profile)[code]]
        names += [f"➕ {r['raw_text']}" for r in requests if r["category"] == C.CODE_TO_CAT[code] and r["active"]]
        return ", ".join(names) if names else "пока ничего"

    def mark_changed(self, profile: dict, requests: list) -> None:
        profile["updated_at"] = profile["interests_updated_at"] = self.stamp()
        profile["onboarding_completed"] = self.is_complete(profile, requests)

    # ---- экраны личного меню -------------------------------------------------------------------------------

    def main_menu(self, profile: dict, owner: bool):
        requests = self.user_requests(self.load_requests(), profile["user_id"])
        lines = [f"{GROUP_NAME} · {BOT_NAME}", SLOGAN, "", f"Привет, {profile['display_name']}!",
                 "Отметь, что тебе интересно, — это нужно, чтобы Трибун знал вкусы компании. "
                 "Матчи, афиши и результаты — в группе."]
        if self.is_complete(profile, requests):
            lines += ["", f"🏅 Виды спорта: {self.chosen_text(profile, 'sp', requests)}",
                      f"🏆 Чемпионаты: {self.chosen_text(profile, 'cp', requests)}",
                      f"❤️ Клубы: {self.chosen_text(profile, 'cl', requests)}"]
        else:
            lines += ["", "👉 Выбери хотя бы по одному пункту в каждом из трёх разделов."]
        rows = [[Btn(CAT_TITLE["sp"], "c:sp")], [Btn(CAT_TITLE["cp"], "c:cp")], [Btn(CAT_TITLE["cl"], "c:cl")]]
        if owner:
            rows.append([Btn("🛠 Управление Трибуной", "adm")])
        return "\n".join(lines), rows

    def category_screen(self, profile: dict, code: str, note: str = ""):
        requests = self.user_requests(self.load_requests(), profile["user_id"], C.CODE_TO_CAT[code])
        chosen = set(self.effective(profile)[code])
        options = self.options(code, profile)
        text = f"{CAT_TITLE[code]}\n\nСейчас: {self.chosen_text(profile, code, self.user_requests(self.load_requests(), profile['user_id']))}\n\n"
        if code != "sp" and not self.stored_sports(profile):
            text += "Чемпионаты и клубы подбираются под выбранные виды спорта — сначала отметь вид спорта."
        else:
            text += "Нажимай, чтобы выбрать или убрать."
        buttons = [Btn(("✅ " if key in chosen else "") + label, f"t:{code}:{key}") for key, label in options]
        buttons += [Btn(f"✅ ➕ {r['raw_text']}", f"rt:{code}:{r['id']}") for r in requests]
        rows = grid(buttons)
        if code != "sp" and not self.stored_sports(profile):
            rows.append([Btn(CAT_TITLE["sp"], "c:sp")])
        rows.append([Btn("➕ Другое", f"o:{code}")])
        nav = [Btn("✅ Сохранить", "sv")]
        if code in NEXT_SECTION:
            nav.append(Btn("Дальше ▶️", f"c:{NEXT_SECTION[code]}"))
        rows += [nav, [Btn("⬅️ Назад", "m")]]
        return (f"{note}\n\n{text}" if note else text), rows

    # ---- изменения профиля ------------------------------------------------------------------------------------------

    async def toggle(self, ctx: Ctx, code: str, key: str) -> None:
        unavailable = False
        async with self.lock:
            store = self.load_members()
            requests = self.load_requests()
            profile = self.touch(store, ctx.user_id)
            if key not in {k for k, _ in self.options(code, profile)}:
                unavailable = True
            else:
                field = FIELD[code]
                profile[field] = [v for v in profile[field] if v != key] if key in profile[field] else profile[field] + [key]
                self.mark_changed(profile, self.user_requests(requests, ctx.user_id))
                self.save_members(store)
        if unavailable:
            await ctx.edit("Этот вариант сейчас недоступен. Открой раздел заново.", [[Btn(CAT_TITLE[code], f"c:{code}")]])
            return
        await ctx.edit(*self.category_screen(profile, code))

    async def toggle_request(self, ctx: Ctx, code: str, rid: str) -> None:
        async with self.lock:
            store = self.load_members()
            requests = self.load_requests()
            profile = self.touch(store, ctx.user_id)
            item = next((i for i in requests["items"] if i["id"] == rid and i["user_id"] == ctx.user_id
                         and i["category"] == C.CODE_TO_CAT[code]), None)
            if item is not None:                                   # чужой запрос по id не тронуть
                item["active"], item["updated_at"] = not item["active"], self.stamp()
                self.save_requests(requests)
                self.mark_changed(profile, self.user_requests(requests, ctx.user_id))
                self.save_members(store)
        await ctx.edit(*self.category_screen(profile, code))

    async def save_interests(self, ctx: Ctx) -> None:
        """«✅ Сохранить»: данные уже записаны при каждом выборе; здесь подтверждение и (один раз, при первом полном профиле) карточка владельцу."""
        async with self.lock:
            store = self.load_members()
            profile = self.touch(store, ctx.user_id)
            requests = self.user_requests(self.load_requests(), ctx.user_id)
            profile["onboarding_completed"] = self.is_complete(profile, requests)
            first = profile["onboarding_completed"] and not profile.get("completion_notified")
            self.save_members(store)
        if first:
            await self.owner_completion_notice(ctx.user_id)
        text, rows = self.main_menu(profile, self.is_owner(ctx.user_id))
        await ctx.edit(f"{SAVED_TEXT}\n\n{text}", rows)

    async def handle_awaited(self, ctx: Ctx, state: dict) -> None:
        kind = state["kind"]
        if kind == "other":
            self.awaiting.pop(ctx.user_id, None)
            code = state["code"]
            category = C.CODE_TO_CAT[code]
            items = parse_custom(ctx.text)
            if not items:
                self.awaiting[ctx.user_id] = state
                await ctx.reply("Не понял. " + OTHER_PROMPT[code], [[Btn("⬅️ Отмена", f"c:{code}")]])
                return
            notes = []
            async with self.lock:
                store = self.load_members()
                requests = self.load_requests()
                profile = self.touch(store, ctx.user_id)
                for raw in items:
                    key = C.match_catalog(category, raw)
                    if key:                                        # это уже есть в справочнике — обычный выбор, а не запрос
                        if key not in profile[FIELD[code]]:
                            profile[FIELD[code]].append(key)
                        notes.append(f"{self.label(code, key)} — есть в списке, отметил")
                    else:
                        item, new = self.add_request(requests, ctx.user_id, category, raw)
                        notes.append(f"{item['raw_text']} — запрос отправлен" if new else f"{item['raw_text']} — уже в запросах")
                self.save_requests(requests)
                self.mark_changed(profile, self.user_requests(requests, ctx.user_id))
                self.save_members(store)
            await ctx.reply(*self.category_screen(profile, code, "✅ " + "\n✅ ".join(notes)))
            return
        if not self.is_owner(ctx.user_id):                      # дальше — только владелец
            self.awaiting.pop(ctx.user_id, None)
            return
        await self.handle_admin_text(ctx, state)

    # ---- агрегат интересов ------------------------------------------------------------------------------------

    def active_profiles(self, store: dict) -> list:
        return [p for p in store["members"].values() if p.get("active_in_group")]

    def interest_map(self, store: dict, requests: dict | None = None) -> dict:
        """Агрегат по участникам, ВСЁ ещё состоящим в группе. Убранный вид спорта скрывает свои чемпионаты/клубы (effective)."""
        requests = requests or self.load_requests()
        active = self.active_profiles(store)
        total = len(active)
        by_user = {p["user_id"]: self.user_requests(requests, p["user_id"]) for p in active}
        configured = sum(1 for p in active if self.is_complete(normalize_profile(p), by_user[p["user_id"]]))
        tallies = {"sp": {}, "cp": {}, "cl": {}}
        for p in active:
            for code, keys in self.effective(normalize_profile(p)).items():
                for key in keys:
                    tallies[code][key] = tallies[code].get(key, 0) + 1

        def ranked(code: str):
            rows = [(self.label(code, k), n, interest_level(n, total)) for k, n in tallies[code].items()]
            return sorted(rows, key=lambda r: (-r[1], r[0]))
        groups = self.request_groups(store, requests)
        return {"active": total, "configured": configured, "sports": ranked("sp"), "championships": ranked("cp"), "clubs": ranked("cl"),
                "requests": len(groups), "keys": {code: dict(t) for code, t in tallies.items()}}

    def interest_map_text(self, store: dict) -> str:
        m = self.interest_map(store)
        total = m["active"]
        lines = ["👥 Интересы «Своей Трибуны»", "", f"Участников: {total}", f"Настроили профиль: {m['configured']}"]
        if m["sports"]:
            lines += [""] + [f"{label} — {n}" for label, n, level in m["sports"]]
        if m["championships"]:
            lines += ["", "🏆 Чемпионаты:"] + [f"{label} — {n}" for label, n, level in m["championships"][:10]]
        if m["clubs"]:
            lines += ["", "❤️ Клубы:"] + [f"{label} — {n}" for label, n, level in m["clubs"][:10]]
        if not total:
            lines += ["", "Пока в группе нет участников с профилем."]
        elif not m["configured"]:
            lines += ["", "Никто ещё не настроил интересы."]
        if m["requests"]:
            lines += ["", f"➕ Запросов «Другое»: {m['requests']} — смотри «📝 Запросы участников»."]
        lines += ["", "Считаются только участники, которые сейчас в группе."]
        return "\n".join(lines)

    def editorial_summary(self, store: dict) -> dict:
        """Данные для будущей редакционной логики: что вызывает высокий / средний / нишевый интерес у активных участников."""
        m = self.interest_map(store)
        out = {lvl: {key: [label for label, n, level in m[key] if level == lvl] for key in ("sports", "championships", "clubs")}
               for lvl in ("high", "medium", "niche")}
        out["active"] = m["active"]
        return out

    def high_interest(self, store: dict | None = None) -> dict:
        """Виды спорта / чемпионаты / клубы с высоким интересом (≥50% активных участников)."""
        ed = self.editorial_summary(store or self.load_members())["high"]
        return {"sports": ed["sports"], "championships": ed["championships"], "clubs": ed["clubs"]}

    def request_groups(self, store: dict, requests: dict | None = None) -> list:
        """Активные запросы «Другое» по нормализованному названию в разрезе раздела. Считаются только участники, ещё состоящие в группе;
        имена наружу не отдаются."""
        requests = requests or self.load_requests()
        active_ids = {p["user_id"] for p in self.active_profiles(store)}
        groups: dict = {}
        for it in requests["items"]:
            if not it["active"] or it["user_id"] not in active_ids:
                continue
            g = groups.setdefault((it["category"], it["normalized_text"]),
                                  {"category": it["category"], "normalized": it["normalized_text"], "label": it["raw_text"],
                                   "users": set(), "first": it["created_at"]})
            g["users"].add(it["user_id"])
            if it["created_at"] < g["first"]:
                g["first"], g["label"] = it["created_at"], it["raw_text"]
        out = [{**g, "count": len(g["users"]), "key": hashlib.sha1(f"{g['category']}|{g['normalized']}".encode()).hexdigest()[:8]}
               for g in groups.values()]
        for g in out:
            del g["users"]
        return sorted(out, key=lambda g: (-g["count"], g["normalized"]))


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


    def admin_hub(self):
        text = f"🛠 Управление Трибуной\n{GROUP_NAME} · {BOT_NAME}"
        rows = [[Btn("👤 Участники", "adm:mem:0"), Btn("👥 Интересы Трибуны", "adm:int")],
                [Btn("📝 Запросы участников", "adm:rq"), Btn("🌐 Источники", "adm:src")],
                [Btn("💰 API / расходы", "adm:api"), Btn("📣 Публикации", "pub:list")],
                [Btn("🗓 Автоматика", "adm:auto"), Btn("⭐ Приоритеты", "pr:list")],
                [Btn("📊 Состояние", "adm:state"), Btn("🔔 Уведомления", "adm:ns")],
                [Btn("📨 Приглашение", "inv")], [Btn("🏠 Меню", "m")]]
        return text, rows

    async def route_admin(self, ctx: Ctx, head: str, parts: list) -> None:
        # owner уже проверен в on_callback; проверяем ещё раз прямо здесь — защита не зависит от вызывающего кода
        if not self.is_owner(ctx.user_id):
            return
        back = [[Btn("🛠 Управление", "adm"), Btn("🏠 Меню", "m")]]
        if head == "adm":
            sub = parts[1] if len(parts) > 1 else ""
            arg = parts[2] if len(parts) > 2 else ""
            if sub == "":
                await ctx.edit(*self.admin_hub())
            elif sub == "int":
                await ctx.edit(self.interest_map_text(self.load_members()), back)
            elif sub == "mem":
                await ctx.edit(*self.members_view(int(arg) if arg else 0))
            elif sub == "mc":
                await ctx.edit(*self.member_card(int(arg)))
            elif sub == "rq":
                await ctx.edit(*self.requests_view())
            elif sub == "rqd":
                await ctx.edit(*self.request_detail(parts[2], parts[3]))
            elif sub == "src":
                await ctx.edit(*self.sources_screen(arg))
            elif sub == "api":
                await ctx.edit(*self.api_screen(arg))
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
        requests = self.load_requests()
        members = sorted(store["members"].values(), key=lambda p: (not p.get("active_in_group"), norm(p["display_name"])))
        active = [p for p in members if p.get("active_in_group")]
        configured = sum(1 for p in active if self.is_complete(normalize_profile(p), self.user_requests(requests, p["user_id"])))
        size = 8
        pages = max(1, (len(members) + size - 1) // size)
        page = max(0, min(page, pages - 1))
        lines = ["👤 Участники", "", f"Участников группы: {len(active)}", f"✅ Настроили интересы: {configured}",
                 f"⏳ Не настроили: {len(active) - configured}", ""]
        rows = []
        for i, p in enumerate(members[page * size:(page + 1) * size], page * size + 1):
            done = self.is_complete(normalize_profile(p), self.user_requests(requests, p["user_id"]))
            state = "🚪 вышел" if not p.get("active_in_group") else "✅ профиль" if done else "⏳ не настроил"
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
        profile = normalize_profile(dict(profile))
        mine = self.user_requests(self.load_requests(), uid)

        def when(value):
            return f"{value[8:10]}.{value[5:7]}.{value[0:4]} {value[11:16]}" if value else "—"

        def asked(cat):
            return ", ".join(r["raw_text"] for r in mine if r["category"] == cat) or "—"
        source = {"event": "", "sync": " (обнаружен при подключении Трибуна)", "dialog": " (определён по личному диалогу)"}.get(profile.get("joined_source"), "")
        eff = self.effective(profile)
        lines = [f"👤 {profile['display_name']}", "", "Статус:", "✅ В группе" if profile.get("active_in_group") else "🚪 Вышел из группы", "",
                 "Профиль:", "✅ Настроен" if self.is_complete(profile, mine) else "⏳ Не настроен", "",
                 "🏅 Виды спорта:\n" + (", ".join(self.label("sp", k) for k in eff["sp"]) or "—"), "",
                 "🏆 Чемпионаты:\n" + (", ".join(self.label("cp", k) for k in eff["cp"]) or "—"), "",
                 "❤️ Клубы:\n" + (", ".join(self.label("cl", k) for k in eff["cl"]) or "—"), "",
                 f"➕ Запросы «Другое»:\nспорт: {asked(C.CAT_SPORT)}\nчемпионаты: {asked(C.CAT_CHAMP)}\nклубы: {asked(C.CAT_CLUB)}", "",
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

    # ---- «📝 Запросы участников» ----------------------------------------------------------------------------------

    ADMIN_BACK = [Btn("🛠 Управление", "adm"), Btn("🏠 Меню", "m")]

    def requests_view(self):
        store = self.load_members()
        groups = self.request_groups(store)
        lines = ["📝 Запросы участников", "", "Что участники попросили через «➕ Другое» (без имён). Запрос ничего не включает сам: "
                 "новый источник, парсер или публикации появятся только по вашему решению.", ""]
        rows = []
        for cat, title in ((C.CAT_SPORT, CAT_TITLE["sp"]), (C.CAT_CHAMP, CAT_TITLE["cp"]), (C.CAT_CLUB, CAT_TITLE["cl"])):
            mine = [g for g in groups if g["category"] == cat]
            if mine:
                lines += [title + ":"] + [f"{g['label']} — {g['count']}" for g in mine] + [""]
                rows += [[Btn(f"{g['label']} — {g['count']}", f"adm:rqd:{C.CAT_TO_CODE[cat]}:{g['key']}")] for g in mine[:6]]
        if not groups:
            lines.append("Запросов пока нет.")
        return "\n".join(lines).rstrip(), rows + [self.ADMIN_BACK]

    def request_detail(self, code: str, key: str):
        group = next((g for g in self.request_groups(self.load_members()) if g["key"] == key and C.CAT_TO_CODE[g["category"]] == code), None)
        back = [[Btn("📝 Запросы", "adm:rq"), Btn("🛠 Управление", "adm")]]
        if group is None:
            return "Запрос не найден (возможно, уже снят участниками).", back
        status, note = self.coverage_of_request(group["category"], group["normalized"])
        if self.registry is None:
            cover = "реестр источников не подключён"
        else:
            cover = f"{src.COVERAGE_ICON[status]} {status} — {src.COVERAGE_TEXT[status]}" + (f"\n   ({note})" if note else "")
        verdict = ("Источников нет: добавлять или нет — решает владелец, автоматически ничего не подключается." if status == src.GAP
                   else "Уже есть источник, но публикации по этому интересу сами не включаются.")
        lines = ["📝 Запрос участников", "", f"Раздел: {CAT_TITLE[code]}", f"Название: {group['label']}", f"Нормализовано: {group['normalized']}",
                 f"Активных запросивших: {group['count']}", f"Покрытие источниками: {cover}", "", f"Статус: {verdict}"]
        return "\n".join(lines), back

    def coverage_of_request(self, category: str, normalized: str) -> tuple:
        if self.registry is None:
            return src.GAP, ""
        return self.registry.request_coverage(category, normalized)

    # ---- «🌐 Источники» --------------------------------------------------------------------------------------------

    def when_text(self, iso: str | None) -> str:
        try:
            return datetime.datetime.fromisoformat(iso).astimezone(YEKB_TZ).strftime("%d.%m %H:%M") if iso else "—"
        except ValueError:
            return "—"

    def sport_status_line(self, sport_key: str) -> str:
        reg = self.registry
        status, note = reg.sport_coverage(sport_key)
        name = C.SPORT_BY_KEY[sport_key][1]
        if status == src.GAP:
            return f"🟡 {name} — источников пока нет"
        if status == src.FAILED:
            return f"❌ {name} — источники не отвечают"
        if status == src.FALLBACK_ONLY:
            return f"🟡 {name} — только поиск, без прямого источника"
        healths = [reg.health(d.source_id) for c in C.CLUBS if c[2] == sport_key for d in reg.serving_club(c[0]) if d.source_type != src.SEARCH and d.enabled]
        if any(h in (src.DEGRADED, src.FAILED_HEALTH) for h in healths):
            return f"🟡 {name} — есть сбои"
        if any(h == src.OK for h in healths):
            return f"✅ {name} — OK"
        return f"✅ {name} — настроен, ещё не проверялся"

    def uncovered_interests(self, store: dict) -> list:
        """Интересы участников, у которых НЕТ рабочего источника (GAP/FAILED): выбранные из каталога и запросы «Другое»."""
        reg = self.registry
        if reg is None:
            return []
        m = self.interest_map(store)
        out = []
        for code, cat in (("sp", C.CAT_SPORT), ("cp", C.CAT_CHAMP), ("cl", C.CAT_CLUB)):
            for key, n in m["keys"][code].items():
                status, note = ((reg.sport_coverage(key) if code == "sp" else reg.competition_coverage(key) if code == "cp" else reg.club_coverage(key)))
                if status in (src.GAP, src.FAILED):
                    out.append({"category": cat, "label": self.label(code, key), "count": n, "status": status, "note": note, "kind": "catalog"})
        for g in self.request_groups(store):
            status, note = reg.request_coverage(g["category"], g["normalized"])
            if status in (src.GAP, src.FAILED):
                out.append({"category": g["category"], "label": g["label"], "count": g["count"], "status": status, "note": note, "kind": "request"})
        return sorted(out, key=lambda x: (-x["count"], x["label"]))

    def sources_screen(self, sub: str):
        back = [[Btn("🌐 Источники", "adm:src"), Btn("🛠 Управление", "adm")]]
        reg = self.registry
        if reg is None:
            return "🌐 Источники\n\nРеестр источников не подключён.", [self.ADMIN_BACK]
        rows_ = reg.rows()
        enabled = [r for r in rows_ if r["def"].enabled]
        if sub == "cov":
            lines = ["🏟 Покрытие", "", "Покрытие = что SPORTBOT реально отслеживает (шесть клубов), а не «весь спорт».", "", "Виды спорта:"]
            for key, icon, name, _ in C.SPORTS:
                st, note = reg.sport_coverage(key)
                lines.append(f"{src.COVERAGE_ICON[st]} {name} — {src.COVERAGE_TEXT[st]}" + (f" ({note})" if note else ""))
            lines += ["", "Чемпионаты:"]
            for c in C.COMPETITIONS:
                st, note = reg.competition_coverage(c[0])
                lines.append(f"{src.COVERAGE_ICON[st]} {c[1]} — {src.COVERAGE_TEXT[st]}")
            lines += ["", "Клубы:"]
            for c in C.CLUBS:
                st, note = reg.club_coverage(c[0])
                lines.append(f"{src.COVERAGE_ICON[st]} {c[1]} — {src.COVERAGE_TEXT[st]}")
            return "\n".join(lines), back
        if sub == "all":
            lines = ["🌍 Все источники", "", "Иерархия: официальный → прямой → агрегатор → медиа → поиск. Поиск (Tavily/OpenAI) и AI — не источники фактов: "
                     "счёт всегда сверяется.", ""]
            for r in rows_:
                d, st = r["def"], r["state"]
                tail = "" if d.enabled else " (выключен)"
                lines.append(f"{src.HEALTH_ICON[r['health']] if d.enabled else '⏸'} {d.name}{tail}")
                lines.append(f"   тип {d.source_type} · {', '.join(d.purpose)} · последний успех {self.when_text(st.get('last_success_at'))} · "
                             f"ошибок подряд {st.get('consecutive_failures', 0)}")
            return "\n".join(lines), back
        if sub == "prob":
            bad = [r for r in enabled if r["health"] in (src.DEGRADED, src.FAILED_HEALTH)]
            lines = ["⚠️ Проблемы", ""]
            for r in bad:
                st = r["state"]
                lines.append(f"{src.HEALTH_ICON[r['health']]} {r['def'].name}")
                lines.append(f"   ошибок подряд {st.get('consecutive_failures', 0)} · последняя {self.when_text(st.get('last_failure_at'))}"
                             f" · {st.get('last_error_short') or 'без текста'}")
            if not bad:
                lines.append("Проблем с источниками сейчас нет.")
            return "\n".join(lines), back
        if sub == "gap":
            store = self.load_members()
            gaps = self.uncovered_interests(store)
            lines = ["🧩 Непокрытые интересы", "", "Участники выбрали или попросили, но надёжного источника нет. Ничего не подключается и не публикуется автоматически.", ""]
            for g in gaps:
                title = {C.CAT_SPORT: "вид спорта", C.CAT_CHAMP: "чемпионат", C.CAT_CLUB: "клуб"}[g["category"]]
                lines.append(f"{src.COVERAGE_ICON[g['status']]} {g['label']} ({title}) — {g['count']} · "
                             f"{'запрос «Другое»' if g['kind'] == 'request' else 'выбран из списка'}")
            if not gaps:
                lines.append("Непокрытых интересов нет.")
            return "\n".join(lines), back
        working = sum(1 for r in enabled if r["health"] == src.OK)
        problem = sum(1 for r in enabled if r["health"] in (src.DEGRADED, src.FAILED_HEALTH))
        unknown = sum(1 for r in enabled if r["health"] == src.UNKNOWN)
        lines = ["🌐 Источники", ""] + [self.sport_status_line(key) for key, *_ in C.SPORTS]
        lines += ["", f"✅ Рабочих источников: {working}", f"⚠️ С проблемами: {problem}", f"▫️ Ещё не проверялись: {unknown}",
                  f"🧩 Непокрытых интересов: {len(self.uncovered_interests(self.load_members()))}"]
        rows = [[Btn("🏟 Покрытие", "adm:src:cov"), Btn("🌍 Все источники", "adm:src:all")],
                [Btn("⚠️ Проблемы", "adm:src:prob"), Btn("🧩 Непокрытые интересы", "adm:src:gap")], self.ADMIN_BACK]
        return "\n".join(lines), rows

    # ---- «💰 API / расходы» ----------------------------------------------------------------------------------------

    @staticmethod
    def num(n) -> str:
        return f"{int(n):,}".replace(",", " ")

    def provider_line(self, provider: str, s: dict | None) -> str:
        title = costs.PROVIDER_TITLE[provider]
        if not s or not (s["real"] or s["cached"] or s["failed"]):
            return f"{title}: вызовов нет"
        parts = [f"{self.num(s['real'])} вызовов"]
        if s["units"]:
            parts.append(f"кредитов {self.num(s['units'])}")
        if s["input_tokens"] or s["output_tokens"]:
            parts.append(f"токены {self.num(s['input_tokens'])} вх / {self.num(s['output_tokens'])} исх")
        parts += [f"из кэша {self.num(s['cached'])}", f"ошибок {self.num(s['failed'])}"]
        return f"{title}: " + " · ".join(parts)

    def cost_line(self, summary: dict, providers=None) -> str:
        totals, unknown, calls = {}, 0, 0
        for p, s in summary["providers"].items():
            if providers and p not in providers:
                continue
            calls += s["real"]
            unknown += s["cost_unknown"]
            for cur, amt in s["cost"].items():
                totals[cur] = totals.get(cur, 0.0) + amt
        if not calls:
            return "Оценка стоимости: —"
        money = " + ".join(f"{amt:.4f} {cur}" for cur, amt in sorted(totals.items()))
        if not totals:
            return "Оценка стоимости: неизвестна (цены не заданы)"
        return f"Оценка стоимости: ≈ {money}" + (f"; для {unknown} вызовов цены нет — их стоимость неизвестна" if unknown else "")

    def period_block(self, title: str, prefix: str, providers) -> list:
        s = self.tracker.summary(prefix)
        lines = [title] + [self.provider_line(p, s["providers"].get(p)) for p in providers]
        return lines + [self.cost_line(s, providers)]

    def api_screen(self, sub: str):
        back = [[Btn("💰 API / расходы", "adm:api"), Btn("🛠 Управление", "adm")]]
        tr = self.tracker
        if tr is None:
            return "💰 API / расходы\n\nУчёт API не подключён.", [self.ADMIN_BACK]
        today = tr.day_key(self.now())
        month = today[:7]
        shown = [p for p in costs.PROVIDERS if p != "sports_api"]
        head = ["💰 API / расходы", "", "Расход (вызовы, токены, кредиты) и оценка стоимости — разные вещи: стоимость считается только по заданной цене."]
        if sub == "d":
            return "\n".join(head + [""] + self.period_block(f"📅 Сегодня · {today}", today, shown)), back
        if sub == "m":
            return "\n".join(head + [""] + self.period_block(f"📆 Месяц · {month}", month, shown)), back
        if sub in ("ai", "api"):
            group = costs.AI_PROVIDERS if sub == "ai" else costs.API_PROVIDERS
            title = "🧠 AI (DeepSeek, OpenAI)" if sub == "ai" else "🌐 Поисковые API (Tavily, будущие sports API)"
            lines = head + ["", title, ""] + self.period_block(f"Сегодня · {today}", today, group) + [""] + self.period_block(f"Месяц · {month}", month, group)
            return "\n".join(lines), back
        if sub == "pur":
            lines = head + ["", "🎯 Назначение вызовов (реальные / из кэша / ошибки)"]
            for title, prefix in ((f"Сегодня · {today}", today), (f"Месяц · {month}", month)):
                purposes = tr.summary(prefix)["purposes"]
                lines += ["", title]
                lines += [f"{costs.PURPOSE_TITLE.get(p, p)}: {v['real']} / {v['cached']} / {v['failed']}" for p, v in sorted(purposes.items())] or ["вызовов нет"]
            return "\n".join(lines), back
        if sub == "err":
            s = tr.summary(month)
            lines = head + ["", f"⚠️ Ошибки · {month}"]
            lines += [f"{costs.PROVIDER_TITLE.get(p, p)} ×{n}: {msg or 'без текста'}" for p, msg, n in s["errors"][:8]]
            if not s["errors"]:
                lines.append("Ошибок вызовов нет.")
            return "\n".join(lines), back
        priced = [e for e in tr.pricing.entries() if e.get("currency")]
        lines = head + [""] + self.period_block(f"📅 Сегодня · {today}", today, shown) + [""] + self.period_block(f"📆 Месяц · {month}", month, shown)
        lines += ["", "Цены: " + (", ".join(f"{costs.PROVIDER_TITLE.get(e['provider'], e['provider'])} {e['model']}" for e in priced) if priced
                                  else "не заданы — стоимость неизвестна. Задать: «/цена» в личке."),
                  "", "Как экономим: факты находит код, не нейросеть; один матч — один поиск; одинаковые запросы берутся из кэша; "
                  "DeepSeek — основной, OpenAI — только резерв; личных рассылок нет."]
        rows = [[Btn("📅 Сегодня", "adm:api:d"), Btn("📆 Месяц", "adm:api:m")], [Btn("🧠 AI", "adm:api:ai"), Btn("🌐 API", "adm:api:api")],
                [Btn("🎯 Назначение", "adm:api:pur"), Btn("⚠️ Ошибки", "adm:api:err")], self.ADMIN_BACK]
        return "\n".join(lines), rows

    def price_command(self, text: str) -> str:
        """«/цена <провайдер> <модель> <вход за 1 млн токенов> <выход за 1 млн> <валюта>» или «/цена <провайдер> <модель> кредит <цена> <валюта>».
        Без аргументов — текущие цены. Цены сами не придумываются: пока владелец не задал, стоимость «неизвестна»."""
        if self.tracker is None:
            return "Учёт API не подключён."
        args = (text or "").split()[1:]
        if not args:
            lines = ["Цены (за 1 млн токенов; кредит — за единицу):"]
            for e in self.tracker.pricing.entries():
                price = (f"вход {e['input_price']} / выход {e['output_price']} {e['currency']}" if e.get("input_price") is not None
                         else f"кредит {e['credit_price']} {e['currency']}" if e.get("credit_price") is not None else "не задана")
                lines.append(f"• {e['provider']} {e['model']}: {price}")
            lines += ["", "Задать: /цена deepseek deepseek-v4-flash 0.14 0.28 USD", "Кредиты: /цена tavily search кредит 0.008 USD"]
            return "\n".join(lines)
        try:
            provider, model = args[0].lower(), args[1]
            if args[2].casefold() in ("кредит", "credit"):
                entry = self.tracker.pricing.set_price(provider, model, credit_price=float(args[3]), currency=args[4], now=self.now())
            else:
                entry = self.tracker.pricing.set_price(provider, model, input_price=float(args[2]), output_price=float(args[3]),
                                                       currency=args[4], now=self.now())
        except (ValueError, IndexError):
            return "Не понял. Пример: /цена deepseek deepseek-v4-flash 0.14 0.28 USD"
        return f"✅ Цена записана: {entry['provider']} {entry['model']} ({entry['currency']}). Пересчёт — для новых вызовов."

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
            lines = ["⭐ Приоритеты", "", "Временный редакционный сигнал для будущих публикаций (команда, вид спорта, турнир, событие). "
                     "Личные интересы участников и прежнюю автоматику клубов не меняет.", ""]
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
            hint = {"team": "название команды", "sport": "вид спорта (футбол, хоккей, футзал, баскетбол)",
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

