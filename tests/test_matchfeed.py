"""Парсеры единого формата матча и нормализация клубов: по одному случаю на турнир (реальные страницы сезона 2026/27 в tests/fixtures/feed)."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import club_aliases as A          # noqa: E402
import feed as F                  # noqa: E402
import matchfeed as M             # noqa: E402
import tribun_catalog as C        # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
NOW = datetime.datetime(2026, 10, 3, 10, 0, tzinfo=datetime.timezone.utc)


def read(name: str) -> str:
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def day_matches(sport: str, fixture: str, day: str):
    return M.parse_sportsru_day(sport, read("feed/" + fixture), datetime.date.fromisoformat(day), source_url="u", retrieved_at=NOW)


def pick(matches, home, away):
    found = [m for m in matches if m["home_team_id"] == home and m["away_team_id"] == away]
    assert len(found) == 1, (home, away, [(m["home_team_id"], m["away_team_id"]) for m in matches])
    return found[0]


class HockeyAndBasketball(unittest.TestCase):
    def test_khl_finished_with_overtime_score_home_away_and_utc_time(self):
        matches, stats = day_matches("hockey", "hockey_2026-10-02.txt", "2026-10-02")
        m = pick(matches, "neftekhimik_h", "barys")
        self.assertEqual((m["competition"], m["status"], m["score_home"], m["score_away"], m["method"]), ("khl", M.FINISHED, 4, 5, "ОТ"))
        self.assertEqual(m["kickoff"], datetime.datetime(2026, 10, 2, 16, 0, tzinfo=datetime.timezone.utc))      # 19:00 мск
        self.assertEqual((m["home_team"], m["source_timezone"], m["sport"], m["season"]), ("Нефтехимик", "Europe/Moscow", "hockey", "2026/27"))
        self.assertEqual(stats["unknown_teams"], [])
        a = pick(matches, "avtomobilist", "amur")
        self.assertEqual((a["score_home"], a["score_away"], a["method"]), (3, 0, "ОСНОВНОЕ"))

    def test_overtime_and_shootout_mark_stands_next_to_the_winners_score(self):
        # живая страница 03.10 (КХЛ): при победе ХОЗЯЕВ пометка «б/от» идёт ПЕРЕД счётом, при победе гостей — ПОСЛЕ (см. «Нефтехимик 4:5 от Барыс»)
        matches, stats = day_matches("hockey", "hockey_2026-10-03_evening.txt", "2026-10-03")
        khl = [m for m in matches if m["competition"] == "khl"]
        self.assertEqual(len(khl), 5)                                                       # раньше блок КХЛ терялся целиком
        self.assertEqual(stats["skipped_records"], 0)
        a = pick(matches, "cska_h", "dinamo_msk_h")
        self.assertEqual((a["score_home"], a["score_away"], a["method"], a["status"]), (4, 3, "БУЛЛИТЫ", M.FINISHED))
        b = pick(matches, "ska", "admiral")
        self.assertEqual((b["score_home"], b["score_away"], b["method"]), (6, 5, "БУЛЛИТЫ"))
        c = pick(matches, "severstal", "shanghai")
        self.assertEqual((c["score_home"], c["score_away"], c["method"]), (2, 1, "ОТ"))
        d = pick(matches, "dinamo_mn_h", "lokomotiv_h")
        self.assertEqual((d["score_home"], d["score_away"], d["method"]), (4, 3, "ОСНОВНОЕ"))
        e = pick(matches, "sochi_h", "torpedo_h")
        self.assertEqual((e["status"], e["score_home"], e["score_away"]), (M.LIVE, 0, 3))
        away_win = day_matches("hockey", "hockey_2026-10-02.txt", "2026-10-02")[0]
        self.assertEqual(pick(away_win, "neftekhimik_h", "barys")["method"], "ОТ")           # пометка после счёта по-прежнему читается

    def test_one_unreadable_record_does_not_lose_the_rest_of_the_block(self):
        lines = ["FONBET Чемпионат КХЛ 2026/2027", "(1)", "17:00", "Завершен", "СКА", "4", ":", "3", "Адмирал",
                 "18:00", "Завершен", "???", "странная", "запись", "без", "счёта",
                 "19:00", "Завершен", "Авангард", "2", ":", "1", "Лада",
                 "НХЛ 2026/2027", "01:30", "Завершен", "Детройт", "0", ":", "2", "Рейнджерс"]
        matches, stats = M.parse_sportsru_day("hockey", "\n".join(lines), datetime.date(2026, 10, 3), source_url="u", retrieved_at=NOW)
        self.assertEqual([(m["home_team_id"], m["away_team_id"]) for m in matches], [("ska", "admiral"), ("avangard", "lada"), ("nhl_det", "nhl_nyr")])
        self.assertEqual(stats["skipped_records"], 1)                                        # потеря видна в статистике (и в логе через отчёт источника)

    def test_record_that_fails_to_parse_does_not_abort_the_block(self):
        lines = ["FONBET Чемпионат КХЛ 2026/2027", "17:00", "Завершен", "СКА", "4", ":", "3", "Адмирал",
                 "18:30", "Завершен", "7", ":", "1",
                 "19:00", "Завершен", "Авангард", "2", ":", "1", "Лада"]
        matches, stats = M.parse_sportsru_day("hockey", "\n".join(lines), datetime.date(2026, 10, 3), source_url="u", retrieved_at=NOW)
        self.assertEqual([(m["home_team_id"], m["away_team_id"]) for m in matches], [("ska", "admiral"), ("avangard", "lada")])
        self.assertGreaterEqual(stats["skipped_records"], 1)

    def test_khl_live_and_future_matches(self):
        matches, _ = day_matches("hockey", "hockey_2026-10-03.txt", "2026-10-03")
        live = pick(matches, "severstal", "shanghai")
        self.assertEqual((live["status"], live["score_home"], live["score_away"]), (M.LIVE, 1, 1))
        future = pick(matches, "sochi_h", "torpedo_h")
        self.assertEqual((future["status"], future["score_home"], future["score_away"]), (M.SCHEDULED, None, None))
        self.assertEqual(future["kickoff"], datetime.datetime(2026, 10, 3, 16, 30, tzinfo=datetime.timezone.utc))   # 19:30 мск
        self.assertEqual(future["day"], datetime.date(2026, 10, 3))

    def test_nhl_from_sportsru_day_page_uses_short_russian_names(self):
        matches, stats = day_matches("hockey", "hockey_2026-10-04.txt", "2026-10-04")
        nhl = [m for m in matches if m["competition"] == "nhl"]
        self.assertGreaterEqual(len(nhl), 10)
        m = pick(matches, "nhl_phi", "nhl_car")
        self.assertEqual((m["status"], m["home_team"]), (M.SCHEDULED, "Филадельфия"))
        self.assertEqual(stats["unknown_teams"], [])
        # ночное (по Москве) время NHL принадлежит московской дате страницы
        self.assertEqual(m["kickoff"], datetime.datetime(2026, 10, 3, 23, 0, tzinfo=datetime.timezone.utc))

    def test_vtb_finished_scores(self):
        matches, stats = day_matches("basketball", "basketball_2026-09-30.txt", "2026-09-30")
        m = pick(matches, "avtodor", "uralmash")
        self.assertEqual((m["competition"], m["status"], m["score_home"], m["score_away"]), ("vtb", M.FINISHED, 75, 80))
        self.assertEqual(m["kickoff"], datetime.datetime(2026, 9, 30, 15, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(pick(matches, "unics", "dinamo_vl")["score_home"], 117)
        self.assertEqual(stats["unknown_teams"], [])


class Football(unittest.TestCase):
    def test_apl_scheduled_and_finished(self):
        future, stats = day_matches("football", "football_2026-10-10.txt", "2026-10-10")
        m = pick(future, "arsenal", "leeds")
        self.assertEqual((m["competition"], m["status"], m["score_home"]), ("apl", M.SCHEDULED, None))
        self.assertEqual(m["kickoff"], datetime.datetime(2026, 10, 10, 11, 30, tzinfo=datetime.timezone.utc))        # 14:30 мск
        self.assertEqual(stats["unknown_teams"], [])
        past, _ = day_matches("football", "football_2026-09-20.txt", "2026-09-20")
        fin = pick(past, "man_city", "sunderland")
        self.assertEqual((fin["status"], fin["score_home"], fin["score_away"], fin["day"]), (M.FINISHED, 5, 3, datetime.date(2026, 9, 20)))
        self.assertIsNone(fin["kickoff"])                                                                                 # время завершённого матча источник не показывает

    def test_each_football_competition_is_recognized(self):
        seen = {}
        for fixture, day in (("football_2026-10-10.txt", "2026-10-10"), ("football_2026-09-20.txt", "2026-09-20"),
                             ("football_2026-10-21.txt", "2026-10-21"), ("football_2026-10-22.txt", "2026-10-22")):
            matches, stats = day_matches("football", fixture, day)
            self.assertEqual(stats["unknown_teams"], [], fixture)
            for m in matches:
                seen[m["competition"]] = seen.get(m["competition"], 0) + 1
        for comp in ("rpl", "apl", "laliga", "seriea", "ucl", "uel", "uecl"):
            self.assertGreater(seen.get(comp, 0), 0, comp)

    def test_european_cup_names_map_to_catalog_ids_and_qualifier_noise_is_ignored(self):
        matches, _ = day_matches("football", "football_2026-10-21.txt", "2026-10-21")
        ucl = [m for m in matches if m["competition"] == "ucl"]
        self.assertEqual(len(ucl), 9)
        self.assertTrue(all(m["home_team_id"] and m["away_team_id"] for m in ucl))
        self.assertFalse(any("Первый матч" in m["home_team"] + m["away_team"] for m in matches))

    def test_two_leg_round_hints_are_not_teams(self):
        lines = ["Лига Конференций", "Европа", "Завершен", "Первый матч: 4-1", "Аякс", "Брага", "2", "0", "Трансляция",
                 "Завершен", "Первый матч: 0-0", "Монако", "Тун", "1", "1", "Трансляция"]
        matches, stats = M.parse_sportsru_day("football", "\n".join(lines), datetime.date(2026, 8, 12), source_url="u", retrieved_at=NOW)
        self.assertEqual([(m["home_team_id"], m["away_team_id"], m["score_home"], m["score_away"]) for m in matches],
                         [("ajax", "braga", 2, 0), ("monaco", "thun", 1, 1)])
        self.assertEqual(stats["unknown_teams"], [])

    def test_rpl_dinamo_means_moscow_dinamo_and_non_catalog_tournaments_are_ignored(self):
        matches, _ = day_matches("football", "football_2026-10-10.txt", "2026-10-10")
        rpl = [m for m in matches if m["competition"] == "rpl"]
        self.assertEqual(len(rpl), 4)
        self.assertEqual({m["competition"] for m in matches}, {"rpl", "apl", "laliga", "seriea"})


class NhlApi(unittest.TestCase):
    def setUp(self):
        self.raw = read("feed/nhl_schedule_2026-10-02.json")

    def test_official_api_finished_and_future_games(self):
        matches, stats = M.parse_nhl_schedule(self.raw, source_url="u", retrieved_at=NOW)
        self.assertEqual(stats["unknown_teams"], [])
        det = pick(matches, "nhl_det", "nhl_nyr")
        self.assertEqual((det["status"], det["score_home"], det["score_away"], det["source_timezone"]), (M.FINISHED, 0, 2, "UTC"))
        self.assertEqual(det["kickoff"], datetime.datetime(2026, 10, 2, 22, 30, tzinfo=datetime.timezone.utc))
        self.assertEqual(det["day"], datetime.date(2026, 10, 3))                                                         # 01:30 мск — московская дата следующего дня
        self.assertIn(M.SCHEDULED, {m["status"] for m in matches})
        self.assertIn(M.FINISHED, {m["status"] for m in matches})

    def test_broken_payload_is_a_format_error_not_an_empty_list(self):
        with self.assertRaises(M.SourceFormatError):
            M.parse_nhl_schedule('{"unexpected": 1}', source_url="u", retrieved_at=NOW)
        with self.assertRaises(M.SourceFormatError):
            M.parse_nhl_schedule("<html>captcha</html>", source_url="u", retrieved_at=NOW)

    def test_abbreviations_cover_every_catalog_club_once(self):
        self.assertEqual(sorted(A.NHL_ABBREV.values()), sorted(C.comp_club_keys("nhl")))
        self.assertEqual(len(A.NHL_ABBREV), 32)


class FutsalAndFirstLeague(unittest.TestCase):
    def test_superliga_league_calendar_uses_moscow_time(self):
        matches, stats = M.parse_superliga_calendar(read("feed/superliga_main.txt"), datetime.date(2026, 10, 3), source_url="u", retrieved_at=NOW)
        self.assertEqual(stats["unknown_teams"], [])
        m = [x for x in matches if (x["home_team_id"], x["away_team_id"]) == ("sibiryak", "iraero") and x["day"] == datetime.date(2026, 10, 2)][0]
        self.assertEqual((m["competition"], m["status"], m["score_home"], m["score_away"]), ("superliga", M.FINISHED, 4, 4))
        self.assertEqual(m["kickoff"], datetime.datetime(2026, 10, 2, 12, 0, tzinfo=datetime.timezone.utc))              # 15:00 мск
        self.assertEqual(m["source_timezone"], "Europe/Moscow")
        sin = [x for x in matches if (x["home_team_id"], x["away_team_id"]) == ("norilsk", "sinara")]
        self.assertEqual(sorted(x["status"] for x in sin), [M.FINISHED, M.SCHEDULED])                 # матч 03.10 сыгран, 04.10 впереди
        played = [x for x in sin if x["status"] == M.FINISHED][0]
        self.assertEqual((played["score_home"], played["score_away"]), (2, 3))
        scheduled = [x for x in matches if x["status"] == M.SCHEDULED]
        self.assertTrue(scheduled)
        self.assertTrue(all(x["score_home"] is None for x in scheduled))
        self.assertTrue(all(x["home_team_id"] and x["away_team_id"] for x in matches))

    def test_superliga_torpedo_is_futsal_torpedo(self):
        matches, _ = M.parse_superliga_calendar(read("feed/superliga_main.txt"), datetime.date(2026, 10, 3), source_url="u", retrieved_at=NOW)
        ids = {m["home_team_id"] for m in matches} | {m["away_team_id"] for m in matches}
        self.assertIn("torpedo_fz", ids)
        self.assertNotIn("torpedo", ids)

    def test_first_league_club_page_home_away_and_scores(self):
        matches, stats = M.parse_sportsru_club_calendar(read("sportsru_ural.txt"), "ural", sport="football", source_url="u", retrieved_at=NOW)
        self.assertEqual(stats["unknown_teams"], [])
        home = pick(matches, "ural", "enisey")
        self.assertEqual((home["competition"], home["status"], home["score_home"], home["score_away"]), ("fnl1", M.FINISHED, 3, 0))
        away = pick(matches, "leningradets", "ural")
        self.assertEqual((away["score_home"], away["score_away"]), (0, 0))                       # «хозяева : гости» независимо от клуба-владельца страницы
        guest = pick(matches, "ska_khb", "ural")
        self.assertEqual((guest["score_home"], guest["score_away"]), (0, 3))
        self.assertTrue(any(m["status"] == M.SCHEDULED and m["kickoff"] is None for m in matches))       # заглушка 03:00 = время не назначено

    def test_slugs_cover_every_first_league_club(self):
        self.assertEqual(sorted(F.FNL1_SLUGS), sorted(C.comp_club_keys("fnl1")))


class ClubNormalization(unittest.TestCase):
    def test_same_name_means_different_clubs_in_different_competitions(self):
        self.assertEqual(A.resolve("rpl", "Динамо"), "dinamo_msk")
        self.assertEqual(A.resolve("khl", "Динамо Москва"), "dinamo_msk_h")
        self.assertEqual(A.resolve("khl", "Торпедо"), "torpedo_h")
        self.assertEqual(A.resolve("fnl1", "Торпедо"), "torpedo")
        self.assertEqual(A.resolve("superliga", "Торпедо"), "torpedo_fz")
        self.assertEqual(A.resolve("khl", "Локомотив"), "lokomotiv_h")
        self.assertEqual(A.resolve("rpl", "Локомотив"), "lokomotiv")
        self.assertEqual(A.resolve("vtb", "ЦСКА"), "cska_b")

    def test_forms_prefixes_quotes_and_dashes(self):
        self.assertEqual(A.resolve("khl", "ХК «Автомобилист»"), "avtomobilist")
        self.assertEqual(A.resolve("fnl1", "СКА-Хабаровск"), "ska_khb")
        self.assertEqual(A.resolve("fnl1", "СКА Хабаровск"), "ska_khb")
        self.assertEqual(A.resolve("laliga", "Реал Мадрид"), "real")
        self.assertEqual(A.resolve("uel", "Ред Булл"), "salzburg")

    def test_yo_is_normalized(self):
        self.assertEqual(A.norm("Шахтёр"), A.norm("Шахтер"))
        self.assertEqual(A.resolve("ucl", "Шахтёр Донецк"), "shakhtar")
        self.assertEqual(A.resolve("ucl", "Шахтер"), "shakhtar")

    def test_no_prefix_or_partial_matching(self):
        for comp, bad in (("apl", "Арс"), ("apl", "Арсенал Л"), ("laliga", "Реал"), ("laliga", "Барс"), ("seriea", "Мил")):
            self.assertIsNone(A.resolve(comp, bad), bad)

    def test_no_fuzzy_matching(self):
        for bad in ("Зенитт", "Арсенал Лондон", "Реал", "Спартак Москва ", ""):
            self.assertIsNone(A.resolve("rpl", bad) if bad != "Арсенал Лондон" else A.resolve("apl", bad), bad)
        self.assertIsNone(A.resolve("nonexistent", "Зенит"))

    def test_every_catalog_club_of_every_competition_is_resolvable_exactly(self):
        for comp in C.SEASONS:
            keys = C.comp_club_keys(comp)
            table = A.ALIASES[comp]
            self.assertEqual(set(keys) - set(table.values()), set(), comp)
            by_key = {}
            for name, key in table.items():
                by_key.setdefault(key, set()).add(name)
            for key in keys:
                self.assertTrue(by_key[key], (comp, key))
            # у одного названия один клуб (словарь это гарантирует) и алиас не уводит в клуб чужого турнира
            self.assertEqual(set(table.values()) - set(keys), set(), comp)

    def test_unverified_alias_is_listed_and_exists(self):
        for comp, ids in A.UNVERIFIED.items():
            for key in ids:
                self.assertIn(key, C.comp_club_keys(comp))
                self.assertIn(key, A.ALIASES[comp].values())


if __name__ == "__main__":
    unittest.main()
