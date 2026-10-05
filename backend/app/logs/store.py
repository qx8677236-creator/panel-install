"""面板自己的日志表。用 SQLite 保存，不依赖外部数据库。"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime

from app.config import DATA_DIR
from app.logs.geo import place

_lock = threading.Lock()
LOGIN_TYPES = {"用户登录", "用户登出"}


class LogError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status
        super().__init__(message)


def _path():
    folder = DATA_DIR / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "panel.sqlite"


def connect() -> sqlite3.Connection:
    database = _path()
    connection = sqlite3.connect(database, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS panel_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator TEXT NOT NULL,
            type TEXT NOT NULL,
            details TEXT NOT NULL,
            ip TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_panel_logs_id ON panel_logs(id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_panel_logs_type ON panel_logs(type)")
    return connection


def _clean(value: str, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    return text[:limit]


def write_log(operator: str, log_type: str, details: str, ip: str, limit: int = 320) -> None:
    user = _clean(operator, 32) or "未知"
    kind = _clean(log_type, 20) or "操作"
    text = _clean(details, limit if limit > 0 else 320) or "执行了操作"
    host = _clean(ip, 64) or "unknown"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        connection = connect()
        try:
            connection.execute(
                "INSERT INTO panel_logs(operator, type, details, ip, created_at) VALUES (?, ?, ?, ?, ?)",
                (user, kind, text, host, stamp),
            )
            connection.commit()
        finally:
            connection.close()


def _page(page: int, page_size: int) -> tuple[int, int]:
    if page < 1 or page > 5000:
        raise LogError("分页参数无效")
    if page_size not in {10, 20}:
        raise LogError("每页只允许 10 或 20 条")
    return page, page_size


def _keyword(keyword: str) -> str:
    text = _clean(keyword, 40)
    return text.replace("%", "").replace("_", "")


def query_logs(section: str, page: int = 1, page_size: int = 10, keyword: str = "", log_type: str = "") -> dict:
    page, page_size = _page(page, page_size)
    keyword = _keyword(keyword)
    clauses = []
    args: list[str] = []
    if section == "login":
        clauses.append("type IN ('用户登录', '用户登出')")
    elif section == "operation":
        clauses.append("type NOT IN ('用户登录', '用户登出')")
    elif section == "audit":
        clauses.append("(type IN ('数据库', '网站管理', '日志管理', '文件管理') OR details LIKE '%删除%' OR details LIKE '%密码%' OR details LIKE '%安装%' OR details LIKE '%清空%')")
    else:
        raise LogError("日志分类无效")
    if log_type and log_type != "全部":
        kind = _clean(log_type, 20)
        clauses.append("type = ?")
        args.append(kind)
    if keyword:
        clauses.append("(operator LIKE ? OR type LIKE ? OR details LIKE ? OR ip LIKE ?)")
        like = f"%{keyword}%"
        args.extend([like, like, like, like])
    where = " WHERE " + " AND ".join(clauses)
    offset = (page - 1) * page_size
    with _lock:
        connection = connect()
        try:
            total = connection.execute(f"SELECT COUNT(*) FROM panel_logs{where}", args).fetchone()[0]
            rows = connection.execute(
                f"SELECT id, operator, type, details, ip, created_at FROM panel_logs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*args, page_size, offset],
            ).fetchall()
        finally:
            connection.close()
    return {
        "items": [_present(row) for row in rows],
        "total": int(total),
        "page": page,
        "page_size": page_size,
    }


def _present(row: sqlite3.Row) -> dict:
    ip = row["ip"]
    located = place(ip)
    shown = f"{ip}({located})" if located and located not in {ip, "未知"} else ip
    return {
        "id": row["id"],
        "operator": row["operator"],
        "type": row["type"],
        "details": row["details"],
        "ip": shown,
        "created_at": row["created_at"],
    }


def export_rows(section: str, keyword: str = "", log_type: str = "") -> list[dict]:
    result = query_logs(section, 1, 20, keyword, log_type)
    # 导出当前筛选，最多再取前 20 页，避免一次拉出全部历史。
    items = list(result["items"])
    page = 2
    while len(items) < 400 and page <= 20 and len(items) < result["total"]:
        more = query_logs(section, page, 20, keyword, log_type)
        if not more["items"]:
            break
        items.extend(more["items"])
        page += 1
    return items[:400]


def clear_section(section: str) -> int:
    if section == "login":
        clause = "type IN ('用户登录', '用户登出')"
    elif section == "operation":
        clause = "type NOT IN ('用户登录', '用户登出')"
    else:
        raise LogError("系统日志不能从面板清空")
    with _lock:
        connection = connect()
        try:
            cursor = connection.execute(f"DELETE FROM panel_logs WHERE {clause}")
            connection.commit()
            return int(cursor.rowcount)
        finally:
            connection.close()
