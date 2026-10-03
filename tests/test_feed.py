"""Оркестратор расписаний: цепочка источников, слияние дублей, сверка, конфликт счёта, «источник недоступен ≠ матчей нет», здоровье в реестре."""
import asyncio
import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MAX_BOT_TOKEN", "test-token")

import feed as F                  # noqa: E402
import matchfeed as M             # noqa: E402
import sources as SRC             # noqa: E402
import tribun_catalog as C        # noqa: E402
import tribun_hooks as H          # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
NOW = datetime.datetime(2026, 10, 3, 10, 0, tzinfo=datetime.timezone.utc)
D3 = datetime.date(2026, 10, 3)


def read(name: str) -> str:
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class FakeFetch:
    """Подмена сети: ключ — подстрока адреса, значение — (статус, тело) или функция. Всё остальное — 404. Запоминает обращения."""
    def __init__(self, routes: dict):
        self.routes, self.calls = dict(routes), []

    async def __call__(self, url: str):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                return value(url) if callable(value) else value
        return 404, ""


def routes_ok(**over) -> dict:
    base = {
        "/hockey/match/2026-10-03/": (200, read("feed/hockey_2026-10-03.txt")),
        "/hockey/match/2026-10-04/": (200, read("feed/hockey_2026-10-04.txt")),
        "/football/match/2026-10-10/": (200, read("feed/football_2026-10-10.txt")),
        "/football/match/2026-10-03/": (200, read("feed/football_2026-10-10.txt").replace("Не начался", "Не начался")),
        "/basketball/match/": (200, read("feed/basketball_2026-09-30.txt")),
        "api-web.nhle.com": (200, read("feed/nhl_schedule_2026-10-02.json")),
        "superliga.rfs.ru": (200, read("feed/superliga_main.txt")),
        "/football/club/": (200, read("sportsru_ural.txt")),
    }
    base.update(over)
    return base


def run(coro):
    return asyncio.run(coro)


def feed_with(routes: dict) -> tuple:
    fetch = FakeFetch(routes)
    return F.MatchFeed(fetch=fetch, now=lambda: NOW), fetch


class Chain(unittest.TestCase):
    def setUp(self):
        H.reset()

    def test_nhl_game_from_two_independent_sources_is_one_confirmed_match(self):
        f, _ = feed_with(routes_ok())
        res = run(f.collect(D3, competitions={"nhl"}))
        det = [m for m in res.matches if (m["home_team_id"], m["away_team_id"]) == ("nhl_det", "nhl_nyr")]
        self.assertEqual(len(det), 1)                                                      # дубль из двух источников не создаёт два матча
        m = det[0]
        self.assertEqual((m["confidence"], m["trusted"], m["status"], m["score_home"], m["score_away"]), ("confirmed", True, M.FINISHED, 0, 2))
        self.assertEqual(m["sources"], ["nhl:api", "sportsru:center:hockey"])             # главный — официальный API
        self.assertEqual(m["source_id"], "nhl:api")
        self.assertEqual(res.failed_competitions, set())

    def test_score_conflict_is_not_trusted(self):
        wrong = read("feed/hockey_2026-10-03.txt").replace("'Детройт', '0'", "").replace("Детройт\n0\n:\n2\nРейнджерс", "Детройт\n1\n:\n2\nРейнджерс")
        self.assertNotEqual(wrong, read("feed/hockey_2026-10-03.txt"))
        f, _ = feed_with(routes_ok(**{"/hockey/match/2026-10-03/": (200, wrong)}))
        res = run(f.collect(D3, competitions={"nhl"}))
        m = [x for x in res.matches if (x["home_team_id"], x["away_team_id"]) == ("nhl_det", "nhl_nyr")][0]
        self.assertEqual((m["confidence"], m["trusted"]), ("conflict", False))
        self.assertEqual((m["score_home"], m["score_away"]), (0, 2))                      # показан счёт главного (официального) источника, но не как достоверный

    def test_primary_failure_falls_back_to_secondary_source(self):
        f, _ = feed_with(routes_ok(**{"api-web.nhle.com": (503, "")}))
        res = run(f.collect(D3, competitions={"nhl"}))
        self.assertGreater(len(res.matches), 0)
        self.assertTrue(all(m["source_id"] == "sportsru:center:hockey" and m["confidence"] == "single" for m in res.matches))
        failed = [r for r in res.reports if r.outcome == F.FAILED]
        self.assertEqual([r.source_id for r in failed], ["nhl:api"])
        self.assertEqual(failed[0].error, "HTTP 503")
        self.assertEqual(res.failed_competitions, set())                                    # у турнира остался рабочий источник

    def test_all_sources_down_is_a_failure_not_an_empty_day(self):
        f, _ = feed_with(routes_ok(**{"api-web.nhle.com": (500, ""), "/hockey/match/2026-10-03/": (403, "")}))
        res = run(f.collect(D3, competitions={"nhl"}))
        self.assertEqual(res.matches, [])
        self.assertEqual(res.failed_competitions, {"nhl"})
        self.assertTrue(all(r.outcome == F.FAILED for r in res.reports))

    def test_network_error_is_reported_with_reason(self):
        f, _ = feed_with(routes_ok(**{"superliga.rfs.ru": (None, "TimeoutError: ")}))
        res = run(f.collect(D3, competitions={"superliga"}))
        self.assertEqual(res.failed_competitions, {"superliga"})
        self.assertIn("нет связи", res.reports[0].error)

    def test_valid_page_without_catalog_matches_is_confirmed_empty_not_failure(self):
        f, _ = feed_with(routes_ok(**{"/football/match/2026-10-03/": (200, read("feed/football_2026-09-20.txt").replace("Премьер-лига Англия (АПЛ)", "Кубок Англии"))}))
        res = run(f.collect(D3, competitions={"apl"}))
        rep = [r for r in res.reports if r.source_id == "sportsru:center:football"][0]
        self.assertEqual(rep.outcome, F.OK if rep.matches else F.EMPTY)
        self.assertEqual(res.failed_competitions, set())

    def test_changed_layout_is_a_failure(self):
        f, _ = feed_with(routes_ok(**{"/football/match/2026-10-03/": (200, "<html><body>Что-то совсем другое\n\nНовая вёрстка</body></html>")}))
        res = run(f.collect(D3, competitions={"rpl"}))
        self.assertEqual(res.failed_competitions, {"rpl"})
        self.assertIn("вёрстка", res.reports[0].error)

    def test_captcha_page_is_a_failure_for_json_source(self):
        f, _ = feed_with(routes_ok(**{"api-web.nhle.com": (200, "<html>captcha</html>"), "/hockey/match/2026-10-03/": (404, "")}))
        res = run(f.collect(D3, competitions={"nhl"}))
        self.assertEqual(res.failed_competitions, {"nhl"})

    def test_nhl_api_is_asked_from_previous_day_because_moscow_night_games_belong_to_us_evening(self):
        f, fetch = feed_with(routes_ok())
        run(f.collect(D3, competitions={"nhl"}))
        nhl_calls = [u for u in fetch.calls if "api-web.nhle.com" in u]
        self.assertEqual(nhl_calls, ["https://api-web.nhle.com/v1/schedule/2026-10-02"])

    def test_collect_asks_only_sources_for_requested_competitions(self):
        f, fetch = feed_with(routes_ok())
        run(f.collect(D3, competitions={"vtb"}))
        self.assertEqual(len(fetch.calls), 1)
        self.assertIn("/basketball/match/2026-10-03/", fetch.calls[0])
        f, fetch = feed_with(routes_ok())
        run(f.collect(D3, competitions=set()))
        self.assertEqual(fetch.calls, [])

    def test_first_league_asks_only_selected_clubs(self):
        f, fetch = feed_with(routes_ok())
        run(f.collect(D3, competitions={"fnl1"}, clubs={"ural", "rotor"}))
        self.assertEqual(sorted(fetch.calls), sorted(["https://www.sports.ru/football/club/ural/calendar/", "https://www.sports.ru/football/club/rotor/calendar/"]))

    def test_first_league_match_from_both_clubs_pages_is_one_match(self):
        # у обоих клубов в календаре один и тот же матч Урал — Енисей: страница клуба Урал и страница клуба Енисей (тот же текст зеркалится)
        f, _ = feed_with(routes_ok())
        res_all = run(f.collect(datetime.date(2026, 7, 19), competitions={"fnl1"}, clubs={"ural", "yenisey"} & set(F.FNL1_SLUGS)))
        self.assertEqual(len([m for m in res_all.matches if m["day"] == datetime.date(2026, 7, 19)]), len({(m["home_team_id"], m["away_team_id"]) for m in res_all.matches}))

    def test_duplicate_inside_one_source_collapses(self):
        batch = M.parse_sportsru_club_calendar(read("sportsru_ural.txt"), "ural", sport="football", source_url="u", retrieved_at=NOW)[0]
        merged = F.merge_matches([batch, batch])
        self.assertEqual(len(merged), len(batch))
        self.assertTrue(all(m["confidence"] == "single" for m in merged))

    def test_same_pair_on_different_days_stays_two_matches(self):
        matches, _ = M.parse_superliga_calendar(read("feed/superliga_main.txt"), datetime.date(2026, 10, 3), source_url="u", retrieved_at=NOW)
        pair = [m for m in matches if (m["home_team_id"], m["away_team_id"]) == ("sibiryak", "iraero")]
        self.assertEqual(sorted(m["day"] for m in pair), [datetime.date(2026, 10, 2), datetime.date(2026, 10, 3)])
        self.assertEqual(len(F.merge_matches([matches])), len(matches))


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="feed-reg-")
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import test_sport_v4 as TS                      # задаёт окружение и импортирует sport_bot
        H.configure(self.tmp, SRC.build_source_defs(TS.S.CLUBS))
        self.addCleanup(H.reset)

    def test_health_is_written_by_real_requests(self):
        f, _ = feed_with(routes_ok(**{"api-web.nhle.com": (503, "")}))
        run(f.collect(D3, competitions={"nhl"}))
        self.assertEqual(H.REGISTRY.health("sportsru:center:hockey"), SRC.OK)
        self.assertEqual(H.REGISTRY.health("nhl:api"), SRC.DEGRADED)
        for _ in range(2):
            run(f.collect(D3, competitions={"nhl"}))
        self.assertEqual(H.REGISTRY.health("nhl:api"), SRC.FAILED_HEALTH)
        self.assertEqual(H.REGISTRY.competition_coverage("nhl")[0], SRC.COVERED)           # хоккейный матч-центр жив
        self.assertEqual(H.REGISTRY.critical_failures(), [row for row in H.REGISTRY.critical_failures() if row["def"].source_id == "nhl:api"])

    def test_confirmed_empty_keeps_source_healthy(self):
        f, _ = feed_with(routes_ok(**{"/hockey/match/2026-10-04/": (200, "Хоккей\nМоя лента\nМатчи 4 октября\nВсе матчи\nМой выбор")}))
        run(f.collect(datetime.date(2026, 10, 4), competitions={"khl"}))
        self.assertEqual(H.REGISTRY.health("sportsru:center:hockey"), SRC.OK)


class Interests(unittest.TestCase):
    def setUp(self):
        H.reset()

    def profiles(self):
        return [
            {"active_in_group": True, "sports": ["hockey"], "championships": ["khl"], "clubs": ["avtomobilist"]},
            {"active_in_group": True, "sports": ["hockey"], "championships": ["nhl"], "clubs": ["nhl_bos"]},
            {"active_in_group": True, "sports": ["hockey"], "championships": ["khl"], "clubs": []},
            {"active_in_group": False, "sports": ["football"], "championships": ["apl"], "clubs": ["arsenal"]},       # вышел — не считается
        ]

    def test_aggregate_has_no_names_and_ignores_inactive(self):
        agg = F.MatchFeed.aggregate_interests(self.profiles())
        self.assertEqual(agg["members"], 3)
        self.assertEqual(agg["competitions"], {"khl": 2, "nhl": 1})
        self.assertEqual(agg["clubs"], {"avtomobilist": 1, "nhl_bos": 1})
        self.assertNotIn("arsenal", agg["clubs"])

    def test_watchlist_is_interests_intersect_matches_today(self):
        f, fetch = feed_with(routes_ok())
        rows = run(f.watchlist(self.profiles(), D3))
        comps = {m["competition"] for m in rows}
        self.assertEqual(comps, {"khl", "nhl"})                                              # football-интересы вышедшего участника не запрашиваются
        self.assertFalse(any("/football/" in u for u in fetch.calls))
        self.assertTrue(all(m["interested"] >= 1 for m in rows))
        khl = [m for m in rows if m["competition"] == "khl"]
        self.assertTrue(all(m["interested"] == 2 for m in khl))                              # два участника следят за КХЛ
        self.assertEqual([m["interested"] for m in rows], sorted((m["interested"] for m in rows), reverse=True))

    def test_watchlist_by_club_only(self):
        f, _ = feed_with(routes_ok())
        rows = run(f.watchlist([{"active_in_group": True, "sports": [], "championships": [], "clubs": ["avtomobilist"]}], datetime.date(2026, 10, 4)))
        self.assertEqual([(m["home_team_id"], m["away_team_id"]) for m in rows], [("avtomobilist", "amur")])
        self.assertEqual(rows[0]["interested"], 1)

    def test_empty_group_asks_nothing(self):
        f, fetch = feed_with(routes_ok())
        self.assertEqual(run(f.watchlist([], D3)), [])
        self.assertEqual(fetch.calls, [])


class Smoke(unittest.TestCase):
    def setUp(self):
        H.reset()

    def test_smoke_lines_for_logs(self):
        f, _ = feed_with(routes_ok(**{"superliga.rfs.ru": (403, "")}))
        lines = run(F.smoke(f, NOW, offsets=(0,)))
        text = "\n".join(lines)
        self.assertIn("[FEED] 2026-10-03 nhl:api: ok", text)
        self.assertIn("[FEED] 2026-10-03 rfs:superliga: FAILED", text)
        self.assertIn("HTTP 403", text)
        self.assertIn("матчей по турнирам:", text)
        self.assertIn("турниры без ответа источников: ['superliga']", text)

    def test_default_smoke_checks_week_ahead_but_season_wide_sources_once(self):
        f, fetch = feed_with(routes_ok())
        lines = run(F.smoke(f, NOW))
        self.assertEqual(len([u for u in fetch.calls if "superliga.rfs.ru" in u]), 1)
        self.assertEqual(len([u for u in fetch.calls if "/football/club/" in u]), 18)
        self.assertTrue(any("/football/match/2026-10-10/" in u for u in fetch.calls))                   # через неделю — день с матчами футбольных лиг
        self.assertTrue(any("[FEED] 2026-10-10" in x and "матчей по турнирам:" in x and "apl=" in x for x in lines))


class HealthLoop(unittest.TestCase):
    def setUp(self):
        H.reset()
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import test_sport_v4 as TS
        self.S = TS.S

    def test_loop_prints_smoke_lines_and_survives_errors(self):
        from unittest import mock
        S = self.S
        printed, calls = [], []

        async def fake_smoke(feed_obj, *a, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("сеть")
            return ["[FEED] проверка ok"]

        async def fake_sleep(delay):
            if len(calls) >= 2:
                raise asyncio.CancelledError()

        async def go():
            with mock.patch.object(S.tribun_feed, "smoke", fake_smoke), mock.patch.object(S.asyncio, "sleep", fake_sleep),                     mock.patch("builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
                try:
                    await S.feed_health_loop()
                except asyncio.CancelledError:
                    pass
        asyncio.run(go())
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("проверка источников не выполнена: RuntimeError" in x for x in printed))
        self.assertIn("[FEED] проверка ok", printed)

    def test_main_starts_the_health_loop_next_to_the_scheduler(self):
        from unittest import mock
        S = self.S
        started = []

        async def fake_loop():
            started.append("loop")
            await asyncio.sleep(3600)

        async def nop(*a, **k):
            return None

        async def no_tasks(bot):
            return []

        async def scheduler(bot):
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        async def go():
            with mock.patch.object(S, "feed_health_loop", fake_loop), mock.patch.object(S, "run_tribun", no_tasks),                     mock.patch.object(S, "notify_startup", nop), mock.patch.object(S, "setup_tribun_hooks", lambda: None),                     mock.patch.object(S, "scheduler_loop", scheduler), mock.patch.object(S, "TRIBUN_ENABLED", True),                     mock.patch.object(S, "Bot", lambda token: object()):
                await S.main()
        asyncio.run(go())
        self.assertEqual(started, ["loop"])


if __name__ == "__main__":
    unittest.main()
