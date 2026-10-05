"""六种数据库的安装状态和可视化操作。Docker 命令只走固定脚本。"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR
from app.databases.security import (
    DatabaseError,
    engine_name,
    generated_password,
    identifier,
    resolve_sqlite_file,
    root_password,
    strong_password,
)
from app.databases import service as mysql
from app.vault import decrypt_text, read_secret_file, write_secret_file
from app.files.security import get_file_root

logger = logging.getLogger("panel.databases")

ADMIN = "/usr/local/sbin/panel-db-admin"
SQLITE_DIR = Path("/www/server/data/sqlite")
LABELS = {
    "mysql": "MySQL",
    "sqlserver": "SQLServer",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "pgsql": "PgSQL",
    "sqlite": "SQLite",
}
PORTS = {"mysql": 3306, "sqlserver": 1433, "mongodb": 27017, "redis": 6379, "pgsql": 5432}
DOCKER_ENGINES = tuple(PORTS)
_locks = {name: threading.Lock() for name in DOCKER_ENGINES}
_running: set[str] = set()


def _meta_dir() -> Path:
    path = DATA_DIR / "databases"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _secret_path(engine: str) -> Path:
    if engine == "mysql":
        folder = DATA_DIR / "mysql"
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        return folder / "root.secret"
    return _meta_dir() / f"{engine}.secret"


def _progress_path(engine: str) -> Path:
    return _meta_dir() / f"{engine}.progress.json"


def _read_progress(engine: str) -> dict:
    path = _progress_path(engine)
    if not path.is_file():
        return {"state": "idle", "message": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "idle", "message": ""}
    return data if isinstance(data, dict) else {"state": "idle", "message": ""}


def _write_progress(engine: str, state: str, message: str, reveal: bool = False) -> None:
    path = _progress_path(engine)
    payload = {"state": state, "message": message, "reveal": reveal}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, 0o600)


def _read_secret(engine: str) -> str:
    path = _secret_path(engine)
    if not path.is_file():
        raise DatabaseError("还没有数据库密码")
    value = read_secret_file(path)
    if not isinstance(value, str) or not value:
        raise DatabaseError("还没有数据库密码")
    return value


def _admin(*args: str, timeout: int = 30) -> tuple[int, str]:
    if not os.path.isfile(ADMIN):
        return 127, "没有安装数据库管理程序"
    completed = subprocess.run(
        ["/usr/bin/sudo", "-n", ADMIN, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    return completed.returncode, output


def _parse_status(output: str) -> dict:
    data = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    return data


def _safe_error(engine: str, text: str, extra: str = "") -> str:
    secret = ""
    raw = ""
    path = _secret_path(engine)
    if path.is_file():
        raw = path.read_text(encoding="utf-8").strip()
        if raw.startswith("enc:v1:"):
            try:
                secret = decrypt_text(raw)
            except Exception:
                secret = ""
        else:
            secret = raw
    cleaned = text
    if secret:
        cleaned = cleaned.replace(secret, "******")
    if raw and raw != secret:
        cleaned = cleaned.replace(raw, "******")
    if extra:
        cleaned = cleaned.replace(extra, "******")
    line = cleaned.strip().splitlines()[-1] if cleaned.strip() else ""
    if line:
        logger.warning("数据库命令失败 engine=%s detail=%s", engine, line[:500])
    return ""


def engine_status(engine: str) -> dict:
    engine = engine_name(engine)
    label = LABELS[engine]
    if engine == "sqlite":
        present = SQLITE_DIR.is_dir()
        return {
            "engine": engine,
            "label": label,
            "installed": present,
            "state": "local" if present else "not_installed",
            "docker": False,
            "port": 0,
            "message": "SQLite 是本机文件，没有独立服务进程" if present else "SQLite 数据目录不存在",
            "data_dir": str(SQLITE_DIR),
        }
    if engine not in _running:
        progress = _read_progress(engine)
        if progress.get("state") in {"pulling", "starting"}:
            _write_progress(engine, "failed", "安装中断，请重新安装")
    code, output = _admin("status", engine, timeout=15)
    parsed = _parse_status(output)
    if code != 0 and "container=" not in output:
        _safe_error(engine, output)
        return {
            "engine": engine,
            "label": label,
            "installed": False,
            "state": "unknown",
            "docker": False,
            "container": "absent",
            "port": PORTS[engine],
            "listening": False,
            "remote": False,
            "message": "无法读取容器状态",
            "data_dir": f"/www/server/data/{engine}",
        }
    container = parsed.get("container") or "absent"
    state = {"running": "running", "stopped": "stopped"}.get(container, "not_installed")
    progress = _read_progress(engine)
    if progress.get("state") in {"pulling", "starting"}:
        state = progress["state"]
    installed = container in {"running", "stopped"} or state in {"pulling", "starting"}
    remote = False
    if engine == "mysql" and container == "absent":
        detected = mysql.detect()
        if detected.get("installed"):
            installed = True
            state = "running"
            remote = bool(detected.get("remote"))
    return {
        "engine": engine,
        "label": label,
        "installed": installed,
        "state": state,
        "docker": True,
        "container": container,
        "port": PORTS[engine],
        "listening": parsed.get("port") == "open",
        "remote": remote,
        "message": progress.get("message") or "",
        "data_dir": f"/www/server/data/{engine}",
    }


def all_status() -> dict:
    return {"items": [engine_status(name) for name in ("mysql", "sqlserver", "mongodb", "redis", "pgsql", "sqlite")]}


def _store_secret(engine: str, raw: str) -> str:
    value = strong_password(raw) if raw else generated_password()
    write_secret_file(_secret_path(engine), value)
    return value


def begin_install(engine: str, raw_password: str = "") -> dict:
    engine = engine_name(engine)
    if engine == "sqlite":
        raise DatabaseError("SQLite 不需要安装")
    raw_password = root_password(raw_password)
    current = engine_status(engine)
    if current["state"] == "running":
        return {"state": "running", "message": f"{LABELS[engine]} 已经在运行"}
    if current["state"] == "stopped":
        raise DatabaseError("容器已停止，请先启动，没有重新安装")
    if engine in _running:
        return {"state": "pulling", "message": "正在拉取镜像..."}
    if current.get("listening") and current.get("container") != "running":
        raise DatabaseError(f"{PORTS[engine]} 已被其他服务占用，没有改动其他端口")
    _write_progress(engine, "pulling", "正在拉取镜像...", reveal=False)
    thread = threading.Thread(target=_install_worker, args=(engine, raw_password), daemon=True)
    with _locks[engine]:
        if engine in _running:
            return {"state": "pulling", "message": "正在拉取镜像..."}
        _running.add(engine)
        thread.start()
    return {"state": "pulling", "message": "正在拉取镜像..."}


def _install_worker(engine: str, raw_password: str) -> None:
    errors: list[str] = []
    proc = None
    try:
        proc = subprocess.Popen(
            ["/usr/bin/sudo", "-n", ADMIN, "install", engine, raw_password],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def drain() -> None:
            if proc.stderr is not None:
                errors.append(proc.stderr.read())

        threading.Thread(target=drain, daemon=True).start()
        if proc.stdout is not None:
            for line in proc.stdout:
                text = line.strip()
                if text == "phase=pull":
                    _write_progress(engine, "pulling", "正在拉取镜像...", reveal=False)
                elif text == "phase=start":
                    _write_progress(engine, "starting", "正在启动容器...", reveal=False)
        code = proc.wait(timeout=900)
        if code != 0:
            _safe_error(engine, "".join(errors), raw_password)
            _write_progress(engine, "failed", "安装失败")
            return
        write_secret_file(_secret_path(engine), raw_password)
        _write_progress(engine, "ready", "安装完成，只监听本机端口，没有改动其他服务", reveal=False)
    except Exception as exc:
        if proc is not None and proc.poll() is None:
            proc.kill()
        _safe_error(engine, str(exc), raw_password)
        _write_progress(engine, "failed", "安装失败")
    finally:
        _running.discard(engine)


def install_progress(engine: str) -> dict:
    engine = engine_name(engine)
    if engine == "sqlite":
        return {"state": "ready", "message": "SQLite 不需要安装"}
    data = _read_progress(engine)
    payload = {"state": data.get("state") or "idle", "message": data.get("message") or "", "engine": engine}
    if data.get("state") == "ready" and data.get("reveal"):
        payload["password"] = ""
        payload["has_password"] = True
        payload["user"] = {"mysql": "root", "sqlserver": "SA", "mongodb": "panel", "pgsql": "panel"}.get(engine, "")
        _write_progress(engine, "ready", data.get("message") or "安装完成", reveal=False)
    return payload


def start_engine(engine: str) -> dict:
    engine = engine_name(engine)
    if engine == "sqlite":
        raise DatabaseError("SQLite 不需要启动容器")
    code, output = _admin("start", engine, timeout=40)
    if code != 0:
        raise DatabaseError(_safe_error(engine, output) or "启动失败")
    _write_progress(engine, "ready", "已启动")
    return {"state": "running"}


def stop_engine(engine: str) -> dict:
    engine = engine_name(engine)
    if engine == "sqlite":
        raise DatabaseError("SQLite 不需要停止容器")
    code, output = _admin("stop", engine, timeout=40)
    if code != 0:
        raise DatabaseError(_safe_error(engine, output) or "停止失败")
    _write_progress(engine, "idle", "已停止")
    return {"state": "stopped"}


def _require_running(engine: str) -> None:
    current = engine_status(engine)
    if current["state"] != "running":
        raise DatabaseError(f"当前未安装 {LABELS[engine]} 环境")


def sqlserver_list() -> dict:
    _require_running("sqlserver")
    code, output = _admin("mssql-list", timeout=30)
    if code != 0:
        raise DatabaseError(_safe_error("sqlserver", output) or "读取失败")
    names = []
    for line in output.splitlines():
        item = line.strip()
        try:
            names.append(identifier(item, "数据库名"))
        except DatabaseError:
            continue
    return {"items": [{"name": name} for name in names]}


def sqlserver_create(name: str) -> dict:
    _require_running("sqlserver")
    name = identifier(name, "数据库名")
    code, output = _admin("mssql-create", name, timeout=40)
    if code != 0:
        raise DatabaseError(_safe_error("sqlserver", output) or "创建失败")
    return {"name": name}


def sqlserver_delete(name: str) -> dict:
    _require_running("sqlserver")
    name = identifier(name, "数据库名")
    code, output = _admin("mssql-drop", name, timeout=40)
    if code != 0:
        raise DatabaseError(_safe_error("sqlserver", output) or "删除失败")
    return {"name": name}


def sqlserver_backup(name: str) -> dict:
    _require_running("sqlserver")
    name = identifier(name, "数据库名")
    filename = f"{name}-{datetime.now().strftime('%Y%m%d%H%M%S')}.bak"
    code, output = _admin("mssql-backup", name, filename, timeout=180)
    if code != 0:
        raise DatabaseError(_safe_error("sqlserver", output) or "备份失败")
    return {"file": filename}


def sqlserver_backups(name: str) -> dict:
    name = identifier(name, "数据库名")
    folder = _meta_dir() / "sqlserver-backups"
    items = []
    if folder.is_dir():
        for path in sorted(folder.glob(name + "-*.bak"), reverse=True):
            if path.is_file() and not path.is_symlink():
                items.append({"file": path.name, "size": path.stat().st_size})
    return {"items": items[:20]}


def sqlserver_restore(name: str, filename: str) -> dict:
    _require_running("sqlserver")
    name = identifier(name, "数据库名")
    if "/" in filename or not filename.startswith(name + "-") or not filename.endswith(".bak"):
        raise DatabaseError("备份文件无效")
    code, output = _admin("mssql-restore", name, filename, timeout=180)
    if code != 0:
        raise DatabaseError(_safe_error("sqlserver", output) or "还原失败")
    return {"file": filename}


def _mongo():
    try:
        from pymongo import MongoClient
    except ImportError:
        raise DatabaseError("没有安装 MongoDB 驱动", 500) from None
    return MongoClient


def _mongo_client():
    _require_running("mongodb")
    client_cls = _mongo()
    client = client_cls(
        host="127.0.0.1",
        port=27017,
        username="panel",
        password=_read_secret("mongodb"),
        authSource="admin",
        serverSelectionTimeoutMS=2500,
    )
    client.admin.command("ping")
    return client


def mongo_overview() -> dict:
    with _mongo_client() as client:
        items = []
        for name in client.list_database_names():
            if name in {"admin", "local", "config"}:
                continue
            items.append({"name": name, "collections": client[name].list_collection_names()})
    return {"items": items, "user": "panel"}


def mongo_create(name: str, user: str, raw_password: str) -> dict:
    name = identifier(name, "数据库名")
    user = identifier(user, "用户名")
    raw_password = strong_password(raw_password)
    with _mongo_client() as client:
        database = client[name]
        database.create_collection("init")
        database.command("createUser", user, pwd=raw_password, roles=[{"role": "readWrite", "db": name}])
    return {"name": name, "user": user}


def mongo_delete(name: str) -> dict:
    name = identifier(name, "数据库名")
    with _mongo_client() as client:
        client.drop_database(name)
    return {"name": name}


def _redis():
    try:
        import redis
    except ImportError:
        raise DatabaseError("没有安装 Redis 驱动", 500) from None
    return redis


def _redis_client():
    _require_running("redis")
    client = _redis().Redis(
        host="127.0.0.1",
        port=6379,
        password=_read_secret("redis"),
        socket_timeout=2,
        decode_responses=True,
    )
    client.ping()
    return client


def redis_info() -> dict:
    client = _redis_client()
    info = client.info()
    return {
        "used_memory_human": info.get("used_memory_human") or "",
        "used_memory": int(info.get("used_memory") or 0),
        "connected_clients": int(info.get("connected_clients") or 0),
        "keys": int(client.dbsize()),
        "uptime_seconds": int(info.get("uptime_in_seconds") or 0),
    }


def redis_flush() -> dict:
    _redis_client().flushall()
    return {"flushed": True}


def redis_password(raw_password: str) -> dict:
    value = strong_password(raw_password)
    write_secret_file(_secret_path("redis"), value)
    code, output = _admin("repass", "redis", timeout=40)
    if code != 0:
        raise DatabaseError(_safe_error("redis", output) or "修改密码失败")
    return {"changed": True}


def _pg():
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        raise DatabaseError("没有安装 PostgreSQL 驱动", 500) from None
    return psycopg, sql


def _pg_connect():
    _require_running("pgsql")
    psycopg, sql = _pg()
    connection = psycopg.connect(
        host="127.0.0.1",
        port=5432,
        user="panel",
        password=_read_secret("pgsql"),
        dbname="postgres",
        connect_timeout=3,
        autocommit=True,
    )
    return connection, sql


def postgres_overview() -> dict:
    connection, _sql = _pg_connect()
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT datname FROM pg_database WHERE datistemplate = false AND datname <> 'postgres' ORDER BY datname")
            databases = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT rolname FROM pg_roles WHERE rolcanlogin = true AND rolname <> 'panel' ORDER BY rolname")
            roles = [row[0] for row in cursor.fetchall()]
    return {"databases": databases, "roles": roles, "user": "panel"}


def postgres_create(name: str, user: str, raw_password: str) -> dict:
    name = identifier(name, "数据库名")
    user = identifier(user, "用户名")
    raw_password = strong_password(raw_password)
    connection, sql = _pg_connect()
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(user)), (raw_password,))
            cursor.execute(sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(user)))
    return {"name": name, "user": user}


def postgres_delete(name: str) -> dict:
    name = identifier(name, "数据库名")
    connection, sql = _pg_connect()
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
    return {"name": name}


def postgres_role_password(user: str, raw_password: str) -> dict:
    user = identifier(user, "用户名") if user != "panel" else "panel"
    raw_password = strong_password(raw_password)
    connection, sql = _pg_connect()
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(user)), (raw_password,))
    if user == "panel":
        write_secret_file(_secret_path("pgsql"), raw_password)
    return {"user": user}


def postgres_delete_role(user: str) -> dict:
    user = identifier(user, "用户名")
    connection, sql = _pg_connect()
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(user)))
    return {"user": user}


def _sqlite_roots() -> list[Path]:
    code, _output = _admin("prepare", "sqlite", timeout=15)
    if code != 0 and not SQLITE_DIR.is_dir():
        raise DatabaseError("SQLite 目录还不可用")
    return [SQLITE_DIR, get_file_root()]


def sqlite_files() -> dict:
    roots = _sqlite_roots()
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        stack = [root]
        seen = 0
        while stack and seen < 2000:
            current = stack.pop()
            try:
                children = list(os.scandir(current))
            except OSError:
                continue
            for child in children:
                seen += 1
                if seen > 2000:
                    break
                if child.is_symlink():
                    continue
                if child.is_dir(follow_symlinks=False):
                    if child.name not in {"node_modules", ".git"}:
                        stack.append(Path(child.path))
                    continue
                if child.is_file(follow_symlinks=False) and Path(child.name).suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                    found.append(child.path)
    found.sort()
    return {"items": found[:100], "directory": str(SQLITE_DIR)}


def sqlite_tables(raw_path: str) -> dict:
    path = resolve_sqlite_file(raw_path, _sqlite_roots())
    if not path.is_file():
        raise DatabaseError("找不到这个数据库文件")
    uri = path.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    return {"file": str(path), "tables": [row[0] for row in rows]}


def sqlite_rows(raw_path: str, table: str, page: int = 1) -> dict:
    path = resolve_sqlite_file(raw_path, _sqlite_roots())
    table = identifier(table, "表名")
    if page < 1 or page > 100:
        raise DatabaseError("分页参数无效")
    uri = path.as_uri() + "?mode=ro"
    offset = (page - 1) * 50
    quoted = '"' + table.replace('"', "") + '"'
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({quoted})")]
        if not columns:
            raise DatabaseError("找不到这张表")
        rows = connection.execute(f"SELECT * FROM {quoted} LIMIT 50 OFFSET ?", (offset,)).fetchall()
    return {
        "file": str(path),
        "table": table,
        "columns": columns,
        "rows": [list(row) for row in rows],
        "page": page,
    }
