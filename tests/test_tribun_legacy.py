"""Регрессия SPORTBOT: шесть клубов и вся прежняя автоматика работают как раньше, не зависят от профилей Трибуна;
хуки учёта/реестра ничего не меняют в логике (все прежние тесты проходят ещё и с включёнными хуками)."""
import asyncio
import datetime
import inspect
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_sport_v4 as TS  # noqa: E402
from test_sport_v4 import S, ekb, sportsru_page  # noqa: E402
import sources as SRC  # noqa: E402
import tribun_hooks as H  # noqa: E402

SIX = ["avtomobilist", "sinara", "ural", "real", "arsenal", "milan"]


class HooksOn:
    """Подмешивается к каждому прежнему тесту: те же проверки, но с включёнными учётом API и реестром источников."""

    def setUp(self):
        super().setUp()
        H.configure(tempfile.mkdtemp(prefix="legacy-hooks-"), SRC.build_source_defs(S.CLUBS, True))
        self.addCleanup(H.reset)


def _clone(name, cls):
    return type(name + "WithHooks", (HooksOn, cls), {"__module__": __name__})


for _name, _cls in list(vars(TS).items()):
    if isinstance(_cls, type) and issubclass(_cls, TS.Base) and _cls is not TS.Base:
        globals()[_name + "WithHooks"] = _clone(_name, _cls)
del _name, _cls


class SixClubs(TS.Base):
    def test_all_six_clubs_still_tracked_with_calendars(self):
        self.assertEqual([c["key"] for c in S.CLUBS], SIX)
        for club in S.CLUBS:
            self.assertTrue(club["extract_urls"], club["key"])
            self.assertIn(club["sport"], ("hockey", "futsal", "football"))

    def test_registry_covers_exactly_these_clubs(self):
        defs = SRC.build_source_defs(S.CLUBS, False)
        self.assertEqual(sorted(d.source_id for d in defs if d.source_id.startswith("calendar:")), sorted(f"calendar:{k}" for k in SIX))

    def test_direct_parsing_is_deterministic_python_not_ai(self):
        for fn in (S.fetch_club_calendar, S.parse_calendar, S.parse_sportsru_calendar):
            src = inspect.getsource(fn).lower()
            for banned in ("deepseek", "openai", "tavily", "collect_web_context"):
                self.assertNotIn(banned, src, fn.__name__)

    def test_hooked_functions_keep_signatures(self):
        for fn, params in ((S.collect_web_context_tavily, ["query", "sites", "extract_urls"]), (S.deepseek_completion, ["messages"]),
                           (S.ask_openai_websearch, ["prompt"]), (S.fetch_result_openai, ["club", "rival_hint", "on_date"]),
                           (S.check_morning, ["club", "today"])):
            self.assertEqual(list(inspect.signature(fn).parameters), params, fn.__name__)
            self.assertTrue(asyncio.iscoroutinefunction(fn), fn.__name__)


class IndependentOfProfiles(TS.Base):
    MONDAY = ekb(2026, 10, 5, 9, 0)

    def pages_for_week(self):
        self.pages[self.club("ural")["extract_urls"][0]] = sportsru_page([("10.10.2026", "12:00", "Первая лига", "Велес", "Дома", ["превью", "–"])])
        self.pages[self.club("avtomobilist")["extract_urls"][0]] = sportsru_page([("06.10.2026", "17:00", "КХЛ", "Авангард", "Дома", ["превью", "–"])])

    def write_profiles(self, interests):
        path = os.path.join(S.DATA_DIR, "tribun_members.json")
        members = {str(i): {"user_id": i, "display_name": f"У{i}", "sports": s, "championships": c, "clubs": k, "active_in_group": True,
                            "onboarding_completed": True} for i, (s, c, k) in enumerate(interests, 1)}
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "meta": {}, "members": members}, f)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))

    async def weekly_text(self):
        for name in ("SCHEDULE_FILE", "LAST_WEEKLY_FILE"):
            if os.path.exists(getattr(S, name)):
                os.unlink(getattr(S, name))
        self.bot.group.clear()
        self.pages_for_week()
        self.assertTrue(await S.job_weekly(self.bot, self.MONDAY))
        return list(self.bot.group)

    async def test_weekly_poster_is_identical_with_hostile_profiles(self):
        plain = await self.weekly_text()
        self.assertTrue(plain)
        self.write_profiles([(["basketball"], ["vtb"], []), ([], [], [])])        # никто не хочет ни Урал, ни Автомобилист
        self.assertEqual(await self.weekly_text(), plain)

    async def test_morning_and_results_do_not_read_member_profiles(self):
        for job in (S.job_weekly, S.job_morning, S.job_check_results, S.check_morning, S.resolve_result, S.retry_failed_morning,
                    S.retry_unsent_announcements):
            src = inspect.getsource(job)
            for token in ("tribun_members", "interest_map", "load_members", "sports\"]", "championships", "tribun."):
                self.assertNotIn(token, src, f"{job.__name__}: {token}")

    async def test_removed_interest_does_not_remove_club_from_weekly(self):
        self.write_profiles([(["hockey"], ["khl"], [])])
        text = " ".join(await self.weekly_text())
        self.assertIn("Автомобилист", text)
        self.assertIn("Урал", text)                                                # футбольный клуб тоже в афише, хотя у участника интереса нет


class Scheduler(TS.Base):
    async def test_single_scheduler_and_tribun_never_starts_its_own(self):
        loop = mock.AsyncMock()
        self._patch(S, "scheduler_loop", loop)
        self._patch(S, "Bot", lambda token: self.bot)
        launched = []

        async def fake_run_tribun(bot):
            launched.append(1)
            return []
        self._patch(S, "run_tribun", fake_run_tribun)
        with mock.patch.dict(os.environ, {"FORCE_MODE": ""}):
            await S.main()
        loop.assert_awaited_once()
        self.assertEqual(launched, [1])

        class Me:
            username = "t"

        class StubBot:
            async def get_me(self):
                return Me()

            async def delete_webhook(self):
                return None
        from maxapi import Dispatcher as RealDispatcher

        class QuietDispatcher(RealDispatcher):
            async def start_polling(self_, bot):
                return None
        loop.reset_mock()
        with mock.patch.object(S, "Dispatcher", QuietDispatcher), mock.patch.object(S.tribun.Tribun, "sync_loop", mock.AsyncMock()):
            tasks = await S.run_tribun(StubBot())
        await asyncio.gather(*tasks, return_exceptions=True)
        loop.assert_not_called()                                                    # слой клуба второй планировщик не создаёт

    async def test_hooks_are_configured_once_and_disabled_with_flag(self):
        H.reset()
        S.setup_tribun_hooks()
        self.assertIsNotNone(H.REGISTRY)
        self.assertIsNotNone(H.TRACKER)
        H.reset()
        with mock.patch.object(S, "TRIBUN_ENABLED", False):
            S.setup_tribun_hooks()
        self.assertIsNone(H.REGISTRY)
        self.assertIsNone(H.TRACKER)
        H.reset()

    async def test_scheduler_tick_runs_alert_step_without_breaking_other_jobs(self):
        now = ekb(2026, 10, 7, 12, 0)
        self._patch(S, "utc_now", lambda: now.astimezone(S.UTC) if hasattr(S, "UTC") else now)

        class Stop(Exception):
            pass

        async def stop(seconds):
            raise Stop()
        self._patch(S.asyncio, "sleep", stop)
        order = []

        async def fake_alerts(bot, n):
            order.append("alerts")
            raise RuntimeError("сбой проверки источников")
        self._patch(S, "tribun_tick_alerts", fake_alerts)
        with self.assertRaises(Stop):
            await S.scheduler_loop(self.bot)
        self.assertEqual(order, ["alerts"])                                         # сбой алертов изолирован guarded()

    async def test_after_deploy_state_files_are_untouched_by_tribun_layer(self):
        H.configure(tempfile.mkdtemp(prefix="state-"), SRC.build_source_defs(S.CLUBS))
        self.addCleanup(H.reset)
        club = self.club("ural")
        rec = self.rec("ural", ekb(2026, 10, 3, 12, 0), rival="Велес", status="found", result_text="x", result_found=True)
        self.put(rec)
        before = open(S.DATA_FILE, encoding="utf-8").read()
        H.source_event("calendar:ural", "failed", "x")
        H.TRACKER.record("tavily", "search", None, True, units=2)
        self.assertEqual(open(S.DATA_FILE, encoding="utf-8").read(), before)        # published/found-состояние матчей не затронуто
        self.assertEqual(club["key"], "ural")


class TickAlerts(TS.Base):
    NOW = ekb(2026, 10, 3, 12, 0)

    def setUp(self):
        super().setUp()
        H.configure(tempfile.mkdtemp(prefix="alerts-"), SRC.build_source_defs(S.CLUBS, False))
        self.addCleanup(H.reset)

    def fail(self, source_id, n):
        for _ in range(n):
            H.source_event(source_id, "failed", "недоступно")

    async def test_no_alert_for_single_failure_or_when_fallback_exists(self):
        self.fail("calendar:ural", 1)
        await S.tribun_tick_alerts(self.bot, self.NOW)
        self.fail("calendar:ural", 5)                                               # поиск жив (ещё не падал) — запасной путь есть
        await S.tribun_tick_alerts(self.bot, self.NOW)
        self.assertEqual(self.bot.admin, [])

    async def test_critical_alert_once_and_one_recovery_message(self):
        self.fail("calendar:ural", 3)
        self.fail("search:tavily", 3)                                               # OpenAI выключен → замены нет
        await S.tribun_tick_alerts(self.bot, self.NOW)
        await S.tribun_tick_alerts(self.bot, self.NOW + datetime.timedelta(minutes=20))
        await S.tribun_tick_alerts(self.bot, self.NOW + datetime.timedelta(minutes=40))
        msgs = [m["text"] for m in self.bot.admin]
        self.assertEqual(len(msgs), 1)                                              # без спама
        self.assertIn("Трибун", msgs[0])
        self.assertIn("Урал", msgs[0])
        H.source_event("calendar:ural", "ok")
        await S.tribun_tick_alerts(self.bot, self.NOW + datetime.timedelta(minutes=60))
        await S.tribun_tick_alerts(self.bot, self.NOW + datetime.timedelta(minutes=80))
        msgs = [m["text"] for m in self.bot.admin]
        self.assertEqual(len(msgs), 2)
        self.assertIn("снова работает", msgs[1])

    async def test_cost_warning_is_sent_once(self):
        with open(os.path.join(H.TRACKER.pricing.path), "w") as f:
            json.dump({"limits": {"daily_calls": {"openai": 1}}}, f)
        for i in range(3):
            H.TRACKER.record("openai", "responses", "fallback", True)
        await S.tribun_tick_alerts(self.bot, self.NOW)
        await S.tribun_tick_alerts(self.bot, self.NOW + datetime.timedelta(minutes=20))
        msgs = [m["text"] for m in self.bot.admin]
        self.assertEqual(len(msgs), 1)
        self.assertIn("OpenAI", msgs[0])

    async def test_dry_run_sends_nothing(self):
        self._patch(S, "DRY_RUN", True)
        self.fail("calendar:ural", 3)
        self.fail("search:tavily", 3)
        await S.tribun_tick_alerts(self.bot, self.NOW)
        self.assertEqual(self.bot.admin, [])


if __name__ == "__main__":
    unittest.main()
