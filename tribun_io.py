"""Общие функции хранилища клубного слоя «Трибун»: атомарная запись JSON, копии, строгое чтение, маскирование секретов."""
import datetime
import json
import os
import re
import shutil
import tempfile


class TribunDataError(Exception):
    def __init__(self, path, reason):
        super().__init__(f"{os.path.basename(path)}: {reason}")
        self.path, self.reason = path, reason


def atomic_write_json(path: str, data) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup_file(path: str, keep: int = 30, min_age_seconds: int = 300) -> None:
    """Копия действующего файла перед перезаписью (не чаще раза в min_age_seconds, хранится последних keep)."""
    if not os.path.exists(path):
        return
    try:
        folder = os.path.join(os.path.dirname(path), "backups")
        os.makedirs(folder, exist_ok=True)
        prefix = os.path.splitext(os.path.basename(path))[0] + "-"
        names = sorted(n for n in os.listdir(folder) if n.startswith(prefix) and n.endswith(".json"))
        if names and (datetime.datetime.now().timestamp() - os.path.getmtime(os.path.join(folder, names[-1]))) < min_age_seconds:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, os.path.join(folder, f"{prefix}{stamp}.json"))
        for old in sorted(n for n in os.listdir(folder) if n.startswith(prefix) and n.endswith(".json"))[:-keep]:
            os.unlink(os.path.join(folder, old))
    except Exception as e:
        print(f"[TRIBUN] не удалось сделать копию {path}: {e}")


def read_strict(path: str, default):
    """Нет файла → default. Файл повреждён → TribunDataError (не пустая база, ничего не перезаписывается)."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise TribunDataError(path, f"{type(e).__name__}: {e}")


def mask_secrets(text: str) -> str:
    return re.sub(r"[A-Za-z0-9_\-]{24,}", "***", text or "")


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)
