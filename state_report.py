"""Только чтение: сводка постоянного состояния для расследований (строки `[STATE] …` в логе при запуске). Ничего не пишет и не отправляет.
Имён участников и секретов в вывод не попадает: профили показываются по короткому хэшу id."""
import glob
import hashlib
import json
import os


def _read(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return default if default is not None else {"_error": f"{type(e).__name__}: {e}"}


def _h(uid) -> str:
    return hashlib.sha1(str(uid).encode()).hexdigest()[:6]


def _flat(x) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def lines(data_dir: str, owner_id=None, days=("2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06")) -> list:
    out = []
    members = _read(os.path.join(data_dir, "tribun_members.json"), {"members": {}})
    for key, p in sorted((members.get("members") or {}).items()):
        out.append(f"[STATE] профиль {_h(key)}{' (владелец)' if owner_id is not None and str(key) == str(owner_id) else ''}: active_in_group={p.get('active_in_group')} "
                   f"sports={p.get('sports')} championships={p.get('championships')} clubs={p.get('clubs')} requests=— created={p.get('created_at')} "
                   f"updated={p.get('updated_at')} interests_updated={p.get('interests_updated_at')} joined_source={p.get('joined_source')} welcomed={p.get('welcomed')}")
    out.append(f"[STATE] профилей всего: {len(members.get('members') or {})}")
    history = []
    for path in sorted(glob.glob(os.path.join(data_dir, "backups", "tribun_members-*.json"))):
        data = _read(path, {"members": {}})
        holders = sorted(_h(k) for k, p in (data.get("members") or {}).items() if "zenit" in (p.get("clubs") or []))
        history.append((os.path.basename(path)[len("tribun_members-"):-5], holders))
    changes, prev = [], None
    for stamp, holders in history:
        if holders != prev:
            changes.append(f"{stamp}: держатели Зенита {holders}")
            prev = holders
    out.append(f"[STATE] копий tribun_members: {len(history)}; смены держателей Зенита: {changes[-12:]}")
    state = _read(os.path.join(data_dir, "today_matches.json"), {"matches": {}})
    for mid, rec in sorted((state.get("matches") or {}).items(), key=lambda kv: kv[1].get("match_date", "")):
        if rec.get("match_date") in days:
            out.append("[STATE] legacy " + _flat({k: rec.get(k) for k in ("match_id", "club_key", "rival", "match_date", "time", "zone", "start_utc", "status", "announce_sent",
                                                                       "result_text", "result_sent", "published_at", "first_check_at", "result_found_at", "conflict",
                                                                       "source_fail_ticks", "last_crosscheck_at")}))
    out.append(f"[STATE] legacy published_ids (последние 12): {(state.get('published_ids') or [])[-12:]}; записей в today_matches: {len(state.get('matches') or {})}")
    sched = _read(os.path.join(data_dir, "schedule.json"), {"entries": []})
    for e in sched.get("entries", []):
        if e.get("date") in days or (e.get("date") or "") >= "2026-10-05":
            out.append("[STATE] legacy schedule " + _flat({k: e.get(k) for k in ("key", "date", "time", "zone", "tournament", "rival", "source")}))
    dyn = _read(os.path.join(data_dir, "dyn_matches.json"), {"matches": {}})
    for mid, rec in sorted((dyn.get("matches") or {}).items()):
        out.append("[STATE] dyn " + _flat({k: rec.get(k) for k in ("match_id", "club_key", "rival", "day", "time", "status", "announce_sent", "result_text", "published_at", "conflict")}))
    dsched = _read(os.path.join(data_dir, "dyn_schedule.json"), {"entries": []})
    for e in dsched.get("entries", []):
        out.append("[STATE] dyn schedule " + _flat({k: e.get(k) for k in ("key", "club_key", "date", "time", "tournament", "rival", "two")}))
    out.append(f"[STATE] dyn published_ids: {(dyn.get('published_ids') or [])[-12:]}")
    alerts = _read(os.path.join(data_dir, "admin_alerts.json"), {})
    out.append("[STATE] алерты: " + _flat({k: {"active": v.get("active"), "delivered": v.get("delivered"), "count": v.get("count"), "since": v.get("since")}
                                          for k, v in alerts.items() if isinstance(v, dict)}))
    for name in ("last_weekly.txt", "last_morning.txt", "dyn_last_morning.txt"):
        try:
            with open(os.path.join(data_dir, name), "r", encoding="utf-8") as f:
                out.append(f"[STATE] {name}: {f.read().strip()}")
        except OSError:
            out.append(f"[STATE] {name}: нет")
    return out
