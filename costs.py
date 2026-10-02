"""Учёт внешних API «Трибуна»: сколько раз и зачем вызывали Tavily / DeepSeek / OpenAI, что это стоило, защита от лишних расходов.

Правила:
• USAGE (вызовы, токены, кредиты) и ESTIMATED COST — разные величины. Usage считается всегда по факту вызова; стоимость — только если
  в прайс-конфиге задана цена. Нет цены → «стоимость неизвестна», число не выдумывается.
• Закэшированный ответ записывается как cached и НЕ считается реальным вызовом (цена 0, провайдеру ничего не ушло).
• Неуспешный вызов — отдельная строка (ok=False), стоимость ему не приписывается.
• Агрегаты по дням переживают рестарт (tribun_usage.json). Секретов в учёте нет: только провайдер, операция, число, время."""
import contextvars
import datetime
import hashlib
import os
import time
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from tribun_io import TribunDataError, atomic_write_json, mask_secrets, read_strict, utc_now

YEKB_TZ = ZoneInfo("Asia/Yekaterinburg")

PROVIDERS = ("tavily", "deepseek", "openai", "sports_api")
PROVIDER_TITLE = {"tavily": "Tavily", "deepseek": "DeepSeek", "openai": "OpenAI", "sports_api": "Sports API"}
AI_PROVIDERS = ("deepseek", "openai")
API_PROVIDERS = ("tavily", "sports_api")
PURPOSES = ("schedule_search", "result_search", "result_crosscheck", "text_generation", "fallback", "admin_test")
PURPOSE_TITLE = {"schedule_search": "поиск расписания", "result_search": "поиск результата", "result_crosscheck": "перепроверка результата",
                 "text_generation": "генерация текста", "fallback": "резервный поиск", "admin_test": "тест владельца", "unknown": "не указано"}

purpose_var: contextvars.ContextVar = contextvars.ContextVar("tribun_purpose", default=None)


@contextmanager
def purpose(name: str):
    token = purpose_var.set(name)
    try:
        yield
    finally:
        purpose_var.reset(token)


# ---- прайс-конфиг ------------------------------------------------------------------------------------
# Цены НЕ заданы: их нельзя выдумывать. Владелец вносит актуальные цены командой «/цена» (см. tribun.py) → файл /data/tribun_pricing.json.
# input_price / output_price — за 1 000 000 токенов; credit_price — за 1 кредит/единицу.

DEFAULT_PRICING = [
    {"provider": "tavily", "model": "search", "input_price": None, "output_price": None, "credit_price": None, "currency": None,
     "effective_from": None, "updated_at": None, "source_note": "цена не задана; расход считается в кредитах (advanced search = 2)"},
    {"provider": "deepseek", "model": "deepseek-v4-flash", "input_price": None, "output_price": None, "credit_price": None, "currency": None,
     "effective_from": None, "updated_at": None, "source_note": "цена не задана"},
    {"provider": "openai", "model": "gpt-5.5", "input_price": None, "output_price": None, "credit_price": None, "currency": None,
     "effective_from": None, "updated_at": None, "source_note": "цена не задана"},
]

# Пороги предупреждений и защиты. Это количество вызовов (не деньги): разумный запас над обычной нагрузкой шести клубов.
DEFAULT_LIMITS = {
    "daily_calls": {"tavily": 80, "deepseek": 200, "openai": 12, "sports_api": 200},
    "monthly_calls": {"tavily": 900, "deepseek": 4000, "openai": 120, "sports_api": 3000},
    "daily_cost": {},                                       # {provider: сумма} — задаётся владельцем вместе с ценой
    "monthly_cost": {},
    "rate_window_minutes": 10,
    "rate_calls": {"tavily": 40, "deepseek": 60, "openai": 8, "sports_api": 60},
    "duplicate_ttl_seconds": {"tavily": 180, "deepseek": 120, "openai": 120},
    "retry_failures": 3,                                    # подряд неудач одного и того же запроса → пауза
    "retry_pause_minutes": 10,
}


class CostGuardBlocked(RuntimeError):
    """Защита от расходов остановила ИМЕННО этот вызов (проблемный workflow); остальные процессы работают."""


class PricingConfig:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "tribun_pricing.json")

    def _saved(self) -> dict:
        try:
            data = read_strict(self.path, {})
        except TribunDataError:
            return {}
        return data if isinstance(data, dict) else {}

    def entries(self) -> list:
        saved = {(e.get("provider"), e.get("model")): e for e in self._saved().get("prices", []) if isinstance(e, dict)}
        out = [dict(saved.get((d["provider"], d["model"]), d)) for d in DEFAULT_PRICING]
        out += [dict(e) for k, e in saved.items() if k not in {(d["provider"], d["model"]) for d in DEFAULT_PRICING}]
        return out

    def price_for(self, provider: str, model: str | None) -> dict | None:
        found = [e for e in self.entries() if e["provider"] == provider]
        for e in found:
            if e.get("model") == model:
                return e
        return found[0] if len(found) == 1 and model is None else None

    def limits(self) -> dict:
        saved = self._saved().get("limits", {})
        out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_LIMITS.items()}
        for k, v in saved.items() if isinstance(saved, dict) else []:
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k].update(v)
            elif k in out and not isinstance(out[k], dict):
                out[k] = v
        return out

    def set_price(self, provider: str, model: str, *, input_price=None, output_price=None, credit_price=None, currency: str,
                  source_note: str = "задано владельцем", now: datetime.datetime | None = None) -> dict:
        if provider not in PROVIDERS:
            raise ValueError("неизвестный провайдер")
        stamp = (now or utc_now()).isoformat(timespec="seconds")
        data = self._saved()
        prices = [e for e in data.get("prices", []) if not (e.get("provider") == provider and e.get("model") == model)]
        entry = {"provider": provider, "model": model, "input_price": input_price, "output_price": output_price,
                 "credit_price": credit_price, "currency": currency.upper()[:6], "effective_from": stamp[:10], "updated_at": stamp,
                 "source_note": source_note}
        prices.append(entry)
        data["prices"] = prices
        atomic_write_json(self.path, data)
        return entry

    def estimate(self, provider: str, model: str | None, input_tokens=None, output_tokens=None, units=None) -> tuple:
        """(стоимость | None, валюта | None). None — цены нет или данных недостаточно: «стоимость неизвестна»."""
        e = self.price_for(provider, model)
        if not e or not e.get("currency"):
            return None, None
        if units and e.get("credit_price") is not None:
            return float(units) * float(e["credit_price"]), e["currency"]
        if (input_tokens is not None and e.get("input_price") is not None
                and (not output_tokens or e.get("output_price") is not None)):
            cost = input_tokens / 1_000_000 * float(e["input_price"]) + (output_tokens or 0) / 1_000_000 * float(e.get("output_price") or 0)
            return cost, e["currency"]
        return None, None


# ---- учёт --------------------------------------------------------------------------------------------

def _empty_row() -> dict:
    return {"n": 0, "latency_ms": 0, "input_tokens": 0, "output_tokens": 0, "units": 0, "cost": {}, "cost_unknown": 0}


class UsageTracker:
    KEEP_DAYS = 400
    KEEP_RECENT = 200

    def __init__(self, data_dir: str, pricing: PricingConfig | None = None, now=utc_now):
        self.path = os.path.join(data_dir, "tribun_usage.json")
        self.pricing = pricing or PricingConfig(data_dir)
        self.now = now

    def day_key(self, when: datetime.datetime | None = None) -> str:
        return (when or self.now()).astimezone(YEKB_TZ).date().isoformat()

    def load(self) -> dict:
        data = read_strict(self.path, {"version": 1, "days": {}, "recent": [], "warned": {}})
        if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
            raise TribunDataError(self.path, "неверная структура")
        data.setdefault("recent", [])
        data.setdefault("warned", {})
        return data

    def record(self, provider: str, operation: str, purpose_name: str | None, ok: bool, *, latency_ms: int = 0, cached: bool = False,
               model: str | None = None, input_tokens: int | None = None, output_tokens: int | None = None,
               units: float | None = None, error: str | None = None) -> dict | None:
        """Запись ОДНОГО вызова. Никогда не бросает исключение наружу: учёт не должен ломать рабочий процесс."""
        try:
            purpose_name = purpose_name or "unknown"
            cost, currency = (None, None)
            if ok and not cached:
                cost, currency = self.pricing.estimate(provider, model, input_tokens, output_tokens, units)
            call = {"provider": provider, "operation": operation, "purpose": purpose_name, "ok": bool(ok), "cached": bool(cached),
                    "model": model, "latency_ms": int(latency_ms), "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "units": units, "estimated_cost": cost, "currency": currency, "at": self.now().isoformat(timespec="seconds")}
            if not ok:
                call["error"] = mask_secrets(error or "")[:120]
            data = self.load()
            day = data["days"].setdefault(self.day_key(), {"rows": {}})
            key = "|".join((provider, operation, purpose_name, "cached" if cached else "real", "ok" if ok else "failed"))
            row = day["rows"].setdefault(key, _empty_row())
            row["n"] += 1
            row["latency_ms"] += int(latency_ms)
            row["input_tokens"] += input_tokens or 0
            row["output_tokens"] += output_tokens or 0
            row["units"] += units or 0
            if ok and not cached:
                if cost is None:
                    row["cost_unknown"] += 1
                else:
                    row["cost"][currency] = round(row["cost"].get(currency, 0.0) + cost, 6)
            if not ok:
                row["last_error"] = call["error"]
            data["recent"] = (data["recent"] + [call])[-self.KEEP_RECENT:]
            for old in sorted(data["days"])[:-self.KEEP_DAYS]:
                del data["days"][old]
            atomic_write_json(self.path, data)
            return call
        except Exception as e:
            print(f"[TRIBUN] учёт API не записан: {type(e).__name__}: {mask_secrets(str(e))}")
            return None

    # ---- сводки ----------------------------------------------------------------------------------------

    def rows(self, prefix: str) -> list:
        """Строки учёта за период: prefix — «2026-10-03» (день) или «2026-10» (месяц)."""
        try:
            data = self.load()
        except TribunDataError:
            return []
        out = []
        for day, body in data["days"].items():
            if day.startswith(prefix):
                for key, row in body["rows"].items():
                    provider, operation, purpose_name, kind, status = key.split("|")
                    out.append({"day": day, "provider": provider, "operation": operation, "purpose": purpose_name,
                                "cached": kind == "cached", "ok": status == "ok", **row})
        return out

    def summary(self, prefix: str) -> dict:
        """{provider: {real, cached, failed, input_tokens, output_tokens, units, cost{cur:amt}, cost_unknown}} + by_purpose + errors."""
        by_provider, by_purpose, errors = {}, {}, {}
        for r in self.rows(prefix):
            p = by_provider.setdefault(r["provider"], {"real": 0, "cached": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0, "units": 0,
                                                       "cost": {}, "cost_unknown": 0})
            if r["cached"]:
                p["cached"] += r["n"]
            elif not r["ok"]:
                p["failed"] += r["n"]
                errors[(r["provider"], r.get("last_error", ""))] = errors.get((r["provider"], r.get("last_error", "")), 0) + r["n"]
            else:
                p["real"] += r["n"]
                p["input_tokens"] += r["input_tokens"]
                p["output_tokens"] += r["output_tokens"]
                p["units"] += r["units"]
                p["cost_unknown"] += r["cost_unknown"]
                for cur, amt in r["cost"].items():
                    p["cost"][cur] = round(p["cost"].get(cur, 0.0) + amt, 6)
            q = by_purpose.setdefault(r["purpose"], {"real": 0, "cached": 0, "failed": 0})
            q["cached" if r["cached"] else ("real" if r["ok"] else "failed")] += r["n"]
        return {"providers": by_provider, "purposes": by_purpose,
                "errors": sorted(((prov, msg, n) for (prov, msg), n in errors.items()), key=lambda x: -x[2])}

    def real_calls(self, provider: str, prefix: str) -> int:
        return sum(r["n"] for r in self.rows(prefix) if r["provider"] == provider and not r["cached"])

    def real_cost(self, provider: str, prefix: str) -> dict:
        total = {}
        for r in self.rows(prefix):
            if r["provider"] == provider:
                for cur, amt in r["cost"].items():
                    total[cur] = total.get(cur, 0.0) + amt
        return total

    def recent(self) -> list:
        try:
            return self.load()["recent"]
        except TribunDataError:
            return []

    def mark_warned(self, key: str) -> bool:
        """True, если предупреждение с таким ключом ещё не отправлялось (и теперь помечено)."""
        try:
            data = self.load()
            if key in data["warned"]:
                return False
            data["warned"][key] = self.now().isoformat(timespec="seconds")
            for old in sorted(data["warned"])[:-200]:
                del data["warned"][old]
            atomic_write_json(self.path, data)
            return True
        except Exception as e:
            print(f"[TRIBUN] отметка предупреждения: {e}")
            return False

    def pending_warnings(self) -> list:
        """Предупреждения о расходе (одно на день/месяц/провайдера): превышение числа вызовов или суммы, заданных в лимитах."""
        limits = self.pricing.limits()
        today, month = self.day_key(), self.day_key()[:7]
        out = []
        for provider in PROVIDERS:
            for scope, prefix, label in (("daily", today, "за сегодня"), ("monthly", month, "за месяц")):
                calls = self.real_calls(provider, prefix)
                cap = limits[f"{scope}_calls"].get(provider)
                if cap and calls > cap and self.mark_warned(f"{scope}:{prefix}:{provider}:calls"):
                    out.append(f"{PROVIDER_TITLE[provider]}: {calls} реальных вызовов {label} (порог {cap}). Проверьте, нет ли лишних повторов.")
                cost_cap = limits[f"{scope}_cost"].get(provider)
                if cost_cap:
                    spent = sum(self.real_cost(provider, prefix).values())
                    if spent > cost_cap and self.mark_warned(f"{scope}:{prefix}:{provider}:cost"):
                        out.append(f"{PROVIDER_TITLE[provider]}: оценочные расходы {label} {spent:.2f} (порог {cost_cap}).")
        return out


# ---- защита от расходов -----------------------------------------------------------------------------------

def request_key(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(repr(p).encode("utf-8", errors="ignore"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


class Guard:
    """Память процесса: кэш одинаковых запросов, лимит частоты на (провайдер, назначение), пауза после серии неудач одного запроса.
    Останавливается только проблемный вызов; сбой защиты никогда не блокирует работу (fail-open)."""

    def __init__(self, tracker: UsageTracker, now=utc_now, clock=time.monotonic):
        self.tracker = tracker
        self.now = now
        self.clock = clock
        self.cache: dict = {}                    # (provider, key) → (monotonic_time, value)
        self.calls: dict = {}                    # (provider, purpose) → [monotonic_time, ...]
        self.failures: dict = {}                 # (provider, key) → [monotonic_time, ...]
        self.inflight: set = set()
        self.alerts: list = []                   # накопленные тексты для владельца (забираются тиком планировщика)

    def cached(self, provider: str, key: str):
        ttl = self.tracker.pricing.limits()["duplicate_ttl_seconds"].get(provider)
        item = self.cache.get((provider, key))
        if ttl and item and self.clock() - item[0] < ttl:
            return item
        return None

    def remember(self, provider: str, key: str, value) -> None:
        if not self.tracker.pricing.limits()["duplicate_ttl_seconds"].get(provider):
            return
        self.cache[(provider, key)] = (self.clock(), value)
        for k in sorted(self.cache, key=lambda x: self.cache[x][0])[:-50]:
            del self.cache[k]

    def _alert_once(self, key: str, text: str) -> None:
        if self.tracker.mark_warned(f"guard:{self.tracker.day_key()}:{self.clock() // 3600:.0f}:{key}"):
            self.alerts.append(text)

    def check(self, provider: str, purpose_name: str | None, key: str) -> None:
        """Бросает CostGuardBlocked, если именно этот вызов нужно остановить. Любая внутренняя ошибка защиты → вызов разрешён."""
        try:
            limits = self.tracker.pricing.limits()
            now = self.clock()
            if (provider, key) in self.inflight:
                raise CostGuardBlocked("тот же запрос уже выполняется")
            window = limits["rate_window_minutes"] * 60
            recent = [t for t in self.calls.get((provider, purpose_name or "unknown"), []) if now - t < window]
            self.calls[(provider, purpose_name or "unknown")] = recent
            cap = limits["rate_calls"].get(provider)
            if cap and len(recent) >= cap:
                self._alert_once(f"rate:{provider}:{purpose_name}", f"{PROVIDER_TITLE[provider]}: аномальная частота вызовов "
                                 f"({len(recent)} за {limits['rate_window_minutes']} мин, назначение «{PURPOSE_TITLE.get(purpose_name, purpose_name)}»). "
                                 "Этот процесс остановлен до снижения частоты, остальное работает.")
                raise CostGuardBlocked("лимит частоты вызовов")
            pause = limits["retry_pause_minutes"] * 60
            fails = [t for t in self.failures.get((provider, key), []) if now - t < pause]
            self.failures[(provider, key)] = fails
            if len(fails) >= limits["retry_failures"]:
                self._alert_once(f"retry:{provider}:{key}", f"{PROVIDER_TITLE[provider]}: один и тот же запрос подряд не удался {len(fails)} раза — "
                                 f"повторы приостановлены на {limits['retry_pause_minutes']} мин.")
                raise CostGuardBlocked("серия неудач одного запроса")
        except CostGuardBlocked:
            raise
        except Exception as e:
            print(f"[TRIBUN] защита расходов: {type(e).__name__}: {e}")

    def started(self, provider: str, purpose_name: str | None, key: str) -> None:
        self.inflight.add((provider, key))
        self.calls.setdefault((provider, purpose_name or "unknown"), []).append(self.clock())

    def finished(self, provider: str, key: str, ok: bool) -> None:
        self.inflight.discard((provider, key))
        if ok:
            self.failures.pop((provider, key), None)
        else:
            self.failures.setdefault((provider, key), []).append(self.clock())
