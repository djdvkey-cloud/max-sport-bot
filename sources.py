"""Реестр источников «Трибуна»: что откуда берётся, здоровье по реальным запросам, покрытие интересов, разрывы.

Правила:
• Реестр ОПИСЫВАЕТ то, что SPORTBOT уже использует (календари клубов напрямую, поиск Tavily, резерв OpenAI web_search); старые источники
  не переделываются, парсеры не пишутся автоматически, платные API не подключаются.
• Иерархия: OFFICIAL > DIRECT > AGGREGATOR > MEDIA > SEARCH. LLM источником факта не является (DeepSeek/OpenAI — оформление/разбор
  найденного, учитываются в расходах, но в реестре источников их нет).
• Здоровье строится из реальных рабочих запросов: успех обновляет, ошибка наращивает consecutive_failures, успех возвращает OK.
• CONFIRMED_EMPTY (страница получена, матчей нет) ≠ SOURCE_FAILED (источник не ответил / вёрстка не разобралась) ≠ NOT_FOUND.
  Ошибка источника никогда не превращается в «событий нет».
• Секреты в реестр не пишутся."""
import datetime
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

import tribun_catalog as C
from tribun_io import TribunDataError, atomic_write_json, mask_secrets, read_strict, utc_now

OFFICIAL, DIRECT, AGGREGATOR, MEDIA, SEARCH = "OFFICIAL", "DIRECT", "AGGREGATOR", "MEDIA", "SEARCH"
TYPE_RANK = {OFFICIAL: 1, DIRECT: 2, AGGREGATOR: 3, MEDIA: 4, SEARCH: 5}
SCHEDULE, LIVE, RESULT, NEWS, CROSSCHECK = "SCHEDULE", "LIVE", "RESULT", "NEWS", "CROSSCHECK"

COVERED, FALLBACK_ONLY, GAP, FAILED = "COVERED", "FALLBACK_ONLY", "GAP", "FAILED"
COVERAGE_ICON = {COVERED: "✅", FALLBACK_ONLY: "🟡", GAP: "⚠️", FAILED: "❌"}
COVERAGE_TEXT = {COVERED: "есть рабочий источник", FALLBACK_ONLY: "только поиск/резерв", GAP: "надёжного источника нет",
                 FAILED: "источник настроен, но не работает"}

OK, DEGRADED, FAILED_HEALTH, UNKNOWN = "OK", "DEGRADED", "FAILED", "UNKNOWN"
HEALTH_ICON = {OK: "✅", DEGRADED: "🟡", FAILED_HEALTH: "❌", UNKNOWN: "▫️"}
DEGRADED_AFTER, FAILED_AFTER = 1, 3                    # подряд ошибок

OUTCOME_OK, OUTCOME_EMPTY, OUTCOME_FAILED = "ok", "confirmed_empty", "failed"


@dataclass
class SourceDef:
    source_id: str
    name: str
    domain: str
    sport: str | None
    source_type: str
    purpose: tuple
    priority: int
    competition: tuple = ()
    entity_ids: tuple = ()
    urls: tuple = ()
    enabled: bool = True


def build_source_defs(clubs: list, openai_enabled: bool = False, search_domains: list | None = None) -> list:
    """Описание уже работающих источников SPORTBOT (список клубов берётся из sport_bot.CLUBS — единственного источника правды)."""
    defs = []
    for club in clubs:
        urls = tuple(club.get("extract_urls") or ())
        if not urls:
            continue
        domain = urlparse(urls[0]).netloc.removeprefix("www.")
        sport = {"futsal": "futsal", "football": "football", "hockey": "hockey"}.get(club["sport"], club["sport"])
        defs.append(SourceDef(source_id=f"calendar:{club['key']}", name=f"Календарь {domain} — {club['name']}", domain=domain, sport=sport,
                              source_type=DIRECT, purpose=(SCHEDULE, RESULT, CROSSCHECK), priority=2,
                              competition=tuple(c[1] for c in C.COMPETITIONS if club["key"] in c[3]),
                              entity_ids=(club["key"],), urls=urls))
    keys = tuple(c["key"] for c in clubs)
    defs.append(SourceDef(source_id="search:tavily", name="Tavily Search (список доверенных доменов)", domain="api.tavily.com", sport=None,
                          source_type=SEARCH, purpose=(SCHEDULE, RESULT), priority=5, entity_ids=keys))
    defs.append(SourceDef(source_id="search:openai", name="OpenAI web_search (резерв результата и перепроверка)", domain="api.openai.com",
                          sport=None, source_type=SEARCH, purpose=(RESULT, CROSSCHECK), priority=5, entity_ids=keys, enabled=openai_enabled))
    return defs


class SourceRegistry:
    def __init__(self, data_dir: str, defs: list, now=utc_now):
        self.defs = {d.source_id: d for d in defs}
        self.path = os.path.join(data_dir, "tribun_sources.json")
        self.now = now

    # ---- состояние ------------------------------------------------------------------------

    def load(self) -> dict:
        data = read_strict(self.path, {"sources": {}, "alerts": {}})
        if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
            raise TribunDataError(self.path, "неверная структура")
        data.setdefault("alerts", {})
        return data

    def save(self, data: dict) -> None:
        atomic_write_json(self.path, data)

    def state(self, source_id: str) -> dict:
        try:
            return dict(self.load()["sources"].get(source_id, {}))
        except TribunDataError:
            return {}

    def health_of(self, state: dict) -> str:
        if not state:
            return UNKNOWN
        failures = state.get("consecutive_failures", 0)
        if failures >= FAILED_AFTER:
            return FAILED_HEALTH
        if failures >= DEGRADED_AFTER:
            return DEGRADED
        return OK if state.get("last_success_at") else UNKNOWN

    def health(self, source_id: str) -> str:
        return self.health_of(self.state(source_id))

    def record(self, source_id: str, outcome: str, error: str | None = None) -> tuple:
        """Итог реального запроса. ok / confirmed_empty — источник отработал; failed — не ответил или вёрстка не разобралась.
        → (прежнее здоровье, новое здоровье). Повреждённый файл реестра не перезаписывается."""
        try:
            data = self.load()
        except TribunDataError as e:
            print(f"[TRIBUN] {e}")
            return UNKNOWN, UNKNOWN
        st = data["sources"].setdefault(source_id, {"consecutive_failures": 0})
        before = self.health_of(st)
        stamp = self.now().isoformat()
        st["last_outcome"] = outcome
        if outcome == OUTCOME_FAILED:
            st["last_failure_at"] = stamp
            st["consecutive_failures"] = st.get("consecutive_failures", 0) + 1
            st["last_error_short"] = mask_secrets(error or "")[:120]
        else:
            st["last_success_at"] = stamp
            st["consecutive_failures"] = 0
        try:
            self.save(data)
        except OSError as e:
            print(f"[TRIBUN] реестр источников не записан: {e}")
        return before, self.health_of(st)

    def source_for_url(self, url: str):
        for d in self.defs.values():
            if url in d.urls:
                return d.source_id
        return None

    # ---- обзор -----------------------------------------------------------------------------------

    def rows(self) -> list:
        try:
            states = self.load()["sources"]
        except TribunDataError:
            states = {}
        out = []
        for d in sorted(self.defs.values(), key=lambda x: (TYPE_RANK[x.source_type], x.source_id)):
            st = states.get(d.source_id, {})
            out.append({"def": d, "health": self.health_of(st) if d.enabled else UNKNOWN, "state": st})
        return out

    # ---- покрытие --------------------------------------------------------------------------------

    def _status(self, sources: list) -> tuple:
        """Статус покрытия по списку источников, обслуживающих сущность (только включённые)."""
        sources = [d for d in sources if d.enabled]
        main = [d for d in sources if d.source_type != SEARCH]
        search = [d for d in sources if d.source_type == SEARCH]
        if main:
            healths = [self.health(d.source_id) for d in main]
            if all(h == FAILED_HEALTH for h in healths):
                return FAILED, "; ".join(d.name for d in main)
            return COVERED, "; ".join(d.name for d in main)
        if search:
            return FALLBACK_ONLY, "; ".join(d.name for d in search)
        return GAP, ""

    def serving_club(self, club_key: str) -> list:
        return [d for d in self.defs.values() if club_key in d.entity_ids]

    def club_coverage(self, club_key: str) -> tuple:
        status, note = self._status(self.serving_club(club_key))
        return status, note

    def sport_coverage(self, sport_key: str) -> tuple:
        """Покрытие вида спорта = клубы этого спорта, которые SPORTBOT отслеживает (а не «весь спорт»)."""
        served = [c for c in C.CLUBS if c[2] == sport_key and self.serving_club(c[0])]
        if not served:
            return GAP, "SPORTBOT не отслеживает ни одного клуба этого вида спорта"
        statuses = [self.club_coverage(c[0])[0] for c in served]
        names = ", ".join(c[1] for c in served)
        if all(s == FAILED for s in statuses):
            return FAILED, f"отслеживаются: {names}"
        if all(s == FALLBACK_ONLY for s in statuses):
            return FALLBACK_ONLY, f"отслеживаются: {names}"
        return COVERED, f"отслеживаются клубы: {names}"

    def competition_coverage(self, comp_key: str) -> tuple:
        comp = C.COMP_BY_KEY[comp_key]
        served = [k for k in comp[3] if self.serving_club(k)]
        if not served:
            return GAP, "ни один отслеживаемый клуб не играет в этом турнире"
        statuses = [self.club_coverage(k)[0] for k in served]
        names = ", ".join(C.CLUB_BY_KEY[k][1] for k in served)
        status = FAILED if all(s == FAILED for s in statuses) else (FALLBACK_ONLY if all(s == FALLBACK_ONLY for s in statuses) else COVERED)
        return status, f"только матчи наших клубов: {names}"

    def request_coverage(self, category: str, normalized: str) -> tuple:
        """Покрытие запроса «Другое». Известное совпадение с каталогом наследует его покрытие, остальное — GAP (без догадок)."""
        known = C.match_any(normalized)
        if known:
            cat, key = known
            if cat == C.CAT_CLUB:
                return self.club_coverage(key)
            if cat == C.CAT_CHAMP:
                return self.competition_coverage(key)
            return self.sport_coverage(key)
        return GAP, "в реестре источников такого направления нет"

    def catalog_gaps(self) -> list:
        """Позиции каталога без источников: [(категория, ключ, название, статус, пояснение)]."""
        gaps = []
        for key, icon, name, _ in C.SPORTS:
            st, note = self.sport_coverage(key)
            if st in (GAP, FAILED, FALLBACK_ONLY):
                gaps.append((C.CAT_SPORT, key, name, st, note))
        for comp in C.COMPETITIONS:
            st, note = self.competition_coverage(comp[0])
            if st in (GAP, FAILED, FALLBACK_ONLY):
                gaps.append((C.CAT_CHAMP, comp[0], comp[1], st, note))
        for club in C.CLUBS:
            st, note = self.club_coverage(club[0])
            if st in (GAP, FAILED, FALLBACK_ONLY):
                gaps.append((C.CAT_CLUB, club[0], club[1], st, note))
        return gaps

    # ---- критичные сбои ---------------------------------------------------------------------------------

    def critical_failures(self) -> list:
        """Источники из реально используемых (включённых), упавшие FAILED_AFTER раз подряд, у которых нет рабочей замены того же клуба."""
        out = []
        for row in self.rows():
            d = row["def"]
            if not d.enabled or row["health"] != FAILED_HEALTH:
                continue
            if d.source_type == SEARCH:
                alive = [x for x in self.defs.values() if x.source_id != d.source_id and x.enabled and self.health(x.source_id) != FAILED_HEALTH]
                if alive:
                    continue                                      # есть другой работающий путь — это не критично
            else:
                fallback = [x for x in self.defs.values() if x.source_type == SEARCH and x.enabled
                            and set(x.entity_ids) & set(d.entity_ids) and self.health(x.source_id) != FAILED_HEALTH]
                if fallback:
                    continue
            out.append(row)
        return out
