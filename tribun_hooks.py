"""Тонкие хуки для SPORTBOT: учёт API-вызовов, здоровье источников, защита от лишних расходов.

Главное правило: хуки НИЧЕГО не меняют в логике SPORTBOT. Не настроены (configure не вызывали, TRIBUN_ENABLED=0, тесты старого кода) —
декораторы просто вызывают исходную функцию. Любая ошибка учёта/реестра проглатывается и пишется в лог: учёт не может сломать рабочий процесс.
Единственное, что хук может сделать с вызовом, — остановить его защитой от расходов (CostGuardBlocked, подкласс RuntimeError, как и прочие
ошибки провайдеров) или отдать ответ из кэша на тот же запрос."""
import contextvars
import functools
import time

import costs
import sources as src
from costs import CostGuardBlocked, Guard, PricingConfig, UsageTracker, purpose, purpose_var   # noqa: F401  (purpose — для sport_bot)
from tribun_io import mask_secrets

TRACKER: UsageTracker | None = None
GUARD: Guard | None = None
REGISTRY: src.SourceRegistry | None = None

_holder: contextvars.ContextVar = contextvars.ContextVar("tribun_usage_holder", default=None)


def configure(data_dir: str, source_defs: list) -> None:
    global TRACKER, GUARD, REGISTRY
    TRACKER = UsageTracker(data_dir, PricingConfig(data_dir))
    GUARD = Guard(TRACKER)
    REGISTRY = src.SourceRegistry(data_dir, source_defs)


def reset() -> None:
    global TRACKER, GUARD, REGISTRY
    TRACKER = GUARD = REGISTRY = None


def report_usage(**fields) -> None:
    """Внутри отслеживаемого вызова: input_tokens / output_tokens / units / model. Вне вызова — ничего не делает."""
    holder = _holder.get()
    if holder is not None:
        holder.update({k: v for k, v in fields.items() if v is not None})


def report_failure(message: str = "") -> None:
    """Вызов формально завершился, но провайдер не дал результата (не-200, битый ответ): учитывается как неуспешный."""
    holder = _holder.get()
    if holder is not None:
        holder["failed"] = True
        holder["error"] = message


def source_event(source_id: str | None, outcome: str, error: str | None = None) -> None:
    """Итог реального запроса к источнику → здоровье в реестре. Безопасно при ненастроенном реестре."""
    if REGISTRY is None or not source_id:
        return
    try:
        REGISTRY.record(source_id, outcome, error)
    except Exception as e:
        print(f"[TRIBUN] реестр источников: {type(e).__name__}: {e}")


def source_for_url(url: str) -> str | None:
    try:
        return REGISTRY.source_for_url(url) if REGISTRY else None
    except Exception:
        return None


def tracked_api(provider: str, operation: str, *, model: str | None = None, source_id: str | None = None, key_from=None):
    """Декоратор асинхронной функции-обращения к платному API: запись вызова, кэш одинаковых запросов, защита от лишних повторов.
    key_from(*args, **kwargs) → то, что определяет «тот же запрос» (по умолчанию все аргументы)."""
    def decorate(fn):
        @functools.wraps(fn)
        async def inner(*args, **kwargs):
            tracker, guard = TRACKER, GUARD
            if tracker is None or guard is None:
                return await fn(*args, **kwargs)
            try:
                key = costs.request_key(operation, *(key_from(*args, **kwargs) if key_from else (args, sorted(kwargs.items()))))
                purp = purpose_var.get()
                hit = guard.cached(provider, key)
            except Exception as e:
                print(f"[TRIBUN] хук {provider}: {type(e).__name__}: {e}")
                return await fn(*args, **kwargs)
            if hit is not None:
                tracker.record(provider, operation, purp, True, cached=True, model=model)
                return hit[1]
            guard.check(provider, purp, key)                      # CostGuardBlocked → вызов не выполняется
            holder: dict = {}
            token = _holder.set(holder)
            guard.started(provider, purp, key)
            began, ok, error, result = time.monotonic(), False, None, None
            try:
                result = await fn(*args, **kwargs)
                ok = not holder.get("failed")
                error = holder.get("error")
                return result
            except BaseException as e:
                error = f"{type(e).__name__}: {e}"
                raise
            finally:
                _holder.reset(token)
                try:
                    guard.finished(provider, key, ok)
                    if ok:
                        guard.remember(provider, key, result)
                    tracker.record(provider, operation, purp, ok, latency_ms=int((time.monotonic() - began) * 1000),
                                   model=holder.get("model", model), input_tokens=holder.get("input_tokens"),
                                   output_tokens=holder.get("output_tokens"), units=holder.get("units"), error=mask_secrets(error or ""))
                    if source_id:
                        source_event(source_id, src.OUTCOME_OK if ok else src.OUTCOME_FAILED, error)
                except Exception as e:
                    print(f"[TRIBUN] хук {provider}: {type(e).__name__}: {e}")
        return inner
    return decorate


def with_purpose(name: str):
    """Декоратор асинхронной функции: все API-вызовы внутри учитываются с этим назначением (если вызывающий не задал своё)."""
    def decorate(fn):
        @functools.wraps(fn)
        async def inner(*args, **kwargs):
            if purpose_var.get() is not None:
                return await fn(*args, **kwargs)
            with purpose(name):
                return await fn(*args, **kwargs)
        return inner
    return decorate
