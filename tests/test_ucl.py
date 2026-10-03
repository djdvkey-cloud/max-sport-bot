"""Лига чемпионов 2026/27 на реальных данных: сыгранный игровой день (8–10.09), будущие дни (13–14.10), сверка sports.ru ↔ Википедия."""
import asyncio
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MAX_BOT_TOKEN", "test-token")

import club_aliases as A          # noqa: E402
import feed as F                  # noqa: E402
import matchfeed as M             # noqa: E402
import sources as SRC             # noqa: E402
import tribun_catalog as C        # noqa: E402
import tribun_hooks as H          # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "feed")
NOW = datetime.datetime(2026, 10, 3, 10, 0, tzinfo=datetime.timezone.utc)


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def ucl_day(fixture, day):
    matches, stats = M.parse_sportsru_day("football", read(fixture), datetime.date.fromisoformat(day), source_url="u", retrieved_at=NOW)
    return [m for m in matches if m["competition"] == "ucl"], stats


def score_of(matches, home, away):
    found = [m for m in matches if (m["home_team_id"], m["away_team_id"]) == (home, away)]
    assert len(found) == 1, (home, away)
    m = found[0]
    return m["status"], m["score_home"], m["score_away"]


# реальные результаты матчдня 1, сезон 2026/27 (sports.ru и Википедия называют одно и то же)
PLAYED = {
    "2026-09-08": {("aek", "lask"): (1, 0), ("dortmund", "villarreal"): (3, 2), ("club_brugge", "aston_villa"): (2, 3), ("lille", "betis"): (2, 3),
                   ("porto", "man_city"): (0, 2), ("real", "inter"): (2, 1)},
    "2026-09-09": {("barcelona", "feyenoord"): (5, 1), ("liverpool", "atletico"): (2, 1), ("napoli", "arsenal"): (0, 1), ("psg", "slovan"): (6, 1),
                   ("sporting", "galatasaray"): (3, 1), ("stuttgart", "viking"): (3, 1)},
    "2026-09-10": {("bayern", "bodo"): (5, 0), ("como", "leipzig"): (4, 1), ("man_united", "sabah"): (4, 0), ("psv", "shakhtar"): (1, 1),
                   ("slavia", "lens"): (2, 3), ("fenerbahce", "roma"): (1, 1)},
}


class SportsRuPlayedAndUpcoming(unittest.TestCase):
    def test_played_matchdays_results_home_away_and_normalization(self):
        for day, expected in PLAYED.items():
            matches, stats = ucl_day(f"football_{day}.txt", day)
            self.assertEqual(len(matches), 6, day)
            self.assertEqual(stats["unknown_teams"], [], day)                                   # неопознанных команд штатного каталога — 0
            self.assertTrue(all(m["home_team_id"] and m["away_team_id"] and m["competition"] == "ucl" for m in matches), day)
            self.assertEqual({m["day"] for m in matches}, {datetime.date.fromisoformat(day)})
            for (home, away), (sh, sa) in expected.items():
                self.assertEqual(score_of(matches, home, away), (M.FINISHED, sh, sa), (home, away))     # хозяева слева, счёт «хозяева : гости»

    def test_twelve_clubs_per_matchday_all_distinct(self):
        for day in PLAYED:
            matches, _ = ucl_day(f"football_{day}.txt", day)
            ids = [m["home_team_id"] for m in matches] + [m["away_team_id"] for m in matches]
            self.assertEqual(len(set(ids)), 12, day)

    def test_upcoming_schedule_dates_times_and_clubs(self):
        matches, stats = ucl_day("football_2026-10-13.txt", "2026-10-13")
        self.assertEqual(len(matches), 9)
        self.assertEqual(stats["unknown_teams"], [])
        self.assertTrue(all(m["status"] == M.SCHEDULED and m["kickoff"] and m["score_home"] is None for m in matches))
        lens = [m for m in matches if (m["home_team_id"], m["away_team_id"]) == ("lens", "sporting")][0]
        self.assertEqual(lens["kickoff"], datetime.datetime(2026, 10, 13, 16, 45, tzinfo=datetime.timezone.utc))          # 19:45 мск
        arsenal = [m for m in matches if (m["home_team_id"], m["away_team_id"]) == ("arsenal", "lille")][0]
        self.assertEqual(arsenal["kickoff"], datetime.datetime(2026, 10, 13, 19, 0, tzinfo=datetime.timezone.utc))        # 22:00 мск
        self.assertEqual(arsenal["day"], datetime.date(2026, 10, 13))

    def test_unknown_team_is_reported_never_silently_accepted(self):
        lines = ["Лига чемпионов", "Европа", "Завершен", "Арсенал", "Неизвестный клуб", "2", "0", "Трансляция"]
        matches, stats = M.parse_sportsru_day("football", "\n".join(lines), datetime.date(2026, 9, 9), source_url="u", retrieved_at=NOW)
        self.assertEqual([(m["home_team_id"], m["away_team_id"]) for m in matches], [("arsenal", None)])
        self.assertEqual(stats["unknown_teams"], [("ucl", "Неизвестный клуб")])
        lines = ["Лига чемпионов", "Европа", "Завершен", "Левая команда", "Чужой клуб", "2", "0", "Трансляция"]
        matches, stats = M.parse_sportsru_day("football", "\n".join(lines), datetime.date(2026, 9, 9), source_url="u", retrieved_at=NOW)
        self.assertEqual((matches, stats["dropped_unknown"]), ([], 1))                                  # квалификации/чужие клубы отбрасываются и считаются


class WikipediaCrosscheckSource(unittest.TestCase):
    def setUp(self):
        self.html = read("wikipedia_ucl_league_phase.html")

    def test_league_phase_table_is_parsed_fully(self):
        matches, stats = M.parse_wikipedia_ucl(self.html, source_url="u", retrieved_at=NOW)
        self.assertEqual(len(matches), 144)                                                     # 36 клубов × 8 матчей / 2
        self.assertEqual(stats["unknown_teams"], [])
        self.assertEqual(sum(m["status"] == M.FINISHED for m in matches), 18)
        counts = {}
        for m in matches:
            for k in (m["home_team_id"], m["away_team_id"]):
                counts[k] = counts.get(k, 0) + 1
        self.assertEqual(set(counts), set(C.comp_club_keys("ucl")))
        self.assertEqual(set(counts.values()), {8})
        first = [m for m in matches if (m["home_team_id"], m["away_team_id"]) == ("aek", "lask")][0]
        self.assertEqual((first["status"], first["score_home"], first["score_away"], first["day"]), (M.FINISHED, 1, 0, datetime.date(2026, 9, 8)))
        self.assertEqual(first["kickoff"], datetime.datetime(2026, 9, 8, 16, 45, tzinfo=datetime.timezone.utc))              # 18:45 CEST

    def test_english_names_cover_exactly_the_catalog_clubs(self):
        self.assertEqual(sorted(A.WIKI_UCL.values()), sorted(C.comp_club_keys("ucl")))
        self.assertEqual(len(A.WIKI_UCL), 36)

    def test_central_european_time_summer_and_winter(self):
        self.assertEqual(M._central_european_offset(datetime.date(2026, 10, 24)), 2)
        self.assertEqual(M._central_european_offset(datetime.date(2026, 10, 25)), 1)
        self.assertEqual(M._central_european_offset(datetime.date(2027, 3, 27)), 1)
        self.assertEqual(M._central_european_offset(datetime.date(2027, 3, 28)), 2)

    def test_unknown_name_and_broken_page(self):
        box = ('<div class="footballbox"><div class="fleft"><time><div class="fdate">1 Nov 2026<span class="bday dtstart published updated itvstart">2026-11-01</span>'
               '</div><div class="ftime">21:00</div></time></div><table class="fevent"><tr><th class="fhome"><span>Arsenal</span></th>'
               '<th class="fscore">v</th><th class="faway"><span>Unknown FC</span></th></tr></table></div>')
        matches, stats = M.parse_wikipedia_ucl("<html>" + box + "</html>", source_url="u", retrieved_at=NOW)
        self.assertEqual(stats["unknown_teams"], [("ucl", "Unknown FC")])
        self.assertEqual((matches[0]["home_team_id"], matches[0]["away_team_id"], matches[0]["status"]), ("arsenal", None, M.SCHEDULED))
        self.assertEqual(matches[0]["kickoff"], datetime.datetime(2026, 11, 1, 20, 0, tzinfo=datetime.timezone.utc))          # CET, зима
        with self.assertRaises(M.SourceFormatError):
            M.parse_wikipedia_ucl("<html>captcha</html>", source_url="u", retrieved_at=NOW)


class FakeFetch:
    def __init__(self, routes):
        self.routes, self.calls = dict(routes), []

    async def __call__(self, url):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                return value
        return 404, ""


def ucl_routes(**over):
    routes = {f"/football/match/{d}/": (200, read(f"football_{d}.txt")) for d in ("2026-09-08", "2026-09-09", "2026-09-10", "2026-10-13", "2026-10-14")}
    routes["wikipedia.org"] = (200, read("wikipedia_ucl_league_phase.html"))
    routes.update(over)
    return routes


def feed_with(routes):
    fetch = FakeFetch(routes)
    return F.MatchFeed(fetch=fetch, now=lambda: NOW), fetch


class CrosscheckedChain(unittest.TestCase):
    def setUp(self):
        H.reset()

    def collect(self, f, day):
        return asyncio.run(f.collect(datetime.date.fromisoformat(day), competitions={"ucl"}))

    def test_played_days_are_confirmed_by_the_independent_source(self):
        f, _ = feed_with(ucl_routes())
        for day in PLAYED:
            res = self.collect(f, day)
            self.assertEqual(len(res.matches), 6, day)
            self.assertTrue(all(m["confidence"] == "confirmed" and m["trusted"] for m in res.matches), day)
            self.assertTrue(all(m["sources"] == ["sportsru:center:football", "wikipedia:ucl"] for m in res.matches))
            self.assertEqual(res.failed_competitions, set())

    def test_upcoming_days_schedule_is_cross_checked_too(self):
        f, _ = feed_with(ucl_routes())
        res = self.collect(f, "2026-10-13")
        self.assertEqual((len(res.matches), {m["confidence"] for m in res.matches}), (9, {"confirmed"}))
        res = self.collect(f, "2026-10-14")
        self.assertEqual(len(res.matches), 9)
        odd = [m for m in res.matches if m["confidence"] == "time_differs"]
        self.assertEqual([(m["home_team_id"], m["away_team_id"]) for m in odd], [("shakhtar", "aek")])                        # реальное расхождение источников на час
        self.assertTrue(odd[0]["trusted"])
        self.assertIn("wikipedia:ucl", odd[0]["other_kickoffs"])

    def test_wrong_score_in_independent_source_is_a_conflict(self):
        html = read("wikipedia_ucl_league_phase.html")
        self.assertIn("1–0", html)
        wrong = html.replace('<th class="fscore">1–0</th>', '<th class="fscore">1–1</th>', 1)
        f, _ = feed_with(ucl_routes(**{"wikipedia.org": (200, wrong)}))
        res = self.collect(f, "2026-09-08")
        bad = [m for m in res.matches if (m["home_team_id"], m["away_team_id"]) == ("aek", "lask")][0]
        self.assertEqual((bad["confidence"], bad["trusted"], bad["score_home"], bad["score_away"]), ("conflict", False, 1, 0))
        self.assertEqual(sum(m["confidence"] == "confirmed" for m in res.matches), 5)

    def test_home_away_swapped_in_one_source_is_not_confirmed(self):
        html = read("wikipedia_ucl_league_phase.html")
        swapped = html.replace(">AEK Athens</a>", ">@@</a>", 1).replace(">LASK</a>", ">AEK Athens</a>", 1).replace(">@@</a>", ">LASK</a>", 1)
        self.assertTrue(swapped != html)
        f, _ = feed_with(ucl_routes(**{"wikipedia.org": (200, swapped)}))
        res = self.collect(f, "2026-09-08")
        self.assertEqual(sum(m["confidence"] == "confirmed" for m in res.matches), 5)

    def test_independent_source_down_leaves_primary(self):
        f, _ = feed_with(ucl_routes(**{"wikipedia.org": (503, "")}))
        res = self.collect(f, "2026-09-08")
        self.assertEqual((len(res.matches), {m["confidence"] for m in res.matches}), (6, {"single"}))
        self.assertEqual([r.source_id for r in res.reports if r.outcome == F.FAILED], ["wikipedia:ucl"])
        self.assertEqual(res.failed_competitions, set())

    def test_primary_down_falls_back_to_independent_source(self):
        f, _ = feed_with(ucl_routes(**{"/football/match/2026-09-08/": (500, "")}))
        res = self.collect(f, "2026-09-08")
        self.assertEqual(len(res.matches), 6)
        self.assertTrue(all(m["source_id"] == "wikipedia:ucl" for m in res.matches))
        self.assertEqual(res.failed_competitions, set())

    def test_wikipedia_is_cached_between_requests(self):
        f, fetch = feed_with(ucl_routes())
        for day in PLAYED:
            self.collect(f, day)
        self.assertEqual(len([u for u in fetch.calls if "wikipedia.org" in u]), 1)

    def test_probe_lines_report_played_and_upcoming_matchdays(self):
        f, fetch = feed_with(ucl_routes())
        lines = asyncio.run(F.ucl_probe(f, NOW))
        text = "\n".join(lines)
        self.assertIn("сыграны ['2026-09-08', '2026-09-09', '2026-09-10'], впереди ['2026-10-13', '2026-10-14']", text)
        for day in PLAYED:
            line = [x for x in lines if f"UCL {day}:" in x][0]
            self.assertIn("матчей 6, клубов распознано 12, неопознанных 0 []", line)
            self.assertIn("сверено с Википедией 6, расхождений времени 0, конфликтов 0", line)
        self.assertIn("Реал Мадрид 2:1 Интер", text)
        self.assertIn("Наполи 0:1 Арсенал", text)
        up = [x for x in lines if "UCL 2026-10-14:" in x][0]
        self.assertIn("клубов распознано 18", up)
        self.assertIn("РАСХОЖДЕНИЕ ВРЕМЕНИ: Шахтёр Донецк—АЕК Афины", up)
        self.assertIn("Ланс—Спортинг 19:45 мск", "\n".join(lines))

    def test_probe_survives_unavailable_calendar(self):
        f, _ = feed_with(ucl_routes(**{"wikipedia.org": (403, "")}))
        lines = asyncio.run(F.ucl_probe(f, NOW))
        self.assertEqual(len(lines), 1)
        self.assertIn("календарь Википедии недоступен", lines[0])


class PilotScopeOfTheFeed(unittest.TestCase):
    def setUp(self):
        H.reset()

    def test_default_collect_and_smoke_never_touch_deferred_directions(self):
        f, fetch = feed_with(ucl_routes(**{"/hockey/": (200, read("hockey_2026-10-03.txt")), "api-web.nhle.com": (200, read("nhl_schedule_2026-10-02.json")),
                                          "superliga.rfs.ru": (200, read("superliga_main.txt")), "/football/club/": (200, read("../sportsru_ural.txt"))}))
        asyncio.run(F.smoke(f, NOW))
        self.assertFalse([u for u in fetch.calls if "/basketball/" in u])
        self.assertEqual(sorted({c for c in C.COMP_BY_KEY}), sorted({"khl", "nhl", "rpl", "fnl1", "apl", "laliga", "seriea", "ucl", "superliga"}))

    def test_deferred_adapters_still_work_when_asked_explicitly(self):
        f, fetch = feed_with({"/basketball/match/2026-09-30/": (200, read("basketball_2026-09-30.txt"))})
        res = asyncio.run(f.collect(datetime.date(2026, 9, 30), competitions={"vtb"}))
        self.assertEqual(len(res.matches), 2)
        self.assertEqual(len(fetch.calls), 1)

    def test_watchlist_ignores_stale_basketball_and_europa_selections(self):
        f, fetch = feed_with(ucl_routes())
        stale = {"active_in_group": True, "sports": ["football", "basketball"], "championships": ["ucl", "vtb", "uel"], "clubs": ["real", "zenit_b", "benfica"]}
        rows = asyncio.run(f.watchlist([stale], datetime.date(2026, 9, 8)))
        self.assertEqual({m["competition"] for m in rows}, {"ucl"})
        self.assertFalse([u for u in fetch.calls if "/basketball/" in u])
        self.assertEqual(rows[0]["club_fans"], 1)
        self.assertEqual((rows[0]["home_team_id"], rows[0]["away_team_id"]), ("real", "inter"))
        agg = F.MatchFeed.aggregate_interests([stale])
        self.assertEqual((agg["sports"], agg["competitions"], agg["clubs"]), ({"football": 1}, {"ucl": 1}, {"real": 1}))


class RegistryTreatsWikipediaAsIndependentMedia(unittest.TestCase):
    def test_wikipedia_is_crosscheck_not_primary(self):
        import test_sport_v4 as TS
        import tempfile
        reg = SRC.SourceRegistry(tempfile.mkdtemp(prefix="ucl-reg-"), SRC.build_source_defs(TS.S.CLUBS))
        m = reg.competition_matrix("ucl")
        self.assertEqual((m["status"], m["covered"], m["total"], m["crosscheck"]), (SRC.COVERED, 36, 36, True))
        self.assertEqual(m["primary"], "Sports.ru — матч-центр (футбол)")
        self.assertEqual(reg.defs["wikipedia:ucl"].source_type, SRC.MEDIA)


if __name__ == "__main__":
    unittest.main()
