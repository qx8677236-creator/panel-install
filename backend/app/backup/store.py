"""备份任务清单和执行记录。

任务放在 data/backup/tasks.json。每次执行追加到 task_logs 表，并写一份文本日志。
压缩包不放在这里，走 archive_base()。
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR

_NAME = __import__("re").compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff ]{1,40}$")


class BackupError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status
        super().__init__(message)


def meta_root() -> Path:
    path = DATA_DIR / "backup"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


_archive_base: Path | None = None


def archive_base() -> Path:
    """优先 /www/backup。面板用户写不了时，退回 data/backup/files。"""
    global _archive_base
    if _archive_base is not None:
        return _archive_base
    override = os.environ.get("PANEL_BACKUP_ROOT", "").strip()
    preferred = Path(override) if override else Path("/www/backup")
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        os.chmod(preferred, 0o700)
        _archive_base = preferred
    except OSError:
        fallback = meta_root() / "files"
        fallback.mkdir(parents=True, exist_ok=True)
        os.chmod(fallback, 0o700)
        _archive_base = fallback
    return _archive_base


def site_dir() -> Path:
    path = archive_base() / "site"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def database_dir() -> Path:
    path = archive_base() / "database"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _tasks_path() -> Path:
    return meta_root() / "tasks.json"


def _lock_path() -> Path:
    return meta_root() / "tasks.lock"


def _logs_db() -> Path:
    return meta_root() / "task_logs.sqlite"


def _text_log(task_id: int) -> Path:
    folder = meta_root() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder / f"{int(task_id)}.log"


def _connect() -> sqlite3.Connection:
    path = _logs_db()
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            status TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            filename TEXT NOT NULL,
            message TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_task ON task_logs(task_id, id)")
    connection.commit()
    os.chmod(path, 0o600)
    return connection


def load_tasks() -> list:
    path = _tasks_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_tasks(items: list) -> None:
    path = _tasks_path()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def get_task(task_id: int) -> dict | None:
    return next((item for item in load_tasks() if int(item["id"]) == int(task_id)), None)


def mutate_tasks(change) -> list:
    """文件锁包住读改写，调度进程和接口进程可以同时改。"""
    meta_root()
    with _lock_path().open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            items = load_tasks()
            result = change(items)
            if result is not None:
                _write_tasks(result)
                return result
            return items
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def check_payload(payload: dict, sites: list, databases: list, task_id: int | None = None) -> dict:
    name = str(payload.get("name") or "").strip()
    if not _NAME.fullmatch(name):
        raise BackupError("任务名称只能使用中文、字母、数字、空格、下划线和短横线")
    kind = str(payload.get("kind") or "")
    if kind not in {"site", "database"}:
        raise BackupError("任务类型无效")
    target = str(payload.get("target") or "").strip()
    engine = str(payload.get("engine") or "").strip()
    if kind == "site":
        engine = ""
        known = {str(item.get("domain") or ""): item for item in sites}
        site = known.get(target)
        if site is None or "/" in target or ".." in target:
            raise BackupError("请选择已有的网站")
        label = f"网站 / {target}"
    else:
        known = {
            (str(item.get("engine") or ""), str(item.get("name") or "")): item
            for item in databases
        }
        chosen = known.get((engine, target))
        if chosen is None:
            raise BackupError("请选择已有的数据库")
        label = str(chosen.get("label") or f"{engine} / {target}")
    try:
        keep = int(payload.get("keep"))
    except (TypeError, ValueError):
        raise BackupError("保留份数无效") from None
    if keep < 1 or keep > 30:
        raise BackupError("保留份数需要在 1 到 30 之间")
    from app.backup.schedule import describe, validate_cron

    cron = validate_cron(str(payload.get("cron") or ""))
    return {
        "id": task_id or 0,
        "name": name,
        "kind": kind,
        "engine": engine,
        "target": target,
        "target_label": label,
        "cron": cron,
        "cycle": describe(cron),
        "keep": keep,
        "enabled": bool(payload.get("enabled", True)),
        "last_run": "",
        "last_status": "",
        "last_size": 0,
        "last_file": "",
    }


def catalogs() -> tuple[list, list]:
    sites = []
    databases = []
    try:
        from app.nginx.service import list_sites

        for site in list_sites().get("sites") or []:
            domain = str(site.get("domain") or "")
            if domain:
                sites.append({"domain": domain, "root": str(site.get("root") or "")})
    except Exception:
        sites = []
    try:
        from app.databases.service import detect, list_databases

        if detect().get("installed"):
            for item in (list_databases(1, 50).get("items") or []):
                name = str(item.get("name") or "")
                if name:
                    databases.append({"engine": "mysql", "name": name, "label": f"MySQL / {name}"})
    except Exception:
        pass
    try:
        from app.databases.engines import engine_status, mongo_overview

        if engine_status("mongodb").get("state") == "running":
            for item in (mongo_overview().get("items") or []):
                name = str(item.get("name") or "")
                if name:
                    databases.append({"engine": "mongodb", "name": name, "label": f"MongoDB / {name}"})
    except Exception:
        pass
    try:
        from app.databases.engines import engine_status

        if engine_status("redis").get("state") == "running":
            databases.append({"engine": "redis", "name": "default", "label": "Redis / 当前实例"})
    except Exception:
        pass
    try:
        from app.databases.engines import sqlite_files

        for raw in sqlite_files().get("items") or []:
            databases.append({"engine": "sqlite", "name": str(raw), "label": f"SQLite / {Path(str(raw)).name}"})
    except Exception:
        pass
    return sites, databases


def list_public() -> dict:
    items = []
    for item in load_tasks():
        copied = dict(item)
        copied["status"] = "已启用" if item.get("enabled") else "已禁用"
        items.append(copied)
    return {
        "items": items,
        "paths": {"site": str(site_dir()), "database": str(database_dir())},
    }


def add_task(payload: dict) -> dict:
    sites, databases = catalogs()

    def change(items: list):
        if len(items) >= 30:
            raise BackupError("最多保存 30 个备份任务")
        task_id = max((int(item["id"]) for item in items), default=0) + 1
        task = check_payload(payload, sites, databases, task_id)
        items.append(task)
        return items

    saved = mutate_tasks(change)
    created = saved[-1]
    return {"id": created["id"], "cron": created["cron"], "cycle": created["cycle"]}


def set_status(task_id: int, enabled: bool) -> dict:
    found = {}

    def change(items: list):
        for item in items:
            if int(item["id"]) == int(task_id):
                item["enabled"] = bool(enabled)
                found["item"] = item
                return items
        raise BackupError("没有这个备份任务", 404)

    mutate_tasks(change)
    return {"id": task_id, "enabled": bool(enabled)}


def forget_site_tasks(target: str) -> int:
    """去掉目标正好是这个站点键的备份任务。不删除备份压缩包，也不动其他任务。"""
    key = str(target or "")
    if not key:
        return 0
    removed: list[int] = []

    def change(items: list):
        kept = []
        for item in items:
            same_site = str(item.get("kind") or "") == "site" and str(item.get("target") or "") == key
            if not same_site:
                kept.append(item)
                continue
            try:
                removed.append(int(item["id"]))
            except (KeyError, TypeError, ValueError):
                pass
        if len(kept) == len(items):
            return None
        return kept

    mutate_tasks(change)
    if not removed:
        return 0
    try:
        with _connect() as connection:
            for task_id in removed:
                connection.execute("DELETE FROM task_logs WHERE task_id = ?", (int(task_id),))
            connection.commit()
    except sqlite3.Error:
        pass
    for task_id in removed:
        text = _text_log(task_id)
        if not text.is_file() or text.is_symlink():
            continue
        try:
            if not _strict_inside(text.resolve(strict=True), meta_root().resolve()):
                continue
        except OSError:
            continue
        text.unlink()
    return len(removed)


def _strict_inside(path: Path, parent: Path) -> bool:
    try:
        return bool(path.relative_to(parent).parts)
    except ValueError:
        return False


def delete_task(task_id: int) -> dict:
    def change(items: list):
        kept = [item for item in items if int(item["id"]) != int(task_id)]
        if len(kept) == len(items):
            raise BackupError("没有这个备份任务", 404)
        return kept

    mutate_tasks(change)
    with _connect() as connection:
        connection.execute("DELETE FROM task_logs WHERE task_id = ?", (int(task_id),))
        connection.commit()
    text = _text_log(task_id)
    if text.is_file() and not text.is_symlink():
        text.unlink()
    return {"id": task_id}


def mark_result(task_id: int, status: str, size: int, filename: str, when: str) -> None:
    def change(items: list):
        for item in items:
            if int(item["id"]) == int(task_id):
                item["last_run"] = when
                item["last_status"] = status
                item["last_size"] = int(size)
                item["last_file"] = filename
                return items
        return None

    mutate_tasks(change)


def append_log(
    task_id: int,
    *,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    status: str,
    size_bytes: int,
    filename: str,
    message: str,
) -> None:
    text = str(message or "").replace("\n", " ").replace("\r", " ")[:400]
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO task_logs
                (task_id, started_at, finished_at, duration_ms, status, size_bytes, filename, message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(task_id), started_at, finished_at, int(duration_ms), status, int(size_bytes), filename, text),
        )
        connection.commit()
    line = (
        f"{finished_at} status={status} duration_ms={int(duration_ms)} "
        f"size={int(size_bytes)} file={filename or '-'} message={text}\n"
    )
    path = _text_log(task_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    os.chmod(path, 0o600)


def read_logs(task_id: int, limit: int = 100) -> dict:
    if get_task(task_id) is None:
        raise BackupError("没有这个备份任务", 404)
    cap = max(1, min(int(limit), 200))
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT started_at, finished_at, duration_ms, status, size_bytes, filename, message
            FROM task_logs WHERE task_id = ? ORDER BY id DESC LIMIT ?
            """,
            (int(task_id), cap),
        ).fetchall()
    items = [dict(row) for row in reversed(rows)]
    lines = [
        f"{item['finished_at']}  {item['status']}  {item['duration_ms']}ms  "
        f"{item['size_bytes']}B  {item['filename'] or '-'}  {item['message']}"
        for item in items
    ]
    return {"items": items, "lines": lines, "files": list_archives(task_id)}


def list_archives(task_id: int) -> list:
    from app.backup.runner import FILE_PATTERN

    task = get_task(task_id)
    if task is None:
        return []
    folder = site_dir() if task.get("kind") == "site" else database_dir()
    found = []
    if not folder.is_dir():
        return []
    for path in folder.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        match = FILE_PATTERN.fullmatch(path.name)
        if match and int(match.group(1)) == int(task_id):
            stat = path.stat()
            from app.backup.schedule import panel_zone

            zone = panel_zone()
            mtime = datetime.fromtimestamp(stat.st_mtime, zone) if zone is not None else datetime.fromtimestamp(stat.st_mtime)
            found.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": mtime.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
    found.sort(key=lambda item: item["name"], reverse=True)
    return found[:30]
