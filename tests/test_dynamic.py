"""Динамический контур: ACTIVE CLUBS из интересов группы обслуживаются так же, как шесть исторических клубов (афиша, утро, изменения, результаты).
Источники подменены реальными страницами сезона 2026/27 (tests/fixtures/feed); в реальную группу ничего не отправляется."""
import asyncio
import datetime
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_sport_v4 as TS  # noqa: E402
from test_sport_v4 import S, FakeBot, ekb, utc, sportsru_page  # noqa: E402
from test_tribun import GROUP, user  # noqa: E402
import dynamic as D  # noqa: E402
import feed as F  # noqa: E402
import matchfeed as M  # noqa: E402
import tribun_catalog as C  # noqa: E402

FIXDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "feed")
LEGACY_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fx(name, base=FIXDIR):
    with open(os.path.join(base, name), encoding="utf-8") as f:
        return f.read()


class FakeFetch:
    def __init__(self, routes):
        self.routes, self.calls = list(routes.items()), []

    def set(self, key, value):
        self.routes = [(k, v) for k, v in self.routes if k != key]
        self.routes.insert(0, (key, value))

    async def __call__(self, url):
        self.calls.append(url)
        for key, value in self.routes:
            if key in url:
                return value
        return 404, ""


def all_routes() -> dict:
    """Все сохранённые страницы по датам + запасные «пустые, но узнаваемые» страницы для остальных дней (источник жив, матчей нет)."""
    routes = {}
    for name in sorted(os.listdir(FIXDIR)):
        m = re.match(r"^(football|hockey|basketball)_(\d{4}-\d\d-\d\d)\.txt$", name)
        if m:
            routes[f"/{m.group(1)}/match/{m.group(2)}/"] = (200, fx(name))
    routes["api-web.nhle.com"] = (200, fx("nhl_schedule_2026-10-02.json"))
    routes["superliga.rfs.ru"] = (200, fx("superliga_main.txt"))
    routes["wikipedia.org"] = (200, fx("wikipedia_ucl_league_phase.html"))
    routes["/football/club/"] = (200, fx("sportsru_ural.txt", LEGACY_FIX))
    for sport in ("football", "hockey", "basketball"):
        routes[f"/{sport}/match/"] = (200, f"{sport}\nМоя лента\nМатчи\nВсе матчи\nМой выбор")
    return routes


# страница клуба sports.ru без матчей на этой неделе: один давно сыгранный матч (узнаваемая вёрстка, «календарь доступен, матчей нет»)
QUIET_PAGE = sportsru_page([("01.09.2026", "17:00", "Лига", "Соперник", "Дома", ["1 : 0", "–"])])


def profile(sports, champs, clubs, active=True, uid=1):
    return {"user_id": uid, "active_in_group": active, "sports": list(sports), "championships": list(champs), "clubs": list(clubs)}


class DynBase(TS.Base):
    def setUp(self):
        super().setUp()
        self._patch(S, "DATA_DIR", self.tmp)
        self.profiles = []
        self.fetch = FakeFetch(all_routes())
        self.feed = F.MatchFeed(fetch=self.fetch, now=lambda: utc(2026, 10, 3, 10))
        self.dyn = D.Dynamic(S, feed=self.feed, profiles=lambda: self.profiles, baseline=())      # изолируем логику выбора участников: постоянные клубы — отдельные тесты
        self._patch(S, "DYNAMIC", self.dyn)
        self._patch(S, "pick_intro", lambda kind, rng=None: "ВВОДНАЯ")

    def route(self, key, value):
        """Подмена страницы источника; кэш страниц сбрасывается — следующий запрос увидит новое содержимое."""
        self.fetch.set(key, value)
        self.feed.clear_cache()

    def admin_texts(self):
        return [m["text"] for m in self.bot.admin]

    def seed(self, rec):
        state = self.dyn.load_state()
        state["matches"][rec["match_id"]] = rec
        self.dyn.save_state(state)

    async def match_of(self, day, comp, clubs, home=None, away=None):
        res = await self.feed.collect(datetime.date.fromisoformat(day), competitions={comp}, clubs=clubs)
        found = [m for m in res.matches if (home is None or m["home_team_id"] == home) and (away is None or m["away_team_id"] == away)]
        assert len(found) == 1, (day, comp, home, away, len(found))
        return found[0]

    async def seed_finished(self, day, comp, club, home, away, start_utc, now_clubs=None):
        """Запись «анонсирован», как после утреннего прогона, для сыгранного матча реальных данных."""
        m = await self.match_of(day, comp, {club}, home, away)
        rec = self.dyn.new_record(m, self.dyn.describe(m, {club}), "")
        rec.update({"start_utc": start_utc.isoformat(), "status": "announced", "announce_sent": True, "announce_text": None})
        self.seed(rec)
        return rec


# ==================================================================== ACTIVE CLUBS

class ActiveClubs(DynBase):
    def test_new_user_gives_new_active_club(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        act = self.dyn.active()
        self.assertEqual((act["dynamic"], act["legacy"], act["fans"]), ({"zenit"}, set(), {"zenit": 1}))

    def test_two_users_make_a_union_and_shared_club_is_one_active_club(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit", "spartak"], uid=1), profile(["football", "hockey"], ["rpl", "khl"], ["zenit", "sibir"], uid=2)]
        act = self.dyn.active()
        self.assertEqual(act["dynamic"], {"zenit", "spartak", "sibir"})
        self.assertEqual(act["fans"], {"zenit": 2, "spartak": 1, "sibir": 1})                   # болельщики — только статистика

    def test_user_leaving_removes_only_his_unique_clubs(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit", "spartak"], uid=1), profile(["football"], ["rpl"], ["zenit"], uid=2)]
        self.assertEqual(self.dyn.active()["dynamic"], {"zenit", "spartak"})
        self.profiles[0]["active_in_group"] = False                                             # вышел из группы
        self.assertEqual(self.dyn.active()["dynamic"], {"zenit"})                               # общий клуб остался
        self.profiles[1]["active_in_group"] = False
        self.assertEqual(self.dyn.active()["dynamic"], set())
        self.profiles[0]["active_in_group"] = True                                              # вернулся
        self.assertEqual(self.dyn.active()["dynamic"], {"zenit", "spartak"})

    def test_hidden_basketball_europa_conference_selections_are_not_active(self):
        self.profiles = [profile(["football", "basketball"], ["rpl", "uel", "uecl", "vtb"], ["zenit", "benfica", "ajax", "zenit_b"])]
        self.assertEqual(self.dyn.active()["dynamic"], {"zenit"})

    def test_club_without_selected_championship_or_sport_is_not_active(self):
        self.profiles = [profile(["football"], [], ["zenit"]), profile([], ["rpl"], ["spartak"], uid=2)]
        self.assertEqual(self.dyn.active()["dynamic"], set())

    def test_club_of_two_championships_is_one_active_club_served_in_both(self):
        self.profiles = [profile(["football"], ["apl", "ucl"], ["man_city"])]
        act = self.dyn.active()
        self.assertEqual(act["dynamic"], {"man_city"})
        self.assertEqual(D.competitions_of(act["dynamic"]), {"apl", "ucl"})

    def test_baseline_clubs_are_always_active_and_members_add_to_them(self):
        dyn = D.Dynamic(S, feed=self.feed, profiles=lambda: self.profiles)                       # постоянные клубы — шесть клубов sport_bot.CLUBS
        self.profiles = []
        act = dyn.active()
        self.assertEqual(act["dynamic"], {"avtomobilist", "sinara", "ural", "real", "arsenal", "milan"})
        self.assertEqual(act["legacy"], act["dynamic"])
        self.assertEqual(set(act["fans"].values()), {0})                                         # болельщиков нет — клубы всё равно активны
        self.profiles = [profile(["football", "hockey"], ["apl", "khl", "rpl"], ["arsenal", "zenit"])]      # выбор участника
        act = dyn.active()
        self.assertEqual(act["dynamic"], {"avtomobilist", "sinara", "ural", "real", "arsenal", "milan", "zenit"})
        self.assertEqual((act["fans"]["arsenal"], act["fans"]["zenit"], act["fans"]["milan"]), (1, 1, 0))
        self.profiles[0]["active_in_group"] = False                                              # участник вышел: постоянные остаются, Зенит снят
        self.assertEqual(dyn.active()["dynamic"], {"avtomobilist", "sinara", "ural", "real", "arsenal", "milan"})

    def test_active_clubs_come_from_the_real_members_file_without_any_config_or_deploy(self):
        from test_tribun import Base as TribunBase

        class Real(TribunBase):
            async def run_it(self):
                await self.join(5, "Иван")
                await self.onboard(5, sports=("football",), comps=("rpl",), clubs=("zenit",))
                return self.tmp
        case = Real("run_it")
        case.setUp()
        data_dir = asyncio.run(case.run_it())
        with mock.patch.object(S, "DATA_DIR", data_dir):
            real = D.Dynamic(S, feed=self.feed, baseline=())
            self.assertEqual(real.active()["dynamic"], {"zenit"})                              # сохранил профиль → клуб появился сам
            asyncio.run(case.tribun.on_user_removed(GROUP, user(5)))
            self.assertEqual(real.active()["dynamic"], set())                                  # вышел → снят

    def test_other_is_never_an_active_club(self):
        from test_tribun import Base as TribunBase

        class Real(TribunBase):
            async def run_it(self):
                await self.join(6, "Пётр")
                await self.onboard(6, sports=("football",), comps=("rpl",), clubs=(), other={"cl": "Бока Хуниорс"})
                return self.tmp
        case = Real("run_it")
        case.setUp()
        data_dir = asyncio.run(case.run_it())
        with mock.patch.object(S, "DATA_DIR", data_dir):
            self.assertEqual(D.Dynamic(S, feed=self.feed, baseline=()).active()["dynamic"], set())

    def test_relevance_rules(self):
        async def go():
            m = await self.match_of("2026-10-10", "apl", {"leeds"}, "arsenal", "leeds")
            self.assertTrue(self.dyn.relevant(m, {"leeds"}))                                     # достаточно ОДНОГО активного клуба в матче
            self.assertTrue(self.dyn.relevant(m, {"arsenal"}))
            n = await self.match_of("2026-10-10", "apl", {"fulham"}, "ipswich", "fulham")
            self.assertTrue(self.dyn.relevant(n, {"fulham"}))
            self.assertFalse(self.dyn.relevant(n, {"chelsea"}))
        asyncio.run(go())


# ==================================================================== афиша

class Weekly(DynBase):
    async def test_zenit_appears_in_weekly_with_real_nearest_match(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        entries, failed = await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        self.assertEqual(failed, set())
        self.assertEqual([(e["club_name"], e["rival"], e["date"], e["time"], e["zone"], e["tournament"]) for e in entries],
                         [("Зенит", "Краснодар", "2026-10-10", "19:30", "мск", "РПЛ / Премьер-лига")])
        text = S.format_weekly(entries)
        self.assertEqual(text, "📅 Наши матчи на этой неделе\n\n⚽ Зенит — Краснодар\n🗓 Суббота · 19:30 (мск)")

    async def test_unknown_time_is_clarified_and_two_active_clubs_are_one_line(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit", "krasnodar"])]
        entries, _ = await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        self.assertEqual(len(entries), 1)                                                        # матч двух активных клубов — один раз
        self.assertTrue(entries[0]["two"])
        self.assertEqual((entries[0]["club_name"], entries[0]["rival"]), ("Краснодар", "Зенит"))
        entries[0]["time"] = None
        self.assertIn("🗓 Суббота · время уточняется", S.format_weekly(entries))

    async def test_nothing_is_sent_when_no_active_club_has_matches(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        for club in S.CLUBS:
            self.pages[club["extract_urls"][0]] = QUIET_PAGE
        self.route("/football/match/2026-10-10/", (200, "football\nМатчи\nВсе матчи"))
        self.assertTrue(await S.job_weekly(self.bot, ekb(2026, 10, 5, 9).astimezone(TS.UTC)))
        self.assertEqual(self.bot.group, [])

    async def test_no_active_clubs_means_no_requests_and_legacy_weekly_unchanged(self):
        self.profiles = []
        entries, failed = await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        self.assertEqual((entries, failed, self.fetch.calls), ([], set(), []))

    async def test_failed_competition_is_reported_and_is_not_an_empty_week(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        for club in S.CLUBS:
            self.pages[club["extract_urls"][0]] = QUIET_PAGE
        self.route("/football/match/", (503, ""))
        self.assertFalse(await S.job_weekly(self.bot, ekb(2026, 10, 5, 9).astimezone(TS.UTC)))      # афиша не помечена выполненной — повторим
        self.assertEqual(self.bot.group, [])
        self.assertTrue(any("РПЛ / Премьер-лига" in t for t in self.admin_texts()))
        self.assertEqual(S.last_weekly_date(), None)

    async def test_weekly_skips_finished_and_cancelled_matches(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        entries, _ = await self.dyn.weekly_entries(ekb(2026, 9, 14, 9).astimezone(TS.UTC))      # неделя 14–20.09: Балтика — Зенит 16.09 уже сыгран
        self.assertEqual(entries, [])
        page = fx("football_2026-10-10.txt").replace("Не начался\nКраснодар\nЗенит\n19:30", "Отменен\nКраснодар\nЗенит\n19:30")
        self.assertNotEqual(page, fx("football_2026-10-10.txt"))
        self.route("/football/match/2026-10-10/", (200, page))
        entries, _ = await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        self.assertEqual(entries, [])

    async def test_weekly_persists_schedule_for_change_detection(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        sched = D.Dynamic(S, feed=self.feed, profiles=lambda: []).load_sched()                    # «после перезапуска»
        self.assertEqual([(e["club_key"], e["time"]) for e in sched["entries"]], [("zenit", "19:30")])


class WeeklyDedup(DynBase):
    def test_match_of_two_legacy_clubs_is_one_line(self):
        a = {"key": "k1", "club_key": "real", "sport": "football", "date": "2026-10-14", "time": "22:00", "zone": "мск", "tournament": "ЛЧ", "rival": "Арсенал"}
        b = {"key": "k2", "club_key": "arsenal", "sport": "football", "date": "2026-10-14", "time": "22:00", "zone": "мск", "tournament": "ЛЧ", "rival": "Реал Мадрид"}
        c = {"key": "k3", "club_key": "milan", "sport": "football", "date": "2026-10-14", "time": "22:00", "zone": "мск", "tournament": "ЛЕ", "rival": "Рома"}
        text = S.format_weekly([a, b, c])
        self.assertEqual(text.count("🗓"), 2)
        self.assertIn("«Реал Мадрид» — Арсенал", text)
        self.assertNotIn("«Арсенал» Лондон — Реал Мадрид", text)
        later = dict(b, date="2026-10-15")                                                        # тот же день недели — другая дата: это другой матч
        self.assertEqual(S.format_weekly([a, later]).count("🗓"), 2)

    def test_competitions_of_a_club_include_all_served_tournaments_but_not_unserved_ones(self):
        self.assertEqual(D.competitions_of({"sunderland"}), {"apl", "uel"})                       # клуб обслуживается во всех своих турнирах, включая Лигу Европы
        self.assertEqual(D.competitions_of({"milan"}), {"seriea", "uel"})
        self.assertEqual(D.competitions_of({"zenit_b"}), set())                                   # ВТБ адаптерами не обслуживается


# ==================================================================== утро и изменения

class Morning(DynBase):
    NOW = ekb(2026, 10, 10, 10, 0).astimezone(TS.UTC)

    async def test_zenit_morning_announcement(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        failed = await self.dyn.morning(self.bot, self.NOW)
        self.assertEqual(failed, [])
        self.assertEqual(self.bot.group, ["ВВОДНАЯ\n\n⚽ Сегодня играет Зенит\nТурнир: РПЛ / Премьер-лига\nВремя: 19:30 (мск)\nСоперник: Краснодар"])
        state = self.dyn.load_state()
        rec = list(state["matches"].values())[0]
        self.assertEqual((rec["status"], rec["announce_sent"], rec["start_utc"]), ("announced", True, "2026-10-10T16:30:00+00:00"))

    async def test_morning_twice_does_not_duplicate(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"], uid=1), profile(["football"], ["rpl"], ["zenit"], uid=2)]      # два болельщика одного клуба
        await self.dyn.morning(self.bot, self.NOW)
        await self.dyn.morning(self.bot, self.NOW)
        await self.dyn.morning(self.bot, self.NOW, with_intro=False)
        self.assertEqual(len(self.bot.group), 1)

    async def test_match_of_two_active_clubs_is_one_announcement(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"], uid=1), profile(["football"], ["rpl"], ["krasnodar"], uid=2)]
        await self.dyn.morning(self.bot, self.NOW)
        self.assertEqual(self.bot.group, ["ВВОДНАЯ\n\n⚽ Сегодня играют Краснодар — Зенит\nТурнир: РПЛ / Премьер-лига\nВремя: 19:30 (мск)"])
        self.assertEqual(len(self.dyn.load_state()["matches"]), 1)

    async def test_any_active_club_in_a_match_is_enough(self):
        self.profiles = [profile(["football"], ["apl"], ["leeds", "fulham"])]                    # Арсенал — Лидс (Лидс активен) и Ипсвич — Фулхэм
        await self.dyn.morning(self.bot, self.NOW)
        joined = "\n".join(self.bot.group)
        self.assertIn("Фулхэм", joined)
        self.assertIn("Лидс", joined)
        self.assertEqual(len(self.dyn.load_state()["matches"]), 2)

    async def test_intro_is_not_repeated_if_legacy_already_added_it(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        S.mark_intro(datetime.date(2026, 10, 10))
        await self.dyn.morning(self.bot, self.NOW)
        self.assertTrue(self.bot.group[0].startswith("⚽ Сегодня играет Зенит"))

    async def test_time_change_against_weekly_schedule(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        entries, _ = await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        sched = self.dyn.load_sched()
        sched["entries"][0]["time"] = "18:30"                                                    # афиша называла 18:30
        self.dyn.save_sched(sched)
        await self.dyn.morning(self.bot, self.NOW)
        self.assertEqual(self.bot.group, ["⚠️ Внимание, изменилось время матча!\n⚽ Зенит — Краснодар\nСегодня · 19:30 (мск)\n"
                                          "Ранее было указано 18:30 (мск).\nТурнир: РПЛ / Премьер-лига"])

    async def test_match_moved_to_today(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        sched = {"entries": [{"key": "dyn|rpl|2026-10-07|krasnodar|zenit", "competition": "rpl", "date": "2026-10-07", "time": "17:00", "zone": "мск",
                              "club_key": "zenit", "club_name": "Зенит", "rival": "Краснодар", "icon": "⚽", "sport": "football", "tournament": "РПЛ / Премьер-лига"}]}
        self.dyn.save_sched(sched)
        await self.dyn.morning(self.bot, self.NOW)
        self.assertEqual(len(self.bot.group), 1)
        self.assertTrue(self.bot.group[0].startswith("⚠️ Внимание, матч перенесён на сегодня!\n⚽ Зенит — Краснодар\nСегодня · 19:30 (мск)\nРанее: Среда, 7 октября · 17:00 (мск)"))
        self.assertEqual([e["key"] for e in self.dyn.load_sched()["entries"]], ["dyn|rpl|2026-10-10|krasnodar|zenit"])

    async def test_cancelled_and_postponed_matches_are_reported_once(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        page = fx("football_2026-10-10.txt").replace("Не начался\nКраснодар\nЗенит\n19:30", "Отменен\nКраснодар\nЗенит\n19:30")
        self.assertNotEqual(page, fx("football_2026-10-10.txt"))
        self.route("/football/match/2026-10-10/", (200, page))
        await self.dyn.morning(self.bot, self.NOW)
        await self.dyn.morning(self.bot, self.NOW)
        self.assertEqual(self.bot.group, ["⚠️ Внимание, матч отменён!\n⚽ Зенит — Краснодар\nБыл назначен на сегодня · 19:30 (мск)."])
        self.assertEqual(self.dyn.load_state()["matches"], {})

    async def test_postponed_status_without_new_date(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        page = fx("football_2026-10-10.txt").replace("Не начался\nКраснодар\nЗенит\n19:30", "Перенесен\nКраснодар\nЗенит\n19:30")
        self.route("/football/match/2026-10-10/", (200, page))
        await self.dyn.morning(self.bot, self.NOW)
        self.assertEqual(self.bot.group, ["⚠️ Внимание, матч перенесён!\n⚽ Зенит — Краснодар\nБыл назначен на сегодня · 19:30 (мск)\nНовая дата уточняется."])

    async def test_match_moved_away_from_today_is_found_in_the_next_days(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        today_page = fx("football_2026-10-10.txt").replace("Не начался\nКраснодар\nЗенит\n19:30\nТрансляция", "")
        self.route("/football/match/2026-10-10/", (200, today_page))
        moved = "Премьер-лига Россия (РПЛ)\nРоссия\nНе начался\nКраснодар\nЗенит\n18:00\nТрансляция"
        self.route("/football/match/2026-10-12/", (200, moved))
        await self.dyn.morning(self.bot, self.NOW)
        self.assertEqual(self.bot.group, ["⚠️ Внимание, матч перенесён!\n⚽ Зенит — Краснодар\nБыл назначен на сегодня · 19:30 (мск)\nТеперь: Понедельник, 12 октября · 18:00 (мск)"])

    async def test_missing_match_without_evidence_is_not_guessed(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        self.route("/football/match/2026-10-10/", (200, fx("football_2026-10-10.txt").replace("Не начался\nКраснодар\nЗенит\n19:30\nТрансляция", "")))
        await self.dyn.morning(self.bot, self.NOW)
        self.assertEqual(self.bot.group, [])

    async def test_source_failure_is_not_no_matches_and_is_retried(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        self.route("/football/match/2026-10-10/", (503, ""))
        failed = await self.dyn.morning(self.bot, self.NOW)
        self.assertEqual(failed, ["rpl"])
        self.assertEqual(self.bot.group, [])
        self.assertTrue(any("Не удалось проверить расписание на сегодня: РПЛ / Премьер-лига" in t for t in self.admin_texts()))
        self.assertEqual(self.dyn.load_state()["meta"]["morning_failed"], {"date": "2026-10-10", "comps": ["rpl"]})
        self.route("/football/match/2026-10-10/", (200, fx("football_2026-10-10.txt")))
        self.feed.clear_cache()
        await self.dyn.retry_morning(self.bot, self.NOW + datetime.timedelta(minutes=20))
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("Сегодня играет Зенит", self.bot.group[0])
        self.assertTrue(any("Расписание на сегодня получено" in t for t in self.admin_texts()))                 # одно «восстановилось»
        await self.dyn.retry_morning(self.bot, self.NOW + datetime.timedelta(minutes=40))
        self.assertEqual(len(self.bot.group), 1)

    async def test_unsent_announcement_is_retried_until_the_match_starts(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        self.bot.fail_group = True
        await self.dyn.morning(self.bot, self.NOW)
        self.assertEqual(self.bot.group, [])
        rec = list(self.dyn.load_state()["matches"].values())[0]
        self.assertFalse(rec["announce_sent"])
        self.assertTrue(any("Не удалось отправить в MAX анонс" in t for t in self.admin_texts()))
        self.bot.fail_group = False
        await self.dyn.retry_unsent(self.bot, self.NOW + datetime.timedelta(minutes=20))
        self.assertEqual(len(self.bot.group), 1)
        await self.dyn.retry_unsent(self.bot, self.NOW + datetime.timedelta(minutes=40))
        self.assertEqual(len(self.bot.group), 1)

    async def test_match_already_finished_by_morning_gets_no_announcement_but_a_result(self):
        self.profiles = [profile(["hockey"], ["nhl"], ["nhl_det"])]
        now = ekb(2026, 10, 3, 10, 0).astimezone(TS.UTC)
        await self.dyn.morning(self.bot, now)
        self.assertEqual(self.bot.group, [])                                                    # «Детройт — Рейнджерс» (01:30 мск) к утру сыгран: анонсировать нечего
        rec = list(self.dyn.load_state()["matches"].values())[0]
        self.assertEqual((rec["announce_sent"], rec["status"]), (True, "awaiting_result"))
        await self.dyn.results(self.bot, now)
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("Детройт Ред Уингз 0:2 Нью-Йорк Рейнджерс", self.bot.group[0])


# ==================================================================== результаты

class Results(DynBase):
    async def test_zenit_result_victory_published_once(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.dyn.morning(self.bot, ekb(2026, 10, 10, 10).astimezone(TS.UTC))
        self.assertEqual(len(self.bot.group), 1)
        finished = fx("football_2026-10-10.txt").replace("Не начался\nКраснодар\nЗенит\n19:30", "Завершен\nКраснодар\nЗенит\n1\n2")
        self.route("/football/match/2026-10-10/", (200, finished))
        self.feed.clear_cache()
        await self.dyn.results(self.bot, utc(2026, 10, 10, 18, 0))                               # через 1.5 ч после начала — рано
        self.assertEqual(len(self.bot.group), 1)
        await self.dyn.results(self.bot, utc(2026, 10, 10, 18, 40))
        self.assertEqual(self.bot.group[1], "🎆🎆🎆 ПОБЕДА!!!\n\n⚽ Краснодар 1:2 Зенит")
        await self.dyn.results(self.bot, utc(2026, 10, 10, 19, 0))
        await self.dyn.results(self.bot, utc(2026, 10, 10, 20, 0))
        self.assertEqual(len(self.bot.group), 2)                                                 # один матч — одна публикация
        rec = list(self.dyn.load_state()["matches"].values())[0]
        self.assertEqual((rec["status"], rec["result_sent"]), ("published", True))

    async def test_real_finished_zenit_match_defeat_and_draw_formats(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        rec = await self.seed_finished("2026-09-12", "rpl", "zenit", "zenit", "lokomotiv", utc(2026, 9, 12, 14, 0))
        await self.dyn.results(self.bot, utc(2026, 9, 12, 20, 0))
        self.assertEqual(self.bot.group, ["🎆🎆🎆 ПОБЕДА!!!\n\n⚽ Зенит 2:1 Локомотив"])
        rec2 = await self.seed_finished("2026-09-16", "rpl", "zenit", "baltika", "zenit", utc(2026, 9, 16, 15, 0))
        await self.dyn.results(self.bot, utc(2026, 9, 16, 20, 0))
        self.assertEqual(self.bot.group[1], "🎆🎆🎆 ПОБЕДА!!!\n\n⚽ Балтика 2:3 Зенит")
        rec3 = await self.seed_finished("2026-09-05", "rpl", "zenit", "zenit", "cska", utc(2026, 9, 5, 14, 0)) if False else None
        loss = await self.match_of("2026-09-12", "rpl", {"lokomotiv"}, "zenit", "lokomotiv")
        lrec = self.dyn.new_record(loss, self.dyn.describe(loss, {"lokomotiv"}), "")
        self.assertEqual(self.dyn.text_result(lrec, loss), "😔 Увы, сегодня проиграли\n\n⚽ Зенит 2:1 Локомотив")           # с точки зрения болельщика Локомотива

    async def test_hockey_overtime_and_shootout_tails(self):
        self.profiles = [profile(["hockey"], ["khl"], ["neftekhimik_h"])]
        rec = await self.seed_finished("2026-10-02", "khl", "neftekhimik_h", "neftekhimik_h", "barys", utc(2026, 10, 2, 16, 0))
        await self.dyn.results(self.bot, utc(2026, 10, 2, 20, 0))
        self.assertEqual(self.bot.group, ["😔 Увы, сегодня проиграли в овертайме\n\n🏒 Нефтехимик 4:5 ОТ Барыс"])
        self.profiles = [profile(["hockey"], ["khl"], ["cska_h"])]
        self.route("/hockey/match/2026-10-03/", (200, fx("hockey_2026-10-03_evening.txt")))
        await self.seed_finished("2026-10-03", "khl", "cska_h", "cska_h", "dinamo_msk_h", utc(2026, 10, 3, 14, 0))
        await self.dyn.results(self.bot, utc(2026, 10, 3, 20, 0))
        self.assertEqual(self.bot.group[1], "🎆🎆🎆 ПОБЕДА ПО БУЛЛИТАМ!!!\n\n🏒 ЦСКА 4:3 Б Динамо Москва")

    async def test_two_active_clubs_get_one_neutral_result(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"], uid=1), profile(["football"], ["rpl"], ["baltika"], uid=2)]
        await self.seed_finished("2026-09-16", "rpl", "zenit", "baltika", "zenit", utc(2026, 9, 16, 15, 0))
        m = await self.match_of("2026-09-16", "rpl", {"zenit", "baltika"}, "baltika", "zenit")
        rec = self.dyn.new_record(m, self.dyn.describe(m, {"zenit", "baltika"}), "")
        self.assertTrue(rec["two"])
        self.assertEqual(self.dyn.text_result(rec, m), "🏁 Результат матча\n\n⚽ Балтика 2:3 Зенит")

    async def test_announced_match_is_brought_to_result_after_last_fan_leaves(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.dyn.morning(self.bot, ekb(2026, 10, 10, 10).astimezone(TS.UTC))
        self.assertEqual(len(self.bot.group), 1)
        self.profiles = []                                                                       # последний болельщик снял клуб / вышел
        self.assertEqual(self.dyn.active()["dynamic"], set())
        finished = fx("football_2026-10-10.txt").replace("Не начался\nКраснодар\nЗенит\n19:30", "Завершен\nКраснодар\nЗенит\n1\n2")
        self.route("/football/match/2026-10-10/", (200, finished))
        self.feed.clear_cache()
        await self.dyn.results(self.bot, utc(2026, 10, 10, 19, 0))
        self.assertEqual(self.bot.group[1], "🎆🎆🎆 ПОБЕДА!!!\n\n⚽ Краснодар 1:2 Зенит")        # анонсированный матч не остаётся без итога
        # новые будущие матчи без болельщиков не попадают ни в афишу, ни в утро
        entries, _ = await self.dyn.weekly_entries(ekb(2026, 10, 12, 9).astimezone(TS.UTC))
        self.assertEqual(entries, [])
        await self.dyn.morning(self.bot, ekb(2026, 10, 17, 10).astimezone(TS.UTC))
        self.assertEqual(len(self.bot.group), 2)

    async def test_send_failure_keeps_result_and_retries_without_new_search(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.seed_finished("2026-09-16", "rpl", "zenit", "baltika", "zenit", utc(2026, 9, 16, 15, 0))
        self.bot.fail_group = True
        await self.dyn.results(self.bot, utc(2026, 9, 16, 20, 0))
        rec = list(self.dyn.load_state()["matches"].values())[0]
        self.assertEqual((rec["status"], rec["result_sent"], bool(rec["result_text"])), ("result_found", False, True))
        self.assertTrue(any("не удалось отправить его в MAX" in t for t in self.admin_texts()))
        self.fetch.calls.clear()
        self.bot.fail_group = False
        await self.dyn.results(self.bot, utc(2026, 9, 16, 20, 20))
        self.assertEqual(self.bot.group, ["🎆🎆🎆 ПОБЕДА!!!\n\n⚽ Балтика 2:3 Зенит"])
        self.assertEqual([u for u in self.fetch.calls if "/football/match/" in u], [])           # результат уже сохранён — источники повторно не опрашиваются
        self.assertTrue(any("получен и опубликован" in t for t in self.admin_texts()))

    async def test_state_survives_restart_and_never_republishes(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.seed_finished("2026-09-16", "rpl", "zenit", "baltika", "zenit", utc(2026, 9, 16, 15, 0))
        await self.dyn.results(self.bot, utc(2026, 9, 16, 20, 0))
        again = D.Dynamic(S, feed=F.MatchFeed(fetch=self.fetch, now=lambda: utc(2026, 9, 16, 22)), profiles=lambda: self.profiles)   # новый процесс
        await again.results(self.bot, utc(2026, 9, 17, 9, 0))
        await again.results(self.bot, utc(2026, 9, 17, 9, 20))
        self.assertEqual(len(self.bot.group), 1)

    async def test_source_failure_is_not_no_result_and_alerts_after_three_ticks(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.seed_finished("2026-09-16", "rpl", "zenit", "baltika", "zenit", utc(2026, 9, 16, 15, 0))
        self.route("/football/match/2026-09-16/", (503, ""))
        self.feed.clear_cache()
        for minute in (0, 20, 40):
            await self.dyn.results(self.bot, utc(2026, 9, 16, 18, minute))
        rec = list(self.dyn.load_state()["matches"].values())[0]
        self.assertEqual((rec["source_fail_ticks"], rec["status"]), (3, "awaiting_result"))
        self.assertTrue(any("источники недоступны" in t for t in self.admin_texts()))
        self.assertEqual(self.bot.group, [])
        self.route("/football/match/2026-09-16/", (200, fx("football_2026-09-16.txt")))
        await self.dyn.results(self.bot, utc(2026, 9, 16, 19, 0))
        self.assertEqual(len(self.bot.group), 1)

    async def test_match_not_finished_yet_is_not_published(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.dyn.morning(self.bot, ekb(2026, 10, 10, 10).astimezone(TS.UTC))
        await self.dyn.results(self.bot, utc(2026, 10, 10, 19, 0))                               # страница всё ещё «Не начался»
        self.assertEqual(len(self.bot.group), 1)
        rec = list(self.dyn.load_state()["matches"].values())[0]
        self.assertEqual(rec["status"], "awaiting_result")

    async def test_give_up_after_36_hours_with_one_alert(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.dyn.morning(self.bot, ekb(2026, 10, 10, 10).astimezone(TS.UTC))
        await self.dyn.results(self.bot, utc(2026, 10, 12, 9, 0))
        rec = list(self.dyn.load_state()["matches"].values())[0]
        self.assertEqual(rec["status"], "expired")
        self.assertEqual(len([t for t in self.admin_texts() if "так и не получен" in t]), 1)

    async def test_conflict_between_sources_is_never_published_as_fact(self):
        self.profiles = [profile(["football"], ["ucl"], ["man_city"])]
        await self.seed_finished("2026-09-08", "ucl", "man_city", "porto", "man_city", utc(2026, 9, 8, 16, 45))
        wrong = fx("wikipedia_ucl_league_phase.html")
        self.assertIn('<th class="fscore">0–2</th>', wrong)
        wrong = wrong.replace('<th class="fscore">0–2</th>', '<th class="fscore">1–2</th>', 1)
        self.route("wikipedia.org", (200, wrong))
        self.feed.clear_cache()
        await self.dyn.results(self.bot, utc(2026, 9, 8, 19, 0))
        await self.dyn.results(self.bot, utc(2026, 9, 8, 20, 5))
        self.assertEqual(self.bot.group, [])
        rec = list(self.dyn.load_state()["matches"].values())[0]
        self.assertEqual(rec["conflict"]["count"], 2)
        self.assertTrue(any("источники расходятся" in t for t in self.admin_texts()))
        self.route("wikipedia.org", (200, fx("wikipedia_ucl_league_phase.html")))              # источник исправлен → результат подтверждён и опубликован
        self.feed.clear_cache()
        await self.dyn.results(self.bot, utc(2026, 9, 8, 21, 0))
        self.assertEqual(self.bot.group, ["🎆🎆🎆 ПОБЕДА!!!\n\n⚽ Порту 0:2 Манчестер Сити"])

    async def test_one_match_found_by_two_sources_is_one_record_and_one_message(self):
        self.profiles = [profile(["hockey"], ["nhl"], ["nhl_det"])]
        await self.dyn.morning(self.bot, ekb(2026, 10, 3, 10).astimezone(TS.UTC))
        self.assertEqual(len(self.dyn.load_state()["matches"]), 1)                              # NHL API + sports.ru → одна запись
        await self.dyn.results(self.bot, utc(2026, 10, 3, 6, 0))
        await self.dyn.results(self.bot, utc(2026, 10, 3, 7, 0))
        self.assertEqual(len(self.bot.group), 1)

    async def test_match_closed_by_the_old_pipeline_is_never_republished(self):
        """Переход: прежний контур уже опубликовал / закрыл матч — единый контур его не заводит и не анонсирует заново."""
        dyn = D.Dynamic(S, feed=self.feed, profiles=lambda: [], baseline=("arsenal",))
        start = ekb(2026, 10, 10, 14, 30).astimezone(TS.UTC)
        self.put(self.rec("arsenal", start, rival="Лидс", status="published", day=datetime.date(2026, 10, 10), result_sent=True))
        await dyn.morning(self.bot, ekb(2026, 10, 10, 10).astimezone(TS.UTC))
        await dyn.results(self.bot, utc(2026, 10, 10, 20, 0))
        await dyn.recover(self.bot, utc(2026, 10, 10, 20, 0), force=True)
        await dyn.results(self.bot, utc(2026, 10, 10, 20, 30))
        self.assertEqual((self.bot.group, dyn.load_state()["matches"]), ([], {}))


# ==================================================================== приёмка: Зенит и контрольные клубы (dry-run без отправки)

class Acceptance(DynBase):
    async def dry(self, club, now, lookback=14):
        sent_before = len(self.bot.group) + len(self.bot.admin)
        out = await self.dyn.dry_run_club(club, now, lookback_days=lookback)
        self.assertEqual(len(self.bot.group) + len(self.bot.admin), sent_before)               # dry-run ничего не отправляет
        self.assertFalse(os.path.exists(self.dyn.state_path))                                  # и ничего не пишет в состояние
        self.assertFalse(os.path.exists(self.dyn.sched_path))
        return out

    async def test_zenit_full_cycle_without_config_changes(self):
        out = await self.dry("zenit", ekb(2026, 10, 5, 9).astimezone(TS.UTC), lookback=30)
        self.assertTrue(out["active"])
        self.assertIn("⚽ Зенит — Краснодар\n🗓 Суббота · 19:30 (мск)", out["weekly"])
        self.assertEqual((out["next_match"]["day"], out["next_match"]["home"], out["next_match"]["away"], out["next_match"]["time"]),
                         ("2026-10-10", "Краснодар", "Зенит", "19:30"))
        self.assertEqual(out["morning"], "⚽ Сегодня играет Зенит\nТурнир: РПЛ / Премьер-лига\nВремя: 19:30 (мск)\nСоперник: Краснодар")
        self.assertIn("Ранее было указано 20:30 (мск).", out["changes"])
        out2 = await self.dry("zenit", ekb(2026, 9, 17, 9).astimezone(TS.UTC), lookback=7)
        self.assertEqual(out2["result"]["day"], "2026-09-16")
        self.assertEqual(out2["result"]["text"], "🎆🎆🎆 ПОБЕДА!!!\n\n⚽ Балтика 2:3 Зенит")

    async def test_nhl_club_tampa_bay(self):
        out = await self.dry("nhl_tbl", ekb(2026, 10, 3, 9).astimezone(TS.UTC), lookback=3)
        self.assertIn("🏒 Тампа-Бэй Лайтнинг — Вашингтон Кэпиталз", out["weekly"])
        self.assertEqual(out["next_match"]["day"], "2026-10-04")
        self.assertIn("🏒 Сегодня играет Тампа-Бэй Лайтнинг", out["morning"])
        self.assertTrue(out["result"]["text"].startswith("😔 Увы, сегодня проиграли"))
        self.assertIn("Нью-Йорк Рейнджерс 5:1 Тампа-Бэй Лайтнинг", out["result"]["text"])

    async def test_khl_club_sibir(self):
        out = await self.dry("sibir", ekb(2026, 10, 3, 9).astimezone(TS.UTC), lookback=3)
        self.assertIn("🏒 Сибирь — Нефтехимик\n🗓 Воскресенье · 14:00 (мск)", out["weekly"])
        self.assertEqual(out["result"]["text"], "🎆🎆🎆 ПОБЕДА!!!\n\n🏒 Салават Юлаев 2:4 Сибирь")
        self.assertEqual(out["result"]["day"], "2026-10-02")

    async def test_apl_club_fulham(self):
        out = await self.dry("fulham", ekb(2026, 10, 5, 9).astimezone(TS.UTC), lookback=30)
        self.assertIn("⚽ Фулхэм — Ипсвич Таун\n🗓 Суббота · 17:00 (мск)", out["weekly"])
        self.assertEqual(out["morning"], "⚽ Сегодня играет Фулхэм\nТурнир: АПЛ\nВремя: 17:00 (мск)\nСоперник: Ипсвич Таун")
        out2 = await self.dry("fulham", ekb(2026, 9, 21, 9).astimezone(TS.UTC), lookback=3)
        self.assertEqual(out2["result"]["text"], "🤝 НИЧЬЯ!\n\n⚽ Фулхэм 1:1 Манчестер Юнайтед")

    async def test_champions_league_club_man_city_with_independent_crosscheck(self):
        out = await self.dry("man_city", ekb(2026, 10, 12, 9).astimezone(TS.UTC), lookback=3)
        self.assertIn("⚽ Манчестер Сити — ПСЖ\n🗓 Среда · 22:00 (мск)", out["weekly"])
        self.assertEqual(out["next_match"]["sources"], ["sportsru:center:football", "wikipedia:ucl"])
        out2 = await self.dry("man_city", ekb(2026, 9, 9, 9).astimezone(TS.UTC), lookback=3)
        self.assertEqual(out2["result"]["text"], "🎆🎆🎆 ПОБЕДА!!!\n\n⚽ Порту 0:2 Манчестер Сити")
        self.assertEqual((out2["result"]["confidence"], out2["result"]["trusted"]), ("confirmed", True))

    async def test_controls_are_picked_from_real_schedule_and_logged(self):
        lines = await self.dyn.controls_log(utc(2026, 10, 4, 9))
        text = "\n".join(lines)
        self.assertIn("[DYN] контрольный клуб Зенит (zenit)", text)
        self.assertGreaterEqual(len(lines), 3)
        self.assertNotIn("Traceback", text)


# ==================================================================== планировщик

class Wiring(DynBase):
    async def test_scheduler_runs_dynamic_parts_once_per_tick_and_sends_one_weekly_message(self):
        monday_10 = ekb(2026, 10, 5, 10, 0)
        self._patch(S, "utc_now", lambda: monday_10.astimezone(TS.UTC))
        self._patch(S, "DYN_LAST_MORNING_FILE", os.path.join(self.tmp, "dyn_last_morning.txt"))
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        for club in S.CLUBS:
            self.pages[club["extract_urls"][0]] = QUIET_PAGE
        self.pages[self.club("ural")["extract_urls"][0]] = sportsru_page([("10.10.2026", "12:00", "Первая лига", "Велес", "Дома", ["превью", "–"])])

        async def no_deepseek(question, sites, search_query=None, extract_urls=None):
            return "НЕТ", ""

        self._patch(S, "ask_deepseek", no_deepseek)
        calls = {"dyn_morning": 0, "dyn_results": 0}
        real_morning, real_results = self.dyn.morning, self.dyn.results

        async def counting_morning(*a, **k):
            calls["dyn_morning"] += 1
            return await real_morning(*a, **k)

        async def counting_results(*a, **k):
            calls["dyn_results"] += 1
            return await real_results(*a, **k)

        self.dyn.morning, self.dyn.results = counting_morning, counting_results

        class Stop(Exception):
            pass

        async def stop_sleep(seconds):
            raise Stop()

        self._patch(S.asyncio, "sleep", stop_sleep)
        with self.assertRaises(Stop):
            await S.scheduler_loop(self.bot)
        self.assertEqual(calls, {"dyn_morning": 1, "dyn_results": 1})
        self.assertEqual(len(self.bot.group), 1)                                                 # одна афиша: Урал + Зенит
        self.assertIn("Зенит — Краснодар", self.bot.group[0])
        self.assertEqual(S.load_dyn_last_morning(), datetime.date(2026, 10, 5))

    async def test_next_wakeup_accounts_for_dynamic_matches(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        await self.dyn.morning(self.bot, ekb(2026, 10, 10, 10).astimezone(TS.UTC))
        now = utc(2026, 10, 10, 18, 20)                                                          # матч в 16:30Z, первая проверка в 18:30Z — через 10 минут
        self.assertAlmostEqual(S.seconds_until_next_event(now), 10 * 60 + 1, delta=2)
        self.assertEqual(S.seconds_until_next_event(utc(2026, 10, 10, 17, 0)), S.CHECK_INTERVAL_SECONDS)     # дальше обычного интервала — спим интервал

class OwnerSummary(DynBase):
    def test_summary_lists_clubs_with_fans_as_statistics(self):
        dyn = D.Dynamic(S, feed=self.feed, profiles=lambda: self.profiles, baseline=("arsenal", "avtomobilist"))
        self.profiles = [profile(["football", "hockey"], ["rpl", "khl", "apl"], ["zenit", "avtomobilist", "arsenal"], uid=1),
                         profile(["football"], ["apl"], ["arsenal"], uid=2)]
        s = dyn.summary()
        self.assertEqual((s["total"], s["dynamic"], s["legacy"], s["members"]), (3, ["arsenal", "avtomobilist", "zenit"], ["arsenal", "avtomobilist"], 2))
        self.assertEqual(s["fans"], {"zenit": 1, "avtomobilist": 1, "arsenal": 2})


if __name__ == "__main__":
    unittest.main()
