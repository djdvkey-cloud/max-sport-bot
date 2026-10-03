"""Оркестратор расписаний и результатов «Трибуна»: турнир → цепочка источников → единый список матчей.

Цепочка по турнирам (PRIMARY → CROSSCHECK; Tavily/OpenAI/DeepSeek остаются только в прежней автоматике шести клубов — источником факта они не являются):
  КХЛ                             sports.ru матч-центр (дата) — один запрос на вид спорта и день отдаёт ВСЕ матчи турнира
  NHL                             официальный API api-web.nhle.com (OFFICIAL) ← сверка со sports.ru матч-центром
  РПЛ, АПЛ, Ла Лига, Серия А      sports.ru матч-центр (футбол)
  Лига чемпионов                  sports.ru матч-центр (футбол) ← сверка с Википедией (независимо; UEFA.com отдаёт только JS-оболочку)
  (отложено: ВТБ, ЛЕ, ЛК — адаптеры в коде остаются, но в пилотный каталог, опрос и покрытие не входят)
  Первая лига                     страницы клубов sports.ru (общего календаря без JS у sports.ru нет, fnl.pro — Next.js без публичного HTML)
  Суперлига (футзал)              superliga.rfs.ru — общий календарь лиги на главной (OFFICIAL)
Ошибка источника ≠ «матчей нет»: сбой фиксируется в отчёте и в реестре источников, матчи этого источника не подставляются «пустым списком».
Конфликт счёта между источниками не считается достоверным (confidence="conflict", trusted=False) и автоматически не публикуется.
День — московская дата (как группирует sports.ru)."""
import asyncio
import datetime
import re

import aiohttp

import club_aliases as A
import matchfeed as M
import tribun_catalog as C
import tribun_hooks as hooks

UA = "Mozilla/5.0 (compatible; SportBot/1.0; +sport-bot)"        # как в прежнем fetch_url_direct (SPORTBOT)
UA_WIKI = "SportBot/1.0 (+https://github.com/djdvkey-cloud/max-sport-bot)"      # политика Викимедиа: описательный User-Agent со ссылкой, обычный браузерный/общий получает 403
FETCH_TIMEOUT = 20

URL_CENTER = "https://www.sports.ru/{sport}/match/{day}/"
URL_NHL = "https://api-web.nhle.com/v1/schedule/{day}"
URL_SUPERLIGA = "https://superliga.rfs.ru/"
URL_CLUB = "https://www.sports.ru/football/club/{slug}/calendar/"
URL_WIKI_UCL = "https://en.wikipedia.org/wiki/2026%E2%80%9327_UEFA_Champions_League_league_phase"
WIKI_TTL = 3600.0                      # страница Википедии тяжёлая (~2 МБ): не чаще раза в час

# Первая лига 2026/27: id клуба каталога → slug страницы клуба на sports.ru (проверено: у всех 18 страница отдаёт 200 и название клуба)
FNL1_SLUGS = {
    "ural": "ural", "torpedo": "torpedo-moscow", "enisey": "yenisey", "chelyabinsk": "fc-chelyabinsk", "veles": "veles", "sochi": "fc-sochi",
    "spartak_kostroma": "spartak-kostroma", "shinnik": "shinnik", "kamaz": "kamaz", "arsenal_tula": "arsenal-tula", "leningradets": "fc-leningradec",
    "neftekhimik_f": "fc-neftekhimik", "pari_nn": "nizhny-novgorod", "rotor": "rotor", "ska_khb": "ska-energiya", "tekstilshchik": "tekstilshchik",
    "ufa": "ufa", "volga": "volga-ulyanovsk",
}

# турнир → (вид спорта, источник центра) для турниров, которые отдаёт матч-центр sports.ru
CENTER_COMPETITIONS = {"khl": "hockey", "nhl": "hockey", "vtb": "basketball", "rpl": "football", "apl": "football", "laliga": "football",
                       "seriea": "football", "ucl": "football", "uel": "football", "uecl": "football"}

# Порядок доверия при слиянии одного матча из нескольких источников (раньше = главнее)
PRECEDENCE = ("nhl:api", "rfs:superliga", "sportsru:center:hockey", "sportsru:center:football", "sportsru:center:basketball", "sportsru:club:football",
              "wikipedia:ucl")

OK, EMPTY, FAILED = "ok", "confirmed_empty", "failed"


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&laquo;", "«").replace("&raquo;", "»")
            .replace("&mdash;", "—").replace("&ndash;", "–").replace("&quot;", '"'))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


async def default_fetch(url: str) -> tuple:
    """→ (HTTP-статус | None, тело). Сетевая ошибка = (None, описание). Редиректы разрешены, капча/403 видны по статусу."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"User-Agent": UA_WIKI if "wikipedia.org" in url else UA},
                                   timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT)) as resp:
                raw = await resp.read()
                return resp.status, raw.decode("utf-8", errors="ignore")
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        return None, f"{type(e).__name__}: {e}"


class SourceReport:
    """Итог обращения к одному источнику за один запрос."""
    def __init__(self, source_id: str, outcome: str, *, error: str | None = None, matches: int = 0, unknown_teams=(), url: str = ""):
        self.source_id, self.outcome, self.error, self.matches, self.unknown_teams, self.url = source_id, outcome, error, matches, list(unknown_teams), url

    def __repr__(self):
        return f"<{self.source_id} {self.outcome} {self.matches}{' ' + self.error if self.error else ''}>"


class FeedResult:
    def __init__(self, day: datetime.date, matches: list, reports: list, failed_competitions: set):
        self.day, self.matches, self.reports, self.failed_competitions = day, matches, reports, failed_competitions

    def for_club(self, club_id: str) -> list:
        return [m for m in self.matches if club_id in (m["home_team_id"], m["away_team_id"])]


def _identity(m: dict) -> tuple:
    """Матч = турнир + хозяева + гости + день (одна пара играет в сезоне несколько раз, но не дважды в один день)."""
    if m["home_team_id"] and m["away_team_id"]:
        return (m["competition"], m["home_team_id"], m["away_team_id"], m["day"])
    return (m["competition"], A.norm(m["home_team"]), A.norm(m["away_team"]), m["day"])


def _rank(source_id: str) -> int:
    return PRECEDENCE.index(source_id) if source_id in PRECEDENCE else len(PRECEDENCE)


def merge_matches(batches: list) -> list:
    """Один матч из нескольких источников → одна запись. Совпавшие счёт и статус = confirmed; расхождение счёта у завершённого = conflict
    (trusted=False). Дубль внутри одного источника (матч клуба-хозяев и клуба-гостей на страницах клубов) схлопывается."""
    groups: dict = {}
    for matches in batches:
        for m in matches:
            groups.setdefault(_identity(m), []).append(m)
    out = []
    for g in groups.values():
        g = sorted(g, key=lambda x: _rank(x["source_id"]))
        main = dict(g[0])
        main["sources"] = list(dict.fromkeys(x["source_id"] for x in g))
        others = [x for x in g[1:] if x["source_id"] != main["source_id"]]
        if not others:
            main["confidence"], main["trusted"] = "single", True
        else:
            agree = all(x["status"] == main["status"] and x["score_home"] == main["score_home"] and x["score_away"] == main["score_away"] for x in others)
            score_conflict = any(main["status"] == M.FINISHED and x["status"] == M.FINISHED
                                 and (x["score_home"], x["score_away"]) != (main["score_home"], main["score_away"]) for x in others)
            time_differs = any(main["status"] == M.SCHEDULED and x["status"] == M.SCHEDULED and main["kickoff"] and x["kickoff"]
                               and main["kickoff"] != x["kickoff"] for x in others)
            if score_conflict:
                main["confidence"], main["trusted"] = "conflict", False
            elif agree and time_differs:
                main["confidence"], main["trusted"] = "time_differs", True      # дата совпала, время начала источники называют по-разному
                main["other_kickoffs"] = {x["source_id"]: x["kickoff"] for x in others if x["kickoff"]}
            elif agree:
                main["confidence"], main["trusted"] = "confirmed", True
            else:
                main["confidence"], main["trusted"] = "status_differs", True
        out.append(main)
    return sorted(out, key=lambda m: (m["kickoff"] or datetime.datetime.max.replace(tzinfo=M.UTC), m["competition"], m["home_team"]))


class MatchFeed:
    def __init__(self, fetch=default_fetch, now=None):
        self.fetch = fetch
        self.now = now or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self._cache: dict = {}

    # ---- один источник ------------------------------------------------------------------------------------------------------

    async def _get(self, url: str) -> tuple:
        return await self.fetch(url)

    def _report(self, rep: SourceReport) -> SourceReport:
        hooks.source_event(rep.source_id, rep.outcome, rep.error)
        return rep

    async def _run(self, source_id: str, url: str, parse, *, text: bool = True, sanity=None) -> tuple:
        """Скачать → разобрать → (матчи, отчёт). Любой сбой — отчёт failed и пустой список матчей (не «матчей нет»)."""
        status, body = await self._get(url)
        if status != 200:
            reason = "нет связи" if status is None else f"HTTP {status}"
            return [], self._report(SourceReport(source_id, FAILED, error=f"{reason}: {str(body)[:80]}" if status is None else reason, url=url))
        try:
            matches, stats = parse(html_to_text(body) if text else body)
        except M.SourceFormatError as e:
            return [], self._report(SourceReport(source_id, FAILED, error=str(e), url=url))
        except Exception as e:                                                         # неожиданная вёрстка не должна ронять сбор
            return [], self._report(SourceReport(source_id, FAILED, error=f"{type(e).__name__}: {e}", url=url))
        if not matches and stats["raw_blocks"] == 0 and not (sanity and sanity(html_to_text(body) if text else body)):
            return [], self._report(SourceReport(source_id, FAILED, error="вёрстка изменилась: страница не распознана", url=url))
        outcome = OK if matches else EMPTY
        skipped = stats.get("skipped_records", 0)
        note = f"пропущено нераспознанных записей: {skipped}" if skipped else None             # источник отработал, но часть записей не разобралась — виден в логе
        return matches, self._report(SourceReport(source_id, outcome, error=note, matches=len(matches), unknown_teams=stats["unknown_teams"], url=url))

    @staticmethod
    def _center_page_ok(text: str) -> bool:
        lines = M.clean_lines(text)
        return any(ln == "Все матчи" or ln.startswith("Матчи ") or ln == "Матчи" for ln in lines)

    async def center(self, sport: str, day: datetime.date) -> tuple:
        now = self.now()
        url = URL_CENTER.format(sport=sport, day=day.isoformat())
        return await self._run(f"sportsru:center:{sport}", url,
                               lambda t: M.parse_sportsru_day(sport, t, day, source_url=url, retrieved_at=now), sanity=self._center_page_ok)

    async def nhl(self, day: datetime.date) -> tuple:
        now = self.now()
        url = URL_NHL.format(day=(day - datetime.timedelta(days=1)).isoformat())      # неделя с предыдущей даты: ночные (по Москве) матчи принадлежат вечеру США
        return await self._run("nhl:api", url, lambda t: M.parse_nhl_schedule(t, source_url=url, retrieved_at=now), text=False)

    async def superliga(self) -> tuple:
        now = self.now()
        ref = now.astimezone(M.EKB).date()
        return await self._run("rfs:superliga", URL_SUPERLIGA,
                               lambda t: M.parse_superliga_calendar(t, ref, source_url=URL_SUPERLIGA, retrieved_at=now))

    async def wiki_ucl(self) -> tuple:
        """Все 144 матча фазы лиги из Википедии (сверка ЛЧ). Кэш на час: страница тяжёлая, а данные для сверки меняются редко."""
        now = self.now()
        hit = self._cache.get("wiki_ucl")
        if hit and (now - hit[0]).total_seconds() < WIKI_TTL:
            return hit[1], hit[2]
        matches, rep = await self._run("wikipedia:ucl", URL_WIKI_UCL,
                                       lambda t: M.parse_wikipedia_ucl(t, source_url=URL_WIKI_UCL, retrieved_at=now), text=False)
        if rep.outcome != FAILED:
            self._cache["wiki_ucl"] = (now, matches, rep)
        return matches, rep

    async def fnl1_club(self, club_id: str) -> tuple:
        now = self.now()
        url = URL_CLUB.format(slug=FNL1_SLUGS[club_id])
        return await self._run("sportsru:club:football", url,
                               lambda t: M.parse_sportsru_club_calendar(t, club_id, sport="football", source_url=url, retrieved_at=now),
                               sanity=lambda t: any(M.CLUB_DATE_RE.match(ln) for ln in M.clean_lines(t)))

    # ---- сбор за день ----------------------------------------------------------------------------------------------------------

    async def collect(self, day: datetime.date, *, competitions=None, clubs=None) -> FeedResult:
        """Матчи дня (московская дата) по турнирам `competitions` (None = все) и, для Первой лиги, только клубов `clubs` (None = все 18)."""
        wanted = set(competitions) if competitions is not None else {c[0] for c in C.COMPETITIONS}
        jobs, labels = [], []

        def add(coro, comps):
            jobs.append(coro)
            labels.append(comps)

        for sport in ("hockey", "football", "basketball"):
            comps = {c for c, s in CENTER_COMPETITIONS.items() if s == sport} & wanted
            if comps:
                add(self.center(sport, day), comps)
        if "nhl" in wanted:
            add(self.nhl(day), {"nhl"})
        if "superliga" in wanted:
            add(self.superliga(), {"superliga"})
        if "ucl" in wanted:
            add(self.wiki_ucl(), {"ucl"})
        if "fnl1" in wanted:
            for club_id in (sorted(set(clubs) & set(FNL1_SLUGS)) if clubs is not None else sorted(FNL1_SLUGS)):
                add(self.fnl1_club(club_id), {"fnl1"})
        results = await asyncio.gather(*jobs)
        batches, reports = [], []
        ok_by_comp: dict = {c: False for c in wanted}
        tried: dict = {c: 0 for c in wanted}
        for (matches, rep), comps in zip(results, labels):
            reports.append(rep)
            batches.append(matches)
            for c in comps:
                tried[c] += 1
                if rep.outcome != FAILED:
                    ok_by_comp[c] = True
        merged = [m for m in merge_matches(batches) if m["competition"] in wanted and m["day"] == day]
        failed = {c for c in wanted if tried[c] and not ok_by_comp[c]}
        return FeedResult(day, merged, reports, failed)

    # ---- интересы группы ---------------------------------------------------------------------------------------------------------

    @staticmethod
    def aggregate_interests(profiles: list) -> dict:
        """Активные участники группы → агрегат без имён: {'clubs': {club_id: n}, 'competitions': {comp: n}, 'sports': {sport: n}, 'members': N}.
        Учитывается только то, что есть в АКТИВНОМ каталоге (C.effective_selection): выбор баскетбола, ВТБ, ЛЕ, ЛК остаётся в профиле, но не считается."""
        agg = {"clubs": {}, "competitions": {}, "sports": {}, "members": 0}
        for p in profiles:
            if not p.get("active_in_group"):
                continue
            agg["members"] += 1
            eff = C.effective_selection(p)
            for code, bucket in (("cl", "clubs"), ("cp", "competitions"), ("sp", "sports")):
                for v in eff[code]:
                    agg[bucket][v] = agg[bucket].get(v, 0) + 1
        return agg

    async def watchlist(self, profiles: list, day: datetime.date) -> list:
        """ACTIVE GROUP INTERESTS ∩ COVERED ∩ MATCHES TODAY: матчи дня, интересные хотя бы одному участнику (клуб или чемпионат), по убыванию числа
        заинтересованных. Рубрика пока НЕ публикуется — функция только готовит данные."""
        agg = self.aggregate_interests(profiles)
        comps = set(agg["competitions"])
        for club_id in agg["clubs"]:
            comps |= {c[0] for c in C.COMPETITIONS if club_id in C.comp_club_keys(c[0])}
        fnl1_clubs = set(FNL1_SLUGS) if "fnl1" in agg["competitions"] else set(agg["clubs"]) & set(FNL1_SLUGS)
        res = await self.collect(day, competitions=comps, clubs=fnl1_clubs)
        out = []
        for m in res.matches:
            by_club = max([agg["clubs"].get(m["home_team_id"], 0), agg["clubs"].get(m["away_team_id"], 0)])
            by_comp = agg["competitions"].get(m["competition"], 0)
            interested = max(by_club, by_comp)
            if interested:
                out.append({**m, "interested": interested, "club_fans": by_club})
        return sorted(out, key=lambda m: (-m["interested"], -m["club_fans"], m["kickoff"] or datetime.datetime.max.replace(tzinfo=M.UTC)))


SMOKE_OFFSETS = (0, 1, 7)          # сегодня, завтра и через неделю (в выходные там матчи футбольных лиг — иначе футбол проверяется только на «пусто»)
WHOLE_SEASON_SOURCES = {"fnl1", "superliga"}         # источники без привязки к дню: достаточно одного обращения за проверку


async def smoke(f: MatchFeed, now: datetime.datetime | None = None, offsets=SMOKE_OFFSETS) -> list:
    """Реальная проверка всех источников с того места, где запущен бот (для логов Amvera): по строке на источник и на турнир.
    Здоровье источников пишется в реестр (hooks.source_event) теми же вызовами, что и боевой сбор."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    start = now.astimezone(M.MSK).date()
    lines = []
    for n, offset in enumerate(offsets):
        day = start + datetime.timedelta(days=offset)
        comps = None if n == 0 else {c[0] for c in C.COMPETITIONS} - WHOLE_SEASON_SOURCES
        res = await f.collect(day, competitions=comps)
        agg: dict = {}
        for rep in res.reports:
            row = agg.setdefault(rep.source_id, {"ok": 0, "failed": 0, "empty": 0, "matches": 0, "errors": set(), "unknown": set()})
            row["ok" if rep.outcome == OK else "empty" if rep.outcome == EMPTY else "failed"] += 1
            row["matches"] += rep.matches
            if rep.error:
                row["errors"].add(rep.error)
            row["unknown"] |= set(rep.unknown_teams)
        for sid, row in sorted(agg.items()):
            state = "FAILED" if row["failed"] and not (row["ok"] or row["empty"]) else ("PARTIAL" if row["failed"] else "ok")
            extra = f" ошибки: {'; '.join(sorted(row['errors']))}" if row["errors"] else ""
            unk = f" неопознанные команды: {sorted(row['unknown'])[:6]}" if row["unknown"] else ""
            lines.append(f"[FEED] {day} {sid}: {state}, обращений ok={row['ok']} пусто={row['empty']} сбой={row['failed']}, матчей разобрано {row['matches']}{extra}{unk}")
        per = {}
        for m in res.matches:
            per.setdefault(m["competition"], {"n": 0, "conflict": 0, "confirmed": 0})
            per[m["competition"]]["n"] += 1
            per[m["competition"]]["conflict"] += m["confidence"] == "conflict"
            per[m["competition"]]["confirmed"] += m["confidence"] == "confirmed"
        lines.append(f"[FEED] {day} матчей по турнирам: " + (", ".join(f"{k}={v['n']}" + (f" (сверено {v['confirmed']})" if v["confirmed"] else "")
                                                               + (f" КОНФЛИКТ {v['conflict']}" if v["conflict"] else "") for k, v in sorted(per.items())) or "нет")
                     + (f"; турниры без ответа источников: {sorted(res.failed_competitions)}" if res.failed_competitions else ""))
    lines += await ucl_probe(f, now)
    return lines


async def ucl_probe(f: MatchFeed, now: datetime.datetime | None = None, past: int = 3, future: int = 2) -> list:
    """Живая проверка Лиги чемпионов с сервера бота: последние сыгранные игровые дни и ближайшие будущие (дни берутся из независимого источника —
    Википедии, а не из того, что проверяем). По каждому дню: сколько матчей, сколько клубов распознано, неопознанные, сверка с Википедией и примеры результатов."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today = now.astimezone(M.MSK).date()
    wiki, rep = await f.wiki_ucl()
    if rep.outcome == FAILED:
        return [f"[FEED] UCL: календарь Википедии недоступен ({rep.error}) — игровые дни не определены"]
    played = sorted({m["day"] for m in wiki if m["status"] == M.FINISHED and m["day"] <= today})[-past:]
    upcoming = sorted({m["day"] for m in wiki if m["status"] == M.SCHEDULED and m["day"] >= today})[:future]
    lines = [f"[FEED] UCL: игровые дни по Википедии — сыграны {[str(d) for d in played]}, впереди {[str(d) for d in upcoming]}"]
    names = {k: C.CLUB_BY_KEY[k][1] for k in C.comp_club_keys("ucl") if k in C.CLUB_BY_KEY}
    for day in played + upcoming:
        res = await f.collect(day, competitions={"ucl"})
        ms = [m for m in res.matches if m["competition"] == "ucl"]
        ids = {m["home_team_id"] for m in ms} | {m["away_team_id"] for m in ms}
        unknown = sorted({n for r in res.reports for _c, n in r.unknown_teams})
        confirmed = sum(m["confidence"] == "confirmed" for m in ms)
        conflict = sum(m["confidence"] == "conflict" for m in ms)
        time_diff = sum(m["confidence"] == "time_differs" for m in ms)
        sample = "; ".join(f"{names.get(m['home_team_id'], m['home_team'])} {m['score_home']}:{m['score_away']} {names.get(m['away_team_id'], m['away_team'])}"
                           for m in ms if m["status"] == M.FINISHED)[:240]
        times = "; ".join(f"{names.get(m['home_team_id'], m['home_team'])}—{names.get(m['away_team_id'], m['away_team'])} "
                          f"{m['kickoff'].astimezone(M.MSK).strftime('%H:%M')} мск" for m in ms if m["kickoff"] and m["status"] == M.SCHEDULED)[:240]
        diffs = "; ".join(f"{names.get(m['home_team_id'], m['home_team'])}—{names.get(m['away_team_id'], m['away_team'])}: {m['source_id']} "
                          f"{m['kickoff'].astimezone(M.MSK).strftime('%H:%M')} мск, " + ", ".join(f"{sid} {k.astimezone(M.MSK).strftime('%H:%M')} мск"
                                                                                                for sid, k in m.get("other_kickoffs", {}).items())
                          for m in ms if m["confidence"] == "time_differs")
        lines.append(f"[FEED] UCL {day}: матчей {len(ms)}, клубов распознано {len(ids - {None})}, неопознанных {len(unknown)} {unknown}, "
                     f"сверено с Википедией {confirmed}, расхождений времени {time_diff}, конфликтов {conflict}, источники без ответа: {sorted(res.failed_competitions)}"
                     + (f" | результаты: {sample}" if sample else "") + (f" | расписание: {times}" if times else "")
                     + (f" | РАСХОЖДЕНИЕ ВРЕМЕНИ: {diffs}" if diffs else ""))
    return lines
