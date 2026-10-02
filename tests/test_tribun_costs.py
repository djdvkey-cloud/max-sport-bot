"""Учёт API: Tavily / DeepSeek / OpenAI — usage отдельно от стоимости, cached ≠ real, ошибки отдельно, защита от лишних расходов."""
import asyncio
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_sport_v4 as TS  # noqa: E402
from test_sport_v4 import S, ekb  # noqa: E402
from test_tribun import Base as TribunBase, OWNER  # noqa: E402
import costs as K  # noqa: E402
import sources as SRC  # noqa: E402
import tribun_hooks as H  # noqa: E402


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def text(self):
        return self._body if isinstance(self._body, str) else json.dumps(self._body)

    async def read(self):
        return (await self.text()).encode()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    """aiohttp.ClientSession: ответы по подстроке URL; все обращения запоминаются."""
    routes: dict = {}
    calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, **kw):
        FakeSession.calls.append(url)
        for part, resp in FakeSession.routes.items():
            if part in url:
                if isinstance(resp, Exception):
                    raise resp
                return FakeResponse(*resp)
        raise AssertionError("неожиданный запрос " + url)

    get = post


TAVILY_OK = (200, {"results": [{"url": "https://www.sports.ru/x", "content": "Урал — Велес 2:1 " + "текст " * 40}]})
DEEPSEEK_OK = (200, {"choices": [{"message": {"content": "НЕТ"}}], "usage": {"prompt_tokens": 1200, "completion_tokens": 30}})
OPENAI_OK = (200, {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Урал|2|Велес|1"}]}],
                   "usage": {"input_tokens": 5000, "output_tokens": 4000}})


class Tracked(TS.Base):
    def setUp(self):
        super().setUp()
        self.d = tempfile.mkdtemp(prefix="cost-")
        H.configure(self.d, SRC.build_source_defs(S.CLUBS, True))
        self.addCleanup(H.reset)
        FakeSession.routes, FakeSession.calls = {}, []
        self._patch(S.aiohttp, "ClientSession", FakeSession)

    def day(self):
        return H.TRACKER.day_key()

    def summary(self):
        return H.TRACKER.summary(self.day())

    async def tavily(self, query="Урал матч", urls=None):
        return await S.collect_web_context_tavily(query, [], urls)


class UsageCounting(Tracked):
    async def test_tavily_usage_credits_purpose_and_source_health(self):
        FakeSession.routes = {"tavily": TAVILY_OK}
        with K.purpose("schedule_search"):
            await self.tavily()
        s = self.summary()["providers"]["tavily"]
        self.assertEqual((s["real"], s["cached"], s["failed"], s["units"]), (1, 0, 0, 2))      # advanced = 2 кредита
        self.assertEqual(self.summary()["purposes"]["schedule_search"]["real"], 1)
        self.assertEqual(H.REGISTRY.health("search:tavily"), SRC.OK)

    async def test_deepseek_tokens_and_unknown_cost_until_price_is_set(self):
        FakeSession.routes = {"deepseek": DEEPSEEK_OK}
        out = await S.deepseek_completion([{"role": "user", "content": "q1"}])
        self.assertEqual(out, "НЕТ")
        s = self.summary()["providers"]["deepseek"]
        self.assertEqual((s["real"], s["input_tokens"], s["output_tokens"]), (1, 1200, 30))
        self.assertEqual(s["cost"], {})
        self.assertEqual(s["cost_unknown"], 1)                                                 # цена не задана → стоимость неизвестна, а не 0
        H.TRACKER.pricing.set_price("deepseek", "deepseek-v4-flash", input_price=1.0, output_price=2.0, currency="usd")
        await S.deepseek_completion([{"role": "user", "content": "q2"}])
        s = self.summary()["providers"]["deepseek"]
        self.assertEqual(s["cost"], {"USD": round(1200 / 1e6 * 1.0 + 30 / 1e6 * 2.0, 6)})
        self.assertEqual(s["cost_unknown"], 1)                                                 # старый вызов задним числом не пересчитывается

    async def test_openai_usage_and_purposes(self):
        FakeSession.routes = {"openai": OPENAI_OK}
        self._patch(S, "OPENAI_API_KEY", "k")
        club = self.club("ural")
        with K.purpose("result_crosscheck"):
            cand = await S.fetch_result_openai(club, "Велес", datetime.date(2026, 10, 3))
        await S.fetch_result_openai(club, "Другой", datetime.date(2026, 10, 3))               # без внешнего назначения → fallback
        s = self.summary()
        self.assertEqual(s["providers"]["openai"]["real"], 2)
        self.assertEqual((s["providers"]["openai"]["input_tokens"], s["providers"]["openai"]["output_tokens"]), (10000, 8000))
        self.assertEqual(s["purposes"]["result_crosscheck"]["real"], 1)
        self.assertEqual(s["purposes"]["fallback"]["real"], 1)
        self.assertEqual(H.REGISTRY.health("search:openai"), SRC.OK)

    async def test_cached_is_not_counted_as_real_and_costs_nothing(self):
        FakeSession.routes = {"tavily": TAVILY_OK}
        H.TRACKER.pricing.set_price("tavily", "search", credit_price=0.01, currency="USD")
        await self.tavily()
        await self.tavily()                                                                    # тот же запрос → из кэша
        await self.tavily("другой запрос")
        s = self.summary()["providers"]["tavily"]
        self.assertEqual((s["real"], s["cached"], s["units"]), (2, 1, 4))
        self.assertEqual(s["cost"], {"USD": 0.04})                                             # кэш — бесплатно
        self.assertEqual(len(FakeSession.calls), 2)                                            # провайдеру ушло только 2 запроса

    async def test_failure_is_separate_and_not_billed(self):
        FakeSession.routes = {"tavily": (403, "forbidden")}
        out = await self.tavily()
        self.assertEqual(out, "")                                                              # поведение SPORTBOT прежнее: пустой контекст
        FakeSession.routes = {"deepseek": (500, {"error": {"message": "boom"}})}
        with self.assertRaises(RuntimeError):
            await S.deepseek_completion([{"role": "user", "content": "q"}])
        s = self.summary()
        self.assertEqual((s["providers"]["tavily"]["real"], s["providers"]["tavily"]["failed"]), (0, 1))
        self.assertEqual((s["providers"]["deepseek"]["real"], s["providers"]["deepseek"]["failed"]), (0, 1))
        self.assertEqual(s["providers"]["tavily"]["units"], 0)
        errors = {(p, m) for p, m, n in s["errors"]}
        self.assertIn(("deepseek", "RuntimeError: boom"), errors)
        self.assertEqual(H.REGISTRY.state("search:tavily")["consecutive_failures"], 1)

    async def test_results_are_the_same_as_without_hooks(self):
        FakeSession.routes = {"tavily": TAVILY_OK, "deepseek": DEEPSEEK_OK}
        with_hooks = (await self.tavily(), await S.deepseek_completion([{"role": "user", "content": "q"}]))
        H.reset()
        FakeSession.calls.clear()
        without = (await self.tavily(), await S.deepseek_completion([{"role": "user", "content": "q"}]))
        self.assertEqual(with_hooks, without)

    async def test_no_secrets_in_usage_file(self):
        FakeSession.routes = {"tavily": TAVILY_OK, "deepseek": (500, {"error": {"message": "bad key sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"}})}
        await self.tavily()
        with self.assertRaises(RuntimeError):
            await S.deepseek_completion([{"role": "user", "content": "q"}])
        raw = open(H.TRACKER.path, encoding="utf-8").read()
        for secret in (S.TAVILY_API_KEY, S.DEEPSEEK_API_KEY, "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            self.assertNotIn(secret, raw)

    async def test_usage_survives_restart(self):
        FakeSession.routes = {"tavily": TAVILY_OK}
        await self.tavily()
        again = K.UsageTracker(self.d)
        self.assertEqual(again.summary(self.day())["providers"]["tavily"]["real"], 1)
        self.assertTrue(again.recent())


class Aggregation(unittest.TestCase):
    def test_monthly_aggregation_over_days(self):
        d = tempfile.mkdtemp(prefix="agg-")
        clock = {"now": datetime.datetime(2026, 10, 3, 5, 0, tzinfo=datetime.timezone.utc)}
        tr = K.UsageTracker(d, now=lambda: clock["now"])
        tr.pricing.set_price("openai", "gpt-5.5", input_price=5.0, output_price=10.0, currency="USD")
        for day in (3, 4, 20):
            clock["now"] = datetime.datetime(2026, 10, day, 5, 0, tzinfo=datetime.timezone.utc)
            tr.record("openai", "responses", "fallback", True, model="gpt-5.5", input_tokens=1000, output_tokens=500)
        clock["now"] = datetime.datetime(2026, 11, 1, 5, 0, tzinfo=datetime.timezone.utc)
        tr.record("openai", "responses", "fallback", True, model="gpt-5.5", input_tokens=1000, output_tokens=500)
        month = tr.summary("2026-10")["providers"]["openai"]
        self.assertEqual((month["real"], month["input_tokens"], month["output_tokens"]), (3, 3000, 1500))
        self.assertEqual(month["cost"], {"USD": round(3 * (1000 / 1e6 * 5 + 500 / 1e6 * 10), 6)})
        self.assertEqual(tr.summary("2026-10-04")["providers"]["openai"]["real"], 1)
        self.assertEqual(tr.summary("2026-11")["providers"]["openai"]["real"], 1)
        self.assertEqual(tr.summary("2026-09")["providers"], {})

    def test_late_evening_utc_belongs_to_next_local_day(self):
        d = tempfile.mkdtemp(prefix="agg-")
        tr = K.UsageTracker(d, now=lambda: datetime.datetime(2026, 10, 3, 20, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(tr.day_key(), "2026-10-04")                                           # Екатеринбург UTC+5

    def test_pricing_config_shape_and_no_invented_prices(self):
        d = tempfile.mkdtemp(prefix="price-")
        pc = K.PricingConfig(d)
        for e in pc.entries():
            for field in ("provider", "model", "input_price", "output_price", "credit_price", "currency", "effective_from", "updated_at", "source_note"):
                self.assertIn(field, e)
            self.assertIsNone(e["input_price"])
            self.assertIsNone(e["credit_price"])
        self.assertEqual(pc.estimate("deepseek", "deepseek-v4-flash", 1000, 100), (None, None))
        e = pc.set_price("deepseek", "deepseek-v4-flash", input_price=0.1, output_price=0.2, currency="rub")
        self.assertEqual((e["currency"], e["effective_from"] is not None, e["updated_at"] is not None), ("RUB", True, True))
        self.assertEqual(K.PricingConfig(d).estimate("deepseek", "deepseek-v4-flash", 1_000_000, 1_000_000), (0.30000000000000004, "RUB"))
        self.assertEqual(pc.estimate("deepseek", "other-model", 10, 10), (None, None))


class Guards(Tracked):
    async def test_duplicate_deepseek_prompt_is_not_generated_twice(self):
        FakeSession.routes = {"deepseek": DEEPSEEK_OK}
        msgs = [{"role": "user", "content": "одинаковый вопрос"}]
        a = await S.deepseek_completion(msgs)
        b = await S.deepseek_completion(msgs)
        self.assertEqual((a, b), ("НЕТ", "НЕТ"))
        self.assertEqual(len(FakeSession.calls), 1)
        s = self.summary()["providers"]["deepseek"]
        self.assertEqual((s["real"], s["cached"]), (1, 1))

    async def test_retry_loop_of_one_request_is_stopped_but_other_requests_work(self):
        FakeSession.routes = {"tavily": (500, "err")}
        for _ in range(3):
            await self.tavily("плохой запрос")
        with self.assertRaises(K.CostGuardBlocked):
            await self.tavily("плохой запрос")                                                 # 4-й повтор того же запроса блокируется
        self.assertEqual(len(FakeSession.calls), 3)
        FakeSession.routes = {"tavily": TAVILY_OK}
        self.assertTrue(await self.tavily("нормальный запрос"))                                # другой запрос — не заблокирован
        self.assertTrue(H.GUARD.alerts)
        self.assertNotIn("плохой запрос", H.GUARD.alerts[0])

    async def test_rate_guard_stops_only_the_problem_purpose(self):
        FakeSession.routes = {"deepseek": DEEPSEEK_OK}
        cap = K.DEFAULT_LIMITS["rate_calls"]["deepseek"]
        with K.purpose("result_search"):
            for i in range(cap):
                await S.deepseek_completion([{"role": "user", "content": f"q{i}"}])
            with self.assertRaises(K.CostGuardBlocked):
                await S.deepseek_completion([{"role": "user", "content": "лишний"}])
        with K.purpose("schedule_search"):                                                      # соседний процесс работает
            self.assertEqual(await S.deepseek_completion([{"role": "user", "content": "утро"}]), "НЕТ")
        self.assertTrue(any("аномальная частота" in a for a in H.GUARD.alerts))

    async def test_guard_error_never_blocks_work(self):
        g = K.Guard(H.TRACKER)
        g.tracker = mock.Mock(pricing=mock.Mock(limits=mock.Mock(side_effect=RuntimeError("сбой конфига"))))
        g.check("deepseek", "p", "k")                                                            # fail-open: исключение не наружу, вызов разрешён

    async def test_warnings_once_per_day_and_provider(self):
        limits_file = os.path.join(self.d, "tribun_pricing.json")
        with open(limits_file, "w") as f:
            json.dump({"limits": {"daily_calls": {"deepseek": 2}}}, f)
        FakeSession.routes = {"deepseek": DEEPSEEK_OK}
        for i in range(3):
            await S.deepseek_completion([{"role": "user", "content": f"w{i}"}])
        first = H.TRACKER.pending_warnings()
        self.assertEqual(len(first), 1)
        self.assertIn("DeepSeek", first[0])
        self.assertEqual(H.TRACKER.pending_warnings(), [])                                       # без спама


class OwnerCostScreens(TribunBase):
    def make(self, owner=OWNER, **kw):
        self.tracker = K.UsageTracker(self.tmp, now=self.clock)
        return super().make(owner=owner, tracker=self.tracker, **kw)

    def feed(self):
        t = self.tracker
        t.record("tavily", "search", "schedule_search", True, units=2, model="search")
        t.record("tavily", "search", "schedule_search", True, cached=True, model="search")
        t.record("tavily", "search", "result_search", False, error="HTTP 403")
        t.record("deepseek", "chat", "result_search", True, model="deepseek-v4-flash", input_tokens=2000, output_tokens=100)
        t.record("openai", "responses", "fallback", True, model="gpt-5.5", input_tokens=9000, output_tokens=8000)

    async def test_main_screen_usage_separate_from_cost_and_unknown_prices(self):
        self.feed()
        await self.press(OWNER, "adm:api")
        text = self.last_text()
        for part in ("💰 API / расходы", "📅 Сегодня · 2026-10-03", "📆 Месяц · 2026-10", "Tavily: 1 вызовов · кредитов 2 · из кэша 1 · ошибок 1",
                     "DeepSeek: 1 вызовов · токены 2 000 вх / 100 исх", "OpenAI: 1 вызовов · токены 9 000 вх / 8 000 исх",
                     "Оценка стоимости: неизвестна (цены не заданы)", "Как экономим"):
            self.assertIn(part, text)
        self.assertEqual([l for l, _ in self.buttons()][:6], ["📅 Сегодня", "📆 Месяц", "🧠 AI", "🌐 API", "🎯 Назначение", "⚠️ Ошибки"])

    async def test_sub_screens(self):
        self.feed()
        for sub, parts in (("d", ("📅 Сегодня", "Tavily")), ("m", ("📆 Месяц",)), ("ai", ("DeepSeek", "OpenAI")), ("api", ("Tavily",)),
                           ("pur", ("поиск расписания: 1 / 1 / 0", "поиск результата: 1 / 0 / 1", "резервный поиск: 1 / 0 / 0")),
                           ("err", ("Tavily ×1: HTTP 403",))):
            await self.press(OWNER, f"adm:api:{sub}")
            for part in parts:
                self.assertIn(part, self.last_text(), sub)
        await self.press(OWNER, "adm:api:ai")
        self.assertNotIn("Tavily", self.last_text())

    async def test_price_command_changes_estimate_and_member_cannot_use_it(self):
        self.feed()
        await self.say(OWNER, "/цена deepseek deepseek-v4-flash 0.14 0.28 USD")
        self.assertIn("✅ Цена записана", self.last_text())
        self.tracker.record("deepseek", "chat", "result_search", True, model="deepseek-v4-flash", input_tokens=1_000_000, output_tokens=0)
        await self.press(OWNER, "adm:api:ai")
        self.assertIn("≈ 0.1400 USD", self.last_text())
        self.assertIn("для 2 вызовов цены нет", self.last_text())                                  # старый вызов DeepSeek и OpenAI без цены — честно «неизвестно»
        await self.say(OWNER, "/цена")
        self.assertIn("deepseek deepseek-v4-flash: вход 0.14 / выход 0.28 USD", self.last_text())
        await self.say(OWNER, "/цена мусор")
        self.assertIn("Не понял", self.last_text())
        before = open(self.tracker.pricing.path, encoding="utf-8").read()
        await self.join(5, "А")
        self.out = []
        await self.press(5, "adm:api")
        self.assertEqual(self.out, [])
        await self.say(5, "/цена deepseek deepseek-v4-flash 99 99 USD")
        self.assertEqual(open(self.tracker.pricing.path, encoding="utf-8").read(), before)
        self.assertEqual([l for l, _ in self.buttons()], ["🏅 Виды спорта", "🏆 Чемпионаты", "❤️ Клубы"])

    async def test_screens_without_tracker_do_not_crash(self):
        t = self.tribun
        t.tracker = None
        await self.press(OWNER, "adm:api")
        self.assertIn("не подключён", self.last_text())


if __name__ == "__main__":
    unittest.main()
