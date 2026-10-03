"""Экран владельца «🎯 Активные клубы»: объединение клубов участников, равноправие, статистика, только владелец."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tribun import Base, OWNER, GROUP, user  # noqa: E402

LEGACY = {"avtomobilist", "sinara", "ural", "real", "arsenal", "milan"}


class ActiveClubsScreen(Base):
    def make(self, owner=OWNER, **kw):
        return super().make(owner=owner, legacy_clubs=LEGACY, **kw)

    async def test_hub_has_the_button_and_screen_lists_union_of_clubs_as_statistics(self):
        await self.press(OWNER, "adm")
        self.assertIn(("🎯 Активные клубы", "adm:ac"), self.buttons())
        await self.join(5, "Анна")
        await self.onboard(5, sports=("football",), comps=("rpl", "apl"), clubs=("zenit", "arsenal"))
        await self.join(6, "Борис")
        await self.onboard(6, sports=("football", "hockey"), comps=("apl", "khl"), clubs=("arsenal", "avtomobilist"))
        await self.join(7, "Вера")
        await self.onboard(7, sports=("football",), comps=("rpl",), clubs=("zenit",))
        await self.press(OWNER, "adm:ac")
        text = self.last_text()
        self.assertTrue(text.startswith("🎯 Активные клубы — 3\n"))
        self.assertIn("⚽ Футбол\nАрсенал — 2 (историческая автоматика)\nЗенит — 2", text)
        self.assertIn("🏒 Хоккей\nАвтомобилист — 1 (историческая автоматика)", text)
        self.assertIn("Все обслуживаются одинаково", text)
        self.assertIn("Число участников — только статистика, на обслуживание оно не влияет.", text)
        self.assertIn("деплой и ручное включение не нужны", text)
        for name in ("Анна", "Борис", "Вера"):
            self.assertNotIn(name, text)                                                       # имён нет
        self.assertIn("Историческая автоматика обслуживает всегда", text)

    async def test_screen_follows_profile_changes_leaving_and_returning(self):
        await self.join(5, "Анна")
        await self.onboard(5, sports=("football",), comps=("rpl",), clubs=("zenit",))
        await self.join(6, "Борис")
        await self.onboard(6, sports=("football",), comps=("rpl",), clubs=("spartak",))
        await self.press(OWNER, "adm:ac")
        self.assertTrue(self.last_text().startswith("🎯 Активные клубы — 2\n"))
        await self.tribun.on_user_removed(GROUP, user(5))
        await self.press(OWNER, "adm:ac")
        text = self.last_text()
        self.assertTrue(text.startswith("🎯 Активные клубы — 1\n"))
        self.assertNotIn("Зенит", text.split("Список пересчитывается")[0])
        await self.join(5, "Анна")
        await self.press(OWNER, "adm:ac")
        self.assertTrue(self.last_text().startswith("🎯 Активные клубы — 2\n"))

    async def test_hidden_and_other_selections_are_not_shown(self):
        await self.join(5, "Анна")
        await self.onboard(5, sports=("football",), comps=("rpl",), clubs=("zenit",), other={"cl": "Бока Хуниорс"})
        async with self.tribun.lock:
            store = self.tribun.load_members()
            store["members"]["5"]["clubs"] += ["benfica", "zenit_b"]
            self.tribun.save_members(store)
        await self.press(OWNER, "adm:ac")
        text = self.last_text()
        self.assertTrue(text.startswith("🎯 Активные клубы — 1\n"))
        self.assertNotIn("Бенфика", text)
        self.assertNotIn("Бока", text)

    async def test_empty_group(self):
        await self.press(OWNER, "adm:ac")
        self.assertTrue(self.last_text().startswith("🎯 Активные клубы — 0\n"))
        self.assertIn("Пока никто не выбрал клубы.", self.last_text())

    async def test_only_owner_sees_it(self):
        await self.join(5, "Анна")
        before = len(self.out) if hasattr(self, "out") else 0
        await self.press(5, "adm:ac")
        self.assertNotIn("Активные клубы", str(self.out))


class LaunchTexts(Base):
    BANNED = ("баскетбол", "теннис", "автоспорт", "спортсмен", "прогноз", "разбор", "AI", "ленту, а про")

    def test_public_texts_are_the_agreed_ones(self):
        import tribun as T
        self.assertEqual(T.GROUP_DESCRIPTION_TEXT.splitlines()[:3], ["🏟️ СВОЯ ТРИБУНА | СПОРТ", "", "Здесь спорт смотрят своей компанией."])
        self.assertTrue(T.GROUP_DESCRIPTION_TEXT.endswith("Выбирай свои команды — и они становятся частью нашей общей Трибуны. 🔥"))
        self.assertIn("Здесь нет чужих и «главных» команд.", T.PINNED_TEXT)
        self.assertIn("📅 по понедельникам — матчи наших клубов на неделю\n🔔 в день матча — напоминание\n"
                      "⚠️ сообщаем о переносах, отменах и изменении времени\n🏁 после игры — результат", T.PINNED_TEXT)
        self.assertIn("вид спорта → чемпионат → клубы.", T.PINNED_TEXT)
        self.assertTrue(T.HOOK_TEXT.endswith("Ссылка:\nhttps://max.ru/join/22mxKdkXVvWHUpBiXQrp1a2zcClhNynt4BAVd-2waWk"))
        self.assertIn("За Зенит? Добавляй Зенит.\nЗа Реал? Будет Реал.\nЗа клуб NHL или КХЛ? Выбирай его.", T.HOOK_TEXT)

    def test_public_texts_mention_only_pilot_directions(self):
        import tribun as T
        for name, text in (("invite", T.INVITE_TEXT), ("description", T.GROUP_DESCRIPTION_TEXT), ("pinned", T.PINNED_TEXT)):
            low = text.lower()
            for gone in ("баскетбол", "теннис", "автоспорт", "спортсмен", "прогноз", "разбор", "ai-", "ии-"):
                self.assertNotIn(gone, low, (name, gone))
        for text in (T.INVITE_TEXT.format(link="x"), T.GROUP_DESCRIPTION_TEXT, T.PINNED_TEXT, T.HOOK_TEXT):
            self.assertIn("⚽", text)
            self.assertLess(len(text), 3900)

    async def test_owner_sees_texts_and_nothing_is_published(self):
        await self.press(OWNER, "adm")
        self.assertIn(("📢 Тексты запуска", "adm:txt"), self.buttons())
        await self.press(OWNER, "adm:txt")
        text = self.last_text()
        for part in ("1️⃣ Описание группы", "2️⃣ Закреп", "3️⃣ Зазывалка", "Добро пожаловать на Свою Трибуну!", "Заходи на Свою Трибуну"):
            self.assertIn(part, text)
        self.assertLess(len(text), 4500)
        self.assertEqual(self.bot.group(), [])                                      # ничего не публикуется, описание/закреп группы не меняются

    async def test_only_owner_can_open_texts(self):
        await self.join(5, "Анна")
        await self.press(5, "adm:txt")
        self.assertNotIn("Тексты запуска", str(self.out))
