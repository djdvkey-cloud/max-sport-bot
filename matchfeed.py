"""Единый слой расписаний и результатов «Трибуна»: SOURCE → COMPETITION ADAPTER → MATCHES → CLUB NORMALIZATION → единый формат.

Парсеры ЧИСТЫЕ (текст/JSON → список матчей), сеть — в matchfeed_net.py. Один источник турнира даёт матчи ВСЕХ клубов турнира; парсеров «на клуб»
нет (исключение — страница клуба sports.ru для Первой лиги, где турнир целиком у sports.ru недоступен).

Единый формат матча (dict):
  sport, competition (ключ каталога: khl, nhl, rpl, …), season, home_team_id/away_team_id (id клуба каталога или None), home_team/away_team
  (название в источнике), kickoff (UTC, aware datetime | None — время не назначено), source_timezone, status (SCHEDULED/LIVE/FINISHED/POSTPONED/
  CANCELLED), score_home/score_away (int | None), method (ОСНОВНОЕ/ОТ/БУЛЛИТЫ | None), source_id, source_url, retrieved_at.

Правила: клуб определяется только по таблице алиасов (club_aliases.py) в рамках турнира — без нечёткого сопоставления и без LLM; неизвестное
имя остаётся home_team_id=None и считается в статистике разбора. Ошибка источника ≠ «матчей нет» (SourceFormatError ≠ пустой список)."""
import datetime
import json
import re

import club_aliases as A
import tribun_catalog as C

MSK = datetime.timezone(datetime.timedelta(hours=3))
EKB = datetime.timezone(datetime.timedelta(hours=5))
UTC = datetime.timezone.utc

SCHEDULED, LIVE, FINISHED, POSTPONED, CANCELLED = "SCHEDULED", "LIVE", "FINISHED", "POSTPONED", "CANCELLED"

TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
MINUTE_RE = re.compile(r"^\d{1,3}(\+\d+)?[’']$")
PERIOD_RE = re.compile(r"^(\d\s*(период|четверть|тайм)|овертайм|буллиты|перерыв.*|пенальти|доп\. время|\d+-й (период|тайм|четверть))$", re.I)
SEASON_HEADER_RE = re.compile(r"^(?P<name>.+?)\s+(?P<season>\d{4}/\d{4}|\d{4})$")


class SourceFormatError(Exception):
    """Страница получена, но вёрстка не разобралась (≠ подтверждённое «матчей нет»)."""


NOISE_RE = re.compile(r"^(Первый|Ответный) матч", re.I)          # «Первый матч: 4-1» в двухматчевых раундах — не команда


def clean_lines(text: str) -> list:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not NOISE_RE.match(ln.strip())]


def make_match(*, sport, competition, season, home, away, kickoff, tz, status, score_home=None, score_away=None, method=None,
               source_id, source_url, retrieved_at, home_id=None, away_id=None, day=None) -> dict:
    """`day` — московская дата матча (как группирует sports.ru); у завершённых футбольных матчей время не показывается, а дата известна."""
    if home_id is None:
        home_id = A.resolve(competition, home)
    if away_id is None:
        away_id = A.resolve(competition, away)
    if kickoff is not None:
        day = kickoff.astimezone(MSK).date()
    return {"sport": sport, "competition": competition, "season": season, "day": day, "home_team_id": home_id, "home_team": home,
            "away_team_id": away_id, "away_team": away, "kickoff": kickoff, "source_timezone": tz, "status": status,
            "score_home": score_home, "score_away": score_away, "method": method, "source_id": source_id, "source_url": source_url,
            "retrieved_at": retrieved_at}


def kickoff_utc(day: datetime.date, hhmm: str | None, zone: datetime.tzinfo, placeholder_below: int | None = None):
    """Время источника → UTC. None, если время не назначено (заглушки 00:00–03:59 у sports.ru-календарей, но не у матч-центра)."""
    if not hhmm or not TIME_RE.match(hhmm):
        return None
    hh, mm = (int(x) for x in hhmm.split(":"))
    if placeholder_below is not None and hh < placeholder_below:
        return None
    return datetime.datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC)


# ---- sports.ru: матч-центр дня (футбол / хоккей / баскетбол) ---------------------------------------------------------------

# (заголовок блока, страна) → ключ чемпионата каталога. Заголовки взяты с реальных страниц sports.ru (см. tests/fixtures/feed).
FOOTBALL_HEADERS = {
    ("Премьер-лига Россия (РПЛ)", "Россия"): "rpl",
    ("Премьер-лига Англия (АПЛ)", "Англия"): "apl",
    ("Ла Лига Испания", "Испания"): "laliga",
    ("Серия А Италия", "Италия"): "seriea",
    ("Лига чемпионов", "Европа"): "ucl",
    ("Лига Европы", "Европа"): "uel",
    ("Лига Конференций", "Европа"): "uecl",
}
# хоккей/баскетбол: подстрока заголовка «<турнир> 2026/2027» → ключ
SEASON_HEADERS = (("Чемпионат КХЛ", "khl", "hockey"), ("НХЛ", "nhl", "hockey"), ("Единая лига ВТБ", "vtb", "basketball"))

_STATUS_WORDS = ("не начался", "завершен", "перенес", "отмен", "прерван", "не состоял", "техническ")


def _is_status(token: str) -> bool:
    low = token.lower()
    return (token in ("Завтра", "Сегодня", "Вчера") or MINUTE_RE.match(token) is not None or PERIOD_RE.match(token) is not None
            or any(low.startswith(w) or low == w for w in _STATUS_WORDS))


def _status_kind(token: str) -> str:
    low = token.lower()
    if low.startswith("завершен"):
        return FINISHED
    if low.startswith("перенес"):
        return POSTPONED
    if low.startswith("отмен") or low.startswith("не состоял"):
        return CANCELLED
    if low.startswith("не начался") or token in ("Завтра", "Сегодня", "Вчера"):
        return SCHEDULED
    return LIVE


def _plain(token: str) -> bool:
    """Обычная строка (не время, не число, не статус, не служебное слово) — кандидат в название команды/заголовок."""
    return not (TIME_RE.match(token) or token.isdigit() or _is_status(token) or token in ("Трансляция", "превью", ":", "-", "–"))


def _finish(recs: list, raw_blocks: int) -> tuple:
    """Матчи без единого клуба каталога (квалификации еврокубков, чужие клубы) отбрасываются и считаются; если известен только один клуб,
    матч остаётся, а второй попадает в unknown_teams (признак устаревшей таблицы алиасов)."""
    kept = [m for m in recs if m["home_team_id"] or m["away_team_id"]]
    unknown = sorted({(m["competition"], name) for m in kept for name, tid in ((m["home_team"], m["home_team_id"]), (m["away_team"], m["away_team_id"])) if tid is None})
    return kept, {"raw_blocks": raw_blocks, "matches": len(kept), "dropped_unknown": len(recs) - len(kept), "unknown_teams": unknown}


def parse_sportsru_day(sport: str, text: str, day: datetime.date, *, source_url: str, retrieved_at: datetime.datetime) -> tuple:
    """Матч-центр sports.ru за день `day` (московская дата) → (матчи каталожных турниров, статистика разбора).

    Футбол: «Турнир / Страна / [Статус] Хозяева / Гости / ЧЧ:ММ | счёт / счёт / Трансляция».
    Хоккей и баскетбол: «Турнир 2026/2027 / ЧЧ:ММ / Статус / Хозяева / a / : / b / [от|б] / Гости» (или «превью» вместо счёта)."""
    lines = clean_lines(text)
    if sport == "football":
        recs, raw = _parse_football_day(lines, day, source_url, retrieved_at)
    elif sport in ("hockey", "basketball"):
        recs, raw = _parse_season_day(sport, lines, day, source_url, retrieved_at)
    else:
        raise SourceFormatError(f"неизвестный вид спорта {sport}")
    return _finish(recs, raw)


def _football_status_day(token: str, day: datetime.date) -> datetime.date:
    return day + datetime.timedelta(days=1) if token == "Завтра" else day - datetime.timedelta(days=1) if token == "Вчера" else day


def _parse_football_day(lines: list, day, source_url, retrieved_at) -> tuple:
    n = len(lines)
    i, cur, comp, blocks = 0, None, None, 0
    out = []
    while i < n:
        tok = lines[i]
        if (i + 2 < n and _plain(tok) and _plain(lines[i + 1]) and _is_status(lines[i + 2])):
            cur = (tok, lines[i + 1])
            comp = FOOTBALL_HEADERS.get(cur)
            blocks += 1
            i += 2
            continue
        if cur is not None and _is_status(tok) and i + 3 < n and _plain(lines[i + 1]) and _plain(lines[i + 2]):
            home, away = lines[i + 1], lines[i + 2]
            j = i + 3
            time_str, scores = None, []
            if TIME_RE.match(lines[j]):
                time_str = lines[j]
                j += 1
            else:
                while j < n and lines[j].isdigit() and len(scores) < 4:
                    scores.append(int(lines[j]))
                    j += 1
            if comp is not None:
                kind = _status_kind(tok)
                match_day = _football_status_day(tok, day)
                has_score = len(scores) >= 2
                score_h, score_a = (scores[0], scores[1]) if has_score else (None, None)
                if kind == SCHEDULED and has_score:
                    kind = LIVE
                if kind in (FINISHED, LIVE) and not has_score:
                    score_h = score_a = None
                out.append(make_match(sport="football", competition=comp, season=C.CURRENT_SEASON, home=home, away=away,
                                      kickoff=kickoff_utc(match_day, time_str, MSK) if kind == SCHEDULED else None, tz="Europe/Moscow",
                                      status=kind, score_home=score_h, score_away=score_a, source_id="sportsru:center:football",
                                      source_url=source_url, retrieved_at=retrieved_at, day=match_day))
            i = j
            continue
        i += 1
    return out, blocks


def _parse_season_day(sport: str, lines: list, day, source_url, retrieved_at) -> tuple:
    n = len(lines)
    out, blocks = [], 0
    i = 0
    while i < n:
        m = SEASON_HEADER_RE.match(lines[i])
        if not m or i + 1 >= n or not (TIME_RE.match(lines[i + 1]) or re.match(r"^\(\d+\)$", lines[i + 1])):
            i += 1
            continue
        header = m.group("name")
        entry = next(((k, sp) for needle, k, sp in SEASON_HEADERS if needle in header and sp == sport), None)
        blocks += 1
        i += 1
        if i < n and re.match(r"^\(\d+\)$", lines[i]):
            i += 1
        while i < n and TIME_RE.match(lines[i]):
            time_str = lines[i]
            j = i + 1
            status_tok = None
            if j < n and _is_status(lines[j]):
                status_tok = lines[j]
                j += 1
            if j >= n or not _plain(lines[j]):
                i = j
                continue
            home = lines[j]
            j += 1
            score_h = score_a = method = None
            if j + 2 < n and lines[j].isdigit() and lines[j + 1] == ":" and lines[j + 2].isdigit():
                score_h, score_a = int(lines[j]), int(lines[j + 2])
                j += 3
            elif j < n and lines[j] == "превью":
                j += 1
            if j < n and lines[j].lower() in ("от", "б", "бул"):
                method = "ОТ" if lines[j].lower() == "от" else "БУЛЛИТЫ"
                j += 1
            if j >= n or not _plain(lines[j]):
                i = j
                continue
            away = lines[j]
            j += 1
            if entry is not None:
                kind = _status_kind(status_tok) if status_tok else (FINISHED if score_h is not None else SCHEDULED)
                if kind in (FINISHED, LIVE) and score_h is None:
                    kind = SCHEDULED if kind == LIVE else kind
                if kind == FINISHED and method is None:
                    method = "ОСНОВНОЕ"
                out.append(make_match(sport=sport, competition=entry[0], season=C.CURRENT_SEASON, home=home, away=away,
                                      kickoff=kickoff_utc(day, time_str, MSK), tz="Europe/Moscow", status=kind,
                                      score_home=score_h, score_away=score_a, method=method if kind == FINISHED else None,
                                      source_id=f"sportsru:center:{sport}", source_url=source_url, retrieved_at=retrieved_at))
            i = j
    return out, blocks


# ---- NHL: официальный API ----------------------------------------------------------------------------------------------------

NHL_STATES = {"FUT": SCHEDULED, "PRE": SCHEDULED, "LIVE": LIVE, "CRIT": LIVE, "FINAL": FINISHED, "OFF": FINISHED}


def parse_nhl_schedule(raw: str, *, source_url: str, retrieved_at: datetime.datetime) -> tuple:
    """https://api-web.nhle.com/v1/schedule/<дата> → (матчи, статистика). Клуб определяется по официальной аббревиатуре, а не по названию."""
    try:
        data = json.loads(raw)
        weeks = data["gameWeek"]
    except (ValueError, KeyError, TypeError) as e:
        raise SourceFormatError(f"NHL API: неожиданный ответ ({type(e).__name__})") from e
    out, unknown = [], set()
    for day in weeks:
        for g in day.get("games", []):
            try:
                if g.get("gameType") not in (2, 3):                  # регулярный сезон и плей-офф; предсезонка не нужна
                    continue
                home, away = g["homeTeam"], g["awayTeam"]
                state = NHL_STATES.get(g.get("gameState"), SCHEDULED)
                if g.get("gameScheduleState") in ("PPD", "SUSP"):
                    state = POSTPONED
                elif g.get("gameScheduleState") == "CNCL":
                    state = CANCELLED
                start = datetime.datetime.strptime(g["startTimeUTC"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                ids = []
                for side in (home, away):
                    tid = A.NHL_ABBREV.get(side.get("abbrev"))
                    if tid is None:
                        unknown.add(("nhl", side.get("abbrev") or "?"))
                    ids.append(tid)
                finished_or_live = state in (FINISHED, LIVE)
                last = (g.get("gameOutcome") or {}).get("lastPeriodType")
                out.append(make_match(
                    sport="hockey", competition="nhl", season=C.CURRENT_SEASON, home=f'{home["placeName"]["default"]} {home["commonName"]["default"]}',
                    away=f'{away["placeName"]["default"]} {away["commonName"]["default"]}', kickoff=start, tz="UTC", status=state,
                    score_home=home.get("score") if finished_or_live else None, score_away=away.get("score") if finished_or_live else None,
                    method=("ОТ" if last == "OT" else "БУЛЛИТЫ" if last == "SO" else "ОСНОВНОЕ") if state == FINISHED else None,
                    source_id="nhl:api", source_url=source_url, retrieved_at=retrieved_at, home_id=ids[0], away_id=ids[1]))
            except (KeyError, TypeError, ValueError) as e:
                raise SourceFormatError(f"NHL API: матч не разобран ({type(e).__name__}: {e})") from e
    return out, {"raw_blocks": len(weeks), "matches": len(out), "unknown_teams": sorted(unknown)}


# ---- Суперлига (футзал): общий календарь лиги на superliga.rfs.ru -------------------------------------------------------------

MONTHS_RU_ABBR = {"ЯНВ": 1, "ФЕВ": 2, "МАР": 3, "АПР": 4, "МАЯ": 5, "МАЙ": 5, "ИЮН": 6, "ИЮЛ": 7, "АВГ": 8, "СЕН": 9, "ОКТ": 10, "НОЯ": 11, "ДЕК": 12}
SUPERLIGA_ROW_RE = re.compile(r"^(\d{1,2}) ([А-Яа-я]+)\.? / (\d{1,2}:\d{2})$")


def _infer_year(month: int, day: int, ref: datetime.date) -> int:
    for year in (ref.year, ref.year + 1, ref.year - 1):
        try:
            cand = datetime.date(year, month, day)
        except ValueError:
            continue
        if abs((cand - ref).days) <= 183:
            return year
    return ref.year


def parse_superliga_calendar(text: str, ref: datetime.date, *, source_url: str, retrieved_at: datetime.datetime) -> tuple:
    """Календарь матчей на главной superliga.rfs.ru: «ДД Месяц / ЧЧ:ММ / Хозяева / счёт|- / [(тайм)] / Гости / счёт|-». Время — екатеринбургское."""
    lines = clean_lines(text)
    n = len(lines)
    out, rows = [], 0
    i = 0
    while i < n:
        m = SUPERLIGA_ROW_RE.match(lines[i])
        if not m or i + 4 >= n:
            i += 1
            continue
        month = MONTHS_RU_ABBR.get(m.group(2).upper()[:3])
        home = lines[i + 1]
        j = i + 2
        score_h = int(lines[j]) if lines[j].isdigit() else None
        if score_h is None and lines[j] not in ("-", "–"):
            i += 1
            continue
        j += 1
        if j < n and re.match(r"^\(\d+:\d+\)$", lines[j]):
            j += 1
        if j + 1 >= n or not _plain(lines[j]):
            i += 1
            continue
        away = lines[j]
        score_a = int(lines[j + 1]) if lines[j + 1].isdigit() else None
        if not month or (score_a is None and lines[j + 1] not in ("-", "–")):
            i += 1
            continue
        rows += 1
        day = datetime.date(_infer_year(month, int(m.group(1)), ref), month, int(m.group(1)))
        finished = score_h is not None and score_a is not None
        out.append(make_match(sport="futsal", competition="superliga", season=C.CURRENT_SEASON, home=home, away=away,
                              kickoff=kickoff_utc(day, m.group(3), EKB, placeholder_below=4), tz="Asia/Yekaterinburg",
                              status=FINISHED if finished else SCHEDULED, score_home=score_h if finished else None,
                              score_away=score_a if finished else None, method="ОСНОВНОЕ" if finished else None,
                              source_id="rfs:superliga", source_url=source_url, retrieved_at=retrieved_at, day=day))
        i = j + 2
    unknown = sorted({("superliga", name) for mm in out for name, tid in ((mm["home_team"], mm["home_team_id"]), (mm["away_team"], mm["away_team_id"])) if tid is None})
    return out, {"raw_blocks": rows, "matches": len(out), "unknown_teams": unknown}


# ---- Первая лига: страница клуба sports.ru (общего календаря лиги, доступного без JS, у sports.ru нет) ---------------------------

CLUB_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
CLUB_SCORE_RE = re.compile(r"^(\d{1,2}) : (\d{1,2})$")
# турнир на странице клуба → ключ каталога
CLUB_PAGE_TOURNAMENTS = {"Россия. Первая лига": "fnl1"}
PLACEHOLDER_HOUR_BELOW = 4                                         # 00:00–03:59 у sports.ru — «время не назначено»


def parse_sportsru_club_calendar(text: str, club_id: str, *, sport: str, source_url: str, retrieved_at: datetime.datetime) -> tuple:
    """Таблица сезона на странице клуба: «ДД.ММ.ГГГГ / | / ЧЧ:ММ / Турнир / Соперник / Дома|В гостях / [от|б] / a : b | превью». Время московское,
    счёт — в порядке «хозяева : гости». Берутся только матчи турниров из CLUB_PAGE_TOURNAMENTS (кубки/товарищеские каталогу не нужны)."""
    lines = clean_lines(text)
    out = []
    i, n = 0, len(lines)
    while i < n:
        m = CLUB_DATE_RE.match(lines[i])
        if not (m and i + 5 < n and lines[i + 1] == "|" and TIME_RE.match(lines[i + 2]) and lines[i + 5] in ("Дома", "В гостях")):
            i += 1
            continue
        try:
            day = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            i += 6
            continue
        time_str = lines[i + 2]
        tournament, rival, at_home = lines[i + 3], lines[i + 4], lines[i + 5] == "Дома"
        j, cells = i + 6, []
        while j < n and len(cells) < 6:
            if CLUB_DATE_RE.match(lines[j]) and j + 1 < n and lines[j + 1] == "|":
                break
            cells.append(lines[j])
            j += 1
        i = j
        comp = CLUB_PAGE_TOURNAMENTS.get(tournament)
        if comp is None:
            continue
        sc = next((CLUB_SCORE_RE.match(c) for c in cells if CLUB_SCORE_RE.match(c)), None)
        method = "ОТ" if "от" in cells else ("БУЛЛИТЫ" if "б" in cells else "ОСНОВНОЕ")
        own = C.CLUB_BY_KEY[club_id][1]
        rival_id = A.resolve(comp, rival)
        home_name, away_name = (own, rival) if at_home else (rival, own)
        home_id, away_id = (club_id, rival_id) if at_home else (rival_id, club_id)
        finished = sc is not None
        out.append(make_match(sport=sport, competition=comp, season=C.CURRENT_SEASON, home=home_name, away=away_name,
                              kickoff=kickoff_utc(day, time_str, MSK, placeholder_below=PLACEHOLDER_HOUR_BELOW), tz="Europe/Moscow",
                              status=FINISHED if finished else SCHEDULED,
                              score_home=int(sc.group(1)) if finished else None, score_away=int(sc.group(2)) if finished else None,
                              method=method if finished else None, source_id="sportsru:club:football", source_url=source_url,
                              retrieved_at=retrieved_at, home_id=home_id, away_id=away_id, day=day))
    return out, {"raw_blocks": len(out), "matches": len(out), "unknown_teams": sorted({(mm["competition"], nm) for mm in out for nm, tid in ((mm["home_team"], mm["home_team_id"]), (mm["away_team"], mm["away_team_id"])) if tid is None})}
