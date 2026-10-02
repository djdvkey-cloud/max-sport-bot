"""«Своя Трибуна» / «Трибун»: приветствие, личные интересы, карта интересов, пульт владельца. MAX подменён заглушками."""
import asyncio
import datetime
import json
import os
import re
import sys
import tempfile
import types as pytypes
import unittest
from unittest import mock

os.environ.setdefault("MAX_BOT_TOKEN", "test-token")
os.environ.setdefault("MAX_CHAT_ID", "-100500")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tribun-import-")
os.environ["ADMIN_USER_ID"] = "777"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tribun as T  # noqa: E402

OWNER, GROUP = 777, -100500
EKB = T.YEKB_TZ


def rd(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def user(uid, first="Иван", last=None, bot=False, username=None):
    return pytypes.SimpleNamespace(user_id=uid, first_name=first, last_name=last, is_bot=bot, username=username)


class FakeBot:
    def __init__(self):
        self.sent = []                # (chat_id|user_id, text, attachments)
        self.fail_send = False
        self.fail_user = False         # личные сообщения (владельцу) не доходят
        self.no_mid = False
        self.counter = 0
        self.members = {}             # user_id -> user (для get_chat_members / get_chat_member)
        self.fail_members = False
        self.chat_link = None
        self.group_log = []

    async def send_message(self, chat_id=None, user_id=None, text=None, attachments=None, **kw):
        if self.fail_send or (self.fail_user and user_id is not None):
            raise RuntimeError("MAX недоступен")
        self.counter += 1
        self.sent.append((chat_id if chat_id is not None else user_id, text, attachments))
        if chat_id == GROUP:
            self.group_log.append((chat_id, text, attachments))
        if self.no_mid:
            return None
        return pytypes.SimpleNamespace(message=pytypes.SimpleNamespace(body=pytypes.SimpleNamespace(mid=f"mid{self.counter}")))

    async def get_chat_by_id(self, chat_id):
        return pytypes.SimpleNamespace(link=self.chat_link)

    async def get_chat_members(self, chat_id=None, user_ids=None, marker=None, count=None):
        if self.fail_members:
            raise RuntimeError("нет прав")
        return pytypes.SimpleNamespace(members=list(self.members.values()), marker=None)

    async def get_chat_member(self, chat_id=None, user_id=None):
        if self.fail_members:
            raise RuntimeError("нет прав")
        return self.members.get(user_id)

    def group(self):
        return [m for m in self.sent if m[0] == GROUP]


class Clock:
    def __init__(self, dt=None):
        self.dt = dt or datetime.datetime(2026, 10, 3, 9, 0, tzinfo=EKB)

    def __call__(self):
        return self.dt.astimezone(datetime.timezone.utc)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tribun-test-")
        self.bot = FakeBot()
        self.clock = Clock()
        self.events = []
        self.names = {}
        self.make()

    def make(self, owner=OWNER, **kw):
        self.tribun = T.Tribun(self.bot, data_dir=self.tmp, group_chat_id=GROUP, owner_id=lambda: owner, version="test-1",
                               now=self.clock, events_for_day=lambda day: [e for e in self.events if e["date"] == day.isoformat()],
                               bot_username="tribun_bot", **kw)
        return self.tribun

    # ---- помощники диалога
    def ctx(self, uid, *, text="", payload="", private=True, name=None):
        name = name or self.names.get(uid, "Иван")
        self.out = []

        async def reply(t, rows=None):
            self.out.append(("reply", t, rows))

        async def edit(t, rows=None):
            self.out.append(("edit", t, rows))
        return T.Ctx(user_id=uid, chat_id=uid if private else GROUP, is_private=private, display_name=name, text=text, payload=payload,
                     reply=reply, edit=edit, ack=None)

    async def press(self, uid, payload, **kw):
        await self.tribun.on_callback(self.ctx(uid, payload=payload, **kw))
        return self.out

    async def say(self, uid, text, **kw):
        await self.tribun.on_message(self.ctx(uid, text=text, **kw))
        return self.out

    def last_text(self):
        return self.out[-1][1]

    def buttons(self):
        rows = self.out[-1][2] or []
        return [(T.as_btn(b).label, T.as_btn(b).payload) for row in rows for b in row]

    def profile(self, uid):
        return self.tribun.load_members()["members"][str(uid)]

    async def join(self, uid, name="Иван", **kw):
        self.names[uid] = name
        return await self.tribun.on_user_added(GROUP, user(uid, name, **kw))

    async def onboard(self, uid, sports=("hockey",), teams=("avto",), comps=("khl",), content=("matches",), level="all"):
        await self.tribun.on_bot_started(user(uid, self.names.get(uid, "Иван")), uid, None, self.ctx(uid).reply)
        for s in sports:
            await self.press(uid, f"t:sp:{s}:o")
        for t in teams:
            await self.press(uid, f"t:tm:{t}:o")
        for c in comps:
            await self.press(uid, f"t:cp:{c}:o")
        for c in content:
            await self.press(uid, f"t:ct:{c}:o")
        await self.press(uid, f"nl:{level}:o")


class Welcome(Base):
    async def test_new_member_gets_exactly_one_welcome_with_link_button(self):
        self.assertEqual(await self.join(1, "Пётр"), "welcomed")
        group = self.bot.group()
        self.assertEqual(len(group), 1)
        text = group[0][1]
        self.assertTrue(text.startswith("🏟️ Добро пожаловать на «Свою Трибуну», Пётр!"))
        for part in ("Здесь спорт не просто смотрят — им живут. 🔥", "Я Трибун 🤖", "📌 В закрепе — что здесь будет.",
                     "настрой их у меня в личке 👇"):
            self.assertIn(part, text)
        att = group[0][2][0]
        button = att.payload.buttons[0][0]
        self.assertEqual(button.text, "⚙️ Настроить мои интересы")
        self.assertEqual(button.url, "https://max.ru/tribun_bot?start=interests")
        self.assertTrue(self.profile(1)["welcomed"] and self.profile(1)["active_in_group"])

    async def test_repeated_events_do_not_spam(self):
        await self.join(1)
        for _ in range(3):
            self.assertEqual(await self.join(1), "duplicate")
        self.assertEqual(len(self.bot.group()), 1)

    async def test_bot_and_foreign_chat_events_do_not_welcome(self):
        self.assertEqual(await self.join(99, "Трибун", bot=True), "ignored")
        self.assertEqual(await self.tribun.on_user_added(-777, user(2, "Чужой")), "ignored")
        self.assertEqual(self.bot.group(), [])
        self.assertNotIn("99", self.tribun.load_members()["members"])

    async def test_failed_welcome_is_not_marked_and_can_repeat_on_next_event(self):
        self.bot.fail_send = True
        self.assertEqual(await self.join(1), "welcome-failed")
        self.assertFalse(self.profile(1)["welcomed"])
        self.bot.fail_send = False
        self.assertEqual(await self.join(1), "welcomed")
        self.assertEqual(await self.join(1), "duplicate")
        self.assertEqual(len(self.bot.group()), 1)

    async def test_welcome_without_bot_username_still_works_without_button(self):
        self.tribun.bot_username = None
        await self.join(1, "Анна")
        text, att = self.bot.group()[0][1], self.bot.group()[0][2]
        self.assertIn("Открой личный диалог со мной", text)
        self.assertFalse(att)

    async def test_leave_and_return_after_long_time_no_second_welcome(self):
        await self.join(1)
        self.assertEqual(await self.tribun.on_user_removed(GROUP, user(1)), "left")
        self.assertFalse(self.profile(1)["active_in_group"])
        self.clock.dt += datetime.timedelta(days=200)
        self.assertEqual(await self.join(1), "returned")
        self.assertTrue(self.profile(1)["active_in_group"])
        self.assertEqual(len(self.bot.group()), 1)

    async def test_dry_run_prints_and_sends_nothing(self):
        self.make(dry_run=True)
        self.assertEqual(await self.join(1), "welcome-failed")
        self.assertEqual(self.bot.sent, [])


class Onboarding(Base):
    async def test_bot_started_with_interests_payload_starts_wizard(self):
        await self.tribun.on_bot_started(user(5, "Мария"), 5, "interests", self.ctx(5).reply)
        self.assertIn("Привет, Мария! Я Трибун", self.last_text())
        self.assertIn(("▶️ Начать настройку", "ob:sp"), self.buttons())

    async def test_full_personal_onboarding_flow(self):
        await self.tribun.on_bot_started(user(5, "Мария"), 5, "interests", self.ctx(5).reply)
        await self.press(5, "ob:sp")
        self.assertIn("Шаг 1 из 5 · Виды спорта", self.last_text())
        labels = [l for l, p in self.buttons()]
        for sport in ("🏒 Хоккей", "⚽ Футбол", "🎾 Теннис", "🏀 Баскетбол", "🏎️ Автоспорт", "🥊 Единоборства", "⛷️ Зимние виды", "➕ Другое"):
            self.assertIn(sport, labels)
        await self.press(5, "t:sp:hockey:o")
        await self.press(5, "ob:ts")
        self.assertIn("Шаг 2 из 5 · Команды и спортсмены", self.last_text())
        await self.press(5, "t:tm:avto:o")
        await self.press(5, "ob:cp")
        await self.press(5, "t:cp:khl:o")
        await self.press(5, "ob:ct")
        await self.press(5, "t:ct:matches:o")
        await self.press(5, "ob:nl")
        self.assertIn("Шаг 5 из 5", self.last_text())
        self.assertFalse(self.profile(5)["onboarding_completed"])
        await self.press(5, "nl:main:o")
        self.assertIn("✅ Запомнил!", self.last_text())
        self.assertIn("Теперь я буду учитывать твои интересы в работе «Своей Трибуны».", self.last_text())
        self.assertEqual([p for _, p in self.buttons()], ["me", "today"])
        p = self.profile(5)
        self.assertTrue(p["onboarding_completed"])
        self.assertEqual((p["sports"], p["teams"], p["competitions"], p["content_preferences"], p["notification_level"]),
                         (["hockey"], ["Автомобилист"], ["КХЛ"], ["matches"], "main"))

    async def test_multi_select_toggles_on_and_off(self):
        await self.tribun.on_bot_started(user(5), 5, None, self.ctx(5).reply)
        for key in ("hockey", "football", "tennis"):
            await self.press(5, f"t:sp:{key}:o")
        self.assertEqual(self.profile(5)["sports"], ["hockey", "football", "tennis"])
        await self.press(5, "t:sp:football:o")
        self.assertEqual(self.profile(5)["sports"], ["hockey", "tennis"])
        self.assertIn("✅ 🏒 Хоккей", [l for l, _ in self.buttons()])
        self.assertIn("⚽ Футбол", [l for l, _ in self.buttons()])

    async def test_suggestions_follow_chosen_sports(self):
        await self.tribun.on_bot_started(user(5), 5, None, self.ctx(5).reply)
        await self.press(5, "t:sp:motorsport:o")
        await self.press(5, "ob:ts")
        labels = [l for l, _ in self.buttons()]
        self.assertIn("Макс Ферстаппен", labels)
        self.assertNotIn("Автомобилист", labels)
        self.assertIn("Сборная России", labels)                   # общие варианты есть всегда

    async def test_add_custom_team_unknown_kept_as_typed_known_canonical(self):
        await self.tribun.on_bot_started(user(5), 5, None, self.ctx(5).reply)
        await self.press(5, "add:tm:o")
        self.assertIn("Напиши команды", self.last_text())
        await self.say(5, "автомобилист\nНовая Команда FC;  Зенит ")
        self.assertEqual(self.profile(5)["teams"], ["Автомобилист", "Новая Команда FC", "Зенит"])      # неизвестное — без правок
        self.assertIn("Добавил: Автомобилист, Новая Команда FC, Зенит", self.last_text())
        await self.press(5, "add:tm:o")
        await self.say(5, "ЗЕНИТ")
        self.assertEqual(self.profile(5)["teams"].count("Зенит"), 1)
        self.assertIn("Это уже в списке", self.last_text())
        labels = [l for l, _ in self.buttons()]
        self.assertIn("✅ Новая Команда FC", labels)               # свой вариант виден и снимается кнопкой
        key = [p for l, p in self.buttons() if l == "✅ Новая Команда FC"][0]
        await self.press(5, key)
        self.assertNotIn("Новая Команда FC", self.profile(5)["teams"])

    async def test_custom_sport_and_athlete_and_competition(self):
        await self.tribun.on_bot_started(user(5), 5, None, self.ctx(5).reply)
        await self.press(5, "add:sp:o")
        await self.say(5, "керлинг")
        await self.press(5, "add:at:o")
        await self.say(5, "Карлос Алькарас, Некто Неизвестный")
        await self.press(5, "add:cp:o")
        await self.say(5, "Чемпионат города")
        p = self.profile(5)
        self.assertEqual((p["sports"], p["athletes"], p["competitions"]), (["керлинг"], ["Карлос Алькарас", "Некто Неизвестный"], ["Чемпионат города"]))

    async def test_empty_custom_input_asks_again(self):
        await self.tribun.on_bot_started(user(5), 5, None, self.ctx(5).reply)
        await self.press(5, "add:tm:o")
        await self.say(5, "  ,  ; ")
        self.assertIn("Не понял", self.last_text())
        self.assertEqual(self.profile(5)["teams"], [])
        await self.say(5, "Спартак")                                # ожидание ввода сохранилось
        self.assertEqual(self.profile(5)["teams"], ["Спартак"])

    async def test_change_one_setting_keeps_everything_else(self):
        await self.onboard(5, sports=("hockey", "football"), teams=("avto", "zenit"), comps=("khl",), content=("matches", "results"))
        before = {k: self.profile(5)[k] for k in ("teams", "competitions", "content_preferences", "notification_level")}
        await self.press(5, "me")
        self.assertIn("🏅 Виды спорта: 🏒 Хоккей, ⚽ Футбол", self.last_text())
        await self.press(5, "ed:sp")
        await self.press(5, "t:sp:football:e")
        await self.press(5, "t:sp:tennis:e")
        self.assertEqual(self.profile(5)["sports"], ["hockey", "tennis"])
        self.assertEqual({k: self.profile(5)[k] for k in before}, before)
        self.assertTrue(self.profile(5)["onboarding_completed"])
        self.assertEqual([p for l, p in self.buttons() if l == "✅ Готово"], ["me"])
        await self.press(5, "ed:nl")
        await self.press(5, "nl:off:e")
        self.assertEqual(self.profile(5)["notification_level"], "off")

    async def test_interests_hub_lists_all_categories(self):
        await self.onboard(5)
        await self.press(5, "me")
        for part in ("Виды спорта:", "Команды:", "Спортсмены:", "Турниры:", "Что интересно:", "Личные уведомления:"):
            self.assertIn(part, self.last_text())
        self.assertEqual({p for _, p in self.buttons()}, {"ed:sp", "ed:tm", "ed:at", "ed:cp", "ed:ct", "ed:nl", "today", "m"})

    async def test_name_change_does_not_create_new_profile(self):
        await self.onboard(5)
        await self.tribun.on_message(self.ctx(5, text="привет", name="Иван Новый"))
        members = self.tribun.load_members()["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members["5"]["display_name"], "Иван Новый")
        self.assertEqual(members["5"]["teams"], ["Автомобилист"])

    async def test_unknown_payload_returns_to_menu(self):
        await self.press(5, "ob:zzz")
        self.assertIn("Своя Трибуна", self.last_text())


class MenusAndRoles(Base):
    async def test_member_menu_has_three_buttons_and_no_admin(self):
        await self.say(5, "/start")
        self.assertEqual([p for _, p in self.buttons() if p in ("today", "me", "about")], ["today", "me", "about"])
        labels = [l for l, _ in self.buttons()]
        self.assertIn("🔥 Что сегодня у меня?", labels)
        self.assertIn("⚙️ Мои интересы", labels)
        self.assertIn("ℹ️ О Трибуне", labels)
        for forbidden in ("🛠 Управление Трибуной", "📨 Приглашение"):
            self.assertNotIn(forbidden, labels)

    async def test_owner_menu_has_everything_plus_admin(self):
        await self.say(OWNER, "меню", name="Дмитрий")
        labels = [l for l, _ in self.buttons()]
        for item in ("🔥 Что сегодня у меня?", "⚙️ Мои интересы", "ℹ️ О Трибуне", "📨 Приглашение", "🛠 Управление Трибуной"):
            self.assertIn(item, labels)
        await self.press(OWNER, "adm")
        labels = [l for l, _ in self.buttons()]
        for item in ("👥 Интересы Трибуны", "👤 Участники", "📣 Публикации", "🗓 Автоматика", "⭐ Приоритеты", "📊 Состояние"):
            self.assertIn(item, labels)

    async def test_member_cannot_use_admin_callbacks_even_by_hand(self):
        await self.join(5)
        pubs = {"items": [{"id": "abc12345", "text": "секрет", "status": "draft", "published": False, "created_at": "x", "updated_at": "x"}]}
        self.tribun.save_pubs(pubs)
        for payload in ("adm", "adm:int", "adm:mem:0", "adm:auto", "adm:state", "pub:list", "pub:new", "pub:go:abc12345", "pub:view:abc12345",
                        "pub:del:abc12345", "pr:list", "pr:add", "pr:type:team", "pr:dur:x:7", "inv"):
            self.out = []
            await self.press(5, payload)
            self.assertEqual(self.out, [], payload)
        self.assertEqual(self.bot.group()[1:], [])                  # в группу ничего не ушло (кроме приветствия при join)
        self.assertEqual(self.tribun.load_pubs()["items"][0]["status"], "draft")
        self.assertNotIn(5, self.tribun.awaiting)

    async def test_member_admin_text_states_never_run(self):
        self.tribun.awaiting[5] = {"kind": "pub_text"}
        await self.say(5, "опубликуй это")
        self.assertEqual(self.tribun.load_pubs()["items"], [])
        self.assertNotIn(5, self.tribun.awaiting)

    async def test_owner_rights_depend_on_id_not_name(self):
        for name in ("Дмитрий", "Совсем Другое Имя", "admin"):
            await self.say(OWNER, "меню", name=name)
            self.assertIn("🛠 Управление Трибуной", [l for l, _ in self.buttons()])
        await self.say(888, "меню", name="Дмитрий")                  # то же имя, другой id
        self.assertNotIn("🛠 Управление Трибуной", [l for l, _ in self.buttons()])
        self.make(owner=None)                                       # владелец не настроен — админ-функций нет ни у кого
        await self.say(OWNER, "меню")
        self.assertNotIn("🛠 Управление Трибуной", [l for l, _ in self.buttons()])

    async def test_group_and_private_do_not_mix(self):
        await self.say(OWNER, "меню", private=False)
        self.assertEqual(self.out, [])
        await self.press(OWNER, "adm", private=False)
        self.assertEqual(self.out, [])
        await self.press(5, "t:sp:hockey:o", private=False)
        self.assertNotIn("5", self.tribun.load_members()["members"])

    async def test_about_text(self):
        await self.press(5, "about")
        self.assertIn("Матчи. Эмоции. Разборы. Своя компания.", self.last_text())
        self.assertIn("🏟️ Своя Трибуна · 🤖 Трибун", self.last_text())

    def test_no_telegram_dependencies(self):
        for name in ("tribun.py", "sport_bot.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                src = f.read().lower()
            for banned in ("import aiogram", "from aiogram", "telegram.org", "python-telegram-bot", "import telebot"):
                self.assertNotIn(banned, src, name)


class Persistence(Base):
    async def test_profiles_survive_restart_and_corrupted_file_is_not_overwritten(self):
        await self.join(5, "Мария")
        await self.onboard(5, teams=("avto", "zenit"))
        before = self.profile(5)
        self.make()                                                # «перезапуск»: новый экземпляр на тех же файлах
        self.assertEqual(self.profile(5), before)
        with open(self.tribun.members_path, "w") as f:
            f.write("{broken")
        self.assertEqual(await self.join(6), "data-error")
        await self.say(5, "привет")
        self.assertIn("временно недоступны", self.last_text())
        await self.press(5, "me")
        self.assertIn("временно недоступны", self.last_text())
        self.assertEqual(rd(self.tribun.members_path), "{broken")
        self.assertEqual(self.bot.group()[1:], [])

    async def test_backup_made_on_change(self):
        await self.join(5)
        self.tribun.save_members(self.tribun.load_members())
        self.assertTrue(os.listdir(os.path.join(self.tmp, "backups")))

    async def test_sync_marks_existing_members_without_welcome_and_deactivates_absent(self):
        await self.join(9, "Ушедший")
        self.bot.sent.clear()
        self.bot.members = {1: user(1, "Старожил"), 2: user(2, "Другой"), 50: user(50, "Трибун", bot=True)}
        self.assertTrue(await self.tribun.sync_members())
        m = self.tribun.load_members()["members"]
        self.assertTrue(m["1"]["active_in_group"] and m["1"]["welcomed"] and m["2"]["active_in_group"])
        self.assertNotIn("50", m)
        self.assertFalse(m["9"]["active_in_group"])
        self.assertEqual(self.bot.group(), [])                      # существующих участников задним числом не приветствуем
        self.assertTrue([m[1] for m in self.bot.sent if m[0] == OWNER][0].startswith("👋 Ушедший покинул"))    # владелец узнаёт о выходе Ушедшего
        self.assertEqual(self.tribun.load_members()["meta"]["last_sync_count"], 2)

    async def test_sync_failure_changes_nothing(self):
        await self.join(9)
        before = rd(self.tribun.members_path, "rb")
        self.bot.fail_members = True
        self.assertFalse(await self.tribun.sync_members())
        self.assertEqual(before, rd(self.tribun.members_path, "rb"))

    async def test_private_start_checks_real_membership(self):
        self.bot.members = {5: user(5, "Мария")}
        await self.tribun.on_bot_started(user(5, "Мария"), 5, None, self.ctx(5).reply)
        self.assertTrue(self.profile(5)["active_in_group"])
        await self.tribun.on_bot_started(user(6, "Гость"), 6, None, self.ctx(6).reply)
        self.assertFalse(self.profile(6)["active_in_group"])


class Aggregation(Base):
    def populate(self, n, hockey_share=1.0):
        store = self.tribun.load_members()
        for i in range(n):
            p = self.tribun.touch(store, 1000 + i, f"U{i}", active=True)
            p["onboarding_completed"] = True
            if i < n * hockey_share:
                p["sports"], p["teams"], p["competitions"] = ["hockey"], ["Автомобилист"], ["КХЛ"]
            p["content_preferences"] = ["matches"]
        self.tribun.save_members(store)
        return self.tribun.interest_map(self.tribun.load_members())

    def test_interest_levels(self):
        self.assertEqual(T.interest_level(5, 10), "high")
        self.assertEqual(T.interest_level(4, 10), "medium")
        self.assertEqual(T.interest_level(3, 10), "medium")
        self.assertEqual(T.interest_level(2, 10), "niche")
        self.assertEqual(T.interest_level(1, 4), "medium")
        self.assertEqual(T.interest_level(0, 0), "niche")
        self.assertEqual(T.interest_level(25, 100), "medium")
        self.assertEqual(T.interest_level(24, 100), "niche")
        self.assertEqual(T.interest_level(50, 100), "high")
        self.assertEqual(T.interest_level(49, 100), "medium")

    def test_aggregation_for_1_3_10_100_users(self):
        for n in (1, 3, 10, 100):
            self.setUp()
            m = self.populate(n)
            self.assertEqual((m["active"], m["configured"]), (n, n), n)
            self.assertEqual(m["sports"], [("🏒 Хоккей", n, "high")])
            self.assertEqual(m["teams"], [("Автомобилист", n, "high")])
            self.assertEqual(m["competitions"], [("КХЛ", n, "high")])

    def test_partial_interest_levels_on_10(self):
        m = self.populate(10, hockey_share=0.3)
        self.assertEqual(m["sports"], [("🏒 Хоккей", 3, "medium")])
        m = self.populate(10, hockey_share=0.0) if False else m
        self.assertEqual(m["configured"], 10)

    async def test_text_example_and_no_personal_data(self):
        await self.onboard(1, sports=("hockey", "football"), teams=("avto",))
        await self.onboard(2, sports=("hockey",), teams=("avto", "zenit"))
        await self.onboard(3, sports=("football",), teams=("zenit",))
        for uid in (1, 2, 3):
            await self.join(uid, f"Участник{uid}")
        self.tribun.touch(store := self.tribun.load_members(), 4, "Без профиля", active=True)
        self.tribun.save_members(store)
        text = self.tribun.interest_map_text(self.tribun.load_members())
        self.assertIn("Участников: 4", text)
        self.assertIn("Настроили профиль: 3", text)
        self.assertIn("🏒 Хоккей — 2 из 4", text)
        self.assertIn("⚽ Футбол — 2 из 4", text)
        self.assertIn("Автомобилист — 2", text)
        self.assertIn("Зенит — 2", text)
        self.assertNotIn("Участник1", text)                           # персональное наружу не идёт

    async def test_user_without_profile_or_empty_store_does_not_break(self):
        text = self.tribun.interest_map_text(self.tribun.load_members())
        self.assertIn("Участников: 0", text)
        await self.join(1)                                          # профиль есть, интересов нет
        text = self.tribun.interest_map_text(self.tribun.load_members())
        self.assertIn("Участников: 1", text)
        self.assertIn("Настроили профиль: 0", text)
        self.assertIn("Никто ещё не настроил интересы", text)

    async def test_leave_excludes_return_restores(self):
        await self.join(1)
        await self.onboard(1, sports=("hockey",))
        await self.join(2)
        await self.onboard(2, sports=("hockey", "football"))
        self.assertEqual(dict((l, n) for l, n, _ in self.tribun.interest_map(self.tribun.load_members())["sports"])["🏒 Хоккей"], 2)
        await self.tribun.on_user_removed(GROUP, user(2))
        m = self.tribun.interest_map(self.tribun.load_members())
        self.assertEqual((m["active"], dict((l, n) for l, n, _ in m["sports"])), (1, {"🏒 Хоккей": 1}))
        self.assertEqual(self.profile(2)["sports"], ["hockey", "football"])                      # профиль не удалён
        await self.join(2)
        m = self.tribun.interest_map(self.tribun.load_members())
        self.assertEqual((m["active"], dict((l, n) for l, n, _ in m["sports"])["🏒 Хоккей"]), (2, 2))
        self.assertEqual(self.profile(2)["sports"], ["hockey", "football"])                      # настройки восстановились

    async def test_owner_screens(self):
        await self.join(1, "Мария")
        await self.onboard(1)
        await self.join(2, "Пётр")
        await self.press(OWNER, "adm:int")
        self.assertIn("Участников: 2", self.last_text())
        await self.press(OWNER, "adm:mem:0")
        text = self.last_text()
        self.assertIn("Участников группы: 2", text)
        self.assertIn("✅ Настроили интересы: 1", text)
        self.assertIn("⏳ Не настроили: 1", text)
        self.assertIn("1. Мария — ✅ профиль", text)
        self.assertIn("2. Пётр — ⏳ не настроил", text)
        await self.tribun.on_user_removed(GROUP, user(2))
        await self.press(OWNER, "adm:mem:0")
        self.assertIn("🚪 вышел", self.last_text())


class TodayView(Base):
    def ev(self, club, rival, tour, time_text="19:00 (мск)", sport="hockey", date="2026-10-03", icon="🏒", aliases=()):
        return {"club_key": club, "club_name": club, "rival": rival, "tournament": tour, "time_text": time_text, "sport_key": sport,
                "date": date, "icon": icon, "aliases": list(aliases)}

    async def test_personal_priority_by_team_and_competition(self):
        self.events = [self.ev("ФК «Урал»", "Велес", "Первая лига", sport="football", icon="⚽", aliases=["Урал"]),
                       self.ev("ХК «Автомобилист»", "Амур", "КХЛ", aliases=["Автомобилист"]),
                       self.ev("«Реал Мадрид»", "Райо", "Ла Лига", sport="football", icon="⚽")]
        await self.onboard(5, sports=("hockey",), teams=("avto",), comps=("khl",))
        await self.press(5, "today")
        text = self.last_text()
        self.assertIn("⭐ По твоим интересам:", text)
        self.assertLess(text.index("Автомобилист"), text.index("Урал"))
        self.assertLess(text.index("Автомобилист"), text.index("Реал Мадрид"))
        self.assertIn("Ещё сегодня у Трибуны:", text)
        self.assertIn("ХК «Автомобилист» — Амур", text)
        self.assertIn("19:00 (мск) · КХЛ", text)

    async def test_competition_only_match(self):
        self.events = [self.ev("ФК «Урал»", "Велес", "РПЛ", sport="football", icon="⚽", aliases=["Урал"])]
        await self.onboard(5, sports=(), teams=(), comps=("rpl",))
        await self.press(5, "today")
        self.assertIn("⭐ По твоим интересам:", self.last_text())

    async def test_no_events_does_not_invent(self):
        await self.onboard(5)
        await self.press(5, "today")
        self.assertIn("Сегодня по моим источникам событий нет.", self.last_text())
        self.assertNotIn("Ближайшее", self.last_text())
        self.events = [self.ev("ХК «Автомобилист»", "Амур", "КХЛ", date="2026-10-05", aliases=["Автомобилист"])]
        await self.press(5, "today")
        self.assertIn("Ближайшее:", self.last_text())
        self.assertIn("05.10 · ", self.last_text())

    async def test_no_match_with_interests_shows_all_with_honest_note(self):
        self.events = [self.ev("«Милан»", "Рома", "Серия А", sport="football", icon="⚽")]
        await self.onboard(5, sports=("tennis",), teams=(), comps=())
        await self.press(5, "today")
        self.assertIn("По твоим интересам на сегодня совпадений нет", self.last_text())
        self.assertIn("«Милан» — Рома", self.last_text())

    async def test_user_without_interests_sees_everything_and_hint(self):
        self.events = [self.ev("«Милан»", "Рома", "Серия А", sport="football", icon="⚽")]
        await self.press(5, "today")
        self.assertIn("Ты ещё не настроил интересы", self.last_text())
        self.assertIn("«Милан» — Рома", self.last_text())

    async def test_admin_priority_boosts_and_does_not_change_personal_interests(self):
        self.events = [self.ev("«Милан»", "Рома", "Серия А", sport="football", icon="⚽"),
                       self.ev("«Арсенал» Лондон", "Челси", "АПЛ", sport="football", icon="⚽", aliases=["Арсенал"])]
        await self.onboard(5, sports=("football",), teams=(), comps=())
        await self.press(5, "today")
        first = self.last_text()
        self.assertLess(first.index("Милан"), first.index("Арсенал"))        # без приоритета — как есть
        await self.press(OWNER, "pr:add")
        await self.press(OWNER, "pr:type:team")
        await self.say(OWNER, "Арсенал")
        self.assertIn("На сколько повысить «Арсенал»?", self.out[-1][1])
        dur = [p for l, p in self.buttons() if l == "7 дней"][0]
        before = self.profile(5)
        await self.press(OWNER, dur)
        self.assertIn("Команда: Арсенал", self.last_text())
        await self.press(5, "today")
        text = self.last_text()
        self.assertLess(text.index("Арсенал"), text.index("Милан"))
        self.assertIn("🔝", text)
        self.assertEqual(self.profile(5), before)                          # личные интересы не тронуты

    async def test_priority_expires_and_can_be_removed(self):
        await self.press(OWNER, "pr:type:sport")
        await self.say(OWNER, "football")
        dur = [p for l, p in self.buttons() if l == "1 день"][0]
        await self.press(OWNER, dur)
        self.assertEqual(len(self.tribun.active_priorities()), 1)
        self.clock.dt += datetime.timedelta(days=2)
        self.assertEqual(self.tribun.active_priorities(), [])
        await self.press(OWNER, "pr:list")
        self.assertIn("Сейчас приоритетов нет", self.last_text())
        await self.press(OWNER, "pr:type:team")
        await self.say(OWNER, "Зенит")
        pid = self.tribun.load_prios()["items"][-1]["id"]
        await self.press(OWNER, f"pr:dur:{pid}:30")
        self.assertEqual(len(self.tribun.active_priorities()), 1)
        await self.press(OWNER, f"pr:del:{pid}")
        self.assertEqual(self.tribun.active_priorities(), [])

    async def test_unfinished_priority_does_not_count(self):
        await self.press(OWNER, "pr:type:team")
        await self.say(OWNER, "Спартак")                                   # длительность не выбрана
        self.assertEqual(self.tribun.active_priorities(), [])


class Publications(Base):
    async def draft(self, text="Анонс: сегодня большой вечер!"):
        await self.press(OWNER, "pub:new")
        await self.say(OWNER, text)
        return self.tribun.load_pubs()["items"][-1]

    async def test_create_and_preview_is_not_publication(self):
        pub = await self.draft()
        self.assertEqual((pub["status"], pub["published"]), ("draft", False))
        self.assertNotIn("message_id", pub)
        self.assertEqual(self.bot.group(), [])
        self.assertIn("ПРЕДПРОСМОТР — в группу НЕ отправлено", self.last_text())
        self.assertIn("Анонс: сегодня большой вечер!", self.last_text())
        self.assertIn(f"pub:go:{pub['id']}", [p for _, p in self.buttons()])
        await self.press(OWNER, f"pub:view:{pub['id']}")                 # просмотр сколько угодно раз — всё ещё не публикация
        self.assertEqual(self.tribun.load_pubs()["items"][0]["status"], "draft")
        self.assertEqual(self.bot.group(), [])

    async def test_explicit_publish_success_records_message_id_after_send(self):
        pub = await self.draft()
        await self.press(OWNER, f"pub:go:{pub['id']}")
        sent = self.bot.group()
        self.assertEqual([m[1] for m in sent], ["Анонс: сегодня большой вечер!"])
        saved = self.tribun.load_pubs()["items"][0]
        self.assertEqual((saved["status"], saved["published"], saved["message_id"], saved["chat_id"]), ("published", True, "mid1", GROUP))
        self.assertIn("published_at", saved)
        self.assertIn("ОПУБЛИКОВАНО в группе", self.last_text())
        self.assertNotIn(f"pub:go:{pub['id']}", [p for _, p in self.buttons()])

    async def test_published_marked_only_after_successful_send(self):
        pub = await self.draft()
        seen = {}
        real = self.bot.send_message

        async def checking(**kw):
            seen["status"] = self.tribun.load_pubs()["items"][0]["status"]
            return await real(**kw)
        self.bot.send_message = checking
        await self.press(OWNER, f"pub:go:{pub['id']}")
        self.assertEqual(seen["status"], "draft")

    async def test_send_error_keeps_draft_and_allows_retry(self):
        pub = await self.draft()
        self.bot.fail_send = True
        await self.press(OWNER, f"pub:go:{pub['id']}")
        self.assertIn("Не удалось отправить", self.last_text())
        self.assertEqual(self.tribun.load_pubs()["items"][0]["status"], "draft")
        self.assertNotIn(pub["id"], self.tribun.published_guard)
        self.bot.fail_send = False
        await self.press(OWNER, f"pub:go:{pub['id']}")
        self.assertEqual(self.tribun.load_pubs()["items"][0]["status"], "published")
        self.assertEqual(len(self.bot.group()), 1)

    async def test_no_message_id_is_not_confirmed(self):
        pub = await self.draft()
        self.bot.no_mid = True
        await self.press(OWNER, f"pub:go:{pub['id']}")
        self.assertIn("не подтвердил отправку", self.last_text())
        self.assertEqual(self.tribun.load_pubs()["items"][0]["status"], "draft")

    async def test_double_press_and_restart_do_not_duplicate(self):
        pub = await self.draft()
        await self.press(OWNER, f"pub:go:{pub['id']}")
        await self.press(OWNER, f"pub:go:{pub['id']}")
        self.assertIn("Уже опубликовано", self.last_text())
        self.make()                                                       # перезапуск
        await self.press(OWNER, f"pub:go:{pub['id']}")
        self.assertEqual(len(self.bot.group()), 1)

    async def test_save_failure_after_send_guard_in_process(self):
        pub = await self.draft()
        with mock.patch.object(self.tribun, "save_pubs", side_effect=OSError("диск")):
            await self.press(OWNER, f"pub:go:{pub['id']}")
            await self.press(OWNER, f"pub:go:{pub['id']}")
        self.assertEqual(len(self.bot.group()), 1)

    async def test_edit_delete_and_list(self):
        pub = await self.draft("Первый вариант")
        await self.press(OWNER, f"pub:edit:{pub['id']}")
        await self.say(OWNER, "Второй вариант")
        self.assertEqual(self.tribun.load_pubs()["items"][0]["text"], "Второй вариант")
        await self.press(OWNER, "pub:list")
        self.assertIn("📝 Второй вариант", [l for l, _ in self.buttons()])
        await self.press(OWNER, f"pub:del:{pub['id']}")
        self.assertEqual(self.tribun.load_pubs()["items"], [])
        self.assertEqual(self.bot.group(), [])

    async def test_published_cannot_be_edited_or_deleted(self):
        pub = await self.draft()
        await self.press(OWNER, f"pub:go:{pub['id']}")
        await self.press(OWNER, f"pub:edit:{pub['id']}")
        self.assertIn("Опубликованное изменить нельзя", self.last_text())
        await self.press(OWNER, f"pub:del:{pub['id']}")
        self.assertEqual(len(self.tribun.load_pubs()["items"]), 1)

    async def test_empty_and_too_long_text_rejected(self):
        await self.press(OWNER, "pub:new")
        await self.say(OWNER, "/menu")
        self.assertEqual(self.tribun.load_pubs()["items"], [])
        await self.press(OWNER, "pub:new")
        await self.say(OWNER, "x" * 4000)
        self.assertIn("Слишком длинно", self.last_text())

    async def test_dry_run_publication_sends_nothing(self):
        self.make(dry_run=True)
        pub = await self.draft()
        await self.press(OWNER, f"pub:go:{pub['id']}")
        self.assertEqual(self.bot.group(), [])
        self.assertEqual(self.tribun.load_pubs()["items"][0]["status"], "draft")


class InvitationAutomationState(Base):
    async def test_invitation_text_exact_with_placeholder_when_no_link(self):
        await self.press(OWNER, "inv")
        text = self.last_text()
        self.assertIn("<ССЫЛКА НА ГРУППУ>", text)
        self.assertIn("Ссылку на группу MAX не отдаёт боту", text)
        for line in ("🏟️ ЗАХОДИ НА «СВОЮ ТРИБУНУ»", "🤖 В группе живёт Трибун — наш спортивный помощник.",
                     "🔥 Здесь не будет новостной помойки из сотни сообщений.", "🖤💛 СВОЯ ТРИБУНА",
                     "Матчи. Эмоции. Разборы. Своя компания. 🔥", "👉 Вступить:"):
            self.assertIn(line, text)
        self.assertEqual(self.bot.group(), [])                              # приглашение не публикуется

    async def test_invitation_uses_real_group_link_when_max_gives_it(self):
        self.bot.chat_link = "https://max.ru/join/abc123"
        await self.press(OWNER, "inv")
        self.assertIn("👉 Вступить:\nhttps://max.ru/join/abc123", self.last_text())
        self.assertNotIn("<ССЫЛКА НА ГРУППУ>", self.last_text())
        self.assertIn("подставлена автоматически", self.last_text())

    def test_invitation_text_fits_one_message(self):
        self.assertLess(len(T.INVITE_TEXT.format(link="https://max.ru/join/" + "x" * 40)), 3900)

    async def test_automation_and_state_views(self):
        self.make(automation=lambda: [{"name": "Утренний анонс матчей", "schedule": "ежедневно в 10:00", "next": "04.10.2026 10:00",
                                       "last_ok": "03.10.2026 10:00", "last_error": None, "enabled": True}],
                  status=lambda: {"running": True, "last_tick": "03.10.2026 09:00", "last_ok": "03.10.2026 09:00", "last_error": None,
                                  "sources": ["DeepSeek: ключ задан"]})
        await self.press(OWNER, "adm:auto")
        text = self.last_text()
        for part in ("Asia/Yekaterinburg", "Утренний анонс матчей", "ежедневно в 10:00", "следующий запуск: 04.10.2026 10:00",
                     "последний успешный: 03.10.2026 10:00", "последняя ошибка: нет"):
            self.assertIn(part, text)
        await self.press(OWNER, "adm:state")
        text = self.last_text()
        for part in ("Трибун: RUNNING", "Версия: test-1", "Планировщик: последний цикл 03.10.2026 09:00", "DeepSeek: ключ задан"):
            self.assertIn(part, text)
        self.assertNotRegex(text, r"test-token|\bsk-")

    async def test_job_status_records_and_masks_secrets(self):
        T.record_job(self.tmp, "weekly", True)
        T.record_job(self.tmp, "results", False, "RuntimeError: boom key=abcdefghijklmnopqrstuvwxyz0123456789")
        T.record_tick(self.tmp)
        jobs = T.read_job_status(self.tmp)
        self.assertIn("last_ok", jobs["weekly"])
        self.assertIn("***", jobs["results"]["error"])
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", jobs["results"]["error"])
        self.assertIn("_tick", jobs)


class ThroughRealDispatcher(unittest.IsolatedAsyncioTestCase):
    """Настоящие модели обновлений MAX и диспетчер maxapi; сеть подменена."""

    async def test_events_reach_handlers(self):
        from maxapi import Bot as RealBot, Dispatcher
        from maxapi.types import BotStarted, MessageCallback, MessageCreated, UserAdded, UserRemoved
        out = []

        async def fake_send(self_, chat_id=None, user_id=None, text=None, attachments=None, **kw):
            out.append((chat_id if chat_id is not None else user_id, text, attachments))
            return pytypes.SimpleNamespace(message=pytypes.SimpleNamespace(body=pytypes.SimpleNamespace(mid="m1")))

        async def fake_cb(self_, callback_id, message=None, notification=None):
            out.append(("callback", callback_id, getattr(message, "text", None)))

        async def fake_member(self_, chat_id=None, user_id=None):
            return None

        async def no_me():
            return None
        real_bot = RealBot("test-token")
        tmp = tempfile.mkdtemp(prefix="tribun-disp-")
        club = T.Tribun(real_bot, data_dir=tmp, group_chat_id=GROUP, owner_id=lambda: OWNER, version="t", bot_username="tribun_bot")
        dp = Dispatcher()
        club.register(dp)
        with mock.patch.object(RealBot, "send_message", fake_send), mock.patch.object(RealBot, "send_callback", fake_cb), \
                mock.patch.object(RealBot, "get_chat_member", fake_member), mock.patch.object(dp, "check_me", no_me), \
                mock.patch.object(dp, "_ready", False):
            await dp._Dispatcher__ready(real_bot)

            def u(uid, first="Иван", bot=False):
                return {"user_id": uid, "first_name": first, "is_bot": bot, "last_activity_time": 1}

            def added(uid, first="Иван", bot=False, chat=GROUP):
                ev = UserAdded.model_validate({"update_type": "user_added", "timestamp": 1, "chat_id": chat, "user": u(uid, first, bot),
                                               "inviter_id": None, "is_channel": False})
                ev.bot = real_bot
                return ev

            def msg(uid, chat_type, text, chat_id=555):
                raw = {"update_type": "message_created", "timestamp": 1,
                       "message": {"sender": u(uid), "recipient": {"chat_id": chat_id, "chat_type": chat_type, "user_id": 1},
                                   "timestamp": 1, "body": {"mid": "m", "seq": 1, "text": text}}}
                ev = MessageCreated.model_validate(raw)
                ev.bot = real_bot
                ev.message.bot = real_bot
                return ev

            def cb(uid, chat_type, payload):
                raw = {"update_type": "message_callback", "timestamp": 1,
                       "callback": {"timestamp": 1, "callback_id": "c1", "payload": payload, "user": u(uid)},
                       "message": {"sender": u(1, "Бот", True), "recipient": {"chat_id": 555, "chat_type": chat_type, "user_id": uid},
                                   "timestamp": 1, "body": {"mid": "m", "seq": 1, "text": "x"}}}
                ev = MessageCallback.model_validate(raw)
                ev.bot = real_bot
                ev.message.bot = real_bot
                return ev
            await dp.handle(added(5, "Мария"))
            welcome = [o for o in out if o[0] == GROUP]
            self.assertEqual(len(welcome), 1)
            self.assertIn("Мария", welcome[0][1])
            await dp.handle(added(5, "Мария"))
            await dp.handle(added(99, "Трибун", bot=True))
            self.assertEqual(len([o for o in out if o[0] == GROUP]), 1)
            out.clear()
            await dp.handle(msg(5, "dialog", "привет"))
            self.assertEqual(len(out), 1)
            self.assertIn("Выбирай", out[0][1])
            out.clear()
            await dp.handle(msg(5, "chat", "что тут", chat_id=GROUP))                 # в группе Трибун молчит
            await dp.handle(cb(5, "dialog", "adm"))                                    # member → админ-кнопка молча отклонена
            self.assertEqual([o for o in out if o[0] != "callback"], [])
            out.clear()
            await dp.handle(cb(OWNER, "dialog", "adm"))
            self.assertTrue(any(o[0] == "callback" and "Управление Трибуной" in str(o[2]) for o in out), out)
            await dp.handle(cb(5, "dialog", "ob:sp"))
            await dp.handle(cb(5, "dialog", "t:sp:hockey:o"))
            store = club.load_members()
            self.assertEqual(store["members"]["5"]["sports"], ["hockey"])
            await dp.handle(UserRemoved.model_validate({"update_type": "user_removed", "timestamp": 1, "chat_id": GROUP, "user": u(5),
                                                        "admin_id": None, "is_channel": False}))
            self.assertFalse(club.load_members()["members"]["5"]["active_in_group"])


if __name__ == "__main__":
    unittest.main()
