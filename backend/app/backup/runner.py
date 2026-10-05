"""真正执行打包。只接收已经校验过的任务，不把用户输入拼进 shell。"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from app.backup.store import BackupError, database_dir, meta_root, site_dir

FILE_PATTERN = re.compile(r"^task(\d+)-(\d{14})\.tar\.gz$")
_REDIS_ROOTS = (
    Path("/www/server/data/redis"),
    Path("/var/lib/redis"),
    Path("/data"),
)
_COMMAND_TIMEOUT = 600


def _which(name: str) -> str:
    found = shutil.which(name)
    return found or ""


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _scrub(text: str, secret: str = "") -> str:
    cleaned = (text or "").replace(secret, "******") if secret else (text or "")
    cleaned = cleaned.replace("\n", " ").replace("\r", " ").strip()
    return cleaned[:300] or "备份失败"


def pack_directory(directory: Path, dest: Path, task_id: int) -> Path:
    if directory.is_symlink() or not directory.is_dir():
        raise BackupError("备份目录不存在")
    name = directory.name
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise BackupError("备份目录无效")
    return _tar(directory.parent, name, dest, task_id)


def pack_file(path: Path, dest: Path, task_id: int) -> Path:
    if path.is_symlink() or not path.is_file():
        raise BackupError("备份文件无效")
    return _tar(path.parent, path.name, dest, task_id)


def _tar(parent: Path, name: str, dest: Path, task_id: int) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    partial = dest / f".task{int(task_id)}-{_stamp()}.tar.gz.partial"
    final = dest / f"task{int(task_id)}-{_stamp()}.tar.gz"
    command = ["/usr/bin/tar", "-czf", str(partial), "-C", str(parent), "--", name]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        partial.unlink(missing_ok=True)
        raise BackupError("打包超时，已中止") from None
    if result.returncode not in {0, 1} or not partial.is_file() or partial.stat().st_size <= 0:
        partial.unlink(missing_ok=True)
        raise BackupError(_scrub(result.stderr or "打包失败"))
    os.chmod(partial, 0o600)
    partial.replace(final)
    return final


def prune(folder: Path, task_id: int, keep: int) -> int:
    """只删除这个任务自己的 task编号-时间.tar.gz，不碰目录里的其他文件。"""
    if not folder.is_dir():
        return 0
    matched = []
    for path in folder.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        match = FILE_PATTERN.fullmatch(path.name)
        if match and int(match.group(1)) == int(task_id):
            matched.append(path)
    matched.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    removed = 0
    for old in matched[max(1, int(keep)) :]:
        old.unlink()
        removed += 1
    return removed


def perform(task: dict) -> tuple[Path, str]:
    if task.get("kind") == "site":
        archive = backup_site(task)
        removed = prune(site_dir(), int(task["id"]), int(task["keep"]))
        return archive, f"网站已打包，清理旧备份 {removed} 个"
    archive, how = backup_database(task)
    removed = prune(database_dir(), int(task["id"]), int(task["keep"]))
    return archive, f"{how}，清理旧备份 {removed} 个"


def backup_site(task: dict) -> Path:
    from app.files.security import PathJailError, get_file_root, locate
    from app.nginx.security import SiteError
    from app.nginx.service import site_detail

    target = str(task.get("target") or "")
    try:
        site = site_detail(target)
    except SiteError as exc:
        raise BackupError(exc.message, exc.status) from None
    root_name = str(site.get("root") or "").strip()
    if not root_name or root_name in {".", "/"}:
        raise BackupError("这个网站没有可备份的目录")
    try:
        directory = locate(root_name)
    except PathJailError as exc:
        raise BackupError(exc.message) from None
    jail = get_file_root().resolve()
    resolved = directory.resolve()
    try:
        resolved.relative_to(jail)
    except ValueError:
        raise BackupError("站点目录超出文件根目录") from None
    if resolved == jail:
        raise BackupError("不能备份整个文件根目录")
    if resolved.is_symlink() or not resolved.is_dir():
        raise BackupError("站点目录不存在")
    return pack_directory(resolved, site_dir(), int(task["id"]))


def backup_database(task: dict) -> tuple[Path, str]:
    engine = str(task.get("engine") or "")
    target = str(task.get("target") or "")
    dest = database_dir()
    task_id = int(task["id"])
    if engine == "mysql":
        return dump_mysql(target, dest, task_id)
    if engine == "mongodb":
        return dump_mongo(target, dest, task_id)
    if engine == "redis":
        return dump_redis(dest, task_id)
    if engine == "sqlite":
        return dump_sqlite(target, dest, task_id)
    raise BackupError("不支持的数据库类型")


def dump_mysql(name: str, dest: Path, task_id: int) -> tuple[Path, str]:
    from app.databases.security import DatabaseError, identifier

    try:
        name = identifier(name, "数据库名")
    except DatabaseError as exc:
        raise BackupError(exc.message) from None
    tool = _which("mysqldump")
    if tool:
        output = dest / f".task{task_id}-{_stamp()}.sql"
        try:
            _mysqldump(tool, name, output)
            archive = pack_file(output, dest, task_id)
        finally:
            output.unlink(missing_ok=True)
        return archive, "已使用 mysqldump 导出并打包"
    from app.databases.service import backup_database as panel_backup

    try:
        saved = panel_backup(name)
    except DatabaseError as exc:
        raise BackupError(exc.message, exc.status) from None
    folder = Path(__file__).resolve().parents[2] / "data" / "mysql" / "backups"
    source = folder / str(saved.get("file") or "")
    if source.is_symlink() or not source.is_file() or source.parent.resolve() != folder.resolve():
        raise BackupError("MySQL 备份文件无效")
    return pack_file(source, dest, task_id), "已使用面板的 MySQL 备份程序导出并打包"


def _mysqldump(tool: str, name: str, output: Path) -> None:
    from app.databases.service import _active_profile

    profile = _active_profile()
    secret = str(profile.get("password") or "")
    folder = meta_root() / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    config = folder / f"mysql-{os.getpid()}.cnf"
    config.write_text(
        "[client]\n"
        f"user={profile['user']}\n"
        f"password={secret}\n"
        f"host={profile['host']}\n"
        f"port={int(profile['port'])}\n",
        encoding="utf-8",
    )
    os.chmod(config, 0o600)
    try:
        with output.open("wb") as handle:
            result = subprocess.run(
                [
                    tool,
                    f"--defaults-extra-file={config}",
                    "--single-transaction",
                    "--routines",
                    "--databases",
                    name,
                ],
                stdout=handle,
                stderr=subprocess.PIPE,
                timeout=_COMMAND_TIMEOUT,
                shell=False,
            )
        if result.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
            raise BackupError(_scrub(result.stderr.decode("utf-8", "replace"), secret))
    except subprocess.TimeoutExpired:
        raise BackupError("mysqldump 超时，已中止") from None
    finally:
        config.unlink(missing_ok=True)


def dump_mongo(name: str, dest: Path, task_id: int) -> tuple[Path, str]:
    from app.databases.engines import _read_secret, engine_status
    from app.databases.security import DatabaseError, identifier

    try:
        name = identifier(name, "数据库名")
    except DatabaseError as exc:
        raise BackupError(exc.message) from None
    if engine_status("mongodb").get("state") != "running":
        raise BackupError("MongoDB 未运行")
    tool = _which("mongodump")
    if tool:
        return _mongodump(tool, name, _read_secret("mongodb"), dest, task_id)
    return _mongo_by_driver(name, dest, task_id)


def _mongodump(tool: str, name: str, secret: str, dest: Path, task_id: int) -> tuple[Path, str]:
    partial = dest / f".task{task_id}-{_stamp()}.archive.gz"
    env = os.environ.copy()
    env["MONGODB_URI"] = f"mongodb://panel:{quote(secret)}@127.0.0.1:27017/{name}?authSource=admin"
    try:
        result = subprocess.run(
            [tool, f"--archive={partial}", "--gzip", "--db", name],
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT,
            shell=False,
            env=env,
        )
        if result.returncode != 0 or not partial.is_file() or partial.stat().st_size <= 0:
            raise BackupError(_scrub(result.stderr or result.stdout, secret))
        archive = pack_file(partial, dest, task_id)
    except subprocess.TimeoutExpired:
        raise BackupError("mongodump 超时，已中止") from None
    finally:
        partial.unlink(missing_ok=True)
    return archive, "已使用 mongodump 导出并打包"


def _mongo_by_driver(name: str, dest: Path, task_id: int) -> tuple[Path, str]:
    import json

    from app.databases.engines import _mongo_client

    temporary = Path(tempfile.mkdtemp(prefix=f".task{task_id}-", dir=dest))
    try:
        with _mongo_client() as client:
            database = client[name]
            names = database.list_collection_names()
            if not names:
                (temporary / "empty.txt").write_text("没有集合\n", encoding="utf-8")
            for collection in names:
                safe = re.sub(r"[^A-Za-z0-9_.-]", "_", collection)[:80] or "collection"
                docs = []
                for document in database[collection].find().limit(100000):
                    document["_id"] = str(document.get("_id"))
                    docs.append(document)
                (temporary / f"{safe}.json").write_text(
                    json.dumps(docs, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
        archive = pack_directory(temporary, dest, task_id)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return archive, "服务器没有 mongodump，已用驱动导出集合并打包"


def dump_redis(dest: Path, task_id: int) -> tuple[Path, str]:
    from app.databases.engines import _redis_client, engine_status

    if engine_status("redis").get("state") != "running":
        raise BackupError("Redis 未运行")
    client = _redis_client()
    try:
        client.save()
        directory = str((client.config_get("dir") or {}).get("dir") or "")
        filename = str((client.config_get("dbfilename") or {}).get("dbfilename") or "dump.rdb")
    except Exception as exc:
        raise BackupError(_scrub(str(exc))) from None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.rdb", filename):
        raise BackupError("Redis 的 rdb 文件名无效")
    source = Path(directory) / filename
    try:
        resolved = source.resolve(strict=True)
    except OSError:
        raise BackupError("找不到 Redis 的 rdb 文件") from None
    allowed = [root.resolve() for root in _REDIS_ROOTS if root.exists()]
    try:
        data_dir = Path(engine_status("redis").get("data_dir") or "")
        if data_dir.exists():
            allowed.append(data_dir.resolve())
    except OSError:
        pass
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise BackupError("Redis 数据目录不在允许的位置，没有拷贝")
    if resolved.is_symlink() or not resolved.is_file():
        raise BackupError("Redis 的 rdb 不是普通文件")
    return pack_file(resolved, dest, task_id), "已执行 SAVE 并打包 rdb"


def dump_sqlite(raw: str, dest: Path, task_id: int) -> tuple[Path, str]:
    from app.databases.engines import _sqlite_roots
    from app.databases.security import DatabaseError, resolve_sqlite_file
    from app.files.security import get_file_root

    try:
        roots = _sqlite_roots()
    except Exception:
        roots = [get_file_root()]
    try:
        source = resolve_sqlite_file(raw, roots)
    except DatabaseError as exc:
        raise BackupError(exc.message) from None
    if not source.is_file():
        raise BackupError("找不到这个数据库文件")
    temporary = dest / f".task{task_id}-{_stamp()}.sqlite"
    try:
        uri = source.as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as origin, sqlite3.connect(temporary) as copy:
            origin.backup(copy)
        os.chmod(temporary, 0o600)
        gzip_path = temporary.with_suffix(".sqlite.gz")
        with temporary.open("rb") as incoming, gzip.open(gzip_path, "wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        os.chmod(gzip_path, 0o600)
        archive = pack_file(gzip_path, dest, task_id)
    finally:
        temporary.unlink(missing_ok=True)
        gzip_path = temporary.with_suffix(".sqlite.gz")
        gzip_path.unlink(missing_ok=True)
    return archive, "已在线复制 SQLite 并打包"
