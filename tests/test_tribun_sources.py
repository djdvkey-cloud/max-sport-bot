"""Реестр источников: здоровье по реальным запросам, покрытие, разрывы для «➕ Другое», экраны владельца."""
import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_sport_v4 as TS  # noqa: E402
from test_sport_v4 import S, sportsru_page  # noqa: E402
from test_tribun import Base as TribunBase, T, OWNER, GROUP, user  # noqa: E402
import sources as SRC  # noqa: E402
import tribun_hooks as H  # noqa: E402
import tribun_catalog as C  # noqa: E402


def make_registry(tmp):
    return SRC.SourceRegistry(tmp, SRC.build_source_defs(S.CLUBS, openai_enabled=True))


class RegistryModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="src-")
        self.reg = make_registry(self.tmp)

    def test_registry_describes_existing_sources_and_hierarchy(self):
        ids = set(self.reg.defs)
        self.assertEqual({i for i in ids if i.startswith("calendar:")}, {f"calendar:{c['key']}" for c in S.CLUBS})
        self.assertIn("search:tavily", ids)
        self.assertIn("search:openai", ids)
        d = self.reg.defs["calendar:sinara"]
        self.assertEqual((d.source_type, d.sport, d.domain), (SRC.DIRECT, "futsal", "superliga.rfs.ru"))
        self.assertEqual(self.reg.defs["search:tavily"].source_type, SRC.SEARCH)
        self.assertLess(SRC.TYPE_RANK[SRC.OFFICIAL], SRC.TYPE_RANK[SRC.DIRECT])
        self.assertLess(SRC.TYPE_RANK[SRC.AGGREGATOR], SRC.TYPE_RANK[SRC.SEARCH])
        flat = str(self.reg.defs)
        for secret in (S.TAVILY_API_KEY, S.DEEPSEEK_API_KEY, S.MAX_BOT_TOKEN):
            self.assertNotIn(secret, flat)
        for ai in ("deepseek", "DeepSeek"):
            self.assertFalse([i for i in ids if ai in i])                     # AI — не источник факта

    def test_healthy_source(self):
        self.assertEqual(self.reg.health("calendar:ural"), SRC.UNKNOWN)
        self.reg.record("calendar:ural", SRC.OUTCOME_OK)
        self.assertEqual(self.reg.health("calendar:ural"), SRC.OK)
        st = self.reg.state("calendar:ural")
        self.assertEqual(st["consecutive_failures"], 0)
        self.assertTrue(st["last_success_at"])

    def test_repeated_failure_and_recovery(self):
        sid = "calendar:ural"
        self.reg.record(sid, SRC.OUTCOME_OK)
        self.reg.record(sid, SRC.OUTCOME_FAILED, "страница недоступна")
        self.assertEqual(self.reg.health(sid), SRC.DEGRADED)
        self.reg.record(sid, SRC.OUTCOME_FAILED, "страница недоступна")
        self.assertEqual(self.reg.health(sid), SRC.DEGRADED)
        before, after = self.reg.record(sid, SRC.OUTCOME_FAILED, "Bearer sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        self.assertEqual((before, after), (SRC.DEGRADED, SRC.FAILED_HEALTH))
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ", self.reg.state(sid)["last_error_short"])      # секреты в реестр не попадают
        self.assertEqual(self.reg.state(sid)["consecutive_failures"], 3)
        self.reg.record(sid, SRC.OUTCOME_OK)
        self.assertEqual(self.reg.health(sid), SRC.OK)
        self.assertEqual(self.reg.state(sid)["consecutive_failures"], 0)

    def test_confirmed_empty_is_not_a_failure(self):
        for _ in range(5):
            self.reg.record("calendar:ural", SRC.OUTCOME_EMPTY)
        self.assertEqual(self.reg.health("calendar:ural"), SRC.OK)
        self.assertEqual(self.reg.state("calendar:ural")["consecutive_failures"], 0)

    def test_health_survives_restart(self):
        self.reg.record("calendar:ural", SRC.OUTCOME_FAILED, "x")
        again = make_registry(self.tmp)
        self.assertEqual(again.health("calendar:ural"), SRC.DEGRADED)

    def test_corrupted_registry_is_not_overwritten(self):
        self.reg.record("calendar:ural", SRC.OUTCOME_OK)
        with open(self.reg.path, "w") as f:
            f.write("{broken")
        self.reg.record("calendar:ural", SRC.OUTCOME_OK)
        with open(self.reg.path) as f:
            self.assertEqual(f.read(), "{broken")
        self.assertEqual(self.reg.health("calendar:ural"), SRC.UNKNOWN)

    def test_critical_alert_only_when_no_fallback(self):
        for _ in range(3):
            self.reg.record("calendar:ural", SRC.OUTCOME_FAILED, "x")
        self.assertEqual(self.reg.health("calendar:ural"), SRC.FAILED_HEALTH)
        self.assertEqual(self.reg.critical_failures(), [])                    # поиск ещё не проверялся — это запасной путь, не критично
        self.reg.record("search:tavily", SRC.OUTCOME_OK)
        self.assertEqual(self.reg.critical_failures(), [])
        for _ in range(3):
            self.reg.record("search:tavily", SRC.OUTCOME_FAILED, "403")
        for _ in range(3):
            self.reg.record("search:openai", SRC.OUTCOME_FAILED, "x")
        crit = [r["def"].source_id for r in self.reg.critical_failures()]
        self.assertEqual(crit, ["calendar:ural"])                              # другие клубы не затронуты, поиск сам по себе не критичен

    def test_coverage_statuses(self):
        for sport in ("football", "hockey", "futsal"):
            self.assertEqual(self.reg.sport_coverage(sport)[0], SRC.COVERED, sport)
        for comp, total in (("khl", 22), ("nhl", 32), ("rpl", 16), ("fnl1", 18), ("apl", 20), ("laliga", 20), ("seriea", 20), ("ucl", 36), ("superliga", 12)):
            self.assertEqual(self.reg.competition_coverage(comp), (SRC.COVERED, f"{total}/{total} клубов"), comp)
        for comp in ("uel", "uecl", "vtb"):                                            # отложены: в активном реестре адаптеров нет
            self.assertEqual(self.reg.competition_coverage(comp)[0], SRC.GAP, comp)
        for key in ("avtomobilist", "sinara", "ural", "real", "arsenal", "milan", "zenit", "nhl_bos", "bayern", "juventus"):
            self.assertEqual(self.reg.club_coverage(key)[0], SRC.COVERED, key)
        self.assertEqual(self.reg.catalog_gaps(), [])                                    # весь пилотный каталог покрыт
        self.assertTrue(self.reg.competition_matrix("ucl")["crosscheck"])
        self.assertTrue(self.reg.competition_matrix("nhl")["crosscheck"])
        self.assertFalse(self.reg.competition_matrix("khl")["crosscheck"])
        for _ in range(3):
            self.reg.record("calendar:avtomobilist", SRC.OUTCOME_FAILED, "x")
        self.assertEqual(self.reg.club_coverage("avtomobilist")[0], SRC.COVERED)         # КХЛ дополнительно закрыт матч-центром sports.ru
        for sid in ("sportsru:center:hockey",):
            for _ in range(3):
                self.reg.record(sid, SRC.OUTCOME_FAILED, "x")
        self.assertEqual(self.reg.competition_coverage("khl")[0], SRC.FAILED)
        self.assertEqual(self.reg.competition_coverage("nhl")[0], SRC.COVERED)           # у NHL есть официальный API
        gaps = {g[2]: g[3] for g in self.reg.catalog_gaps()}
        self.assertEqual(gaps.get("КХЛ"), SRC.FAILED)
        self.assertNotIn("Единая лига ВТБ", gaps)

    def test_sport_without_catalog_clubs_is_gap_not_covered(self):
        self.assertEqual(self.reg.sport_coverage("basketball")[0], SRC.GAP)               # отложенный вид спорта: клубов в каталоге нет — покрытие не утверждается

    def test_club_not_yet_seen_in_real_data_is_not_counted_and_competition_is_partial(self):
        # механизм «ждёт первого матча»: клуб с алиасом, но без подтверждения в реальных данных источника, покрытым не считается
        from unittest import mock
        import club_aliases as A
        with mock.patch.dict(A.UNVERIFIED, {"ucl": {"sabah"}}):
            self.assertEqual(self.reg.club_coverage("sabah")[0], SRC.GAP)
            m = self.reg.competition_matrix("ucl")
            self.assertEqual((m["status"], m["covered"], m["total"], m["pending"]), (SRC.PARTIAL, 35, 36, ["sabah"]))
            self.assertEqual(self.reg.competition_coverage("ucl"), (SRC.PARTIAL, "35/36 клубов"))
            self.assertEqual([g[2] for g in self.reg.catalog_gaps()], ["Лига чемпионов", "Сабах"])
        self.assertEqual(self.reg.competition_coverage("ucl")[0], SRC.COVERED)

    def test_fallback_only_when_only_search_serves(self):
        reg = SRC.SourceRegistry(self.tmp, [d for d in SRC.build_source_defs(S.CLUBS)
                                            if not d.source_id.startswith("calendar:ural") and d.source_id != "sportsru:club:football"])
        self.assertEqual(reg.club_coverage("ural")[0], SRC.FALLBACK_ONLY)

    def test_request_coverage_gap_for_unknown_and_known_inherit(self):
        self.assertEqual(self.reg.request_coverage("sport", "теннис")[0], SRC.GAP)
        self.assertEqual(self.reg.request_coverage("championship", "nba")[0], SRC.GAP)
        self.assertEqual(self.reg.request_coverage("club", "бока хуниорс")[0], SRC.GAP)
        self.assertEqual(self.reg.request_coverage("club", "ювентус")[0], SRC.COVERED)                # клуб штатного каталога (Серия А)
        self.assertEqual(self.reg.request_coverage("championship", "кхл")[0], SRC.COVERED)
        self.assertEqual(self.reg.request_coverage("club", "milan")[0], SRC.COVERED)


class WiredIntoSportbot(TS.Base):
    """Хуки SPORTBOT пишут реальные итоги запросов в реестр; логика SPORTBOT не меняется."""

    def setUp(self):
        super().setUp()
        self.reg_dir = tempfile.mkdtemp(prefix="src-wired-")
        H.configure(self.reg_dir, SRC.build_source_defs(S.CLUBS))
        self.addCleanup(H.reset)

    def url(self, key):
        return self.club(key)["extract_urls"][0]

    async def test_working_calendar_marks_source_ok(self):
        self.pages[self.url("ural")] = sportsru_page([("10.10.2026", "12:00", "Первая лига", "Велес", "Дома", ["превью", "–"])])
        fixtures = await S.fetch_club_calendar(self.club("ural"), datetime.date(2026, 10, 5))
        self.assertEqual(len(fixtures), 1)
        self.assertEqual(H.REGISTRY.health("calendar:ural"), SRC.OK)

    async def test_confirmed_empty_page_is_ok_not_failed(self):
        self.pages[self.url("ural")] = "Календарь\nБудущие матчи\nДата\nТурнир\nСоперник\nСчет\nЗрители"
        self.assertEqual(await S.fetch_club_calendar(self.club("ural"), datetime.date(2026, 10, 5)), [])
        st = H.REGISTRY.state("calendar:ural")
        self.assertEqual(st["last_outcome"], "confirmed_empty")
        self.assertEqual(H.REGISTRY.health("calendar:ural"), SRC.OK)

    async def test_unavailable_page_is_source_failed_and_never_becomes_empty_schedule(self):
        for n in range(1, 4):
            with self.assertRaises(S.SourceError):
                await S.fetch_club_calendar(self.club("ural"), datetime.date(2026, 10, 5))        # страница не отдаётся → не «матчей нет»
            self.assertEqual(H.REGISTRY.state("calendar:ural")["consecutive_failures"], n)
        self.assertEqual(H.REGISTRY.health("calendar:ural"), SRC.FAILED_HEALTH)
        self.assertEqual(H.REGISTRY.state("calendar:avtomobilist"), {})                          # другие источники не затронуты

    async def test_changed_layout_is_source_failed(self):
        self.pages[self.url("ural")] = "совсем другая страница без календаря"
        with self.assertRaises(S.SourceError):
            await S.fetch_club_calendar(self.club("ural"), datetime.date(2026, 10, 5))
        self.assertEqual(H.REGISTRY.health("calendar:ural"), SRC.DEGRADED)
        self.assertIn("вёрстка", H.REGISTRY.state("calendar:ural")["last_error_short"])

    async def test_direct_calendar_works_when_tavily_is_down(self):
        async def broken(*a, **k):
            raise RuntimeError("Tavily 403")
        self._patch(S, "collect_web_context_tavily", broken)
        self.pages[self.url("avtomobilist")] = sportsru_page([("06.10.2026", "17:00", "КХЛ", "Авангард", "Дома", ["превью", "–"])])
        fixtures = await S.fetch_club_calendar(self.club("avtomobilist"), datetime.date(2026, 10, 5))
        self.assertEqual(len(fixtures), 1)
        self.assertEqual(H.REGISTRY.health("calendar:avtomobilist"), SRC.OK)


class OwnerScreens(TribunBase):
    def make(self, owner=OWNER, **kw):
        self.reg = make_registry(self.tmp)
        return super().make(owner=owner, registry=self.reg, **kw)

    async def test_sources_screen_shows_sports_and_does_not_claim_basketball(self):
        await self.press(OWNER, "adm:src")
        text = self.last_text()
        for line in ("✅ Футбол — настроен, ещё не проверялся", "✅ Хоккей", "✅ Футзал"):
            self.assertIn(line, text)
        self.assertNotIn("Баскетбол", text)
        for line in ("Рабочих источников: 0", "С проблемами: 0", "Ещё не проверялись: 14", "Непокрытых интересов: 0"):
            self.assertIn(line, text)
        self.assertEqual([l for l, _ in self.buttons()][:4], ["🏟 Покрытие", "🌍 Все источники", "⚠️ Проблемы", "🧩 Непокрытые интересы"])
        self.reg.record("calendar:ural", SRC.OUTCOME_OK)
        self.reg.record("calendar:avtomobilist", SRC.OUTCOME_OK)
        self.reg.record("calendar:sinara", SRC.OUTCOME_OK)
        await self.press(OWNER, "adm:src")
        self.assertIn("✅ Футбол — OK", self.last_text())
        self.assertIn("✅ Хоккей — OK", self.last_text())
        self.assertIn("✅ Футзал — OK", self.last_text())
        self.assertIn("Рабочих источников: 3", self.last_text())

    async def test_problem_screen_and_recovery(self):
        await self.press(OWNER, "adm:src:prob")
        self.assertIn("Проблем с источниками сейчас нет", self.last_text())
        for _ in range(3):
            self.reg.record("calendar:sinara", SRC.OUTCOME_FAILED, "страница недоступна")
        await self.press(OWNER, "adm:src:prob")
        self.assertIn("МФК «Синара»", self.last_text())
        self.assertIn("ошибок подряд 3", self.last_text())
        await self.press(OWNER, "adm:src")
        self.assertIn("🟡 Футзал — есть сбои", self.last_text())                          # у Суперлиги есть второй источник (официальный сайт)
        self.reg.record("calendar:sinara", SRC.OUTCOME_OK)
        await self.press(OWNER, "adm:src:prob")
        self.assertIn("Проблем с источниками сейчас нет", self.last_text())

    async def test_coverage_and_all_sources_screens(self):
        await self.press(OWNER, "adm:src:cov")
        text = self.last_text()
        for gone in ("Баскетбол", "ВТБ", "Лига Европы", "Лига конференций"):
            self.assertNotIn(gone, text)
        self.assertIn("✅ КХЛ — 22/22", text)
        self.assertIn("✅ NHL — 32/32", text)
        self.assertIn("✅ Лига чемпионов — 36/36", text)
        self.assertIn("Клубы каталога: покрыто 182 из 182.", text)
        await self.press(OWNER, "adm:src:all")
        text = self.last_text()
        self.assertIn("Tavily Search", text)
        self.assertIn("DIRECT", text)
        self.assertIn("не источники фактов", text)

    async def test_requests_become_uncovered_interests_and_requests_screen_has_no_names(self):
        await self.join(5, "Мария Секретная")
        await self.join(6, "Пётр Секретный")
        for uid in (5, 6):
            await self.press(uid, "t:sp:football")
            await self.press(uid, "o:cp")
            await self.say(uid, "NBA" if uid == 5 else "НБА")
        await self.press(6, "o:cl")
        await self.say(6, "Бока Хуниорс")
        await self.press(OWNER, "adm:rq")
        text = self.last_text()
        self.assertIn("🏆 Чемпионаты:\nNBA — 2", text)
        self.assertIn("❤️ Клубы:\nБока Хуниорс — 1", text)
        self.assertNotIn("Секретн", text)
        detail = [p for _, p in self.buttons() if p.startswith("adm:rqd:cp:")][0]
        await self.press(OWNER, detail)
        text = self.last_text()
        for part in ("Раздел: 🏆 Чемпионаты", "Название: NBA", "Нормализовано: nba", "Активных запросивших: 2", "GAP", "Источников нет"):
            self.assertIn(part, text)
        self.assertNotIn("Секретн", text)
        await self.press(OWNER, "adm:src:gap")
        text = self.last_text()
        self.assertIn("NBA (чемпионат) — 2 · запрос «Другое»", text)
        self.assertNotIn("Баскетбол (вид спорта)", text)                                  # штатный вид спорта покрыт — «Другое» остаётся GAP
        await self.press(OWNER, "adm:src")
        self.assertIn("Непокрытых интересов: 2", self.last_text())
        await self.tribun.on_user_removed(GROUP, user(6))
        await self.press(OWNER, "adm:rq")
        self.assertIn("NBA — 1", self.last_text())                           # вышедший не считается
        self.assertNotIn("Бока Хуниорс", self.last_text())

    async def test_request_status_for_known_covered_interest(self):
        await self.join(5, "А")
        await self.press(5, "o:cp")
        await self.say(5, "Серия А")                                         # известный чемпионат → обычный выбор, не запрос
        self.assertEqual(self.tribun.request_groups(self.tribun.load_members()), [])
        self.assertEqual(self.profile(5)["championships"], ["seriea"])

    async def test_member_never_sees_sources(self):
        await self.join(5, "А")
        for payload in ("adm:src", "adm:src:gap", "adm:rq", "adm:rqd:cp:12345678"):
            self.out = []
            await self.press(5, payload)
            self.assertEqual(self.out, [], payload)
        self.assertNotIn("Источники", str(self.bot.sent))


if __name__ == "__main__":
    unittest.main()
