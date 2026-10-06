"""Одноразовые безопасные миграции постоянного состояния (запуск при старте, идемпотентно, с резервной копией и журналом).

2026-10-06-unified:
  1. резервная копия всего состояния, которое затрагивает единый контур матчей, в /data/backups/migration-<id>/ (копии, оригиналы не меняются);
  2. очистка ТЕСТОВОГО выбора «Зенит»: тестовый профиль владельца (единственный профиль группы) выбрал Зенит при приёмке ACTIVE CLUBS 2026-10-03,
     и Зенит попал в боевую афишу. Убирается ТОЛЬКО club=zenit и ТОЛЬКО если доказано: профиль принадлежит владельцу и других профилей в группе нет.
     Остальной выбор (в том числе постоянные клубы владельца) не трогается; удалённое записывается в профиль (test_choices_removed) — откат вручную.
Старые файлы (today_matches.json, schedule.json, last_*.txt) не удаляются и не переписываются: прежний контур больше не пишет, но его состояние читается
как список уже закрытых матчей (dynamic.legacy_for)."""
import datetime
import json
import os
import shutil

from tribun_io import TribunDataError, atomic_write_json, read_strict

MIGRATION_ID = "2026-10-06-unified"
MARKER = "tribun_migrations.json"
BACKUP_FILES = ("today_matches.json", "schedule.json", "admin_alerts.json", "last_morning.txt", "last_weekly.txt", "sport_meta.json", "dyn_matches.json",
                "dyn_schedule.json", "dyn_last_morning.txt", "tribun_members.json", "tribun_requests.json", "tribun_settings.json", "tribun_sources.json",
                "tribun_publications.json", "tribun_priorities.json")


def backup_state(data_dir: str, now: datetime.datetime) -> str:
    folder = os.path.join(data_dir, "backups", f"migration-{MIGRATION_ID}")
    os.makedirs(folder, exist_ok=True)
    for name in BACKUP_FILES:
        src = os.path.join(data_dir, name)
        if os.path.exists(src) and not os.path.exists(os.path.join(folder, name)):          # не перезаписываем копию при повторном запуске
            shutil.copy2(src, os.path.join(folder, name))
    return folder


def remove_test_zenit(data_dir: str, owner_id, now: datetime.datetime) -> str:
    path = os.path.join(data_dir, "tribun_members.json")
    try:
        store = read_strict(path, None)
    except TribunDataError as e:
        return f"профили не читаются ({e.reason}) — очистка пропущена"
    if not store or not isinstance(store.get("members"), dict):
        return "профилей нет — очищать нечего"
    members = store["members"]
    holders = [k for k, p in members.items() if "zenit" in (p.get("clubs") or [])]
    if not holders:
        return "Зенита в профилях нет — очищать нечего"
    if owner_id is None:
        return "владелец не определён — очистка Зенита не доказана и не выполнена"
    others = [k for k in members if str(k) != str(owner_id)]
    if others:
        return f"в группе есть другие профили ({len(others)}) — Зенит не доказан как тестовый, не тронут"
    if {str(h) for h in holders} != {str(owner_id)}:
        return "Зенит выбран не владельцем — не тронут"
    profile = members[str(owner_id)]
    profile["clubs"] = [c for c in profile.get("clubs", []) if c != "zenit"]
    profile.setdefault("test_choices_removed", []).append({"club": "zenit", "at": now.isoformat(timespec="seconds"), "migration": MIGRATION_ID,
                                                           "why": "тестовый выбор владельца при приёмке ACTIVE CLUBS (единственный профиль группы)"})
    atomic_write_json(path, store)
    return "тестовый выбор Зенит удалён из профиля владельца (единственного в группе)"


def run(data_dir: str, owner_id=None, now: datetime.datetime | None = None) -> list:
    """→ строки журнала. Повторный запуск ничего не делает."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    marker_path = os.path.join(data_dir, MARKER)
    try:
        marker = read_strict(marker_path, {"done": {}})
    except TribunDataError:
        return [f"[MIGRATION] {MARKER} повреждён — миграции не выполнялись"]
    if MIGRATION_ID in marker.get("done", {}):
        return []
    lines = []
    folder = backup_state(data_dir, now)
    lines.append(f"[MIGRATION] {MIGRATION_ID}: резервная копия состояния → {folder}")
    result = remove_test_zenit(data_dir, owner_id, now)
    lines.append(f"[MIGRATION] {MIGRATION_ID}: {result}")
    marker.setdefault("done", {})[MIGRATION_ID] = {"at": now.isoformat(timespec="seconds"), "zenit": result}
    atomic_write_json(marker_path, marker)
    return lines
