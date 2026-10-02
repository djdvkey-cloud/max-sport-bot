"""Цепочка выбора: вид спорта → чемпионаты → клубы только выбранных чемпионатов; навигация «Назад/Дальше»; сезонность справочника."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tribun import Base, T, OWNER  # noqa: E402
import tribun_catalog as C  # noqa: E402

NAV = ("✅ Сохранить", "Дальше ▶️", "⬅️ Назад")
THREE = ["🏅 Виды спорта", "🏆 Чемпионаты", "❤️ Клубы"]


class Flow(Base):
    def shown(self, code):
        """Названия вариантов экрана раздела (без отметок и навигации)."""
        return [l.removeprefix("✅ ") for l, p in self.buttons() if p.startswith(f"t:{code}:") or p == f"o:{code}"]

    def keys(self, code):
        return [p.split(":")[2] for _, p in self.buttons() if p.startswith(f"t:{code}:")]

    def nav(self, label):
        return [p for l, p in self.buttons() if l == label]

    async def pick(self, uid, sports=(), comps=(), clubs=()):
        for s in sports:
            await self.press(uid, f"t:sp:{s}")
        for c in comps:
            await self.press(uid, f"t:cp:{c}")
        for c in clubs:
            await self.press(uid, f"t:cl:{c}")

    # ---------- чемпионаты ----------
    async def test_hockey_shows_khl_nhl_other(self):
        await self.press(5, "t:sp:hockey")
        await self.press(5, "c:cp")
        self.assertEqual(self.shown("cp"), ["КХЛ", "NHL", "➕ Другое"])

    async def test_football_list_is_exactly_agreed(self):
        await self.press(5, "t:sp:football")
        await self.press(5, "c:cp")
        self.assertEqual(self.shown("cp"), ["РПЛ / Премьер-лига", "Первая лига", "АПЛ", "Ла Лига", "Серия А", "Лига чемпионов", "Лига Европы",
                                            "Лига конференций", "➕ Другое"])

    async def test_russian_cup_is_gone_everywhere(self):
        self.assertNotIn("Кубок России", [c[1] for c in C.COMPETITIONS])
        await self.press(5, "t:sp:football")
        await self.press(5, "t:sp:hockey")
        await self.press(5, "c:cp")
        self.assertNotIn("Кубок России", str(self.out))
        for banned in ("Бундеслига", "Лига 1", "MLS"):
            self.assertNotIn(banned, [c[1] for c in C.COMPETITIONS])

    async def test_futsal_and_basketball_not_mixed_in_unless_selected(self):
        await self.pick(5, sports=("football", "hockey"))
        await self.press(5, "c:cp")
        text = str(self.shown("cp"))
        self.assertNotIn("Суперлига", text)
        self.assertNotIn("ВТБ", text)
        await self.press(5, "c:sp")
        await self.press(5, "t:sp:futsal")
        await self.press(5, "c:cp")
        self.assertIn("Суперлига", self.shown("cp"))
        self.assertNotIn("Единая лига ВТБ", self.shown("cp"))
        await self.press(5, "c:sp")
        await self.press(5, "t:sp:basketball")
        await self.press(5, "c:cp")
        self.assertIn("Единая лига ВТБ", self.shown("cp"))

    async def test_futsal_catalog_is_minimal_and_has_sinara(self):
        await self.pick(5, sports=("futsal",), comps=("superliga",))
        await self.press(5, "c:cl")
        self.assertEqual(self.shown("cl"), ["Синара", "➕ Другое"])

    # ---------- клубы ----------
    async def test_only_khl_gives_only_khl_clubs(self):
        await self.pick(5, sports=("hockey", "football"), comps=("khl",))
        await self.press(5, "c:cl")
        self.assertEqual(sorted(self.keys("cl")), sorted(C.SEASONS["khl"]))
        self.assertIn("Автомобилист", self.shown("cl"))
        self.assertNotIn("Бостон Брюинз", self.shown("cl"))
        self.assertNotIn("Реал Мадрид", self.shown("cl"))

    async def test_only_nhl_gives_only_nhl_clubs(self):
        await self.pick(5, sports=("hockey",), comps=("nhl",))
        await self.press(5, "c:cl")
        self.assertEqual(sorted(self.keys("cl")), sorted(C.SEASONS["nhl"]))
        self.assertEqual(len(self.keys("cl")), 32)
        self.assertNotIn("Автомобилист", self.shown("cl"))

    async def test_laliga_gives_only_laliga_clubs(self):
        await self.pick(5, sports=("football", "hockey"), comps=("laliga",))
        await self.press(5, "c:cl")
        self.assertEqual(sorted(self.keys("cl")), sorted(C.SEASONS["laliga"]))
        self.assertIn("Реал Мадрид", self.shown("cl"))
        for other in ("Арсенал", "Милан", "Автомобилист"):
            self.assertNotIn(other, self.shown("cl"))

    async def test_apl_plus_champions_league_is_union_without_duplicates(self):
        await self.pick(5, sports=("football",), comps=("apl", "ucl"))
        await self.press(5, "c:cl")
        keys = self.keys("cl")
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), set(C.SEASONS["apl"]) | set(C.SEASONS["ucl"]))
        self.assertEqual(keys.count("arsenal"), 1)                                       # клуб из двух турниров — одна кнопка
        self.assertIn("Бавария", self.shown("cl"))                                       # клуб только ЛЧ
        self.assertIn("real", keys)
        self.assertNotIn("zenit", keys)

    async def test_unselecting_championship_deactivates_only_its_unique_clubs(self):
        await self.join(5, "Мария")
        await self.onboard(5, sports=("football",), comps=("apl", "uel"), clubs=("aston_villa", "crystal_palace"))
        m = self.tribun.interest_map(self.tribun.load_members())
        self.assertEqual([x[0] for x in m["clubs"]], ["Астон Вилла", "Кристал Пэлас"])
        await self.press(5, "c:cp")
        await self.press(5, "t:cp:apl")                                                  # сняли АПЛ
        m = self.tribun.interest_map(self.tribun.load_members())
        self.assertEqual([x[0] for x in m["clubs"]], ["Астон Вилла"])                    # Кристал Пэлас — только АПЛ → неактивен; Вилла осталась через ЛЕ
        self.assertEqual([x[0] for x in m["championships"]], ["Лига Европы"])
        self.assertEqual(self.profile(5)["clubs"], ["aston_villa", "crystal_palace"])    # выбор сохранён
        await self.press(5, "c:cl")
        self.assertNotIn("crystal_palace", self.keys("cl"))
        self.assertIn("✅ Астон Вилла", [l for l, _ in self.buttons()])
        await self.press(5, "c:cp")
        await self.press(5, "t:cp:apl")                                                  # вернули — клуб снова активен
        m = self.tribun.interest_map(self.tribun.load_members())
        self.assertEqual([x[0] for x in m["clubs"]], ["Астон Вилла", "Кристал Пэлас"])

    async def test_aggregation_counts_only_active_choices_in_owner_screen(self):
        await self.join(1, "А")
        await self.join(2, "Б")
        await self.onboard(1, sports=("hockey", "football"), comps=("khl", "laliga"), clubs=("avtomobilist", "real"))
        await self.onboard(2, sports=("football",), comps=("laliga", "apl"), clubs=("real", "arsenal"))
        await self.press(2, "c:cp")
        await self.press(2, "t:cp:apl")                                                  # у второго Арсенал больше не активен
        await self.press(OWNER, "adm:int")
        text = self.last_text()
        self.assertIn("⚽ Футбол — 2\n🏒 Хоккей — 1", text)
        self.assertIn("🏆 Чемпионаты:\nЛа Лига — 2\nКХЛ — 1", text)
        self.assertIn("❤️ Клубы:\nРеал Мадрид — 2\nАвтомобилист — 1", text)
        self.assertNotIn("Арсенал", text)

    async def test_clubs_screen_without_championships_asks_for_championship(self):
        await self.press(5, "t:sp:football")
        await self.press(5, "c:cl")
        self.assertIn("сначала отметь чемпионат", self.last_text())
        self.assertFalse(self.keys("cl"))
        self.assertIn("c:cp", [p for _, p in self.buttons()])
        await self.press(5, "t:cl:real")                                                 # вручную подставленный клуб недоступен
        self.assertEqual(self.profile(5)["clubs"], [])

    async def test_other_known_club_is_stored_but_waits_for_championship(self):
        await self.press(5, "t:sp:football")
        await self.press(5, "o:cl")
        await self.say(5, "Барселона")
        self.assertIn("появится в выборе", self.last_text())
        self.assertEqual(self.requests_count(), 0)
        self.assertEqual(self.profile(5)["clubs"], ["barcelona"])
        self.assertEqual(self.tribun.effective(self.profile(5))["cl"], [])
        await self.press(5, "t:cp:laliga")
        self.assertEqual(self.tribun.effective(self.profile(5))["cl"], ["barcelona"])

    def requests_count(self):
        return len(self.tribun.load_requests()["items"])

    # ---------- каталог и сезоны ----------
    def test_six_clubs_sit_in_their_competitions_without_invented_europe(self):
        where = {k: {c for c in C.SEASONS if k in C.SEASONS[c]} for k in ("avtomobilist", "sinara", "ural", "real", "arsenal", "milan")}
        self.assertEqual(where["avtomobilist"], {"khl"})
        self.assertEqual(where["sinara"], {"superliga"})
        self.assertEqual(where["ural"], {"fnl1"})
        self.assertEqual(where["real"], {"laliga", "ucl"})
        self.assertEqual(where["arsenal"], {"apl", "ucl"})
        self.assertEqual(where["milan"], {"seriea"})                                    # в еврокубке не придумываем
        for key in where:
            self.assertIn(key, C.CLUB_BY_KEY)

    def test_catalog_is_consistent(self):
        for comp, clubs in C.SEASONS.items():
            self.assertIn(comp, C.COMP_BY_KEY)
            self.assertEqual(len(clubs), len(set(clubs)), comp)
            for k in clubs:
                self.assertIn(k, C.CLUB_BY_KEY, (comp, k))
        self.assertEqual(set(C.SEASONS), set(C.COMP_BY_KEY))
        names = [c[1] for c in C.CLUBS]
        self.assertEqual(len(names), len(set(names)))                                    # названия клубов однозначны
        self.assertTrue(C.CURRENT_SEASON)

    async def test_next_season_roster_is_replaced_without_touching_the_interface(self):
        await self.pick(5, sports=("football",), comps=("ucl",))
        with mock.patch.dict(C.SEASONS, {"ucl": ["milan", "zenit"]}):
            await self.press(5, "c:cl")
            self.assertEqual(set(self.keys("cl")), {"milan", "zenit"})

    async def test_legacy_automation_is_not_touched_by_catalog_choices(self):
        import sport_bot as S
        keys = [c["key"] for c in S.CLUBS]
        await self.join(5, "Мария")
        await self.onboard(5, sports=("football",), comps=("laliga",), clubs=("real",))
        await self.press(5, "c:cp")
        await self.press(5, "t:cp:laliga")
        await self.press(5, "c:sp")
        await self.press(5, "t:sp:football")
        self.assertEqual([c["key"] for c in S.CLUBS], keys)
        self.assertEqual(keys, ["avtomobilist", "sinara", "ural", "real", "arsenal", "milan"])

    # ---------- «Назад» / «Дальше» / «Сохранить» ----------
    async def test_back_chain_clubs_championships_sports_menu(self):
        await self.pick(5, sports=("football",), comps=("laliga",))
        await self.press(5, "c:cl")
        self.assertEqual(self.nav("⬅️ Назад"), ["c:cp"])
        await self.press(5, "c:cp")
        self.assertTrue(self.last_text().startswith("🏆 Чемпионаты"))
        self.assertEqual(self.nav("⬅️ Назад"), ["c:sp"])
        await self.press(5, "c:sp")
        self.assertTrue(self.last_text().startswith("🏅 Виды спорта"))
        self.assertEqual(self.nav("⬅️ Назад"), ["m"])
        await self.press(5, "m")
        self.assertEqual([l for l, _ in self.buttons()], THREE)

    async def test_back_keeps_everything_selected_and_creates_no_state(self):
        await self.join(5, "Мария")
        await self.press(5, "c:sp")
        await self.pick(5, sports=("football", "hockey"))
        await self.press(5, self.nav("Дальше ▶️")[0])
        await self.pick(5, comps=("khl", "laliga"))
        await self.press(5, self.nav("Дальше ▶️")[0])
        await self.pick(5, clubs=("avtomobilist", "real"))
        before = dict(self.profile(5))
        members_before = set(self.tribun.load_members()["members"])
        await self.press(5, self.nav("⬅️ Назад")[0])
        self.assertIn("✅ КХЛ", [l for l, _ in self.buttons()])
        self.assertIn("✅ Ла Лига", [l for l, _ in self.buttons()])
        await self.press(5, self.nav("⬅️ Назад")[0])
        self.assertIn("✅ ⚽ Футбол", [l for l, _ in self.buttons()])
        self.assertIn("✅ 🏒 Хоккей", [l for l, _ in self.buttons()])
        await self.press(5, self.nav("⬅️ Назад")[0])
        self.assertEqual([l for l, _ in self.buttons()], THREE)
        after = self.profile(5)
        self.assertEqual({k: after[k] for k in ("sports", "championships", "clubs", "onboarding_completed")},
                         {k: before[k] for k in ("sports", "championships", "clubs", "onboarding_completed")})
        self.assertEqual(set(self.tribun.load_members()["members"]), members_before)     # новых профилей/сессий нет
        self.assertNotIn(5, self.tribun.awaiting)
        self.assertEqual(self.requests_count(), 0)
        await self.press(5, "c:cp")                                                      # снова вперёд — актуальное сохранённое состояние
        self.assertIn("✅ КХЛ", [l for l, _ in self.buttons()])
        await self.press(5, "c:cl")
        self.assertIn("✅ Автомобилист", [l for l, _ in self.buttons()])
        self.assertIn("✅ Реал Мадрид", [l for l, _ in self.buttons()])

    async def test_next_buttons_only_where_a_next_level_exists(self):
        await self.press(5, "c:sp")
        self.assertEqual(self.nav("Дальше ▶️"), ["c:cp"])
        await self.press(5, "c:cp")
        self.assertEqual(self.nav("Дальше ▶️"), ["c:cl"])
        await self.press(5, "c:cl")
        self.assertEqual(self.nav("Дальше ▶️"), [])
        self.assertEqual(self.nav("✅ Сохранить"), ["sv"])

    async def test_save_in_section_returns_to_menu_and_keeps_other_categories(self):
        await self.join(5, "Мария")
        await self.onboard(5, sports=("hockey",), comps=("khl",), clubs=("avtomobilist",))
        before = dict(self.profile(5))
        await self.press(5, "c:cl")
        await self.press(5, "t:cl:avtomobilist")
        await self.press(5, "t:cl:ska")
        await self.press(5, "sv")
        self.assertTrue(self.last_text().startswith("✅ Интересы сохранены"))
        self.assertEqual([l for l, _ in self.buttons()], THREE)
        after = self.profile(5)
        self.assertEqual((after["sports"], after["championships"]), (before["sports"], before["championships"]))
        self.assertEqual(after["clubs"], ["ska"])


if __name__ == "__main__":
    unittest.main()
