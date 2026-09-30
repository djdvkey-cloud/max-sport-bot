"""Тесты SPORTBOT/MAX (v4). Сеть, MAX и нейросети подменены заглушками, данные — во
временной папке; в реальную MAX-группу ничего не отправляется.

Запуск:  python -m unittest discover -s tests -v
"""
import asyncio
import datetime
import json
import os
import random
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MAX_BOT_TOKEN", "test-token")
os.environ.setdefault("MAX_CHAT_ID", "-100500")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="sport-import-")
os.environ["ADMIN_USER_ID"] = "777"
os.environ.pop("OPENAI_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sport_bot as S  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
EKB = S.YEKB_TZ
UTC = datetime.timezone.utc


def read_fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def utc(y, m, d, h=0, mi=0):
    return datetime.datetime(y, m, d, h, mi, tzinfo=UTC)


def ekb(y, m, d, h=0, mi=0):
    return datetime.datetime(y, m, d, h, mi, tzinfo=EKB)


class FakeBot:
    """MAX: группа (MAX_CHAT_ID) и личные сообщения (user_id / chat_id)."""

    def __init__(self):
        self.group, self.admin = [], []
        self.fail_group = False
        self.fail_admin = False

    async def send_message(self, chat_id=None, user_id=None, text=None, notify=None, **kw):
        if chat_id == S.MAX_CHAT_ID:
            if self.fail_group:
                raise RuntimeError("MAX недоступен")
            self.group.append(text)
            return
        if self.fail_admin:
            raise RuntimeError("личка недоступна")
        self.admin.append({"to": user_id or chat_id, "text": text, "notify": notify})


async def _no_sleep(*a, **k):
    return None


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sport-test-")
        self.patches = []
        paths = {"DATA_FILE": "today_matches.json", "LAST_MORNING_FILE": "last_morning.txt",
                 "ALERTS_FILE": "admin_alerts.json", "SCHEDULE_FILE": "schedule.json",
                 "LAST_WEEKLY_FILE": "last_weekly.txt", "META_FILE": "sport_meta.json"}
        for name, fn in paths.items():
            self._patch(S, name, os.path.join(self.tmp, fn))
        self._patch(S, "DRY_RUN", False)
        self._patch(S, "OPENAI_API_KEY", "")
        self._patch(S.asyncio, "sleep", _no_sleep)
        self.bot = FakeBot()
        self.pages = {}           # url -> текст страницы календаря
        self.deepseek = {}        # club_key -> ответ утренней проверки / исключение

        async def fake_fetch(url):
            return self.pages.get(url, "")

        self._patch(S, "fetch_url_direct", fake_fetch)

    def _patch(self, obj, name, value):
        p = mock.patch.object(obj, name, value)
        p.start()
        self.patches.append(p)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    # --- помощники ---
    def club(self, key):
        return S.club_by_key(key)

    def rec(self, club_key, start, rival="Соперник", status="announced", day=None, **extra):
        club = self.club(club_key)
        d = day or start.astimezone(EKB).date()
        rec = {
            "match_id": S.match_identity(club_key, d.isoformat(), rival), "club_key": club_key,
            "sport": S.sport_family(club), "match_date": d.isoformat(), "tournament": "Лига", "time": "17:00",
            "zone": "мск", "place": "Дома", "rival": rival, "start_utc": start.isoformat(), "status": status,
            "announce_sent": True,
        }
        rec.update(extra)
        return S.normalize_match(rec)

    def put(self, *recs):
        state = S.empty_state()
        for r in recs:
            state["matches"][r["match_id"]] = r
        S.save_state(state)
        return state

    def state(self):
        return S.load_state(utc(2026, 10, 6, 12))

    def cand(self, club_key, club_goals, rival_goals, rival="Соперник", context="", method=None, origin="search"):
        club = self.club(club_key)
        hockey = club["sport"] == "hockey"
        outcome = "ПОБЕДА" if club_goals > rival_goals else ("НИЧЬЯ" if club_goals == rival_goals else "ПОРАЖЕНИЕ")
        parsed = (outcome, club["name"], club_goals, rival, rival_goals) + ((method or "ОСНОВНОЕ",) if hockey else ())
        return S._make_candidate(club, hockey, parsed, context, origin)

    def patch_fetch(self, club_key, *results):
        """Заглушка основного пути: результаты по очереди (исключение — сбой источника)."""
        club = self.club(club_key)
        seq = list(results)
        calls = mock.Mock()

        async def fake(club_arg, rival, day=None):
            calls(rival)
            item = seq.pop(0) if len(seq) > 1 else seq[0]
            if isinstance(item, Exception):
                raise item
            return item

        self._patch(S, "fetch_result_hockey" if club["sport"] == "hockey" else "fetch_result_football", fake)
        return calls

    def alerts(self):
        return S.read_json_safe(S.ALERTS_FILE, {})

    def admin_texts(self):
        return [m["text"] for m in self.bot.admin]


# ============================================================ сообщения не менялись

class ExistingMessagesUnchanged(Base):
    def test_result_messages_literal(self):
        avto, ural, real = self.club("avtomobilist"), self.club("ural"), self.club("real")
        self.assertEqual(S.format_result_hockey(avto, "ПОРАЖЕНИЕ", "Авангард", 3, "ХК «Автомобилист»", 2, "ОТ"),
                         "😔 Увы, сегодня проиграли в овертайме\n\n🏒 Авангард 3:2 ОТ ХК «Автомобилист»")
        self.assertEqual(S.format_result_hockey(avto, "ПОБЕДА", "ХК «Автомобилист»", 4, "Лада", 3, "БУЛЛИТЫ"),
                         "🎆🎆🎆 ПОБЕДА ПО БУЛЛИТАМ!!!\n\n🏒 ХК «Автомобилист» 4:3 Б Лада")
        self.assertEqual(S.format_result_hockey(avto, "ПОБЕДА", "ХК «Автомобилист»", 3, "Лада", 1, "ОСНОВНОЕ"),
                         "🎆🎆🎆 ПОБЕДА!!!\n\n🏒 ХК «Автомобилист» 3:1 Лада")
        self.assertEqual(S.format_result_football(ural, "ПОБЕДА", "ФК «Урал»", 2, "Шинник", 1),
                         "🎆🎆🎆 ПОБЕДА!!!\n\n⚽ ФК «Урал» 2:1 Шинник")
        self.assertEqual(S.format_result_football(real, "НИЧЬЯ", "«Реал Мадрид»", 1, "Барселона", 1),
                         "🤝 НИЧЬЯ!\n\n⚽ «Реал Мадрид» 1:1 Барселона")
        self.assertEqual(S.format_result_football(ural, "ПОРАЖЕНИЕ", "Шинник", 2, "ФК «Урал»", 0),
                         "😔 Увы, сегодня проиграли\n\n⚽ Шинник 2:0 ФК «Урал»")

    def test_morning_message_literal(self):
        m = {"tournament": "Первая лига", "time": "18:30", "zone": "мск", "place": "Ярославль", "rival": "Шинник"}
        self.assertEqual(S.format_morning(self.club("ural"), m),
                         "⚽ Сегодня играет ФК «Урал»\nТурнир: Первая лига\nВремя: 18:30 (мск)\n"
                         "Место: Ярославль\nСоперник: Шинник")

    def test_safety_checks_still_work(self):
        avto = self.club("avtomobilist")
        with self.assertRaises(ValueError):
            S.resolve_reported_result("Авангард-2", "3", "ХК «Автомобилист»", "2", avto, "Авангард", "3:2")
        with self.assertRaises(ValueError):                       # счёт, которого нет в материалах
            S.resolve_reported_result("Авангард", "3", "ХК «Автомобилист»", "2", avto, "Авангард", "1:0")
        out = S.resolve_reported_result("Авангард", "3", "ХК «Автомобилист»", "2", avto, "Авангард", "Авангард 3:2 ОТ")
        self.assertEqual(out, ("ПОРАЖЕНИЕ", "Авангард", 3, "ХК «Автомобилист»", 2))
        self.assertTrue(S.has_forbidden_squad_label("Урал U-19"))
        self.assertTrue(S.has_forbidden_squad_label("Урал-2"))

    async def test_send_to_group_contract(self):
        ok = await S.send_to_group(self.bot, "текст")
        self.assertTrue(ok)
        self.bot.fail_group = True
        self.assertFalse(await S.send_to_group(self.bot, "текст"))
        self._patch(S, "DRY_RUN", True)
        self.bot.fail_group = False
        self.assertFalse(await S.send_to_group(self.bot, "текст"))   # DRY_RUN не шлёт и не «подтверждает»
        self.assertEqual(self.bot.group, ["текст"])


# ============================================================ состояние и публикация

class ResultPublication(Base):
    START = utc(2026, 10, 6, 14)        # 17:00 мск

    def setup_match(self, **extra):
        r = self.rec("avtomobilist", self.START, "Амур", **extra)
        self.put(r)
        return r

    async def test_first_check_exactly_two_hours_after_start(self):
        self.setup_match()
        calls = self.patch_fetch("avtomobilist", None)
        await S.job_check_results(self.bot, self.START + datetime.timedelta(hours=1, minutes=59))
        calls.assert_not_called()
        await S.job_check_results(self.bot, self.START + datetime.timedelta(hours=2))
        calls.assert_called_once()
        self.assertEqual(S.RESULT_DELAY_HOURS, 2.0)

    async def test_found_and_published_once(self):
        r = self.setup_match()
        cand = self.cand("avtomobilist", 3, 1, "Амур")
        calls = self.patch_fetch("avtomobilist", cand)
        now = self.START + datetime.timedelta(hours=2, minutes=5)
        await S.job_check_results(self.bot, now)
        self.assertEqual(self.bot.group, [cand["text"]])
        rec = self.state()["matches"][r["match_id"]]
        self.assertEqual((rec["status"], rec["result_sent"]), ("published", True))
        self.assertIn(r["match_id"], self.state()["published_ids"])
        # повторные проверки и «перезапуски» не публикуют второй раз и не ищут заново
        for minutes in (20, 40, 600):
            await S.job_check_results(self.bot, now + datetime.timedelta(minutes=minutes))
        self.assertEqual(len(self.bot.group), 1)
        calls.assert_called_once()
        self.assertEqual(self.bot.admin, [])       # при нормальной работе бот молчит

    async def test_send_failure_keeps_result_then_recovers(self):
        r = self.setup_match()
        cand = self.cand("avtomobilist", 3, 1, "Амур")
        calls = self.patch_fetch("avtomobilist", cand)
        self.bot.fail_group = True
        t1 = self.START + datetime.timedelta(hours=2, minutes=5)
        await S.job_check_results(self.bot, t1)
        rec = self.state()["matches"][r["match_id"]]
        self.assertEqual(rec["status"], "result_found")            # результат найден и СОХРАНЁН
        self.assertFalse(rec["result_sent"])
        self.assertEqual(rec["result_text"], cand["text"])
        self.assertEqual(self.bot.group, [])
        self.assertEqual(len(self.bot.admin), 1)                   # одно личное уведомление
        self.assertIn("не удалось отправить", self.bot.admin[0]["text"])
        self.assertNotIn("Traceback", self.bot.admin[0]["text"])
        self.assertEqual(self.bot.admin[0]["to"], 777)
        # следующие тики: результат не ищется заново, повторный алерт не приходит
        await S.job_check_results(self.bot, t1 + datetime.timedelta(minutes=20))
        await S.job_check_results(self.bot, t1 + datetime.timedelta(minutes=40))
        calls.assert_called_once()
        self.assertEqual(len(self.bot.admin), 1)
        # MAX восстановился
        self.bot.fail_group = False
        await S.job_check_results(self.bot, t1 + datetime.timedelta(minutes=60))
        self.assertEqual(self.bot.group, [cand["text"]])
        self.assertEqual(self.state()["matches"][r["match_id"]]["status"], "published")
        self.assertEqual(len(self.bot.admin), 2)
        self.assertTrue(self.bot.admin[1]["text"].startswith("✅ SPORTBOT"))
        self.assertIn("опубликован", self.bot.admin[1]["text"])
        calls.assert_called_once()

    async def test_restart_between_found_and_sent(self):
        r = self.setup_match()
        cand = self.cand("avtomobilist", 2, 3, "Амур", method="ОТ")
        self.patch_fetch("avtomobilist", cand)
        self.bot.fail_group = True
        now = self.START + datetime.timedelta(hours=2, minutes=1)
        await S.job_check_results(self.bot, now)
        # «процесс перезапущен»: новый бот, источники теперь недоступны, состояние — только из файла
        new_bot = FakeBot()
        self.patch_fetch("avtomobilist", S.SourceError("источники лежат"))
        await S.job_check_results(new_bot, now + datetime.timedelta(minutes=30))
        self.assertEqual(new_bot.group, [cand["text"]])
        self.assertEqual(self.state()["matches"][r["match_id"]]["status"], "published")

    async def test_duplicate_protection_by_published_ids(self):
        r = self.setup_match(status="result_found", result_text="ИТОГ")
        state = S.load_state(utc(2026, 10, 6, 12))
        state["published_ids"].append(r["match_id"])
        S.save_state(state)
        await S.job_check_results(self.bot, self.START + datetime.timedelta(hours=3))
        self.assertEqual(self.bot.group, [])
        self.assertEqual(self.state()["matches"][r["match_id"]]["status"], "published")

    async def test_dry_run_never_marks_published_and_never_alerts(self):
        r = self.setup_match()
        self.patch_fetch("avtomobilist", self.cand("avtomobilist", 1, 0, "Амур"))
        self._patch(S, "DRY_RUN", True)
        await S.job_check_results(self.bot, self.START + datetime.timedelta(hours=3))
        rec = self.state()["matches"][r["match_id"]]
        self.assertEqual(rec["status"], "result_found")
        self.assertEqual(self.bot.group, [])
        self.assertEqual(self.bot.admin, [])

    async def test_no_result_quiet_until_alert_threshold_then_one_alert(self):
        self.setup_match()
        self.patch_fetch("avtomobilist", None)
        await S.job_check_results(self.bot, self.START + datetime.timedelta(hours=2, minutes=10))
        await S.job_check_results(self.bot, self.START + datetime.timedelta(hours=4))
        self.assertEqual(self.bot.admin, [])                      # матч мог идти в овертайме — молчим
        t = self.START + datetime.timedelta(hours=4, minutes=40)
        await S.job_check_results(self.bot, t)
        await S.job_check_results(self.bot, t + datetime.timedelta(minutes=20))
        self.assertEqual(len(self.bot.admin), 1)
        self.assertIn("пока не найден", self.bot.admin[0]["text"])

    async def test_source_failures_alert_after_three_ticks_only(self):
        self.setup_match()
        self.patch_fetch("avtomobilist", S.SourceError("Tavily 500"))
        base = self.START + datetime.timedelta(hours=2, minutes=5)
        for i in range(2):
            await S.job_check_results(self.bot, base + datetime.timedelta(minutes=20 * i))
        self.assertEqual(self.bot.admin, [])
        for i in range(2, 6):
            await S.job_check_results(self.bot, base + datetime.timedelta(minutes=20 * i))
        self.assertEqual(len(self.bot.admin), 1)
        self.assertIn("источники недоступны", self.bot.admin[0]["text"])
        self.assertNotIn("Tavily", self.bot.admin[0]["text"])     # технических подробностей нет

    async def test_give_up_after_36_hours_alerts_once(self):
        r = self.setup_match()
        self.patch_fetch("avtomobilist", None)
        await S.job_check_results(self.bot, self.START + datetime.timedelta(hours=37))
        await S.job_check_results(self.bot, self.START + datetime.timedelta(hours=38))
        self.assertEqual(self.state()["matches"][r["match_id"]]["status"], "expired")
        self.assertEqual(len([t for t in self.admin_texts() if "так и не получен" in t]), 1)

    async def test_match_without_start_time_alerts_once(self):
        r = self.rec("ural", self.START, "Шинник", start_utc=None)
        r["start_utc"] = None
        self.put(r)
        await S.job_check_results(self.bot, self.START + datetime.timedelta(hours=3))
        await S.job_check_results(self.bot, self.START + datetime.timedelta(hours=4))
        self.assertEqual(len(self.bot.admin), 1)
        self.assertIn("время начала", self.bot.admin[0]["text"])

    def test_wakeup_aligned_to_first_check(self):
        self.setup_match()
        now = self.START + datetime.timedelta(hours=1, minutes=50)       # до проверки 10 минут
        self.assertAlmostEqual(S.seconds_until_next_event(now), 601, delta=2)
        now2 = self.START - datetime.timedelta(hours=10)
        self.assertEqual(S.seconds_until_next_event(now2), S.CHECK_INTERVAL_SECONDS)


class StateMigration(Base):
    def write_v1(self, payload, saved_at):
        payload["saved_at"] = saved_at.isoformat()
        with open(S.DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    async def test_old_published_results_are_not_sent_again(self):
        start = utc(2026, 10, 6, 7)
        self.write_v1({"ural": {"tournament": "Первая лига", "time": "12:00", "zone": "екб", "place": "Дом",
                                "rival": "Шинник", "start_utc": start.isoformat(), "result_sent": True}},
                      utc(2026, 10, 6, 12))
        self.patch_fetch("ural", self.cand("ural", 2, 0, "Шинник"))
        state = S.load_state(utc(2026, 10, 6, 13))
        rec = list(state["matches"].values())[0]
        self.assertEqual((rec["status"], rec["result_sent"], rec["announce_sent"]), ("published", True, True))
        self.assertIn(rec["match_id"], state["published_ids"])
        await S.job_check_results(self.bot, utc(2026, 10, 6, 13))
        await S.retry_unsent_announcements(self.bot, utc(2026, 10, 6, 13))
        self.assertEqual(self.bot.group, [])

    async def test_old_pending_match_continues_without_reannounce(self):
        start = utc(2026, 10, 6, 14)
        self.write_v1({"avtomobilist": {"tournament": "КХЛ", "time": "17:00", "zone": "мск", "place": "Дома",
                                        "rival": "Амур", "start_utc": start.isoformat(), "result_sent": False}},
                      utc(2026, 10, 6, 5))
        cand = self.cand("avtomobilist", 3, 1, "Амур")
        self.patch_fetch("avtomobilist", cand)
        now = start + datetime.timedelta(hours=2, minutes=3)
        await S.retry_unsent_announcements(self.bot, now)
        self.assertEqual(self.bot.group, [])                      # анонс повторно не уходит
        await S.job_check_results(self.bot, now)
        self.assertEqual(self.bot.group, [cand["text"]])

    def test_stale_v1_state_is_dropped_as_before(self):
        self.write_v1({"ural": {"rival": "Шинник", "start_utc": utc(2026, 10, 1, 7).isoformat(), "result_sent": False,
                                "tournament": "x", "time": "12:00", "zone": "екб", "place": "x"}},
                      utc(2026, 10, 1, 8))
        self.assertEqual(S.load_state(utc(2026, 10, 6, 8))["matches"], {})

    def test_corrupt_state_is_preserved_not_overwritten(self):
        with open(S.DATA_FILE, "w", encoding="utf-8") as f:
            f.write("{сломано")
        state = S.load_state(utc(2026, 10, 6, 8))
        self.assertEqual(state["matches"], {})
        self.assertTrue([n for n in os.listdir(self.tmp) if ".corrupt-" in n])

    def test_atomic_write_keeps_old_file_on_failure(self):
        S.atomic_write_json(S.DATA_FILE, {"a": 1})
        with mock.patch("os.replace", side_effect=OSError("диск полон")):
            with self.assertRaises(OSError):
                S.atomic_write_json(S.DATA_FILE, {"a": 2})
        with open(S.DATA_FILE, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"a": 1})
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".tmp")], [])

    def test_state_roundtrip_survives_restart(self):
        r = self.rec("ural", utc(2026, 10, 6, 7), "Шинник", status="result_found", result_text="ИТОГ")
        self.put(r)
        again = S.load_state(utc(2026, 10, 6, 8))
        self.assertEqual(again["matches"][r["match_id"]]["result_text"], "ИТОГ")
        self.assertEqual(again["version"], 2)

    def test_same_time_unknown_does_not_inherit_published_flag(self):
        """Прежняя ошибка: у двух матчей без времени одинаковый «start_utc=None» —
        флаг result_sent переносился на новый матч. Теперь матч опознаётся по дате и сопернику."""
        a = S.match_identity("ural", "2026-10-06", "Шинник")
        b = S.match_identity("ural", "2026-10-13", "Шинник")
        c = S.match_identity("ural", "2026-10-06", "Велес")
        self.assertEqual(len({a, b, c}), 3)


# ============================================================ администратор

class AdminNotifications(Base):
    async def test_alert_once_then_silent_then_single_recovery(self):
        self.assertTrue(await S.raise_alert(self.bot, "k", "⚠️ SPORTBOT\nсбой"))
        for _ in range(5):
            self.assertFalse(await S.raise_alert(self.bot, "k", "⚠️ SPORTBOT\nсбой"))
        self.assertEqual(len(self.bot.admin), 1)
        await S.clear_alert(self.bot, "k", "✅ SPORTBOT\nвсё хорошо")
        await S.clear_alert(self.bot, "k", "✅ SPORTBOT\nвсё хорошо")
        self.assertEqual([m["text"] for m in self.bot.admin], ["⚠️ SPORTBOT\nсбой", "✅ SPORTBOT\nвсё хорошо"])
        # новая проблема с тем же ключом после восстановления — снова одно сообщение
        await S.raise_alert(self.bot, "k", "⚠️ SPORTBOT\nснова")
        self.assertEqual(len(self.bot.admin), 3)

    async def test_no_recovery_if_problem_never_reached_admin(self):
        self.bot.fail_admin = True
        await S.raise_alert(self.bot, "k", "⚠️ x")
        self.bot.fail_admin = False
        await S.clear_alert(self.bot, "k", "✅ x")
        self.assertEqual(self.bot.admin, [])

    async def test_undelivered_alert_is_retried_not_spammed(self):
        self.bot.fail_admin = True
        t0 = utc(2026, 10, 6, 10)
        await S.raise_alert(self.bot, "k", "⚠️ x", t0)
        self.bot.fail_admin = False
        await S.raise_alert(self.bot, "k", "⚠️ x", t0 + datetime.timedelta(minutes=10))
        self.assertEqual(self.bot.admin, [])
        await S.raise_alert(self.bot, "k", "⚠️ x", t0 + datetime.timedelta(minutes=40))
        self.assertEqual(len(self.bot.admin), 1)

    async def test_no_channel_configured_is_safe(self):
        with mock.patch.dict(os.environ, {"ADMIN_USER_ID": "", "ADMIN_CHAT_ID": ""}):
            with mock.patch.object(S, "read_json_safe", return_value={}):
                self.assertIsNone(S.admin_target())
                self.assertFalse(await S.admin_notify(self.bot, "x"))

    async def test_chat_id_takes_precedence_and_is_personal(self):
        with mock.patch.dict(os.environ, {"ADMIN_CHAT_ID": "555"}):
            await S.admin_notify(self.bot, "привет")
        self.assertEqual(self.bot.admin[0]["to"], 555)

    async def test_startup_notice_once_per_version_and_silent(self):
        await S.notify_startup(self.bot)
        await S.notify_startup(self.bot)
        self.assertEqual(len(self.bot.admin), 1)
        self.assertFalse(self.bot.admin[0]["notify"])             # без звука
        self.assertIn(S.BOT_VERSION, self.bot.admin[0]["text"])

    async def test_admin_message_never_goes_to_group(self):
        await S.raise_alert(self.bot, "k", "⚠️ секрет")
        self.assertEqual(self.bot.group, [])


# ============================================================ утренние анонсы

class Morning(Base):
    TODAY = ekb(2026, 10, 6, 10, 0)

    def setUp(self):
        super().setUp()

        async def fake_deepseek(question, sites, search_query=None, extract_urls=None):
            for club in S.CLUBS:
                if club["name"] in question:
                    item = self.deepseek.get(club["key"], "НЕТ")
                    if isinstance(item, Exception):
                        raise item
                    rival = item.split("|")[-1] if "|" in item else "Соперник"
                    return item, f"Источник x:\n{club['name']} {rival}"
            raise AssertionError("неизвестный клуб")

        self._patch(S, "ask_deepseek", fake_deepseek)
        self.cal_calls = 0

    async def run_morning(self, **kw):
        await S.job_morning(self.bot, self.TODAY)

    def intro_line(self, text):
        """Вводная фраза сообщения или None (часть фраз по ТЗ входит в несколько наборов,
        поэтому проверяем принадлежность нужному набору, а не «определяем» набор по фразе)."""
        first = text.split("\n\n")[0]
        return first if any(first in phrases for phrases in S.INTRO_SETS.values()) else None

    def assert_intro_from(self, text, kind):
        first = self.intro_line(text)
        self.assertIsNotNone(first, text)
        self.assertIn(first, S.INTRO_SETS[kind])

    async def test_football_only_uses_football_set_and_keeps_format(self):
        self.deepseek["ural"] = "Первая лига|12:00|екб|Екатеринбург|Шинник"
        await self.run_morning()
        self.assertEqual(len(self.bot.group), 1)
        expected_body = S.format_morning(self.club("ural"), {
            "tournament": "Первая лига", "time": "12:00", "zone": "екб", "place": "Екатеринбург", "rival": "Шинник"})
        self.assertTrue(self.bot.group[0].endswith("\n\n" + expected_body))
        self.assert_intro_from(self.bot.group[0], "football")

    async def test_hockey_only_uses_hockey_set(self):
        self.deepseek["avtomobilist"] = "КХЛ|17:00|мск|Екатеринбург|Амур"
        await self.run_morning()
        self.assert_intro_from(self.bot.group[0], "hockey")

    async def test_football_plus_hockey_uses_universal_set_once(self):
        self.deepseek["ural"] = "Первая лига|12:00|екб|Екатеринбург|Шинник"
        self.deepseek["avtomobilist"] = "КХЛ|17:00|мск|Екатеринбург|Амур"
        self.deepseek["real"] = "Ла Лига|22:00|мск|Мадрид|Бетис"
        await self.run_morning()
        self.assertEqual(len(self.bot.group), 3)
        lines = [self.intro_line(t) for t in self.bot.group]
        self.assertIn(lines[0], S.MIXED_INTROS)                   # вводная фраза — универсальная и только в первом
        self.assertEqual(lines[1:], [None, None])                 # и только один раз на утро

    async def test_futsal_counts_as_football(self):
        self.deepseek["sinara"] = "Суперлига|12:00|екб|ДИВС|Торпедо"
        await self.run_morning()
        self.assert_intro_from(self.bot.group[0], "football")
        self.deepseek["avtomobilist"] = "КХЛ|17:00|мск|Екатеринбург|Амур"
        self.bot.group.clear()
        os.unlink(S.DATA_FILE)
        await self.run_morning()
        self.assert_intro_from(self.bot.group[0], "mixed")     # футзал + хоккей

    async def test_no_match_no_message(self):
        await self.run_morning()
        self.assertEqual(self.bot.group, [])
        self.assertEqual(self.bot.admin, [])

    async def test_rerun_same_day_does_not_duplicate(self):
        self.deepseek["ural"] = "Первая лига|12:00|екб|Екатеринбург|Шинник"
        await self.run_morning()
        await self.run_morning()
        self.assertEqual(len(self.bot.group), 1)

    def test_phrase_sets_are_exactly_the_approved_ones(self):
        self.assertEqual(len(S.FOOTBALL_INTROS), 10)
        self.assertEqual(len(S.HOCKEY_INTROS), 10)
        self.assertEqual(len(S.MIXED_INTROS), 10)
        self.assertEqual(S.FOOTBALL_INTROS[0], "⚽ Сегодня футбольный день!")
        self.assertEqual(S.FOOTBALL_INTROS[9], "⚽ Время футбола!")
        self.assertEqual(S.HOCKEY_INTROS[8], "🥅 Ждём голов… то есть шайб! 😄")
        self.assertEqual(S.MIXED_INTROS[8], "🔥 Большой игровой день!")
        self.assertEqual(S.MIXED_INTROS[9], "💪 Наши сегодня в деле!")

    def test_no_repeat_in_a_row_for_each_set(self):
        rng = random.Random(3)
        for kind in S.INTRO_SETS:
            prev, seen = None, set()
            for _ in range(300):
                phrase = S.pick_intro(kind, rng)
                self.assertNotEqual(phrase, prev)
                prev = phrase
                seen.add(phrase)
            self.assertEqual(seen, set(S.INTRO_SETS[kind]))

    def test_sport_is_explicit_for_every_tracked_club(self):
        expected = {"avtomobilist": "hockey", "sinara": "football", "ural": "football",
                    "real": "football", "arsenal": "football", "milan": "football"}
        self.assertEqual({c["key"]: S.sport_family(c) for c in S.CLUBS}, expected)
        for club in S.CLUBS:
            self.assertIn(club["sport"], S.SPORT_FAMILY)

    async def test_failed_check_alerts_once_retries_and_recovers(self):
        self.deepseek["ural"] = S.SourceError("Tavily 500")
        self.deepseek["avtomobilist"] = "КХЛ|17:00|мск|Екатеринбург|Амур"
        await self.run_morning()
        self.assertEqual(len(self.bot.group), 1)                  # остальные клубы не пострадали
        self.assertEqual(len(self.bot.admin), 1)
        self.assertIn("ФК «Урал»", self.bot.admin[0]["text"])
        # повтор на следующем тике: снова сбой → тихо
        await S.retry_failed_morning(self.bot, utc(2026, 10, 6, 5, 20))
        self.assertEqual(len(self.bot.admin), 1)
        # источник ожил
        self.deepseek["ural"] = "Первая лига|12:00|екб|Екатеринбург|Шинник"
        await S.retry_failed_morning(self.bot, utc(2026, 10, 6, 5, 40))
        self.assertEqual(len(self.bot.group), 2)
        self.assertIsNone(self.intro_line(self.bot.group[1]))     # в повторе вводной фразы нет
        self.assertEqual(len(self.bot.admin), 2)
        self.assertTrue(self.bot.admin[1]["text"].startswith("✅"))
        # после успешной проверки больше не повторяем
        await S.retry_failed_morning(self.bot, utc(2026, 10, 6, 6, 0))
        self.assertEqual(len(self.bot.group), 2)

    async def test_retry_stops_after_cutoff_hour(self):
        self.deepseek["ural"] = S.SourceError("x")
        await self.run_morning()
        self.deepseek["ural"] = "Первая лига|12:00|екб|Екатеринбург|Шинник"
        await S.retry_failed_morning(self.bot, ekb(2026, 10, 6, 15, 1))
        self.assertEqual(self.bot.group, [])

    async def test_unsent_announcement_is_retried_before_match_start(self):
        self.deepseek["avtomobilist"] = "КХЛ|17:00|мск|Екатеринбург|Амур"
        self.bot.fail_group = True
        await self.run_morning()
        rec = list(self.state()["matches"].values())[0]
        self.assertFalse(rec["announce_sent"])
        self.assertEqual(rec["status"], "found")
        self.assertEqual(len(self.bot.admin), 1)
        self.bot.fail_group = False
        await S.retry_unsent_announcements(self.bot, utc(2026, 10, 6, 6, 0))
        self.assertEqual(len(self.bot.group), 1)
        self.assertEqual(self.state()["matches"][rec["match_id"]]["status"], "announced")
        await S.retry_unsent_announcements(self.bot, utc(2026, 10, 6, 6, 20))
        self.assertEqual(len(self.bot.group), 1)
        self.assertTrue(self.bot.admin[-1]["text"].startswith("✅"))

    async def test_unsent_announcement_not_retried_after_match_started(self):
        self.deepseek["avtomobilist"] = "КХЛ|17:00|мск|Екатеринбург|Амур"
        self.bot.fail_group = True
        await self.run_morning()
        self.bot.fail_group = False
        await S.retry_unsent_announcements(self.bot, utc(2026, 10, 6, 15, 0))
        self.assertEqual(self.bot.group, [])

    async def test_status_progression(self):
        self.deepseek["avtomobilist"] = "КХЛ|17:00|мск|Екатеринбург|Амур"
        await self.run_morning()
        rec = list(self.state()["matches"].values())[0]
        self.assertEqual(rec["status"], "announced")
        self.patch_fetch("avtomobilist", None)
        await S.job_check_results(self.bot, utc(2026, 10, 6, 16, 5))
        self.assertEqual(self.state()["matches"][rec["match_id"]]["status"], "awaiting_result")


# ============================================================ афиша недели

def sportsru_page(rows, top=None):
    lines = ["Календарь"]
    for t in (top or []):
        lines += t
    lines += ["Дата", "Турнир", "Соперник", "Счет", "Зрители"]
    for date, time, tournament, rival, where, cells in rows:
        lines += [date, "|", time, tournament, rival, where] + cells
    return "\n".join(lines)


class Weekly(Base):
    MONDAY = ekb(2026, 10, 5, 9, 0)

    def url(self, key):
        return self.club(key)["extract_urls"][0]

    def test_real_page_snapshots_parse(self):
        ref = datetime.date(2026, 10, 5)
        monday, sunday = S.week_bounds(ref)
        avto = S.parse_calendar(self.club("avtomobilist"), read_fixture("sportsru_avtomobilist.txt"), ref)
        week = [(str(f["date"]), f["time"], f["rival"], f["home"]) for f in avto if monday <= f["date"] <= sunday and not f["finished"]]
        self.assertEqual(week, [("2026-10-06", "17:00", "Авангард", True)])
        finished = [f for f in avto if f["finished"]]
        self.assertGreaterEqual(len(finished), 8)
        last = [f for f in finished if str(f["date"]) == "2026-09-30"][0]
        self.assertEqual((last["rival"], last["club_goals"], last["rival_goals"]), ("Лада", 3, 1))
        # Авангард, 15.09: реальный итог 3:2 ОТ в пользу Авангарда; у «В гостях» счёт зеркальный
        oct15 = [f for f in avto if str(f["date"]) == "2026-09-15"][0]
        self.assertEqual((oct15["club_goals"], oct15["rival_goals"], oct15["method"]), (2, 3, "ОТ"))
        # время-заглушка (01:00) у далёких матчей — «время не назначено», а не 01:00
        far = [f for f in avto if str(f["date"]) == "2026-10-16"][0]
        self.assertIsNone(far["time"])
        ural = S.parse_calendar(self.club("ural"), read_fixture("sportsru_ural.txt"), ref)
        self.assertEqual([(str(f["date"]), f["time"], f["rival"]) for f in ural if monday <= f["date"] <= sunday],
                         [("2026-10-10", "12:00", "Велес")])
        sinara = S.parse_calendar(self.club("sinara"), read_fixture("superliga_sinara.txt"), ref)
        self.assertEqual([(str(f["date"]), f["time"], f["zone"], f["rival"]) for f in sinara if monday <= f["date"] <= sunday],
                         [("2026-10-09", "18:30", "екб", "Торпедо"), ("2026-10-10", "15:00", "екб", "Торпедо")])
        later = [f for f in sinara if str(f["date"]) == "2026-10-26"][0]
        self.assertIsNone(later["time"])                          # 00:00 на странице лиги = не назначено

    async def test_weekly_message_sorted_icons_and_unknown_time(self):
        self.pages[self.url("ural")] = sportsru_page([
            ("10.10.2026", "12:00", "Первая лига", "Велес", "Дома", ["превью", "–"]),
            ("04.10.2026", "12:00", "Первая лига", "Старый", "Дома", ["1 : 0", "100"]),       # прошлая неделя
        ])
        self.pages[self.url("avtomobilist")] = sportsru_page([
            ("06.10.2026", "17:00", "КХЛ", "Авангард", "Дома", ["превью", "–"]),
            ("11.10.2026", "01:00", "КХЛ", "Металлург Мг", "В гостях", ["превью", "–"]),       # время-заглушка
            ("12.10.2026", "17:00", "КХЛ", "Сибирь", "Дома", ["превью", "–"]),                 # следующая неделя
        ])
        ok = await S.job_weekly(self.bot, self.MONDAY)
        self.assertTrue(ok)
        self.assertEqual(len(self.bot.group), 1)
        text = self.bot.group[0]
        self.assertEqual(text, "\n".join([
            "📅 Наши матчи на этой неделе", "",
            "🏒 ХК «Автомобилист» — Авангард", "🗓 Вторник · 17:00 (мск)", "",
            "⚽ ФК «Урал» — Велес", "🗓 Суббота · 12:00 (мск)", "",
            "🏒 ХК «Автомобилист» — Металлург Мг", "🗓 Воскресенье · время уточняется",
        ]))
        self.assertEqual(S.last_weekly_date(), datetime.date(2026, 10, 5))
        entries = S.load_schedule()["entries"]
        self.assertEqual(len(entries), 3)
        self.assertEqual({e["club_key"] for e in entries}, {"ural", "avtomobilist"})

    async def test_no_matches_sends_nothing_but_week_is_done(self):
        for club in S.CLUBS:
            self.pages[self.url(club["key"])] = sportsru_page([("20.10.2026", "19:00", "Лига", "Кто-то", "Дома", ["превью", "–"])])
        ok = await S.job_weekly(self.bot, self.MONDAY)
        self.assertTrue(ok)
        self.assertEqual(self.bot.group, [])
        self.assertEqual(S.last_weekly_date(), datetime.date(2026, 10, 5))

    async def test_one_source_down_others_still_in_afisha_single_alert(self):
        self.pages[self.url("ural")] = sportsru_page([("10.10.2026", "12:00", "Первая лига", "Велес", "Дома", ["превью", "–"])])
        # остальные страницы пустые → SourceError
        await S.job_weekly(self.bot, self.MONDAY)
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("ФК «Урал» — Велес", self.bot.group[0])
        alerts = [t for t in self.admin_texts() if "Не удалось получить расписание недели" in t]
        self.assertEqual(len(alerts), 5)                          # по одному на клуб, не повторяясь
        await S.job_weekly(self.bot, self.MONDAY + datetime.timedelta(minutes=20))
        self.assertEqual(len([t for t in self.admin_texts() if "Не удалось получить расписание недели" in t]), 5)

    async def test_send_failure_retries_and_alerts_once(self):
        self.pages[self.url("ural")] = sportsru_page([("10.10.2026", "12:00", "Первая лига", "Велес", "Дома", ["превью", "–"])])
        for club in S.CLUBS:
            self.pages.setdefault(self.url(club["key"]), sportsru_page([]))
        self.bot.fail_group = True
        self.assertFalse(await S.job_weekly(self.bot, self.MONDAY))
        self.assertIsNone(S.last_weekly_date())
        self.assertEqual(len([t for t in self.admin_texts() if "недельную афишу" in t]), 1)
        self.bot.fail_group = False
        self.assertTrue(await S.job_weekly(self.bot, self.MONDAY + datetime.timedelta(minutes=20)))
        self.assertEqual(len(self.bot.group), 1)
        self.assertTrue(self.admin_texts()[-1].startswith("✅"))

    async def test_weekly_trigger_monday_9_only_once_per_week(self):
        """Условие запуска в scheduler_loop."""
        monday = datetime.date(2026, 10, 5)

        def due(local):
            return (local.weekday() == S.WEEKLY_WEEKDAY and local.hour >= S.WEEKLY_HOUR
                    and S.last_weekly_date() != S.week_bounds(local.date())[0])

        self.assertFalse(due(ekb(2026, 10, 5, 8, 59)))
        self.assertTrue(due(ekb(2026, 10, 5, 9, 0)))
        self.assertTrue(due(ekb(2026, 10, 5, 13, 0)))            # бот вернулся позже — догоняет в тот же день
        self.assertFalse(due(ekb(2026, 10, 6, 9, 0)))            # вторник — нет
        S.save_last_weekly(monday)
        self.assertFalse(due(ekb(2026, 10, 5, 14, 0)))
        self.assertTrue(due(ekb(2026, 10, 12, 9, 0)))            # следующий понедельник — снова

    async def test_afisha_does_not_replace_morning_announcement(self):
        """Афиша и утренний анонс независимы: афиша ничего не отмечает в состоянии матчей."""
        self.pages[self.url("ural")] = sportsru_page([("10.10.2026", "12:00", "Первая лига", "Велес", "Дома", ["превью", "–"])])
        await S.job_weekly(self.bot, self.MONDAY)
        self.assertEqual(self.state()["matches"], {})


# ============================================================ контроль изменений расписания

class ScheduleChanges(Base):
    TODAY = ekb(2026, 10, 6, 10, 0)

    def setUp(self):
        super().setUp()
        self.answer = {}

        async def fake_deepseek(question, sites, search_query=None, extract_urls=None):
            for club in S.CLUBS:
                if club["name"] in question:
                    item = self.answer.get(club["key"], "НЕТ")
                    rival = item.split("|")[-1] if "|" in item else "Соперник"
                    return item, f"Источник x:\n{club['name']} {rival}"

        self._patch(S, "ask_deepseek", fake_deepseek)

    def seed(self, club_key, date, time, rival, zone="мск"):
        club = self.club(club_key)
        sched = S.load_schedule()
        S.schedule_upsert(sched, S.schedule_entry(club, date, time, zone, "КХЛ", rival, "calendar"))
        S.save_schedule(sched)

    async def test_unchanged_schedule_gives_normal_announcement_only(self):
        self.seed("avtomobilist", datetime.date(2026, 10, 6), "17:00", "Амур")
        self.answer["avtomobilist"] = "КХЛ|17:00|мск|Екатеринбург|Амур"
        await S.job_morning(self.bot, self.TODAY)
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("Сегодня играет ХК «Автомобилист»", self.bot.group[0])
        self.assertNotIn("изменилось", self.bot.group[0])

    async def test_time_change_is_one_message_not_two(self):
        self.seed("avtomobilist", datetime.date(2026, 10, 6), "17:00", "Амур")
        self.answer["avtomobilist"] = "КХЛ|19:00|мск|Екатеринбург|Амур"
        await S.job_morning(self.bot, self.TODAY)
        self.assertEqual(len(self.bot.group), 1)
        text = self.bot.group[0]
        self.assertTrue(text.startswith("⚠️ Внимание, изменилось время матча!"))
        self.assertIn("🏒 ХК «Автомобилист» — Амур", text)
        self.assertIn("Сегодня · 19:00 (мск)", text)
        self.assertIn("Ранее было указано 17:00 (мск).", text)
        self.assertNotIn("Сегодня играет", text)                 # второго, обычного анонса нет
        self.assertNotIn(S.INTRO_SETS["hockey"][0], text)
        # матч дальше отслеживается как обычный и попал в афишу с новым временем
        rec = list(self.state()["matches"].values())[0]
        self.assertEqual((rec["time"], rec["announce_sent"]), ("19:00", True))
        self.assertEqual(S.load_schedule()["entries"][0]["time"], "19:00")

    async def test_zone_label_difference_is_not_a_time_change(self):
        self.seed("avtomobilist", datetime.date(2026, 10, 6), "17:00", "Амур", zone="мск")
        self.answer["avtomobilist"] = "КХЛ|19:00|екб|Екатеринбург|Амур"          # то же время в другом поясе
        await S.job_morning(self.bot, self.TODAY)
        self.assertIn("Сегодня играет", self.bot.group[0])

    async def test_previously_unknown_time_is_normal_announcement(self):
        self.seed("avtomobilist", datetime.date(2026, 10, 6), None, "Амур")
        self.answer["avtomobilist"] = "КХЛ|19:00|мск|Екатеринбург|Амур"
        await S.job_morning(self.bot, self.TODAY)
        self.assertIn("Сегодня играет", self.bot.group[0])

    async def test_moved_to_today_single_message(self):
        self.seed("ural", datetime.date(2026, 10, 10), "12:00", "Шинник")
        self.answer["ural"] = "Первая лига|12:00|екб|Екатеринбург|Шинник"
        await S.job_morning(self.bot, self.TODAY)
        self.assertEqual(len(self.bot.group), 1)
        self.assertTrue(self.bot.group[0].startswith("⚠️ Внимание, матч перенесён на сегодня!"))
        self.assertIn("Ранее: Суббота, 10 октября · 12:00 (мск)", self.bot.group[0])
        self.assertNotIn("Сегодня играет", self.bot.group[0])
        entries = [e for e in S.load_schedule()["entries"] if e["club_key"] == "ural"]
        self.assertEqual([e["date"] for e in entries], ["2026-10-06"])        # старая запись заменена

    async def test_two_game_series_is_not_a_move(self):
        """Серия из двух матчей с одним соперником: вчерашняя игра — не «прежняя дата» сегодняшней."""
        yesterday = datetime.date(2026, 10, 5)
        self.seed("sinara", yesterday, "13:00", "Норильск", zone="екб")            # записи на сегодня в афише нет
        played = self.rec("sinara", utc(2026, 10, 5, 8), "Норильск", day=yesterday, status="published")
        self.put(played)
        self.answer["sinara"] = "Суперлига|13:00|екб|ДИВС|Норильск"
        await S.job_morning(self.bot, self.TODAY)
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("Сегодня играет", self.bot.group[0])
        self.assertNotIn("перенесён", self.bot.group[0])
    async def test_moved_away_from_today(self):
        self.seed("ural", datetime.date(2026, 10, 6), "12:00", "Шинник")
        self.pages[self.club("ural")["extract_urls"][0]] = sportsru_page(
            [("13.10.2026", "14:00", "Первая лига", "Шинник", "Дома", ["превью", "–"])])
        await S.job_morning(self.bot, self.TODAY)
        self.assertEqual(len(self.bot.group), 1)
        text = self.bot.group[0]
        self.assertTrue(text.startswith("⚠️ Внимание, матч перенесён!"))
        self.assertIn("⚽ ФК «Урал» — Шинник", text)
        self.assertIn("Теперь: Вторник, 13 октября · 14:00 (мск)", text)
        entries = S.load_schedule()["entries"]
        self.assertEqual([e["date"] for e in entries if e["club_key"] == "ural"], ["2026-10-13"])

    async def test_cancelled_match(self):
        self.seed("ural", datetime.date(2026, 10, 6), "12:00", "Шинник")
        top = [["Урал", "–", "Шинник", "06 октября 12:00", "Первая лига", "|", "отменен"]]
        self.pages[self.club("ural")["extract_urls"][0]] = sportsru_page(
            [("06.10.2026", "12:00", "Первая лига", "Шинник", "Дома", ["превью", "–"])], top=top)
        await S.job_morning(self.bot, self.TODAY)
        self.assertEqual(len(self.bot.group), 1)
        self.assertTrue(self.bot.group[0].startswith("⚠️ Внимание, матч отменён!"))
        self.assertEqual([e for e in S.load_schedule()["entries"] if e["club_key"] == "ural"], [])

    async def test_missing_match_without_evidence_stays_silent(self):
        self.seed("ural", datetime.date(2026, 10, 6), "12:00", "Шинник")
        self.pages[self.club("ural")["extract_urls"][0]] = sportsru_page(
            [("06.10.2026", "12:00", "Первая лига", "Шинник", "Дома", ["превью", "–"])])
        await S.job_morning(self.bot, self.TODAY)
        self.assertEqual(self.bot.group, [])                      # не гадаем

    async def test_source_error_does_not_trigger_false_cancellation(self):
        self.seed("ural", datetime.date(2026, 10, 6), "12:00", "Шинник")
        await S.job_morning(self.bot, self.TODAY)                # страницы календаря пустые → SourceError
        self.assertEqual(self.bot.group, [])


# ============================================================ конфликт источников

def ctx(*chunks):
    return "\n\n---\n\n".join(f"Источник {url}:\n{text}" for url, text in chunks)


class SourceConflicts(Base):
    START = utc(2026, 10, 6, 14)
    T1 = START + datetime.timedelta(hours=2, minutes=5)

    def setUp(self):
        super().setUp()
        self.r = self.rec("avtomobilist", self.START, "Амур")
        self.put(self.r)

    def avto_cand(self, cg, rg, context, method="ОСНОВНОЕ"):
        return self.cand("avtomobilist", cg, rg, "Амур", context=context, method=method)

    def table_for_avto(self, score_cells, home="Дома", where_date="06.10.2026"):
        self.pages[self.club("avtomobilist")["extract_urls"][0]] = sportsru_page(
            [(where_date, "17:00", "КХЛ", "Амур", home, score_cells)])

    def rec_now(self):
        return self.state()["matches"][self.r["match_id"]]

    async def test_agreeing_sources_publish_as_usual(self):
        context = ctx(("a.ru", "06.10.2026 ХК «Автомобилист» 3:1 Амур"), ("b.ru", "Автомобилист — Амур 3:1, итоги"))
        self.patch_fetch("avtomobilist", self.avto_cand(3, 1, context))
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(len(self.bot.group), 1)
        self.assertEqual(self.bot.admin, [])

    async def test_dated_contradicting_score_blocks_publication(self):
        context = ctx(("a.ru", "Автомобилист 2:1 Амур"), ("b.ru", "6 октября: Автомобилист 1:1 Амур"))
        self.patch_fetch("avtomobilist", self.avto_cand(2, 1, context))
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(self.bot.group, [])                      # сомнительный счёт в MAX не уходит
        rec = self.rec_now()
        self.assertEqual(rec["status"], "awaiting_result")
        self.assertIsNone(rec["result_text"])
        self.assertIn("1:1", rec["conflict"]["reason"])
        self.assertEqual(self.bot.admin, [])                      # сразу не беспокоим

    async def test_undated_old_score_is_not_a_conflict(self):
        """Прошлые встречи тех же команд (другая дата) — не повод блокировать результат."""
        context = ctx(("a.ru", "Автомобилист 3:1 Амур"), ("b.ru", "В прошлом сезоне Автомобилист 1:1 Амур"))
        self.patch_fetch("avtomobilist", self.avto_cand(3, 1, context))
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(len(self.bot.group), 1)

    async def test_live_vs_final_is_a_conflict(self):
        context = ctx(("a.ru", "Автомобилист — Амур 3:1 матч завершен"), ("b.ru", "Автомобилист — Амур матч идёт, 2-й период"))
        self.patch_fetch("avtomobilist", self.avto_cand(3, 1, context))
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(self.bot.group, [])
        self.assertIn("идёт", self.rec_now()["conflict"]["reason"])

    async def test_calendar_table_disagrees_blocks_then_agrees_publishes(self):
        self.patch_fetch("avtomobilist", self.avto_cand(3, 1, ctx(("a.ru", "Автомобилист 3:1 Амур"))))
        self.table_for_avto(["2 : 1", "1000"])                    # в таблице клуба 2:1
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(self.bot.group, [])
        self.assertIn("2:1", self.rec_now()["conflict"]["reason"])
        self.table_for_avto(["3 : 1", "1000"])                    # источник исправился
        await S.job_check_results(self.bot, self.T1 + datetime.timedelta(minutes=20))
        self.assertEqual(len(self.bot.group), 1)
        self.assertIsNone(self.rec_now()["conflict"])

    async def test_calendar_orientation_for_away_match(self):
        """В гостях таблица показывает «хозяева:гости» — проверка учитывает зеркальный порядок."""
        self.patch_fetch("avtomobilist", self.avto_cand(3, 2, ctx(("a.ru", "Авангард — Автомобилист 2:3"))))
        self.table_for_avto(["2 : 3", "100"], home="В гостях")     # хозяин Амур 2, наш Автомобилист 3
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(len(self.bot.group), 1)

    async def test_hockey_finish_method_mismatch_is_conflict(self):
        self.patch_fetch("avtomobilist", self.avto_cand(3, 2, ctx(("a.ru", "Автомобилист 3:2 Амур")), method="ОСНОВНОЕ"))
        self.table_for_avto(["от", "3 : 2", "100"])
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(self.bot.group, [])
        self.assertIn("способ", self.rec_now()["conflict"]["reason"])

    async def test_one_source_down_others_agree_does_not_block(self):
        # календарь недоступен (пустая страница) — публикуем по согласным источникам
        self.patch_fetch("avtomobilist", self.avto_cand(3, 1, ctx(("a.ru", "Автомобилист 3:1 Амур"))))
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(len(self.bot.group), 1)
        self.assertEqual(self.bot.admin, [])

    async def test_openai_crosscheck_disagree_blocks_and_failure_does_not(self):
        self._patch(S, "OPENAI_API_KEY", "key")
        self.patch_fetch("avtomobilist", self.avto_cand(3, 1, ctx(("a.ru", "Автомобилист 3:1 Амур"))))
        other = self.cand("avtomobilist", 3, 2, "Амур", origin="openai", method="ОСНОВНОЕ")
        self._patch(S, "fetch_result_openai", mock.AsyncMock(return_value=other))
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(self.bot.group, [])
        self.assertIn("независимая проверка", self.rec_now()["conflict"]["reason"])
        # OpenAI недоступен → не блокирует (а платный запрос не чаще раза в час)
        self._patch(S, "fetch_result_openai", mock.AsyncMock(return_value=None))
        await S.job_check_results(self.bot, self.T1 + datetime.timedelta(minutes=70))
        self.assertEqual(len(self.bot.group), 1)

    async def test_two_agreeing_sources_skip_paid_crosscheck(self):
        self._patch(S, "OPENAI_API_KEY", "key")
        crosscheck = mock.AsyncMock(return_value=None)
        self._patch(S, "fetch_result_openai", crosscheck)
        context = ctx(("a.ru", "Автомобилист 3:1 Амур"), ("b.ru", "Автомобилист — Амур 3:1"))
        self.patch_fetch("avtomobilist", self.avto_cand(3, 1, context))
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(len(self.bot.group), 1)
        crosscheck.assert_not_called()

    async def test_persistent_conflict_alerts_once_after_an_hour_and_recovers(self):
        bad = ctx(("a.ru", "Автомобилист 2:1 Амур"), ("b.ru", "6 октября: Автомобилист 1:1 Амур"))
        self.patch_fetch("avtomobilist", self.avto_cand(2, 1, bad))
        await S.job_check_results(self.bot, self.T1)
        await S.job_check_results(self.bot, self.T1 + datetime.timedelta(minutes=30))
        self.assertEqual(self.bot.admin, [])
        await S.job_check_results(self.bot, self.T1 + datetime.timedelta(minutes=61))
        await S.job_check_results(self.bot, self.T1 + datetime.timedelta(minutes=90))
        await S.job_check_results(self.bot, self.T1 + datetime.timedelta(minutes=120))
        self.assertEqual(len(self.bot.admin), 1)
        self.assertIn("требует проверки", self.bot.admin[0]["text"])
        self.assertEqual(self.bot.group, [])                      # всё это время в MAX тихо
        good = ctx(("a.ru", "Автомобилист 2:1 Амур"), ("b.ru", "Автомобилист 2:1 Амур"))
        self.patch_fetch("avtomobilist", self.avto_cand(2, 1, good))
        await S.job_check_results(self.bot, self.T1 + datetime.timedelta(minutes=140))
        self.assertEqual(len(self.bot.group), 1)
        self.assertEqual(len(self.bot.admin), 2)
        self.assertTrue(self.bot.admin[1]["text"].startswith("✅ SPORTBOT"))

    async def test_conflict_resolved_before_an_hour_sends_no_admin_messages(self):
        bad = ctx(("b.ru", "6 октября: Автомобилист 1:1 Амур"))
        self.patch_fetch("avtomobilist", self.avto_cand(2, 1, bad), self.avto_cand(2, 1, ctx(("a.ru", "Автомобилист 2:1 Амур"))))
        await S.job_check_results(self.bot, self.T1)
        self.assertEqual(self.bot.group, [])
        await S.job_check_results(self.bot, self.T1 + datetime.timedelta(minutes=20))
        self.assertEqual(len(self.bot.group), 1)
        self.assertEqual(self.bot.admin, [])

    def test_time_is_not_mistaken_for_a_score(self):
        avto = self.club("avtomobilist")
        claims = S.extract_score_claims("Автомобилист – Амур 6 октября 17:00 мск", avto, "Амур", datetime.date(2026, 10, 6))
        self.assertEqual(claims, [])
        claims = S.extract_score_claims("Амур 1:2 Автомобилист", avto, "Амур", datetime.date(2026, 10, 6))
        self.assertEqual([(c["club_goals"], c["rival_goals"]) for c in claims], [(2, 1)])

# ============================================================ планировщик, main, совместимость с maxapi

class Wiring(Base):
    async def test_scheduler_runs_weekly_morning_results_in_one_tick_then_sleeps_until_check(self):
        monday_10 = ekb(2026, 10, 5, 10, 0)
        self._patch(S, "utc_now", lambda: monday_10.astimezone(UTC))
        for club in S.CLUBS:
            self.pages[club["extract_urls"][0]] = sportsru_page([])
        self.pages[self.club("ural")["extract_urls"][0]] = sportsru_page(
            [("10.10.2026", "12:00", "Первая лига", "Велес", "Дома", ["превью", "–"])])

        async def fake_deepseek(question, sites, search_query=None, extract_urls=None):
            if self.club("avtomobilist")["name"] in question:
                return "КХЛ|19:00|мск|Екатеринбург|Амур", "Источник x:\nАвтомобилист Амур"
            return "НЕТ", ""

        self._patch(S, "ask_deepseek", fake_deepseek)

        class Stop(Exception):
            pass

        slept = []

        async def stop_sleep(seconds):
            slept.append(seconds)
            raise Stop()

        self._patch(S.asyncio, "sleep", stop_sleep)
        with self.assertRaises(Stop):
            await S.scheduler_loop(self.bot)
        texts = self.bot.group
        self.assertTrue(texts[0].startswith("📅 Наши матчи на этой неделе"))        # сначала афиша
        self.assertIn("Сегодня играет ХК «Автомобилист»", texts[1])                 # затем утренний анонс
        self.assertEqual(len(texts), 2)
        self.assertEqual(S.last_weekly_date(), datetime.date(2026, 10, 5))
        self.assertEqual(S.load_last_morning_date(), datetime.date(2026, 10, 5))
        # до первой проверки результата (16:00 мск + 2 ч = 18:00 мск) ещё долго: спим обычный интервал
        self.assertEqual(slept, [S.CHECK_INTERVAL_SECONDS])

    async def test_scheduler_survives_failing_part_and_alerts_after_three_ticks(self):
        now = ekb(2026, 10, 7, 12, 0)             # среда, не время афиши/утра
        self._patch(S, "utc_now", lambda: now.astimezone(UTC))

        async def boom(bot, now_arg):
            raise RuntimeError("внутренняя ошибка со stacktrace")

        self._patch(S, "job_check_results", boom)
        count = {"n": 0}

        class Stop(Exception):
            pass

        async def limited_sleep(seconds):
            count["n"] += 1
            if count["n"] >= 4:
                raise Stop()

        self._patch(S.asyncio, "sleep", limited_sleep)
        with self.assertRaises(Stop):
            await S.scheduler_loop(self.bot)
        alerts = [t for t in self.admin_texts() if "Внутренний сбой планировщика" in t]
        self.assertEqual(len(alerts), 1)
        self.assertNotIn("stacktrace", alerts[0])

    async def test_main_sends_startup_notice_and_starts_scheduler(self):
        started = mock.AsyncMock()
        self._patch(S, "scheduler_loop", started)
        self._patch(S, "Bot", lambda token: self.bot)
        with mock.patch.dict(os.environ, {"FORCE_MODE": ""}):
            await S.main()
        started.assert_awaited_once()
        self.assertEqual(len(self.bot.admin), 1)
        self.assertIn("SPORTBOT запущен", self.bot.admin[0]["text"])
        self.assertEqual(self.bot.group, [])                         # в группу при старте ничего не уходит

    def test_maxapi_send_message_accepts_what_we_use(self):
        import inspect
        from maxapi import Bot as RealBot
        params = inspect.signature(RealBot.send_message).parameters
        for name in ("chat_id", "user_id", "text", "notify"):
            self.assertIn(name, params)

    def test_new_files_are_under_data_dir_and_survive_rebuilds(self):
        for name in ("ALERTS_FILE", "SCHEDULE_FILE", "LAST_WEEKLY_FILE", "META_FILE", "DATA_FILE"):
            self.assertTrue(getattr(S, name).startswith(self.tmp))

if __name__ == "__main__":
    unittest.main()
