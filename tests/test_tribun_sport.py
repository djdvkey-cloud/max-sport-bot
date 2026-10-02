"""Стыковка клубного слоя «Трибун» с SPORTBOT: события дня из афиши/состояния, автоматика, состояние, запуск слушателя."""
import asyncio
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_sport_v4 as TS  # noqa: E402
from test_sport_v4 import Base, ekb, utc, S  # noqa: E402
import tribun as T  # noqa: E402

REAL_SLEEP = asyncio.sleep          # Base подменяет asyncio.sleep пустышкой — для реальных пауз берём оригинал


class ClubLayer(Base):
    def put_schedule(self, *entries):
        sched = {"entries": list(entries)}
        S.save_schedule(sched)

    def entry(self, club_key, day, time_str, zone, tournament, rival):
        return S.schedule_entry(S.club_by_key(club_key), day, time_str, zone, tournament, rival, "calendar")

    def test_owner_is_user_id_from_config_not_name(self):
        self.assertEqual(S.owner_user_id(), 777)
        with mock.patch.dict(os.environ, {"ADMIN_USER_ID": "", "ADMIN_CHAT_ID": "555"}):
            self.assertIsNone(S.owner_user_id())                    # чат — не user_id: владельцем никого не назначаем

    def test_events_for_day_from_schedule_and_state_without_duplicates_or_invention(self):
        day = datetime.date(2026, 10, 3)
        self.put_schedule(self.entry("avtomobilist", day, "19:00", "мск", "КХЛ", "Амур"),
                          self.entry("ural", datetime.date(2026, 10, 4), "12:00", "мск", "Первая лига", "Велес"))
        self.put(self.rec("avtomobilist", ekb(2026, 10, 3, 21, 0), rival="Амур", day=day))     # то же событие в состоянии — без дубля
        self.put(self.rec("real", ekb(2026, 10, 3, 23, 0), rival="Райо", day=day))
        events = S.tribun_events_for_day(day)
        self.assertEqual(sorted(e["club_key"] for e in events), ["avtomobilist", "real"])
        auto = [e for e in events if e["club_key"] == "avtomobilist"][0]
        self.assertEqual((auto["rival"], auto["tournament"], auto["sport_key"], auto["icon"]), ("Амур", "КХЛ", "hockey", "🏒"))
        self.assertIn("19:00", auto["time_text"])
        self.assertEqual(S.tribun_events_for_day(datetime.date(2026, 10, 9)), [])

    async def test_today_view_uses_real_sources(self):
        day = datetime.date(2026, 10, 3)
        self.put_schedule(self.entry("avtomobilist", day, "19:00", "мск", "КХЛ", "Амур"),
                          self.entry("milan", day, "21:45", "мск", "Серия А", "Рома"))
        import tempfile
        club = T.Tribun(self.bot, data_dir=tempfile.mkdtemp(), group_chat_id=S.MAX_CHAT_ID, owner_id=S.owner_user_id, version="t",
                        now=lambda: ekb(2026, 10, 3, 9, 0).astimezone(datetime.timezone.utc), events_for_day=S.tribun_events_for_day)
        store = club.load_members()
        p = club.touch(store, 5, "Мария", active=True)
        p.update({"sports": ["hockey"], "teams": ["Автомобилист"], "competitions": ["КХЛ"]})
        club.save_members(store)
        text, _ = club.today_view(p)
        self.assertIn("⭐ По твоим интересам:", text)
        self.assertLess(text.index("Автомобилист"), text.index("Милан"))

    def test_automation_lists_existing_jobs_with_timezone_and_status(self):
        self._patch(S, "utc_now", lambda: ekb(2026, 10, 3, 9, 30).astimezone(S.UTC) if hasattr(S, "UTC") else ekb(2026, 10, 3, 9, 30))
        S.save_last_morning_date(datetime.date(2026, 10, 2))
        T.record_job(S.DATA_DIR, "weekly", True, now=ekb(2026, 9, 28, 9, 5).astimezone(datetime.timezone.utc))
        T.record_job(S.DATA_DIR, "results", False, "RuntimeError: boom", now=ekb(2026, 10, 2, 22, 0).astimezone(datetime.timezone.utc))
        jobs = {j["name"]: j for j in S.tribun_automation()}
        self.assertEqual(set(jobs), {"Утренний анонс матчей", "Недельная афиша", "Проверка результатов матчей", "Сверка состава группы «Своя Трибуна»"})
        morning = jobs["Утренний анонс матчей"]
        self.assertEqual(morning["next"], "03.10.2026 10:00")
        self.assertIn("ежедневно в 10:00", morning["schedule"])
        self.assertIn("28.09.2026 09:05", jobs["Недельная афиша"]["last_ok"])
        self.assertIn("05.10.2026 09:00", jobs["Недельная афиша"]["next"])
        self.assertIn("02.10.2026 22:00", jobs["Проверка результатов матчей"]["last_error"])
        self.assertTrue(all(j["enabled"] for j in jobs.values()))

    def test_status_has_no_secrets(self):
        st = S.tribun_status()
        flat = str(st)
        for secret in (S.MAX_BOT_TOKEN, S.DEEPSEEK_API_KEY, S.TAVILY_API_KEY):
            self.assertNotIn(secret, flat) if len(secret) > 6 else None
        self.assertTrue(st["running"])
        self.assertTrue(any("Tavily" in s for s in st["sources"]))

    async def test_scheduler_records_tick_and_job_results_for_owner_panel(self):
        now = ekb(2026, 10, 7, 12, 0)
        self._patch(S, "utc_now", lambda: now.astimezone(S.UTC) if hasattr(S, "UTC") else now)

        class Stop(Exception):
            pass

        async def stop(seconds):
            raise Stop()
        self._patch(S.asyncio, "sleep", stop)
        with self.assertRaises(Stop):
            await S.scheduler_loop(self.bot)
        jobs = T.read_job_status(S.DATA_DIR)
        self.assertIn("_tick", jobs)
        self.assertIn("last_ok", jobs["results"])

    async def test_run_tribun_starts_listener_and_survives_polling_crash(self):
        class Me:
            username = "tribun_bot"

        class StubBot:
            async def get_me(self):
                return Me()

            async def delete_webhook(self):
                return None
        calls = []
        real_sleep = REAL_SLEEP
        from maxapi import Dispatcher as RealDispatcher

        class FakeDispatcher(RealDispatcher):
            async def start_polling(self_, bot):
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError("сеть")
                await real_sleep(3600)

        async def no_sync(self_, interval_hours=3.0):
            await real_sleep(3600)
        with mock.patch.object(S, "Dispatcher", FakeDispatcher), mock.patch.object(T.Tribun, "sync_loop", no_sync),                 mock.patch.object(S, "TRIBUN_RESTART_DELAY", 0.01):
            tasks = await S.run_tribun(StubBot())
            for _ in range(300):
                await real_sleep(0.01)
                if len(calls) >= 2:
                    break
            self.assertEqual(len(tasks), 2)
            self.assertEqual(len(calls), 2)                          # слушатель перезапущен после сбоя
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_run_tribun_failure_never_stops_scheduler_start(self):
        class Broken:
            async def get_me(self):
                raise RuntimeError("нет сети")

            async def delete_webhook(self):
                raise RuntimeError("нет сети")
        from maxapi import Dispatcher as RealDispatcher

        class QuietDispatcher(RealDispatcher):
            async def start_polling(self_, bot):
                return None
        with mock.patch.object(S, "Dispatcher", QuietDispatcher), mock.patch.object(T.Tribun, "sync_loop", mock.AsyncMock()):
            tasks = await S.run_tribun(Broken())
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_main_starts_listener_next_to_scheduler_and_cancels_it(self):
        started = mock.AsyncMock()
        self._patch(S, "scheduler_loop", started)
        self._patch(S, "Bot", lambda token: self.bot)
        fake_task = mock.Mock()
        launched = []

        async def fake_run_tribun(bot):
            launched.append(bot)
            return [fake_task]
        self._patch(S, "run_tribun", fake_run_tribun)
        with mock.patch.dict(os.environ, {"FORCE_MODE": ""}):
            await S.main()
        started.assert_awaited_once()
        self.assertEqual(launched, [self.bot])
        fake_task.cancel.assert_called_once()
        self.assertEqual(self.bot.group, [])

    def test_brand_texts_for_owner_use_tribun(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sport_bot.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("SPORTBOT\\n", src)                         # уведомления владельцу — «Трибун»
        self.assertIn("✅ Трибун запущен", src)


if __name__ == "__main__":
    unittest.main()
