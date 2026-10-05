"""MySQL 的检测、建库、授权、备份。磁盘和数据库操作由路由放进线程。"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pymysql
from pymysql.err import MySQLError

from app.config import DATA_DIR
from app.vault import PREFIX, decrypt_text, encrypt_text, read_secret_file, write_secret_file
from app.databases.security import (
    SYSTEM_DATABASES,
    DatabaseError,
    account_host,
    identifier,
    note,
    password,
    pymysql_sql,
    quote_host,
    quote_ident,
    root_password,
    saved_host,
)

logger = logging.getLogger("panel.databases")

ADMIN = "/usr/local/sbin/panel-mysql-admin"
PAGE_MAX = 50
BACKUP_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}-\d{14}\.sql\.gz$")
_IO_SLOT = threading.Lock()
_JOBS_GUARD = threading.Lock()
_JOBS: dict = {}


def _dir() -> Path:
    path = DATA_DIR / "mysql"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    backups = path / "backups"
    backups.mkdir(exist_ok=True)
    os.chmod(backups, 0o700)
    return path


def _state_path() -> Path:
    return _dir() / "state.json"


def _secret_path() -> Path:
    return _dir() / "root.secret"



def public_password(stored: str) -> dict:
    """浏览器只知道有没有密码，拿不到原文。"""
    return {"password": "", "has_password": bool(stored)}


def _seal_passwords(data: dict) -> bool:
    changed = False
    remote = data.get("remote")
    if isinstance(remote, dict):
        current = str(remote.get("password") or "")
        if current and not current.startswith(PREFIX):
            remote["password"] = encrypt_text(current)
            changed = True
    records = data.get("records") or {}
    if isinstance(records, dict):
        for record in records.values():
            if not isinstance(record, dict):
                continue
            current = str(record.get("password") or "")
            if current and not current.startswith(PREFIX):
                record["password"] = encrypt_text(current)
                changed = True
    return changed


def _load() -> dict:
    path = _state_path()
    if not path.is_file():
        return {"remote": None, "records": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DatabaseError("数据库记录损坏", 500) from None
    if not isinstance(data, dict):
        raise DatabaseError("数据库记录损坏", 500)
    data.setdefault("remote", None)
    data.setdefault("records", {})
    if _seal_passwords(data):
        _save(data)
    return data


def _save(data: dict) -> None:
    path = _state_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _ledger_path() -> Path:
    return _dir() / "panel_databases.sqlite"


def _open_ledger() -> sqlite3.Connection:
    path = _ledger_path()
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS databases (
            db_name TEXT PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            password_enc TEXT NOT NULL DEFAULT '',
            host TEXT NOT NULL DEFAULT '',
            auth_type TEXT NOT NULL DEFAULT '本地',
            privileges TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS ledger_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )
    connection.commit()
    os.chmod(path, 0o600)
    return connection


def _seal_only(stored: str) -> str:
    """账本里只留 Fernet。明文先加密，加密失败就留空，不能把原文写进 sqlite。"""
    if not stored:
        return ""
    if stored.startswith(PREFIX):
        return stored
    sealed = encrypt_text(stored)
    if not sealed.startswith(PREFIX):
        return ""
    return sealed


def _ledger_save(
    db_name: str,
    username: str,
    password_enc: str,
    host: str,
    privileges: str,
    *,
    replace: bool,
    created_at: str = "",
) -> None:
    sealed = _seal_only(password_enc)
    if password_enc and not sealed:
        raise DatabaseError("不能把明文密码写入记录", 500)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    created = created_at or now
    connection = _open_ledger()
    try:
        if replace:
            connection.execute(
                """INSERT INTO databases
                    (db_name, username, password_enc, host, auth_type, privileges, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(db_name) DO UPDATE SET
                    username = excluded.username,
                    password_enc = excluded.password_enc,
                    host = excluded.host,
                    auth_type = excluded.auth_type,
                    privileges = excluded.privileges,
                    updated_at = excluded.updated_at
                """,
                (db_name, username, sealed, host, "本地", privileges, created, now),
            )
        else:
            connection.execute(
                """INSERT OR IGNORE INTO databases
                    (db_name, username, password_enc, host, auth_type, privileges, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (db_name, username, sealed, host, "本地", privileges, created, now),
            )
        connection.commit()
    finally:
        connection.close()
        os.chmod(_ledger_path(), 0o600)


def ledger_upsert(db_name: str, username: str, raw_password: str, host: str, privileges: str = "ALL") -> None:
    db_name = identifier(db_name, "数据库名")
    if username:
        username = identifier(username, "用户名")
    if host:
        host = saved_host(host)
    sealed = _seal_only(encrypt_text(raw_password) if raw_password else "")
    if raw_password and not sealed.startswith(PREFIX):
        raise DatabaseError("密码保存失败", 500)
    _migrate_state_once()
    _ledger_save(db_name, username, sealed, host, privileges, replace=True)


def _ledger_rows() -> list:
    connection = _open_ledger()
    try:
        return [dict(row) for row in connection.execute(
            "SELECT db_name, username, password_enc, host, auth_type, privileges, created_at, updated_at "
            "FROM databases ORDER BY created_at, db_name"
        )]
    finally:
        connection.close()


def _ledger_one(db_name: str) -> dict:
    connection = _open_ledger()
    try:
        row = connection.execute("SELECT * FROM databases WHERE db_name = ?", (db_name,)).fetchone()
        return dict(row) if row else {}
    finally:
        connection.close()


def _ledger_delete(db_name: str) -> None:
    connection = _open_ledger()
    try:
        connection.execute("DELETE FROM databases WHERE db_name = ?", (db_name,))
        connection.commit()
    finally:
        connection.close()
        os.chmod(_ledger_path(), 0o600)


def _ledger_user_remains(username: str, host: str, db_name: str) -> bool:
    connection = _open_ledger()
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM databases WHERE username = ? AND host = ? AND db_name != ?",
            (username, host, db_name),
        ).fetchone()
        return bool(row and int(row[0]) > 0)
    finally:
        connection.close()


def _migrate_state_once() -> None:
    connection = _open_ledger()
    try:
        found = connection.execute("SELECT value FROM ledger_meta WHERE key = ?", ("state_json",)).fetchone()
        if found:
            return
        state = _load()
        records = state.get("records") or {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(records, dict):
            for raw_name, record in records.items():
                if not isinstance(record, dict):
                    continue
                try:
                    db_name = identifier(str(raw_name), "数据库名")
                except DatabaseError:
                    continue
                stored = _seal_only(str(record.get("password") or ""))
                username = str(record.get("user") or "")
                if username:
                    try:
                        username = identifier(username, "用户名")
                    except DatabaseError:
                        username = ""
                host = str(record.get("host") or "")
                if host:
                    try:
                        host = saved_host(host)
                    except DatabaseError:
                        host = ""
                privileges = str(record.get("privileges") or "")[:40]
                created = str(record.get("created_at") or now)
                connection.execute(
                    """INSERT OR IGNORE INTO databases
                        (db_name, username, password_enc, host, auth_type, privileges, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (db_name, username, stored, host, "本地", privileges, created, now),
                )
        connection.execute(
            "INSERT OR REPLACE INTO ledger_meta (key, value) VALUES (?, ?)",
            ("state_json", "1"),
        )
        connection.commit()
    finally:
        connection.close()
        os.chmod(_ledger_path(), 0o600)


def _public_item(row: dict) -> dict:
    username = str(row.get("username") or "")
    return {
        "db_name": row.get("db_name") or "",
        "name": row.get("db_name") or "",
        "username": username,
        "password": "",
        "has_password": bool(row.get("password_enc")),
        "privileges": str(row.get("privileges") or ""),
        "host": str(row.get("host") or ""),
        "auth_type": "本地",
    }


def _import_live_databases() -> None:
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                _execute(cursor, "SHOW DATABASES")
                names = [row[0] for row in cursor.fetchall()]
    except DatabaseError:
        return
    known = {row["db_name"] for row in _ledger_rows()}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for raw in names:
        if not isinstance(raw, str) or raw.lower() in SYSTEM_DATABASES:
            continue
        try:
            db_name = identifier(raw, "数据库名")
        except DatabaseError:
            continue
        if db_name in known:
            continue
        _ledger_save(db_name, "", "", "", "", replace=False, created_at=now)
        known.add(db_name)


def _port_open(port: int = 3306) -> bool:
    sock = socket.socket()
    sock.settimeout(0.4)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def _mysqld_process() -> bool:
    try:
        import psutil
    except ImportError:
        return False
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name in {"mysqld", "mariadbd"}:
            return True
    return False


def _admin(*args: str, timeout: int = 20) -> tuple:
    if not os.path.isfile(ADMIN):
        return 127, "没有安装数据库管理程序"
    completed = subprocess.run(
        ["/usr/bin/sudo", "-n", ADMIN, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output.strip()


def _container_running() -> bool:
    code, output = _admin("status")
    return code == 0 and "container=running" in output


def detect() -> dict:
    remote = _load().get("remote") or None
    listening = _port_open()
    process = _mysqld_process()
    container = _container_running()
    local = listening or process or container
    remote_ok = False
    if remote and not local:
        try:
            connection = _connect(remote=remote)
            connection.close()
            remote_ok = True
        except DatabaseError:
            remote_ok = False
    return {
        "engine": "mysql",
        "installed": local or remote_ok,
        "local": local,
        "listening": listening,
        "process": process,
        "container": container,
        "remote": bool(remote_ok),
        "phpmyadmin": "",
    }


def install_mysql(raw_password: str) -> dict:
    raw_password = root_password(raw_password)
    if detect()["local"]:
        return {"installed": True, "message": "MySQL 已经在运行"}
    if _port_open() and not _container_running():
        raise DatabaseError("3306 已被其他服务占用，没有改动其他端口")
    code, output = _admin("install", raw_password, timeout=180)
    if code != 0:
        logger.warning("安装 MySQL 失败: %s", (output or "").replace(raw_password, "******")[:500])
        raise DatabaseError("安装 MySQL 失败", 400)
    write_secret_file(_secret_path(), raw_password)
    return {"installed": True, "message": "MySQL 已安装，只监听本机 3306，没有改动其他服务端口"}


def save_remote(host: str, port: str, user: str, raw_password: str) -> dict:
    user = identifier(user, "用户名") if user != "root" else "root"
    raw_password = password(raw_password)
    if not isinstance(host, str) or not re_host(host):
        raise DatabaseError("主机地址无效")
    label = str(port).strip()
    if isinstance(port, bool) or not label.isdigit() or not 1 <= len(label) <= 5:
        raise DatabaseError("端口无效")
    number = int(label)
    if not 1 <= number <= 65535 or number == 22:
        raise DatabaseError("端口无效")
    profile = {"host": host, "port": label, "user": user, "password": raw_password}
    connection = _connect(remote={**profile, "port": number})
    connection.close()
    state = _load()
    stored = dict(profile)
    stored["password"] = encrypt_text(raw_password)
    state["remote"] = stored
    _save(state)
    return {"saved": True}


def re_host(host: str) -> bool:
    import re

    if host in {"localhost", "127.0.0.1"}:
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host)) and ".." not in host


def list_databases(page: int = 1, page_size: int = 10) -> dict:
    if page < 1 or page_size < 1 or page_size > PAGE_MAX:
        raise DatabaseError("分页参数无效")
    _migrate_state_once()
    status = detect()
    if status.get("installed"):
        _import_live_databases()
    items = [_public_item(row) for row in _ledger_rows()]
    start = (page - 1) * page_size
    return {
        "ready": bool(status.get("installed")) or bool(items),
        "items": items[start : start + page_size],
        "total": len(items),
        "page": page,
        "page_size": page_size,
    }


def create_database(name: str, user: str, raw_password: str, access: str, ip: str = "", raw_note: str = "") -> dict:
    name = identifier(name, "数据库名")
    user = identifier(user, "用户名")
    raw_password = password(raw_password)
    host = saved_host(account_host(access, ip))
    raw_note = note(raw_note)
    account = quote_ident(user, "用户名") + "@" + quote_host(access, ip)
    database = quote_ident(name, "数据库名")
    try:
        with _connect() as connection:
            escaped = connection.escape(raw_password)
            statements = (
                "FLUSH PRIVILEGES",
                "CREATE USER IF NOT EXISTS " + account + " IDENTIFIED BY " + escaped,
                "ALTER USER " + account + " IDENTIFIED BY " + escaped,
                "CREATE DATABASE IF NOT EXISTS " + database + " CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci",
                "GRANT ALL PRIVILEGES ON " + database + ".* TO " + account,
                "FLUSH PRIVILEGES",
            )
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            connection.commit()
    except DatabaseError:
        raise
    except (MySQLError, ValueError, OSError):
        raise DatabaseError("创建数据库失败") from None
    _upsert_binding(name, user, host, raw_password, raw_note)
    return {"name": name}


def _upsert_binding(name: str, user: str, host: str, raw_password: str, raw_note: str) -> None:
    _migrate_state_once()
    state = _load()
    records = state.get("records")
    if not isinstance(records, dict):
        records = {}
        state["records"] = records
    sealed = encrypt_text(raw_password)
    current = records.get(name)
    if isinstance(current, dict):
        current["user"] = user
        current["host"] = host
        current["password"] = sealed
        current["privileges"] = "ALL"
        if raw_note:
            current["note"] = raw_note
    else:
        records[name] = {
            "user": user,
            "host": host,
            "password": sealed,
            "privileges": "ALL",
            "note": raw_note,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    _save(state)
    _ledger_save(name, user, sealed, host, "ALL", replace=True)


def delete_database(name: str, confirm: str | None = None) -> dict:
    name = identifier(name, "数据库名")
    if name.lower() in SYSTEM_DATABASES:
        raise DatabaseError("不能删除系统库")
    if confirm is not None and confirm != name:
        raise DatabaseError("请输入数据库名以确认删除")
    _migrate_state_once()
    record = _ledger_one(name)
    username = str(record.get("username") or "")
    host = str(record.get("host") or "")
    drop_user = bool(username) and username.lower() != "root" and host
    if drop_user:
        try:
            username = identifier(username, "用户名")
            host = saved_host(host)
        except DatabaseError:
            drop_user = False
        else:
            drop_user = not _ledger_user_remains(username, host, name)
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DROP DATABASE " + quote_ident(name, "数据库名"))
                if drop_user:
                    account = quote_ident(username, "用户名") + "@'" + host.replace("'", "''") + "'"
                    cursor.execute("DROP USER IF EXISTS " + account)
                _execute(cursor, "FLUSH PRIVILEGES")
            connection.commit()
    except DatabaseError:
        raise
    except (MySQLError, ValueError, OSError) as exc:
        code = exc.args[0] if isinstance(exc, MySQLError) and exc.args else None
        if code not in (1008, 1049):
            raise DatabaseError("删除数据库失败") from None
    _ledger_delete(name)
    state = _load()
    records = state.get("records")
    if isinstance(records, dict):
        records.pop(name, None)
        _save(state)
    return {"name": name}


def change_password(name: str, raw_password: str) -> dict:
    name = identifier(name, "数据库名")
    raw_password = root_password(raw_password)
    _migrate_state_once()
    record = _ledger_one(name)
    if not record or not record.get("username") or not record.get("host"):
        raise DatabaseError("这个库不是面板创建的，不能在这里改密")
    username = identifier(str(record["username"]), "用户名")
    host = saved_host(str(record["host"]))
    account = quote_ident(username, "用户名") + "@'" + host.replace("'", "''") + "'"
    with _connect() as connection:
        escaped = connection.escape(raw_password)
        statement = "ALTER USER " + account + " IDENTIFIED BY " + escaped
        with connection.cursor() as cursor:
            cursor.execute(statement)
            _execute(cursor, "FLUSH PRIVILEGES")
        connection.commit()
    sealed = _seal_only(encrypt_text(raw_password))
    if not sealed.startswith(PREFIX):
        raise DatabaseError("密码保存失败", 500)
    _ledger_save(name, username, sealed, host, str(record.get("privileges") or "ALL"), replace=True)
    state = _load()
    current = (state.get("records") or {}).get(name)
    if isinstance(current, dict):
        current["password"] = sealed
        current["user"] = username
        current["host"] = host
        _save(state)
    return {"name": name}


def change_access(name: str, access: str, ip: str = "") -> dict:
    name = identifier(name, "数据库名")
    host = account_host(access, ip)
    state = _load()
    record = (state.get("records") or {}).get(name)
    if not record or not record.get("password"):
        raise DatabaseError("没有记录原密码，不能修改访问权限")
    old = quote_ident(record["user"], "用户名") + "@'" + saved_host(record["host"]) + "'"
    new = quote_ident(record["user"], "用户名") + "@" + quote_host(access, ip)
    database = quote_ident(name, "数据库名")
    with _connect() as connection:
        with connection.cursor() as cursor:
            _execute(cursor, "CREATE USER " + new + " IDENTIFIED BY %s", (decrypt_text(str(record["password"])),))
            _execute(cursor, "GRANT ALL PRIVILEGES ON " + database + ".* TO " + new)
            _execute(cursor, "DROP USER IF EXISTS " + old)
            _execute(cursor, "FLUSH PRIVILEGES")
        connection.commit()
    record["host"] = host
    _save(state)
    return {"name": name, "host": host}


def show_root_password() -> dict:
    path = _secret_path()
    if not path.is_file():
        return {"password": "", "has_password": False}
    try:
        value = read_secret_file(path)
    except Exception:
        raise DatabaseError("无法读取 root 密码", 502) from None
    return {"password": value, "has_password": bool(value)}


def change_root_password(raw_password: str) -> dict:
    raw_password = root_password(raw_password)
    try:
        with _connect() as connection:
            changed = False
            with connection.cursor() as cursor:
                for host in ("localhost", "%"):
                    account = "'root'@'" + host + "'"
                    try:
                        _execute(cursor, "ALTER USER " + account + " IDENTIFIED BY %s", (raw_password,))
                        changed = True
                    except MySQLError:
                        continue
                if not changed:
                    raise DatabaseError("没有找到 root 账号")
                _execute(cursor, "FLUSH PRIVILEGES")
            connection.commit()
    except DatabaseError:
        raise
    except (MySQLError, ValueError, OSError):
        raise DatabaseError("修改 root 密码失败", 502) from None
    write_secret_file(_secret_path(), raw_password)
    remote = _load().get("remote")
    if remote and remote.get("user") == "root":
        remote["password"] = encrypt_text(raw_password)
        state = _load()
        state["remote"] = remote
        _save(state)
    return {"changed": True}


def resolve_backup_file(name: str, filename: str, must_exist: bool = True, folder: Path | None = None) -> Path:
    name = identifier(name, "数据库名")
    if (
        not isinstance(filename, str)
        or not filename
        or filename != Path(filename).name
        or not BACKUP_NAME.fullmatch(filename)
        or not filename.startswith(name + "-")
    ):
        raise DatabaseError("备份文件无效")
    base = (folder if folder is not None else _dir() / "backups").resolve()
    raw = base / filename
    if raw.is_symlink():
        raise DatabaseError("备份文件无效")
    candidate = raw.resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise DatabaseError("备份文件无效") from None
    if candidate.parent != base:
        raise DatabaseError("备份文件无效")
    if must_exist and (not candidate.is_file() or candidate.is_symlink()):
        raise DatabaseError("备份文件无效")
    return candidate


def _hold_slot(locked: bool) -> bool:
    if locked:
        return False
    if not _IO_SLOT.acquire(blocking=False):
        raise DatabaseError("已有备份或恢复正在进行")
    return True


def backup_database(name: str, _locked: bool = False) -> dict:
    name = identifier(name, "数据库名")
    owns = _hold_slot(_locked)
    try:
        if not _container_running():
            raise DatabaseError("当前没有面板安装的 MySQL，不能调用 mysqldump")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{name}-{stamp}.sql.gz"
        resolve_backup_file(name, filename, must_exist=False)
        code, _output = _admin("dump", name, filename, timeout=120)
        if code != 0:
            raise DatabaseError("备份失败", 400)
        path = resolve_backup_file(name, filename)
        os.chmod(path, 0o600)
        return {"file": filename}
    finally:
        if owns:
            _IO_SLOT.release()


def list_backups(name: str) -> dict:
    name = identifier(name, "数据库名")
    folder = _dir() / "backups"
    items = []
    if folder.is_dir():
        for path in sorted(folder.glob(name + "-*.sql.gz"), reverse=True):
            try:
                resolved = resolve_backup_file(name, path.name, folder=folder)
            except DatabaseError:
                continue
            if resolved.is_file() and not resolved.is_symlink():
                items.append({"file": resolved.name, "size": resolved.stat().st_size})
    return {"items": items[:20]}


def restore_database(name: str, filename: str, _locked: bool = False) -> dict:
    path = resolve_backup_file(name, filename)
    owns = _hold_slot(_locked)
    try:
        code, _output = _admin("restore", name, path.name, timeout=120)
        if code != 0:
            raise DatabaseError("还原失败", 400)
        return {"file": path.name}
    finally:
        if owns:
            _IO_SLOT.release()


def _set_job(job_id: str, **fields) -> None:
    with _JOBS_GUARD:
        current = _JOBS.get(job_id) or {}
        current.update(fields)
        _JOBS[job_id] = current


def job_status(job_id: str) -> dict:
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{16}", job_id):
        raise DatabaseError("任务不存在", 404)
    with _JOBS_GUARD:
        job = dict(_JOBS.get(job_id) or {})
    if not job:
        raise DatabaseError("任务不存在", 404)
    ready = job.get("status") == "ready"
    return {
        "job_id": job_id,
        "status": job.get("status") or "",
        "ready": ready,
        "file": job.get("file") or "" if ready else "",
    }


def _backup_thread(job_id: str, name: str) -> None:
    try:
        result = backup_database(name, _locked=True)
        _set_job(job_id, status="ready", file=result.get("file") or "")
    except Exception as exc:
        message = exc.message if isinstance(exc, DatabaseError) else "备份失败"
        _set_job(job_id, status="failed", file="", message=str(message)[:200])
    finally:
        _IO_SLOT.release()


def _restore_thread(job_id: str, name: str, filename: str) -> None:
    try:
        result = restore_database(name, filename, _locked=True)
        _set_job(job_id, status="ready", file=result.get("file") or "")
    except Exception as exc:
        message = exc.message if isinstance(exc, DatabaseError) else "还原失败"
        _set_job(job_id, status="failed", file="", message=str(message)[:200])
    finally:
        _IO_SLOT.release()


def begin_backup(name: str) -> dict:
    name = identifier(name, "数据库名")
    if not _IO_SLOT.acquire(blocking=False):
        raise DatabaseError("已有备份或恢复正在进行")
    job_id = secrets.token_hex(8)
    _set_job(job_id, status="running", file="", kind="backup")
    try:
        threading.Thread(target=_backup_thread, args=(job_id, name), daemon=True).start()
    except Exception:
        _IO_SLOT.release()
        raise
    return {"job_id": job_id, "ready": False, "status": "running", "file": ""}


def begin_restore(name: str, filename: str) -> dict:
    name = identifier(name, "数据库名")
    path = resolve_backup_file(name, filename)
    if not _IO_SLOT.acquire(blocking=False):
        raise DatabaseError("已有备份或恢复正在进行")
    job_id = secrets.token_hex(8)
    _set_job(job_id, status="running", file="", kind="restore")
    try:
        threading.Thread(target=_restore_thread, args=(job_id, name, path.name), daemon=True).start()
    except Exception:
        _IO_SLOT.release()
        raise
    return {"job_id": job_id, "ready": False, "status": "running", "file": ""}


def _execute(cursor, query: str, args=None):
    query = pymysql_sql(query)
    if args is None:
        cursor.execute(query)
        return
    cursor.execute(query, args)


def _connect(remote: dict | None = None):
    profile = remote if remote is not None else _active_profile()
    try:
        return pymysql.connect(
            host=profile["host"],
            port=int(profile["port"]),
            user=profile["user"],
            password=profile["password"],
            connect_timeout=5,
            charset="utf8mb4",
            autocommit=False,
        )
    except MySQLError as exc:
        raise DatabaseError("连接 MySQL 失败", 400) from exc


def _active_profile() -> dict:
    if _port_open() or _container_running():
        secret = _secret_path()
        if not secret.is_file():
            raise DatabaseError("还没有保存 MySQL root 密码", 400)
        return {"host": "127.0.0.1", "port": 3306, "user": "root", "password": read_secret_file(secret)}
    remote = _load().get("remote")
    if remote:
        profile = dict(remote)
        profile["password"] = decrypt_text(str(profile.get("password") or ""))
        return profile
    raise DatabaseError("当前未安装 Mysql 环境/远程数据库", 400)


def _sizes(cursor, names: list) -> dict:
    if not names:
        return {}
    marks = ",".join(["%s"] * len(names))
    _execute(cursor, 
        "SELECT table_schema, COALESCE(SUM(data_length + index_length), 0) "
        "FROM information_schema.tables WHERE table_schema IN (" + marks + ") GROUP BY table_schema",
        names,
    )
    return {row[0]: int(row[1] or 0) for row in cursor.fetchall()}


def _charsets(cursor, names: list) -> dict:
    if not names:
        return {}
    marks = ",".join(["%s"] * len(names))
    _execute(cursor, 
        "SELECT schema_name, default_character_set_name FROM information_schema.schemata "
        "WHERE schema_name IN (" + marks + ")",
        names,
    )
    return {row[0]: row[1] for row in cursor.fetchall()}


def _latest_backup(name: str) -> str:
    folder = _dir() / "backups"
    found = sorted(folder.glob(name + "-*.sql.gz")) if folder.is_dir() else []
    return found[-1].name if found else ""


def _access_label(host: str) -> str:
    if host == "localhost":
        return "本地"
    if host == "%":
        return "所有人"
    return host


def _generated_password() -> str:
    import secrets

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789_"
    return "".join(secrets.choice(alphabet) for _ in range(24))
