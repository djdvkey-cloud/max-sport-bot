"""Снимок каталога сезона 2026/27: сезон, источники, проверка дат, легаси-клубы, объединение; каталог выбора ≠ покрытие источниками."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tribun import Base, OWNER  # noqa: E402
import tribun_catalog as C  # noqa: E402
import sources as SRC  # noqa: E402

COUNTS = {"khl": 22, "nhl": 32, "rpl": 16, "fnl1": 18, "apl": 20, "laliga": 20, "seriea": 20, "ucl": 36, "uel": 36, "uecl": 36, "superliga": 12, "vtb": 12}


class Snapshot(unittest.TestCase):
    def comps_of(self, club):
        return {c for c in C.SEASONS if club in C.comp_club_keys(c)}

    def test_every_competition_is_season_2026_27_with_source_metadata(self):
        self.assertEqual(set(C.SEASONS), set(C.COMP_BY_KEY))
        self.assertEqual(C.CURRENT_SEASON, "2026/27")
        for key, snap in C.SEASONS.items():
            info = C.season_info(key)
            self.assertEqual(info["season"], "2026/27", key)
            self.assertTrue(info["source_name"], key)
            self.assertTrue(info["source_url"].startswith("https://"), key)
            self.assertRegex(info["verified_at"], r"^2026-10-\d\d$")
            self.assertIsInstance(info["official"], bool)
            self.assertTrue(snap["clubs"] or key == "vtb", key)

    def test_roster_sizes_match_verified_sources(self):
        self.assertEqual({k: len(C.comp_club_keys(k)) for k in C.SEASONS}, COUNTS)

    def test_uefa_and_laliga_and_nhl_come_from_official_sites(self):
        for key, domain in (("ucl", "uefa.com"), ("uel", "uefa.com"), ("uecl", "uefa.com"), ("laliga", "laliga.com"), ("nhl", "nhl.com")):
            self.assertTrue(C.season_info(key)["official"], key)
            self.assertIn(domain, C.season_info(key)["source_url"])

    def test_legacy_clubs_actual_tournaments_2026_27(self):
        self.assertEqual(self.comps_of("milan"), {"seriea", "uel"})                       # Милан — Лига Европы 2026/27 (UEFA.com)
        self.assertEqual(self.comps_of("real"), {"laliga", "ucl"})
        self.assertEqual(self.comps_of("arsenal"), {"apl", "ucl"})
        self.assertEqual(self.comps_of("avtomobilist"), {"khl"})
        self.assertEqual(self.comps_of("sinara"), {"superliga"})
        self.assertEqual(self.comps_of("ural"), {"fnl1"})                                 # Урал — Первая лига 2026/27, не РПЛ
        self.assertNotIn("ural", C.comp_club_keys("rpl"))

    def test_stale_2025_26_membership_is_gone(self):
        for club, comp in (("girona", "laliga"), ("pisa", "seriea"), ("cremonese", "seriea"), ("west_ham", "apl"), ("burnley", "apl"),
                           ("wolves", "apl"), ("sochi", "rpl"), ("pari_nn", "rpl"), ("vityaz", "khl"), ("milan", "ucl"), ("ural", "rpl")):
            self.assertNotIn(club, C.comp_club_keys(comp), (club, comp))
        for club, comp in (("coventry", "apl"), ("hull", "apl"), ("ipswich", "apl"), ("frosinone", "seriea"), ("monza", "seriea"), ("venezia", "seriea"),
                           ("rodina", "rpl"), ("fakel", "rpl"), ("deportivo", "laliga"), ("racing", "laliga"), ("malaga", "laliga"), ("shanghai", "khl"),
                           ("como", "ucl"), ("leipzig", "ucl"), ("juventus", "uel"), ("atalanta", "uecl")):
            self.assertIn(club, C.comp_club_keys(comp), (club, comp))

    def test_union_without_duplicates_and_catalog_integrity(self):
        union = C.clubs_for_competitions(["apl", "ucl"])
        keys = [c[0] for c in union]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), set(C.comp_club_keys("apl")) | set(C.comp_club_keys("ucl")))
        self.assertEqual(keys.count("arsenal"), 1)
        for comp in C.SEASONS:
            ks = C.comp_club_keys(comp)
            self.assertEqual(len(ks), len(set(ks)), comp)
            for k in ks:
                self.assertIn(k, C.CLUB_BY_KEY)
        names = [c[1] for c in C.CLUBS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len({c[0] for c in C.CLUBS}), len(C.CLUBS))


class CatalogIsNotCoverage(Base):
    def make(self, owner=OWNER, **kw):
        import sport_bot as S
        self.reg = SRC.SourceRegistry(self.tmp, SRC.build_source_defs(S.CLUBS, False))
        return super().make(owner=owner, registry=self.reg, **kw)

    def test_catalog_club_without_source_is_gap_and_tracked_clubs_follow_new_tournaments(self):
        for key in ("zenit", "bayern", "nhl_bos", "uralmash"):
            self.assertIn(key, C.CLUB_BY_KEY)
            self.assertEqual(self.reg.club_coverage(key)[0], SRC.GAP, key)
        self.assertEqual(self.reg.club_coverage("milan")[0], SRC.COVERED)
        self.assertEqual(self.reg.competition_coverage("uel")[0], SRC.COVERED)           # через Милан (отслеживается)
        self.assertEqual(self.reg.competition_coverage("uecl")[0], SRC.GAP)
        self.assertEqual(self.reg.competition_coverage("nhl")[0], SRC.GAP)
        self.assertEqual(self.reg.competition_coverage("vtb")[0], SRC.GAP)

    async def test_owner_sees_catalog_snapshot_and_gap_for_selected_catalog_club(self):
        await self.join(5, "А")
        await self.onboard(5, sports=("football",), comps=("uecl",), clubs=("ajax",))
        await self.press(OWNER, "adm:src:cov")
        self.assertIn("сезон 2026/27, проверен 2026-10-03", self.last_text())
        await self.press(OWNER, "adm:src:gap")
        self.assertIn("Аякс (клуб) — 1 · выбран из списка", self.last_text())
        self.assertIn("Лига конференций (чемпионат) — 1", self.last_text())

    async def test_competition_coverage_lists_only_tracked_clubs_not_whole_tournament(self):
        await self.press(OWNER, "adm:src:cov")
        text = self.last_text()
        comps = text.split("Чемпионаты:\n", 1)[1].split("\n\nКлубы:", 1)[0].splitlines()
        by_name = {l[2:].split(" — ")[0].strip(): l for l in comps}
        self.assertEqual(by_name["КХЛ"], "✅ КХЛ — покрывается: Автомобилист")
        self.assertEqual(by_name["АПЛ"], "✅ АПЛ — покрывается: Арсенал")
        self.assertEqual(by_name["Ла Лига"], "✅ Ла Лига — покрывается: Реал Мадрид")
        self.assertEqual(by_name["Серия А"], "✅ Серия А — покрывается: Милан")
        self.assertEqual(by_name["Первая лига"], "✅ Первая лига — покрывается: Урал")
        self.assertEqual(by_name["Лига Европы"], "✅ Лига Европы — покрывается: Милан")
        self.assertEqual(by_name["Лига чемпионов"], "✅ Лига чемпионов — покрываются: Арсенал, Реал Мадрид")
        self.assertTrue(by_name["Суперлига"].startswith("✅ Суперлига — покрывается: "))
        self.assertIn("Синара", by_name["Суперлига"])
        for gap in ("NHL", "Лига конференций", "Единая лига ВТБ"):
            self.assertEqual(by_name[gap], f"⚠️ {gap} — надёжного источника нет")
        self.assertTrue(any(n.startswith("РПЛ") and "надёжного источника нет" in l and l.startswith("⚠️") for n, l in by_name.items()))
        self.assertNotIn("есть рабочий источник", "\n".join(comps))
        self.assertIn("✅ Автомобилист — есть рабочий источник", text.split("Клубы:\n", 1)[1])


if __name__ == "__main__":
    unittest.main()
