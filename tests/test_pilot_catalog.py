"""Стартовый пилот: футбол (РПЛ, Первая лига, АПЛ, Ла Лига, Серия А, ЛЧ), хоккей (КХЛ, NHL), футзал (Суперлига). Баскетбол/ВТБ, Лига Европы и Лига
конференций отложены: не в меню, не в агрегате, не в покрытии; выбор в профилях не удаляется; прежняя автоматика шести клубов (включая Милан в ЛЕ) не затронута."""
import asyncio
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_sport_v4 as TS  # noqa: E402
from test_tribun import Base, OWNER  # noqa: E402
from test_sport_v4 import S, sportsru_page  # noqa: E402
import tribun_catalog as C  # noqa: E402

PILOT_FOOTBALL = ["РПЛ / Премьер-лига", "Первая лига", "АПЛ", "Ла Лига", "Серия А", "Лига чемпионов"]


class PilotCatalog(Base):
    def shown(self, code):
        return [l.removeprefix("✅ ") for l, p in self.buttons() if p.startswith(f"t:{code}:") or p == f"o:{code}"]

    def keys(self, code):
        return [p.split(":")[2] for _, p in self.buttons() if p.startswith(f"t:{code}:")]

    async def test_sports_are_exactly_football_hockey_futsal(self):
        self.assertEqual([k for k, *_ in C.SPORTS], ["football", "hockey", "futsal"])
        await self.join(5, "А")
        await self.press(5, "c:sp")
        self.assertEqual(self.shown("sp"), ["⚽ Футбол", "🏒 Хоккей", "🥅 Футзал", "➕ Другое"])
        self.assertNotIn("Баскетбол", str(self.buttons()))

    async def test_competitions_by_sport(self):
        await self.join(5, "А")
        for sport, expected in (("football", PILOT_FOOTBALL), ("hockey", ["КХЛ", "NHL"]), ("futsal", ["Суперлига"])):
            await self.press(5, f"t:sp:{sport}")
            await self.press(5, "c:cp")
            self.assertEqual(self.shown("cp")[:-1] if sport == "football" else [x for x in self.shown("cp") if x in expected], expected)
            await self.press(5, f"t:sp:{sport}")
        self.assertEqual([c[1] for c in C.COMPETITIONS if c[2] == "football"], PILOT_FOOTBALL)
        self.assertEqual([c[1] for c in C.COMPETITIONS if c[2] == "hockey"], ["КХЛ", "NHL"])
        self.assertEqual([c[1] for c in C.COMPETITIONS if c[2] == "futsal"], ["Суперлига"])

    async def test_deferred_directions_are_nowhere_in_the_menu(self):
        await self.join(5, "А")
        for sport in ("football", "hockey", "futsal"):
            await self.press(5, f"t:sp:{sport}")
        await self.press(5, "c:cp")
        text = str(self.buttons())
        for gone in ("Лига Европы", "Лига конференций", "ВТБ", "Баскетбол"):
            self.assertNotIn(gone, text)
        for comp in [c[0] for c in C.COMPETITIONS]:
            await self.press(5, f"t:cp:{comp}")
        await self.press(5, "c:cl")
        clubs = set(self.keys("cl"))
        self.assertNotIn("benfica", clubs)
        self.assertNotIn("ajax", clubs)
        self.assertNotIn("zenit_b", clubs)
        self.assertEqual(clubs, {c[0] for c in C.CLUBS})

    async def test_old_buttons_of_removed_choices_do_nothing(self):
        await self.join(5, "А")
        for payload in ("t:sp:basketball", "t:cp:vtb", "t:cp:uel", "t:cp:uecl", "t:cl:zenit_b"):
            await self.press(5, payload)
            self.assertIn("недоступен", self.last_text(), payload)
        profile = self.profile(5)
        self.assertEqual((profile["sports"], profile["championships"], profile["clubs"]), ([], [], []))

    async def test_clubs_union_of_selected_pilot_competitions(self):
        await self.join(5, "А")
        await self.press(5, "t:sp:football")
        await self.press(5, "t:cp:apl")
        await self.press(5, "t:cp:ucl")
        await self.press(5, "c:cl")
        self.assertEqual(set(self.keys("cl")), set(C.comp_club_keys("apl")) | set(C.comp_club_keys("ucl")))
        self.assertEqual(self.keys("cl").count("arsenal"), 1)
        self.assertNotIn("benfica", self.keys("cl"))                                 # клуб только Лиги Европы список не пополняет

    async def test_existing_profile_survives_the_catalog_change(self):
        await self.join(5, "Дмитрий")
        await self.onboard(5, sports=("football", "hockey"), comps=("khl", "apl"), clubs=("avtomobilist", "arsenal"))
        eff = self.tribun.effective(self.profile(5))
        self.assertEqual(eff, {"sp": ["football", "hockey"], "cp": ["khl", "apl"], "cl": ["avtomobilist", "arsenal"]})
        self.assertTrue(self.profile(5)["onboarding_completed"])

    async def test_stale_selections_stay_in_the_profile_but_are_not_active(self):
        await self.join(5, "Дмитрий")
        await self.onboard(5, sports=("football", "hockey"), comps=("khl", "apl"), clubs=("avtomobilist", "arsenal"))
        async with self.tribun.lock:
            store = self.tribun.load_members()
            profile = store["members"]["5"]
            profile["sports"] += ["basketball"]
            profile["championships"] += ["vtb", "uel", "uecl"]
            profile["clubs"] += ["zenit_b", "benfica", "ajax"]
            self.tribun.save_members(store)
        raw = self.profile(5)
        self.assertIn("basketball", raw["sports"])
        eff = self.tribun.effective(raw)
        self.assertEqual(eff, {"sp": ["football", "hockey"], "cp": ["khl", "apl"], "cl": ["avtomobilist", "arsenal"]})
        m = self.tribun.interest_map(self.tribun.load_members())
        labels = [label for rows in (m["sports"], m["championships"], m["clubs"]) for label, _n, _lvl in rows]
        for gone in ("Баскетбол", "Единая лига ВТБ", "Лига Европы", "Лига конференций", "Бенфика", "Аякс"):
            self.assertNotIn(gone, labels)
        self.assertNotIn("Бенфика", self.tribun.chosen_text(raw, "cl", []))
        await self.press(5, "c:sp")                                                  # дальнейшие правки ничего не стирают
        await self.press(5, "t:sp:futsal")
        raw = self.profile(5)
        self.assertEqual((raw["championships"][-3:], raw["clubs"][-3:]), (["vtb", "uel", "uecl"], ["zenit_b", "benfica", "ajax"]))

    async def test_other_requests_are_just_requests(self):
        await self.join(5, "А")
        for text in ("Баскетбол", "NBA", "Лига Европы", "Лига конференций"):
            self.assertIsNone(C.match_any(text), text)                              # не распознаются как штатные направления
        await self.press(5, "o:sp")
        await self.say(5, "Баскетбол")
        await self.press(5, "o:cp")
        await self.say(5, "Лига Европы")
        await self.press(5, "o:cp")
        await self.say(5, "Лига конференций")
        await self.press(5, "o:cp")
        await self.say(5, "NBA")
        profile = self.profile(5)
        self.assertEqual((profile["sports"], profile["championships"], profile["clubs"]), ([], [], []))        # автоматически ничего не включено
        reqs = self.tribun.user_requests(self.tribun.load_requests(), 5)
        self.assertEqual(sorted(r["raw_text"] for r in reqs), ["NBA", "Баскетбол", "Лига Европы", "Лига конференций"])


class LegacyIsIndependentOfTheCatalog(unittest.TestCase):
    def test_six_legacy_clubs_are_intact(self):
        self.assertEqual([c["key"] for c in S.CLUBS], ["avtomobilist", "sinara", "ural", "real", "arsenal", "milan"])
        for club in S.CLUBS:
            self.assertTrue(club["extract_urls"], club["key"])

    def test_milan_europa_league_match_is_still_seen_by_the_old_club_calendar(self):
        self.assertNotIn("uel", C.COMP_BY_KEY)                                       # ЛЕ из пользовательского каталога убрана…
        milan = [c for c in S.CLUBS if c["key"] == "milan"][0]
        page = sportsru_page([("08.10.2026", "22:00", "Лига Европы", "Ренн", "Дома", ["превью", "–"]),
                              ("11.10.2026", "21:45", "Серия А", "Рома", "В гостях", ["превью", "–"])])

        async def fake_direct(url):
            return page

        async def go():
            with mock.patch.object(S, "fetch_url_direct", fake_direct):
                return await S.fetch_club_calendar(milan, datetime.date(2026, 10, 5))
        fixtures = asyncio.run(go())
        europa = [f for f in fixtures if f["tournament"] == "Лига Европы"]
        self.assertEqual([(str(f["date"]), f["rival"], f["home"], f["time"]) for f in europa], [("2026-10-08", "Ренн", True, "22:00")])   # …а матч Милана виден
        self.assertEqual(len(fixtures), 2)

    def test_legacy_source_defs_still_serve_milan(self):
        import sources as SRC
        defs = {d.source_id for d in SRC.build_source_defs(S.CLUBS)}
        self.assertIn("calendar:milan", defs)
        self.assertNotIn("sportsru:center:basketball", defs)                         # отложенные адаптеры в активный реестр (и опрос) не входят
        comps = {k for d in SRC.build_source_defs(S.CLUBS) for k in d.comp_keys}
        self.assertEqual(comps, {c[0] for c in C.COMPETITIONS})


if __name__ == "__main__":
    unittest.main()
