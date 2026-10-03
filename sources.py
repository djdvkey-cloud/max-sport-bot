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

import club_aliases as A
import tribun_catalog as C
from tribun_io import TribunDataError, atomic_write_json, mask_secrets, read_strict, utc_now

OFFICIAL, DIRECT, AGGREGATOR, MEDIA, SEARCH = "OFFICIAL", "DIRECT", "AGGREGATOR", "MEDIA", "SEARCH"
TYPE_RANK = {OFFICIAL: 1, DIRECT: 2, AGGREGATOR: 3, MEDIA: 4, SEARCH: 5}
SCHEDULE, LIVE, RESULT, NEWS, CROSSCHECK = "SCHEDULE", "LIVE", "RESULT", "NEWS", "CROSSCHECK"

COVERED, FALLBACK_ONLY, GAP, FAILED, PARTIAL = "COVERED", "FALLBACK_ONLY", "GAP", "FAILED", "PARTIAL"
COVERAGE_ICON = {COVERED: "✅", FALLBACK_ONLY: "🟡", GAP: "⚠️", FAILED: "❌", PARTIAL: "🟡"}
COVERAGE_TEXT = {COVERED: "есть рабочий источник", FALLBACK_ONLY: "только поиск/резерв", GAP: "надёжного источника нет",
                 FAILED: "источник настроен, но не работает", PARTIAL: "покрыто частично"}

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
    comp_keys: tuple = ()          # турниры каталога, ВСЕ клубы которых отдаёт этот источник (адаптеры feed.py)
    live: bool = False             # отдаёт ли идущие матчи


def adapter_source_defs() -> list:
    """АКТИВНЫЕ источники-адаптеры пилотного каталога (feed.py): один источник отдаёт все клубы турнира, парсеров «на клуб» нет."""
    football = ("rpl", "apl", "laliga", "seriea", "ucl")
    return [
        SourceDef("sportsru:center:football", "Sports.ru — матч-центр (футбол)", "sports.ru", "football", AGGREGATOR, (SCHEDULE, RESULT, LIVE), 2,
                  competition=tuple(C.COMP_BY_KEY[k][1] for k in football), urls=("https://www.sports.ru/football/match/",), comp_keys=football, live=True),
        SourceDef("wikipedia:ucl", "Википедия — Лига чемпионов 2026/27 (сверка)", "en.wikipedia.org", "football", MEDIA, (SCHEDULE, RESULT, CROSSCHECK), 4,
                  competition=("Лига чемпионов",), urls=("https://en.wikipedia.org/wiki/2026%E2%80%9327_UEFA_Champions_League_league_phase",),
                  comp_keys=("ucl",)),
        SourceDef("sportsru:center:hockey", "Sports.ru — матч-центр (хоккей)", "sports.ru", "hockey", AGGREGATOR, (SCHEDULE, RESULT, LIVE, CROSSCHECK), 2,
                  competition=("КХЛ", "NHL"), urls=("https://www.sports.ru/hockey/match/",), comp_keys=("khl", "nhl"), live=True),
        SourceDef("nhl:api", "NHL — официальный API (api-web.nhle.com)", "api-web.nhle.com", "hockey", OFFICIAL, (SCHEDULE, RESULT, LIVE, CROSSCHECK), 1,
                  competition=("NHL",), urls=("https://api-web.nhle.com/v1/schedule/",), comp_keys=("nhl",), live=True),
        SourceDef("rfs:superliga", "Суперлига — официальный сайт superliga.rfs.ru", "superliga.rfs.ru", "futsal", OFFICIAL, (SCHEDULE, RESULT), 1,
                  competition=("Суперлига",), urls=("https://superliga.rfs.ru/",), comp_keys=("superliga",)),
        SourceDef("sportsru:club:football", "Sports.ru — страницы клубов (Первая лига)", "sports.ru", "football", AGGREGATOR, (SCHEDULE, RESULT), 2,
                  competition=("Первая лига",), urls=("https://www.sports.ru/football/club/",), comp_keys=("fnl1",)),
    ]


# ОТЛОЖЕНО (не в пилоте, в реестр и покрытие не входит, опроса нет): адаптеры матч-центра для Лиги Европы, Лиги конференций (футбол) и ВТБ (баскетбол)
# работают в matchfeed.py/feed.py. Чтобы вернуть направление: перенести турнир из C.DEFERRED_COMPETITIONS в C.COMPETITIONS и добавить сюда его SourceDef.
DEFERRED_ADAPTERS = {"uel": "sportsru:center:football", "uecl": "sportsru:center:football", "vtb": "sportsru:center:basketball"}


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
                              competition=tuple(c[1] for c in C.COMPETITIONS if club["key"] in C.comp_club_keys(c[0])),
                              entity_ids=(club["key"],), urls=urls))
    defs.extend(adapter_source_defs())
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

    def adapters_for(self, comp_key: str) -> list:
        return [d for d in self.defs.values() if d.enabled and comp_key in d.comp_keys]

    def serving_club(self, club_key: str) -> list:
        """Источники, обслуживающие клуб: прежние (календарь клуба из sport_bot.CLUBS) и адаптеры его турниров (кроме клубов, ещё не встреченных
        в реальных данных источника — A.UNVERIFIED)."""
        out = [d for d in self.defs.values() if club_key in d.entity_ids]
        for comp in C.COMPETITIONS:
            if club_key in C.comp_club_keys(comp[0]) and club_key not in A.UNVERIFIED.get(comp[0], ()):
                out += [d for d in self.adapters_for(comp[0]) if d not in out]
        return out

    def club_coverage(self, club_key: str) -> tuple:
        status, note = self._status(self.serving_club(club_key))
        return status, note

    def competition_matrix(self, comp_key: str) -> dict:
        """Покрытие турнира по клубам: {status, covered, total, pending:[id], sources:[имена], crosscheck:bool, primary:str}."""
        keys = C.comp_club_keys(comp_key)
        adapters = self.adapters_for(comp_key)
        pending = [k for k in keys if k in A.UNVERIFIED.get(comp_key, ())]
        if not adapters:
            return {"status": GAP, "covered": 0, "total": len(keys), "pending": [], "sources": [], "crosscheck": False, "primary": ""}
        ordered = sorted(adapters, key=lambda d: TYPE_RANK[d.source_type])
        healths = [self.health(d.source_id) for d in adapters]
        covered = len(keys) - len(pending)
        if all(h == FAILED_HEALTH for h in healths):
            status = FAILED
        else:
            status = COVERED if covered == len(keys) else PARTIAL
        return {"status": status, "covered": covered, "total": len(keys), "pending": pending, "sources": [d.name for d in ordered],
                "crosscheck": len(adapters) > 1, "primary": ordered[0].name}

    def sport_coverage(self, sport_key: str) -> tuple:
        """Покрытие вида спорта = клубы этого спорта в каталоге, которые SPORTBOT/Трибун получают источником."""
        all_clubs = [c for c in C.CLUBS if c[2] == sport_key]
        served = [c for c in all_clubs if self.serving_club(c[0])]
        if not served:
            return GAP, "SPORTBOT не отслеживает ни одного клуба этого вида спорта"
        statuses = [self.club_coverage(c[0])[0] for c in served]
        note = f"{len(served)} из {len(all_clubs)} клубов каталога"
        if all(s == FAILED for s in statuses):
            return FAILED, note
        if all(s == FALLBACK_ONLY for s in statuses):
            return FALLBACK_ONLY, note
        return (COVERED if len(served) == len(all_clubs) else PARTIAL), note

    def competition_coverage(self, comp_key: str) -> tuple:
        m = self.competition_matrix(comp_key)
        if m["status"] == GAP:
            return GAP, "для турнира нет источника"
        return m["status"], f"{m['covered']}/{m['total']} клубов"

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
            if st in (GAP, FAILED, FALLBACK_ONLY, PARTIAL):
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
