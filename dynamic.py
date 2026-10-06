"""ЕДИНЫЙ контур матчей SPORTBOT: SOURCE → MATCH NORMALIZATION → MATCH STORE → STATUS → FINAL SCORE → PUBLICATION. Для ВСЕХ клубов одинаково.

ACTIVE CLUBS = постоянные клубы владельца (BASELINE: Автомобилист, Синара, Урал, Реал Мадрид, Арсенал, Милан — всегда) ∪ клубы, выбранные активными
участниками группы (через C.effective_selection). Разница между ними только в том, КАК клуб становится активным; дальше обслуживание одно и то же:
афиша недели, утро (анонс / изменения), результаты, восстановление пропущенного. Список пересчитывается из файла участников на каждом тике.

Спортивный факт даёт ТОЛЬКО детерминированный источник (feed.py). Нейросети / Tavily источником факта не являются и в этот контур не входят.
Правила результата:
  • публикуется только матч со статусом «завершён»: явным у источника (sports.ru «Завершен», NHL API FINAL) — либо, если источник статус не пишет
    (таблица лиги, страница клуба), счёт устойчив (stable_final: одинаков в ≥2 проверках с интервалом ≥15 мин и прошло минимальное время матча);
  • счёт «идёт» / без подтверждения никогда не становится итогом; два завершённых источника с разным счётом — конфликт (в группу ничего, владельцу уведомление);
  • ошибка источника ≠ «матча нет»; опубликованное не дублируется (published_ids + канонический id матча: турнир|день|хозяева|гости).
Один матч — одна запись (dyn_matches.json): SCHEDULED → LIVE → FINAL → PUBLISHED (+ CONFLICT / ERROR / EXPIRED).
recover(): регулярно проверяет последние 48 часов и восстанавливает матчи, пропущенные при первом обнаружении.
Состояние шести клубов прежнего контура (today_matches.json) читается ТОЛЬКО как список уже закрытых матчей, чтобы ничего не опубликовать дважды.
Модуль не импортирует sport_bot: нужный модуль (отправка, алерты, константы) передаётся в конструктор — так контур проверяется тестами без сети и без MAX."""
import datetime
import os
import re

import club_aliases as A
import feed as F
import matchfeed as M
import tribun_catalog as C

ICON = {"football": "⚽", "hockey": "🏒", "futsal": "🥅"}
CLOSED = ("published", "expired")
DYN_STATE_VERSION = 1
# Источники, обслуживаемые адаптерами feed.py (турниры вне этого списка — в т.ч. ВТБ — не опрашиваются)
SERVED = set(F.CENTER_COMPETITIONS) - {"vtb"} | {"fnl1", "superliga"}
# Правило устойчивого счёта для источников БЕЗ явного статуса «завершён»: минимальная длительность матча и срок неизменности счёта
MIN_MATCH_MINUTES = {"football": 125, "hockey": 150, "futsal": 105}
STABLE_MINUTES = 15
RECOVERY_DAYS = 2                                    # окно восстановления: последние 48 часов (сегодня и два предыдущих дня)
RECOVERY_EVERY_MINUTES = 30
RECOVERY_GIVE_UP_HOURS = 12                          # сколько ждём итог матча, заведённого восстановлением (его начало может быть давно в прошлом)


def plain_name(club_id: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", C.NAME_BY_KEY.get(club_id, club_id))


def match_id(m: dict) -> str:
    """Канонический id матча: турнир + день + хозяева + гости, не зависит от того, чьими болельщиками он интересен (один матч — один id)."""
    home = m["home_team_id"] or A.norm(m["home_team"])
    away = m["away_team_id"] or A.norm(m["away_team"])
    return f"dyn|{m['competition']}|{m['day']}|{home}|{away}"


def competitions_of(club_ids) -> set:
    """Все турниры снимка сезона, в которых играет хотя бы один из клубов и которые умеют читать адаптеры (клуб обслуживается во всех своих турнирах:
    Милан — Серия А и Лига Европы). Кубки и товарищеские вне снимка постоянных клубов читаются со страницы клуба (feed.club_page)."""
    return {k for k in C.SEASONS if k in SERVED and set(club_ids) & set(C.comp_club_keys(k))}


def comp_info(m: dict) -> tuple:
    """(название турнира, вид спорта) матча: каталог → отложенные турниры → название из источника (кубки, товарищеские)."""
    c = m["competition"]
    if c in C.COMP_BY_KEY:
        return C.COMP_BY_KEY[c][1], C.COMP_BY_KEY[c][2]
    for d in C.DEFERRED_COMPETITIONS:
        if d[0] == c:
            return d[1], d[2]
    return (m.get("tournament") or c), m.get("sport") or "football"


def comp_name(comp: str) -> str:
    if comp in C.COMP_BY_KEY:
        return C.COMP_BY_KEY[comp][1]
    return next((d[1] for d in C.DEFERRED_COMPETITIONS if d[0] == comp), comp)


def norm_pair_name(name: str) -> str:
    return A.norm(name or "")


def same_team_name(a: str, b: str) -> bool:
    """Одна и та же команда в разных написаниях: «Норильск» = «Норильский никель», «Амур» = «Амур»; без нечёткого поиска — общее начало ≥5 символов или вхождение."""
    a, b = norm_pair_name(a), norm_pair_name(b)
    if not a or not b:
        return False
    return a == b or a in b or b in a or (len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5])


def hhmm(kickoff) -> str | None:
    return kickoff.astimezone(M.MSK).strftime("%H:%M") if kickoff else None


class Dynamic:
    def __init__(self, S, feed=None, profiles=None, baseline=None):
        self.S = S                                  # модуль sport_bot (или его тестовая подмена)
        self._baseline = None if baseline is None else frozenset(baseline)       # постоянные клубы; None = клубы sport_bot.CLUBS
        self.feed = feed or F.MatchFeed()
        self._profiles = profiles                   # необязательный загрузчик профилей (тесты)

    # ---- хранилище -------------------------------------------------------------------------------------------------------------

    @property
    def state_path(self) -> str:
        return os.path.join(self.S.DATA_DIR, "dyn_matches.json")

    @property
    def sched_path(self) -> str:
        return os.path.join(self.S.DATA_DIR, "dyn_schedule.json")

    def load_state(self) -> dict:
        raw = self.S.read_json_safe(self.state_path, {})
        if not isinstance(raw, dict) or not isinstance(raw.get("matches"), dict):
            raw = {"version": DYN_STATE_VERSION, "matches": {}, "published_ids": [], "meta": {}}
        raw.setdefault("published_ids", [])
        raw.setdefault("meta", {})
        for rec in raw["matches"].values():
            for key, value in self.S.MATCH_DEFAULTS.items():
                rec.setdefault(key, value)
        return raw

    def save_state(self, state: dict) -> None:
        state["saved_at"] = self.S.ekb_now().isoformat()
        state["published_ids"] = state["published_ids"][-self.S.PUBLISHED_IDS_KEEP:]
        self.S.atomic_write_json(self.state_path, state)

    def load_sched(self) -> dict:
        data = self.S.read_json_safe(self.sched_path, {"entries": []})
        return data if isinstance(data, dict) and isinstance(data.get("entries"), list) else {"entries": []}

    def save_sched(self, sched: dict) -> None:
        sched["saved_at"] = self.S.ekb_now().isoformat()
        self.S.atomic_write_json(self.sched_path, sched)

    def prune(self, state: dict, now: datetime.datetime) -> None:
        for mid, rec in list(state["matches"].items()):
            start = self.S.parse_iso(rec.get("start_utc")) or self.S.parse_iso(f"{rec.get('day')}T00:00:00+03:00")
            if start and rec["status"] in CLOSED and now - start > datetime.timedelta(hours=48):
                del state["matches"][mid]

    # ---- ACTIVE CLUBS ------------------------------------------------------------------------------------------------------------

    def profiles(self) -> list:
        if self._profiles is not None:
            return list(self._profiles() if callable(self._profiles) else self._profiles)
        import tribun
        store = tribun.read_strict(os.path.join(self.S.DATA_DIR, "tribun_members.json"), {})
        return [tribun.normalize_profile(p) for p in (store.get("members") or {}).values()]

    def legacy_keys(self) -> set:
        """Постоянные клубы владельца (BASELINE): всегда активны, независимо от интересов участников."""
        return set(self._baseline) if self._baseline is not None else {c["key"] for c in self.S.CLUBS}

    baseline_keys = legacy_keys

    def active(self) -> dict:
        """{'fans': {club_id: число участников}, 'dynamic': ВСЕ активные клубы, 'legacy': постоянные клубы владельца, 'members': N}.
        ACTIVE = BASELINE ∪ выбор активных участников. Пересчитывается при каждом вызове."""
        agg = F.MatchFeed.aggregate_interests(self.profiles())
        base = self.legacy_keys()
        fans = dict(agg["clubs"])
        for k in base:
            fans.setdefault(k, 0)                                   # у постоянного клуба может не быть болельщиков — он всё равно активен
        return {"fans": fans, "dynamic": set(fans), "legacy": set(base), "members": agg["members"]}

    def relevant(self, m: dict, active_ids: set) -> bool:
        """Матч обслуживается, если в нём играет хотя бы один активный клуб (постоянный или выбранный участниками) — без исключений."""
        ids = {m["home_team_id"], m["away_team_id"]} - {None}
        return bool(ids & active_ids)

    # ---- закрытые матчи прежнего контура (чтобы ничего не опубликовать дважды при переходе) -----------------------------------------

    def legacy_records(self) -> list:
        raw = self.S.read_json_safe(self.S.DATA_FILE, {})
        recs = list((raw.get("matches") or {}).values()) if isinstance(raw, dict) and isinstance(raw.get("matches"), dict) else []
        out = [r for r in recs if isinstance(r, dict) and r.get("club_key") and r.get("match_date")]
        for pid in (raw.get("published_ids") or []) if isinstance(raw, dict) else []:
            parts = str(pid).split("|")
            if len(parts) == 3:
                out.append({"club_key": parts[0], "match_date": parts[1], "rival": parts[2], "status": "published", "result_sent": True, "announce_sent": True})
        return out

    def legacy_for(self, m: dict):
        """Запись прежнего контура о ЭТОМ матче постоянного клуба (тот же клуб, день и соперник) или None."""
        base = self.legacy_keys()
        for rec in self.legacy_records():
            key = rec["club_key"]
            if key not in base or rec["match_date"] != str(m["day"]):
                continue
            if key == m["home_team_id"]:
                other = [m["away_team"], plain_name(m["away_team_id"]) if m["away_team_id"] else ""]
            elif key == m["away_team_id"]:
                other = [m["home_team"], plain_name(m["home_team_id"]) if m["home_team_id"] else ""]
            else:
                continue
            if any(same_team_name(rec.get("rival", ""), o) for o in other if o):
                return rec
        return None

    def legacy_closed(self, rec) -> bool:
        return bool(rec) and (rec.get("status") in CLOSED or rec.get("result_sent") or rec.get("result_text"))

    # ---- сбор матчей --------------------------------------------------------------------------------------------------------------

    async def gather(self, days, dynamic_ids: set, only_comps=None) -> tuple:
        comps = competitions_of(dynamic_ids)
        if only_comps is not None:
            comps &= set(only_comps)
        fnl = set(dynamic_ids) & set(F.FNL1_SLUGS)
        pages = set(dynamic_ids) & set(F.BASELINE_PAGES)              # постоянные футбольные клубы: страница клуба целиком (кубки вне каталога)
        found, failed = {}, set()
        for day in days:
            res = await self.feed.collect(day, competitions=comps, clubs=fnl, pages=pages)
            failed |= res.failed_competitions
            for m in res.matches:
                found[match_id(m)] = m
        return list(found.values()), failed

    # ---- представление матча ------------------------------------------------------------------------------------------------------

    def describe(self, m: dict, dynamic_ids: set) -> dict:
        """Кому интересен матч и как его называть: один активный клуб — «наш» клуб и соперник; два — нейтрально «хозяева — гости»."""
        home = plain_name(m["home_team_id"]) if m["home_team_id"] else m["home_team"]
        away = plain_name(m["away_team_id"]) if m["away_team_id"] else m["away_team"]
        actives = [k for k in (m["home_team_id"], m["away_team_id"]) if k in dynamic_ids]
        if len(actives) == 2:
            club_key, club, rival = m["home_team_id"], home, away
        elif actives[0] == m["home_team_id"]:
            club_key, club, rival = actives[0], home, away
        else:
            club_key, club, rival = actives[0], away, home
        tournament, sport = comp_info(m)
        return {"club_key": club_key, "club_name": club, "rival": rival, "two": len(actives) == 2, "actives": actives,
                "icon": ICON.get(sport, "🏟"), "sport": sport, "tournament": tournament}

    def entry(self, m: dict, dynamic_ids: set) -> dict:
        d = self.describe(m, dynamic_ids)
        return {"key": match_id(m), "club_key": d["club_key"], "club_name": d["club_name"], "icon": d["icon"], "sport": d["sport"],
                "date": str(m["day"]), "time": hhmm(m["kickoff"]), "zone": "мск" if m["kickoff"] else None, "tournament": d["tournament"],
                "rival": d["rival"], "two": d["two"], "competition": m["competition"], "source": "feed", "updated": self.S.ekb_now().isoformat()}

    # ---- афиша -----------------------------------------------------------------------------------------------------------------------

    async def weekly_entries(self, now: datetime.datetime, *, persist: bool = True, bot=None) -> tuple:
        """Записи афиши для активных динамических клубов на неделю пн–вс → (записи в формате legacy-афиши, упавшие турниры).
        Матч двух активных клубов — одна запись. Завершённые и отменённые не включаются."""
        S = self.S
        today = now.astimezone(S.YEKB_TZ).date()
        monday, sunday = S.week_bounds(today)
        act = self.active()
        if not act["dynamic"]:
            return [], set()
        days = [monday + datetime.timedelta(days=i) for i in range(7)]
        matches, failed = await self.gather(days, act["dynamic"])
        if bot is not None:                                                          # «источник не ответил» ≠ «матчей нет»: одно предупреждение и одно «восстановилось»
            for comp in competitions_of(act["dynamic"]):
                name = comp_name(comp)
                if comp in failed:
                    if not S.DRY_RUN:
                        await S.raise_alert(bot, f"dynweekly:{comp}:{monday}", f"⚠️ Трибун\nНе удалось получить расписание недели: {name}. "
                                                                              f"Эти клубы не попадут в афишу. Повторю автоматически.", now)
                else:
                    await S.clear_alert(bot, f"dynweekly:{comp}:{monday}", f"✅ Трибун\nРасписание недели получено: {name}.", now)
        fresh = []
        for m in matches:
            if not self.relevant(m, act["dynamic"]) or m["status"] in (M.FINISHED, M.CANCELLED):
                continue
            if monday <= m["day"] <= sunday:
                fresh.append(self.entry(m, act["dynamic"]))
        if persist:
            sched = self.load_sched()
            fresh_keys = {e["key"] for e in fresh}
            sched["entries"] = [e for e in sched["entries"]
                                if not (monday.isoformat() <= e.get("date", "") <= sunday.isoformat() and e["key"] not in fresh_keys
                                        and e.get("competition") not in failed)]
            for e in fresh:
                sched["entries"] = [x for x in sched["entries"] if x.get("key") != e["key"]] + [e]
            self.save_sched(sched)
        return fresh, failed

    async def refresh_schedule(self, now: datetime.datetime) -> tuple:
        """Один раз после запуска: расписание текущей недели в хранилище (для контроля изменений времени/переноса). Ничего не отправляет."""
        entries, failed = await self.weekly_entries(now, persist=True, bot=None)
        return len(entries), failed

    # ---- тексты (форма — как у шести клубов; места у источников нет — строка «Место» не выдумывается) ----------------------------------

    @staticmethod
    def _clock(rec: dict) -> str:
        return f"{rec['time']} ({rec['zone']})" if rec.get("time") else "время уточняется"

    def text_morning(self, rec: dict) -> str:
        head = (f"{rec['icon']} Сегодня играют {rec['club_name']} — {rec['rival']}" if rec["two"] else f"{rec['icon']} Сегодня играет {rec['club_name']}")
        lines = [head, f"Турнир: {rec['tournament']}", f"Время: {self._clock(rec)}"]
        if not rec["two"]:
            lines.append(f"Соперник: {rec['rival']}")
        return "\n".join(lines)

    def text_time_changed(self, rec: dict, old: dict) -> str:
        return (f"⚠️ Внимание, изменилось время матча!\n{rec['icon']} {rec['club_name']} — {rec['rival']}\nСегодня · {self._clock(rec)}\n"
                f"Ранее было указано {old['time']} ({old['zone']}).\nТурнир: {rec['tournament']}")

    def text_moved_here(self, rec: dict, old: dict) -> str:
        old_day = datetime.date.fromisoformat(old["date"])
        return (f"⚠️ Внимание, матч перенесён на сегодня!\n{rec['icon']} {rec['club_name']} — {rec['rival']}\nСегодня · {self._clock(rec)}\n"
                f"Ранее: {self.S.fmt_day(old_day, long=True)} · {self.S.fmt_clock(old.get('time'), old.get('zone'))}\nТурнир: {rec['tournament']}")

    def text_moved_away(self, old: dict, new_day, new_time, new_zone) -> str:
        return (f"⚠️ Внимание, матч перенесён!\n{old['icon']} {old['club_name']} — {old['rival']}\n"
                f"Был назначен на сегодня · {self.S.fmt_clock(old.get('time'), old.get('zone'))}\n"
                f"Теперь: {self.S.fmt_day(new_day, long=True)} · {self.S.fmt_clock(new_time, new_zone)}")

    def text_postponed(self, old: dict) -> str:
        return (f"⚠️ Внимание, матч перенесён!\n{old['icon']} {old['club_name']} — {old['rival']}\n"
                f"Был назначен на сегодня · {self.S.fmt_clock(old.get('time'), old.get('zone'))}\nНовая дата уточняется.")

    def text_cancelled(self, old: dict) -> str:
        return (f"⚠️ Внимание, матч отменён!\n{old['icon']} {old['club_name']} — {old['rival']}\n"
                f"Был назначен на сегодня · {self.S.fmt_clock(old.get('time'), old.get('zone'))}.")

    def text_result(self, rec: dict, m: dict) -> str:
        """Один активный клуб — как у шести клубов (победа / ничья / поражение с точки зрения клуба); два — нейтральный итог."""
        S = self.S
        home = plain_name(m["home_team_id"]) if m["home_team_id"] else m["home_team"]
        away = plain_name(m["away_team_id"]) if m["away_team_id"] else m["away_team"]
        gh, ga = m["score_home"], m["score_away"]
        hockey = rec["sport"] == "hockey"
        method = m.get("method") or "ОСНОВНОЕ"
        tail = (" ОТ" if method == "ОТ" else " Б" if method == "БУЛЛИТЫ" else "") if hockey else ""
        if rec["two"]:
            return f"🏁 Результат матча\n\n{rec['icon']} {home} {gh}:{ga}{tail} {away}"
        pseudo = {"icon": rec["icon"], "name": rec["club_name"]}
        own_home = rec["club_key"] == m["home_team_id"]
        own, other = (gh, ga) if own_home else (ga, gh)
        outcome = "ПОБЕДА" if own > other else ("НИЧЬЯ" if own == other else "ПОРАЖЕНИЕ")
        if hockey:
            return S.format_result_hockey(pseudo, outcome, home, gh, away, ga, method)
        return S.format_result_football(pseudo, outcome, home, gh, away, ga)

    # ---- состояние и отправка -----------------------------------------------------------------------------------------------------

    def new_record(self, m: dict, d: dict, text: str) -> dict:
        rec = {"match_id": match_id(m), "club_key": d["club_key"], "club_name": d["club_name"], "rival": d["rival"], "two": d["two"],
               "icon": d["icon"], "sport": d["sport"], "competition": m["competition"], "tournament": d["tournament"], "day": str(m["day"]),
               "match_date": str(m["day"]), "time": hhmm(m["kickoff"]), "zone": "мск" if m["kickoff"] else None,
               "start_utc": m["kickoff"].isoformat() if m["kickoff"] else None, "home_id": m["home_team_id"], "away_id": m["away_team_id"],
               "announce_text": text, "phase": "SCHEDULED", "score": None, "final_confirmed": False, "last_checked_at": None, "last_source_status": None,
               "obs": None, "created_via": "morning"}
        for key, value in self.S.MATCH_DEFAULTS.items():
            rec.setdefault(key, value)
        return rec

    async def deliver(self, bot, state: dict, rec: dict, now: datetime.datetime) -> bool:
        S = self.S
        if rec.get("announce_sent") or not rec.get("announce_text"):
            return True
        sent = await S.send_to_group(bot, rec["announce_text"])
        key = f"dynannounce:{rec['match_id']}"
        if sent:
            rec["announce_sent"] = True
            if rec["status"] == "found":
                rec["status"] = "announced"
            self.save_state(state)
            await S.clear_alert(bot, key, f"✅ Трибун\nАнонс матча {rec['club_name']} — {rec['rival']} отправлен.", now)
        else:
            self.save_state(state)
            if not S.DRY_RUN:
                await S.raise_alert(bot, key, f"⚠️ Трибун\nНе удалось отправить в MAX анонс матча {rec['club_name']} — {rec['rival']}.\n"
                                              f"Следующая попытка будет автоматически.", now)
        return sent

    # ---- утро ---------------------------------------------------------------------------------------------------------------------

    def classify(self, sched: dict, state: dict, rec: dict, today: datetime.date) -> dict:
        """normal | time_changed | moved_here — как classify_morning_match: сравнение с афишей (запись на другую дату — «прежняя дата» переноса,
        только если матч не отслеживался и не сыгран)."""
        same = next((e for e in sched["entries"] if e.get("key") == rec["match_id"]), None)
        if same:
            if same.get("time") and rec["time"] and same["time"] != rec["time"]:
                return {"kind": "time_changed", "old": same}
            return {"kind": "normal", "old": same}
        pair = {rec["home_id"] or "", rec["away_id"] or ""}
        for other in sched["entries"]:
            if other.get("competition") != rec["competition"] or other.get("key") == rec["match_id"]:
                continue
            parts = other["key"].split("|")
            if len(parts) != 5 or {parts[3], parts[4]} != pair:
                continue
            try:
                other_day = datetime.date.fromisoformat(other["date"])
            except ValueError:
                continue
            if other_day == today or abs((other_day - today).days) > 21:
                continue
            if other["key"] not in state["matches"]:
                return {"kind": "moved_here", "old": other}
        return {"kind": "normal", "old": None}

    async def morning(self, bot, now: datetime.datetime, *, with_intro: bool = True, only_comps=None) -> list:
        """Утренний прогон. → турниры, по которым источники не ответили (их повторяют позже; «источник не ответил» ≠ «матчей нет»)."""
        S = self.S
        today = now.astimezone(S.YEKB_TZ).date()
        act = self.active()
        state, sched = self.load_state(), self.load_sched()
        self.prune(state, now)
        dyn = act["dynamic"]
        if not dyn:
            state["meta"]["morning_failed"] = {"date": today.isoformat(), "comps": []}
            self.save_state(state)
            return []
        matches, failed = await self.gather([today], dyn, only_comps)
        comps_asked = competitions_of(dyn) if only_comps is None else competitions_of(dyn) & set(only_comps)
        for comp in comps_asked:
            name = comp_name(comp)
            if comp in failed:
                if not S.DRY_RUN:
                    await S.raise_alert(bot, f"dynmorning:{comp}", f"⚠️ Трибун\nНе удалось проверить расписание на сегодня: {name}.\nПовторю автоматически.", now)
            else:
                await S.clear_alert(bot, f"dynmorning:{comp}", f"✅ Трибун\nРасписание на сегодня получено: {name}.", now)

        todays = {match_id(m): m for m in matches if self.relevant(m, dyn)}
        extra, outgoing = [], []
        changes_sent = state["meta"].setdefault("changes_sent", [])
        for mid, m in todays.items():
            d = self.describe(m, dyn)
            if m["status"] in (M.CANCELLED, M.POSTPONED):
                entry = next((e for e in sched["entries"] if e.get("key") == mid), None)
                if entry and mid not in changes_sent:
                    extra.append(self.text_cancelled(entry) if m["status"] == M.CANCELLED else self.text_postponed(entry))
                    changes_sent.append(mid)
                sched["entries"] = [e for e in sched["entries"] if e.get("key") != mid]
                continue
            rec = self.new_record(m, d, "")
            existing = state["matches"].get(mid)
            if existing:                                                            # повторный прогон в тот же день — ничего не анонсируем заново
                existing.update({k: rec[k] for k in ("tournament", "time", "zone", "start_utc")})
                continue
            if mid in state["published_ids"]:
                continue
            leg = self.legacy_for(m)
            if leg:                                                                 # матч уже вёл прежний контур (переход): повторно не анонсируем и закрытое не открываем
                if not self.legacy_closed(leg):
                    rec.update({"announce_sent": bool(leg.get("announce_sent")), "status": "announced" if leg.get("announce_sent") else "found",
                                "created_via": "legacy"})
                    state["matches"][mid] = rec
                continue
            if m["status"] == M.FINISHED:                                           # матч уже сыгран к моменту утра (например ночной NHL): без анонса, результат — по общему порядку
                rec["announce_sent"], rec["status"], rec["announce_text"] = True, "awaiting_result", None
                if rec["start_utc"] is None:                                        # у завершённых футбольных матчей источник время не показывает: матч уже сыгран — ищем результат сразу
                    rec["start_utc"] = datetime.datetime(m["day"].year, m["day"].month, m["day"].day, tzinfo=M.MSK).astimezone(datetime.timezone.utc).isoformat()
                state["matches"][mid] = rec
                continue
            verdict = self.classify(sched, state, rec, today)
            if verdict["kind"] == "time_changed":
                rec["announce_text"] = self.text_time_changed(rec, verdict["old"])
            elif verdict["kind"] == "moved_here":
                rec["announce_text"] = self.text_moved_here(rec, verdict["old"])
                sched["entries"] = [e for e in sched["entries"] if e.get("key") != verdict["old"]["key"]]
            else:
                rec["announce_text"] = self.text_morning(rec)
            state["matches"][mid] = rec
            outgoing.append((rec, verdict["kind"] == "normal"))
            sched["entries"] = [e for e in sched["entries"] if e.get("key") != mid] + [self.entry(m, dyn)]

        # назначенные на сегодня в афише, но сегодня не найденные: перенос (тот же матч в ближайшие дни) — иначе молчим, не гадаем
        todays_comps_ok = {c for c in comps_asked if c not in failed}
        missing = [e for e in sched["entries"] if e.get("date") == today.isoformat() and e["key"] not in todays and e.get("competition") in todays_comps_ok
                   and e["key"] not in changes_sent]
        if missing:
            future, _ = await self.gather([today + datetime.timedelta(days=i) for i in range(1, 8)], dyn, only_comps)
            by_pair = {}
            for fm in future:
                if fm["status"] in (M.SCHEDULED, M.LIVE):
                    by_pair[(fm["competition"], fm["home_team_id"], fm["away_team_id"])] = fm
            for e in missing:
                parts = e["key"].split("|")
                fm = by_pair.get((e["competition"], parts[3] if parts[3] != "" else None, parts[4] if parts[4] != "" else None))
                if fm is not None:
                    extra.append(self.text_moved_away(e, fm["day"], hhmm(fm["kickoff"]), "мск" if fm["kickoff"] else None))
                    changes_sent.append(e["key"])
                    sched["entries"] = [x for x in sched["entries"] if x.get("key") != e["key"]] + [self.entry(fm, dyn)]
        if with_intro and any(normal for _r, normal in outgoing) and state["meta"].get("intro_date") != today.isoformat() \
                and self.legacy_intro_date() != today.isoformat():
            intro = S.pick_intro(S.intro_kind([{"sport": r["sport"]} for r, _n in outgoing]))
            for rec, normal in outgoing:
                if normal:
                    rec["announce_text"] = f"{intro}\n\n{rec['announce_text']}"
                    break
            state["meta"]["intro_date"] = today.isoformat()
            S.mark_intro(today)
        state["meta"]["morning_failed"] = {"date": today.isoformat(), "comps": sorted(failed)}
        state["meta"]["changes_sent"] = changes_sent[-200:]
        self.save_state(state)                                                      # сначала фиксируем найденное, потом отправляем
        self.save_sched(sched)
        for text in extra:
            await S.send_to_group(bot, text)
        for rec, _normal in outgoing:
            await self.deliver(bot, state, rec, now)
        return sorted(failed)

    def legacy_intro_date(self):
        return self.S.read_json_safe(self.S.META_FILE, {}).get("intro_date")

    async def retry_morning(self, bot, now: datetime.datetime) -> None:
        S = self.S
        local = now.astimezone(S.YEKB_TZ)
        info = self.load_state()["meta"].get("morning_failed") or {}
        if info.get("date") != local.date().isoformat() or not info.get("comps") or local.hour >= S.MORNING_RETRY_UNTIL_HOUR:
            return
        await self.morning(bot, now, with_intro=False, only_comps=set(info["comps"]))

    async def retry_unsent(self, bot, now: datetime.datetime) -> None:
        state = self.load_state()
        for rec in list(state["matches"].values()):
            if rec["announce_sent"] or not rec.get("announce_text"):
                continue
            start = self.S.parse_iso(rec.get("start_utc"))
            if start and now >= start:
                continue
            await self.deliver(bot, state, rec, now)

    # ---- результаты ---------------------------------------------------------------------------------------------------------------

    def stable_final(self, rec: dict, found: dict, now: datetime.datetime) -> bool:
        """Матч завершён по версии источника БЕЗ явного статуса «завершён»: принимаем только устойчивый счёт — одинаков в ≥2 проверках с интервалом
        ≥STABLE_MINUTES и после минимальной длительности матча. Изменился счёт — отсчёт заново (идущий матч так не пройдёт)."""
        score = f"{found['score_home']}:{found['score_away']}"
        obs = rec.get("obs")
        if not obs or obs.get("score") != score:
            rec["obs"] = {"score": score, "first": now.isoformat(), "last": now.isoformat(), "n": 1}
            return False
        obs["last"], obs["n"] = now.isoformat(), obs.get("n", 1) + 1
        first = self.S.parse_iso(obs["first"])
        start = found.get("kickoff_latest") or self.S.parse_iso(rec.get("start_utc"))               # при неоднозначном поясе источника — самое позднее начало
        enough_time = start is None or now >= start + datetime.timedelta(minutes=MIN_MATCH_MINUTES.get(rec.get("sport"), 125))
        return obs["n"] >= 2 and now - first >= datetime.timedelta(minutes=STABLE_MINUTES) and enough_time

    async def resolve(self, rec: dict, now: datetime.datetime) -> dict:
        """state: ok (text) | none | conflict (reason) | error (reason) | cancelled. Сбой источника ≠ «результата нет»; расхождение источников — не факт;
        счёт без доказанного «завершён» — не результат."""
        day = datetime.date.fromisoformat(rec["day"])
        clubs = {k for k in (rec.get("home_id"), rec.get("away_id")) if k}
        comps = {rec["competition"]} if not rec["competition"].startswith(M.PSEUDO_PREFIX) else set()
        res = await self.feed.collect(day, competitions=comps, clubs=clubs, pages=clubs & set(F.BASELINE_PAGES))
        found = next((m for m in res.matches if match_id(m) == rec["match_id"]), None)
        rec["last_checked_at"] = now.isoformat()
        if found is None:
            if rec["competition"] in res.failed_competitions or (rec["competition"].startswith(M.PSEUDO_PREFIX) and res.failed_clubs):
                return {"state": "error", "reason": "источники турнира не ответили"}
            rec["last_source_status"] = "NOT_FOUND"
            return {"state": "none"}
        rec["last_source_status"] = found["status"]
        if found["status"] in (M.CANCELLED, M.POSTPONED):
            return {"state": "cancelled", "reason": found["status"]}
        if found["status"] != M.FINISHED or found["score_home"] is None or found["score_away"] is None:
            rec["phase"], rec["obs"] = ("LIVE" if found["status"] == M.LIVE else "SCHEDULED"), None
            if found["score_home"] is not None:
                rec["score"] = f"{found['score_home']}:{found['score_away']}"
            return {"state": "none"}
        rec["score"] = f"{found['score_home']}:{found['score_away']}"
        if found["confidence"] == "conflict":
            return {"state": "conflict", "reason": "источники называют разный счёт"}
        if found.get("final_confirmed"):
            rec.update({"final_confirmed": True, "phase": "FINAL", "obs": None})
            return {"state": "ok", "text": self.text_result(rec, found)}
        rec["phase"] = "LIVE"                                                       # источник не написал «завершён»: ждём устойчивого счёта
        if self.stable_final(rec, found, now):
            rec.update({"final_confirmed": True, "phase": "FINAL"})
            return {"state": "ok", "text": self.text_result(rec, found)}
        return {"state": "none"}

    async def results(self, bot, now: datetime.datetime) -> None:
        """Жизненный цикл результата: первая проверка через RESULT_DELAY_HOURS после начала (это расписание опроса, а не доказательство окончания),
        затем каждый тик; публикуется только доказанный итог (resolve). Сохранение до отправки, «опубликован» — только после подтверждённой отправки.
        Идёт по ВСЕМ неоконченным записям, даже если болельщик уже снял клуб."""
        S = self.S
        state = self.load_state()
        for rec in list(state["matches"].values()):
            if rec["status"] in CLOSED:
                continue
            title = f"{rec['club_name']} — {rec['rival']}"
            start = S.parse_iso(rec.get("start_utc"))
            if not start:
                if not S.DRY_RUN:
                    await S.raise_alert(bot, f"dynnostart:{rec['match_id']}",
                                        f"⚠️ Трибун\nНе удалось определить время начала матча {title}, результат автоматически искать не смогу.", now)
                continue
            if now < start + datetime.timedelta(hours=S.RESULT_DELAY_HOURS):
                continue
            age = (now - start).total_seconds() / 3600
            give_up_age, give_up_limit = age, S.RESULT_GIVE_UP_HOURS
            if rec.get("created_via") == "recovery" and S.parse_iso(rec.get("created_at")):          # восстановленному матчу — свои 12 часов с момента восстановления
                give_up_age, give_up_limit = (now - S.parse_iso(rec["created_at"])).total_seconds() / 3600, RECOVERY_GIVE_UP_HOURS
            if rec["result_text"] is None and give_up_age > give_up_limit:
                rec["status"], rec["phase"] = "expired", "EXPIRED"
                self.save_state(state)
                if not S.DRY_RUN:
                    await S.raise_alert(bot, f"dyngiveup:{rec['match_id']}",
                                        f"⚠️ Трибун\nРезультат матча {title} так и не получен за {S.RESULT_GIVE_UP_HOURS} ч. Автоматический поиск остановлен.", now)
                continue
            if rec["status"] in ("found", "announced"):
                rec["status"], rec["first_check_at"] = "awaiting_result", now.isoformat()
                self.save_state(state)
            if rec["result_text"] is None:
                res = await self.resolve(rec, now)
                if res["state"] == "ok":
                    rec.update({"source_fail_ticks": 0, "conflict": None, "result_text": res["text"], "result_found_at": now.isoformat(), "status": "result_found"})
                    self.save_state(state)                                          # результат сохранён ДО попытки отправки
                elif res["state"] == "cancelled":
                    rec.update({"status": "expired", "phase": "CANCELLED", "closed_reason": res["reason"]})
                    self.save_state(state)
                    continue
                elif res["state"] == "conflict":
                    conflict = rec.get("conflict") or {"since": now.isoformat(), "count": 0}
                    conflict.update({"count": conflict["count"] + 1, "last": now.isoformat(), "reason": res["reason"]})
                    rec["conflict"], rec["phase"] = conflict, "CONFLICT"
                    self.save_state(state)
                    since = S.parse_iso(conflict["since"])
                    if not S.DRY_RUN and now - since >= datetime.timedelta(minutes=S.CONFLICT_ALERT_AFTER_MINUTES):
                        await S.raise_alert(bot, f"dynconflict:{rec['match_id']}",
                                            f"⚠️ Трибун\nРезультат матча {title} требует проверки: источники расходятся. В MAX ничего не отправлено, проверяю дальше.", now)
                    continue
                elif res["state"] == "error":
                    rec["source_fail_ticks"] += 1
                    rec["phase"] = "ERROR"
                    self.save_state(state)
                    if not S.DRY_RUN and rec["source_fail_ticks"] >= S.SOURCE_FAIL_ALERT_TICKS:
                        await S.raise_alert(bot, f"dynsrcfail:{rec['match_id']}",
                                            f"⚠️ Трибун\nНе удаётся получить результат матча {title}: источники недоступны. Следующая попытка будет автоматически.", now)
                    continue
                else:
                    rec["source_fail_ticks"] = 0
                    self.save_state(state)
                    if not S.DRY_RUN and age >= S.RESULT_ALERT_AFTER_HOURS:
                        await S.raise_alert(bot, f"dynnores:{rec['match_id']}",
                                            f"⚠️ Трибун\nРезультат матча {title} пока не найден ({S.RESULT_ALERT_AFTER_HOURS:g} ч после начала). "
                                            f"Следующая попытка будет автоматически.", now)
                    continue
            if rec["result_text"]:
                await self.publish(bot, state, rec, now)

    # ---- восстановление (self-healing) ---------------------------------------------------------------------------------------------

    async def recover(self, bot, now: datetime.datetime, *, force: bool = False) -> int:
        """Проверяет последние 48 часов: матчи активных клубов, которые уже начались или завершились, но не имеют записи в хранилище (пропущены утром,
        потеряны после рестарта, источник временно не работал), — заводит запись, дальше их доводит results(). Опубликованное, закрытое прежним контуром и
        известное хранилищу не заводится заново. Возвращает число восстановленных матчей."""
        S = self.S
        state = self.load_state()
        last = S.parse_iso(state["meta"].get("recovered_at"))
        if not force and last and now - last < datetime.timedelta(minutes=RECOVERY_EVERY_MINUTES):
            return 0
        act = self.active()
        today = now.astimezone(M.MSK).date()
        days = [today - datetime.timedelta(days=i) for i in range(RECOVERY_DAYS, -1, -1)]
        matches, failed = await self.gather(days, act["dynamic"])
        added = 0
        for m in matches:
            mid = match_id(m)
            if not self.relevant(m, act["dynamic"]) or m["status"] in (M.CANCELLED, M.POSTPONED):
                continue
            started = m["status"] in (M.FINISHED, M.LIVE) or (m["kickoff"] is not None and m["kickoff"] <= now)
            if not started or mid in state["matches"] or mid in state["published_ids"]:
                continue
            leg = self.legacy_for(m)
            if self.legacy_closed(leg):
                continue
            d = self.describe(m, act["dynamic"])
            rec = self.new_record(m, d, "")
            rec.update({"announce_sent": True, "status": "awaiting_result", "created_via": "recovery", "created_at": now.isoformat()})
            if leg and leg.get("announce_sent") is False:
                rec["announce_sent"] = True                                         # матч уже начался: анонс не нужен
            if rec["start_utc"] is None:                                            # у завершённых футбольных матчей время не показано — известна только дата
                rec["start_utc"] = datetime.datetime(m["day"].year, m["day"].month, m["day"].day, tzinfo=M.MSK).astimezone(datetime.timezone.utc).isoformat()
            state["matches"][mid] = rec
            added += 1
        state["meta"]["recovered_at"] = now.isoformat()
        if added:
            state["meta"]["recovered_total"] = state["meta"].get("recovered_total", 0) + added
        self.save_state(state)
        if added:
            print(f"[DYN] восстановление: заведено матчей {added}")
        return added

    async def publish(self, bot, state: dict, rec: dict, now: datetime.datetime) -> bool:
        S = self.S
        if rec["status"] == "published" or rec["match_id"] in state["published_ids"]:
            rec["status"], rec["result_sent"] = "published", True
            self.save_state(state)
            return True
        sent = await S.send_to_group(bot, rec["result_text"])
        title = f"{rec['club_name']} — {rec['rival']}"
        if sent:
            rec.update({"status": "published", "result_sent": True, "published_at": now.isoformat()})
            if rec["match_id"] not in state["published_ids"]:
                state["published_ids"].append(rec["match_id"])
            self.save_state(state)
            mid = rec["match_id"]
            await S.clear_alerts(bot, [f"dynsend:{mid}", f"dynsrcfail:{mid}", f"dynnores:{mid}", f"dynconflict:{mid}", f"dyngiveup:{mid}"],
                                 f"✅ Трибун\nРезультат матча {title} получен и опубликован.", now)
            return True
        self.save_state(state)
        if not S.DRY_RUN:
            await S.raise_alert(bot, f"dynsend:{rec['match_id']}",
                                f"⚠️ Трибун\nРезультат матча {title} найден, но не удалось отправить его в MAX. Результат сохранён, повторю автоматически.", now)
        return False

    def next_result_delta(self, now: datetime.datetime, best: float) -> float:
        """Секунды до ближайшей первой проверки результата динамического матча (чтобы планировщик проснулся точно ко времени)."""
        for rec in self.load_state()["matches"].values():
            if rec["status"] in CLOSED + ("result_found",):
                continue
            start = self.S.parse_iso(rec.get("start_utc"))
            if start:
                delta = (start + datetime.timedelta(hours=self.S.RESULT_DELAY_HOURS) - now).total_seconds()
                if 0 < delta < best:
                    best = delta + 1
        return best

    # ---- сводка для владельца ------------------------------------------------------------------------------------------------------

    def summary(self) -> dict:
        act = self.active()
        return {"fans": act["fans"], "dynamic": sorted(act["dynamic"]), "legacy": sorted(act["legacy"]), "members": act["members"],
                "total": len(act["fans"])}

    # ---- dry-run: проверка клуба без отправки и без записи состояния -----------------------------------------------------------------

    async def dry_run_club(self, club_id: str, now: datetime.datetime, *, lookback_days: int = 14) -> dict:
        """Как бы клуб обслуживался сейчас: афиша недели, утренний анонс в день ближайшего матча, изменения (афиша ↔ утро), результат последнего
        завершённого матча. Ничего не отправляет и не пишет в /data. Профиль и состояние — синтетические."""
        S = self.S
        saved = self._profiles
        self._profiles = lambda: [self.synthetic_profile(club_id)]
        try:
            today = now.astimezone(S.YEKB_TZ).date()
            monday, _ = S.week_bounds(today)
            dyn = {club_id}
            out = {"club": club_id, "active": club_id in self.active()["dynamic"], "weekly": None, "morning": None, "result": None, "next_match": None}
            entries, failed = await self.weekly_entries(now, persist=False)
            out["weekly"] = S.format_weekly(entries) if entries else None
            out["failed"] = sorted(failed)
            upcoming, _ = await self.gather([today + datetime.timedelta(days=i) for i in range(0, 14)], dyn)
            upcoming = sorted([m for m in upcoming if m["status"] == M.SCHEDULED and self.relevant(m, dyn)], key=lambda m: (m["day"], m["kickoff"] or now))
            if upcoming:
                m = upcoming[0]
                out["next_match"] = {"day": str(m["day"]), "home": plain_name(m["home_team_id"]) if m["home_team_id"] else m["home_team"],
                                     "away": plain_name(m["away_team_id"]) if m["away_team_id"] else m["away_team"], "time": hhmm(m["kickoff"]), "sources": m["sources"]}
                rec = self.new_record(m, self.describe(m, dyn), "")
                out["morning"] = self.text_morning(rec)
                shifted = dict(self.entry(m, dyn))
                if shifted["time"]:
                    h, mm = (int(x) for x in shifted["time"].split(":"))
                    shifted["time"] = f"{(h + 1) % 24:02d}:{mm:02d}"                  # «афиша называла другое время» → проверка текста изменения времени
                out["changes"] = self.text_time_changed(rec, shifted)
            past, _ = await self.gather([today - datetime.timedelta(days=i) for i in range(1, lookback_days + 1)], dyn)
            done = sorted([m for m in past if m["status"] == M.FINISHED and m["score_home"] is not None and self.relevant(m, dyn)],
                          key=lambda m: m["day"], reverse=True)
            if done:
                m = done[0]
                rec = self.new_record(m, self.describe(m, dyn), "")
                out["result"] = {"day": str(m["day"]), "text": self.text_result(rec, m), "confidence": m["confidence"], "trusted": m["trusted"]}
            return out
        finally:
            self._profiles = saved

    @staticmethod
    def synthetic_profile(club_id: str) -> dict:
        comps = [c[0] for c in C.COMPETITIONS if club_id in C.comp_club_keys(c[0])]
        sports = sorted({C.COMP_BY_KEY[c][2] for c in comps})
        return {"user_id": 0, "active_in_group": True, "sports": sports, "championships": comps, "clubs": [club_id]}

    # ---- контрольная проверка при запуске --------------------------------------------------------------------------------------------

    async def pick_controls(self, now: datetime.datetime, comps=("nhl", "khl", "apl", "ucl"), horizon: int = 15) -> list:
        """Контрольные клубы: Зенит (РПЛ) + по клубу из NHL/КХЛ/АПЛ/ЛЧ, у которого ближайший матч — по реальному расписанию (не из шести исторических)."""
        today = now.astimezone(self.S.YEKB_TZ).date()
        out = ["zenit"]
        legacy = self.legacy_keys()
        for comp in comps:
            for i in range(0, horizon):
                r = await self.feed.collect(today + datetime.timedelta(days=i), competitions={comp})
                pick = next((m["home_team_id"] for m in r.matches if m["status"] == M.SCHEDULED and m["home_team_id"] and m["home_team_id"] not in legacy
                             and m["away_team_id"] not in legacy and m["home_team_id"] not in out), None)
                if pick:
                    out.append(pick)
                    break
        return out

    async def controls_log(self, now: datetime.datetime) -> list:
        """Строки для лога: как обслуживались бы контрольные клубы, если бы их выбрали (dry-run на реальных данных; в группу ничего не отправляется)."""
        lines = []
        for club in await self.pick_controls(now):
            r = await self.dry_run_club(club, now, lookback_days=7 if C.CLUB_BY_KEY[club][2] == "hockey" else 30)     # футбол играет реже (паузы сборных): ищем результат за 30 дней
            nm = r["next_match"]
            flat = lambda t: (t or "—").replace("\n", " ⏎ ")
            lines.append(f"[DYN] контрольный клуб {plain_name(club)} ({club}): активен={r['active']}; ближайший матч: "
                         + (f"{nm['day']} {nm['home']} — {nm['away']} {nm['time'] or 'время уточняется'} (источники: {', '.join(nm['sources'])})" if nm else "в ближайшие 14 дней нет")
                         + f"; афиша: {flat(r['weekly'])}; утро: {flat(r['morning'])}; изменения: {flat(r.get('changes'))}; "
                         + (f"результат {r['result']['day']} ({r['result']['confidence']}): {flat(r['result']['text'])}" if r["result"] else "результат: завершённых матчей за неделю нет")
                         + (f"; турниры без ответа: {r['failed']}" if r.get("failed") else ""))
        return lines
