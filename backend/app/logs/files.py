"""只读取固定位置的日志，并且每次只取文件末尾的一小段。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from app.clock import BEIJING
from app.config import DATA_DIR
from app.logs.geo import place
from app.logs.store import LogError

NGINX_DIR = Path("/var/log/nginx")
AUTH_LOG = Path("/var/log/auth.log")
SECURE_LOG = Path("/var/log/secure")
RUNTIME_LOG = DATA_DIR / "logs" / "runtime.log"
TASK_LOG = DATA_DIR / "logs" / "tasks.log"
SITE_NAME = re.compile(r"^(?:access|error|[A-Za-z0-9._-]{1,80}\.(?:access|error))\.log$")
SSH_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\S+).+sshd\[\d+\]: Accepted \S+ for (?P<user>[A-Za-z0-9_.-]{1,32}) from (?P<ip>(?:\d{1,3}\.){3}\d{1,3}) "
)
MAX_BYTES = 1_048_576
SSH_BYTES = 8_000_000
LINE_LIMIT = 1000


def tail_lines(path: Path, limit: int, max_bytes: int = MAX_BYTES) -> list[str]:
    if limit < 1 or limit > 5000:
        raise LogError("读取行数无效")
    if not path.is_file() or path.is_symlink():
        return []
    size = path.stat().st_size
    amount = min(size, max_bytes)
    with path.open("rb") as handle:
        handle.seek(size - amount)
        chunk = handle.read(amount)
    text = chunk.decode("utf-8", errors="replace")
    if size > amount:
        text = text.split("\n", 1)[-1]
    lines = [line[:LINE_LIMIT] for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def read_after(path: Path, offset: int) -> tuple[list[str], int]:
    if not path.is_file() or path.is_symlink():
        return [], 0
    size = path.stat().st_size
    if offset < 0 or offset > size:
        offset = max(0, size - 4096)
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read(min(size - offset, 65_536))
    text = chunk.decode("utf-8", errors="replace")
    lines = [line[:LINE_LIMIT] for line in text.splitlines() if line.strip()]
    return lines[-50:], offset + len(chunk)


def setup_runtime_log() -> None:
    import logging
    from logging.handlers import RotatingFileHandler

    path = runtime_path()
    root = logging.getLogger()
    if any(getattr(handler, "name", "") == "panel-runtime" for handler in root.handlers):
        return
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=1, encoding="utf-8")
    handler.name = "panel-runtime"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    formatter.converter = lambda timestamp: datetime.fromtimestamp(timestamp, BEIJING).timetuple()
    handler.setFormatter(formatter)
    root.addHandler(handler)


def runtime_path() -> Path:
    RUNTIME_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not RUNTIME_LOG.exists():
        RUNTIME_LOG.touch(mode=0o600)
    return RUNTIME_LOG


def task_lines(limit: int, keyword: str = "") -> dict:
    lines = _filter(tail_lines(TASK_LOG, limit) if TASK_LOG.is_file() else [], keyword)
    return {"items": lines, "total": len(lines), "message": "" if lines else "还没有计划任务执行记录"}


def runtime_lines(limit: int, keyword: str = "") -> dict:
    lines = _filter(tail_lines(runtime_path(), limit), keyword)
    return {"items": lines, "total": len(lines), "message": "" if lines else "运行日志还是空的"}


def software_lines(name: str, limit: int, keyword: str = "") -> dict:
    if name == "nginx":
        path = _nginx_file("error.log")
    elif name == "panel":
        path = runtime_path()
    else:
        raise LogError("没有这个软件日志")
    lines = _filter(tail_lines(path, limit), keyword)
    return {"name": name, "items": lines, "total": len(lines)}


def site_files() -> dict:
    items = []
    if NGINX_DIR.is_dir():
        for child in NGINX_DIR.iterdir():
            if child.is_symlink() or not child.is_file():
                continue
            if SITE_NAME.fullmatch(child.name):
                items.append(child.name)
    items.sort()
    return {"items": items}


def site_lines(name: str, limit: int, keyword: str = "") -> dict:
    path = _nginx_file(name)
    lines = _filter(tail_lines(path, limit), keyword)
    return {"file": name, "items": lines, "total": len(lines), "window": limit}


def _nginx_file(name: str) -> Path:
    if not SITE_NAME.fullmatch(name or ""):
        raise LogError("不能读取这个日志")
    root = NGINX_DIR.resolve()
    path = (NGINX_DIR / name).resolve()
    if path.is_symlink() or root not in path.parents:
        raise LogError("不能读取这个日志")
    if not path.is_file():
        raise LogError("日志文件不存在")
    return path


def stream_path(source: str) -> Path:
    if source == "runtime":
        return runtime_path()
    if source == "nginx-access":
        return _nginx_file("access.log")
    if source == "nginx-error":
        return _nginx_file("error.log")
    raise LogError("不能跟随这个日志")


def ssh_page(page: int, page_size: int, keyword: str = "") -> dict:
    if page < 1 or page > 500 or page_size not in {10, 20}:
        raise LogError("分页参数无效")
    text = _clean_keyword(keyword)
    path = AUTH_LOG if AUTH_LOG.is_file() else SECURE_LOG
    if not path.is_file() or path.is_symlink():
        return {"items": [], "total": 0, "page": page, "page_size": page_size, "message": "没有找到 SSH 登录日志"}
    found = []
    for line in tail_lines(path, 4000, SSH_BYTES):
        item = _ssh(line)
        if item is None:
            continue
        blob = f"{item['operator']} {item['details']} {item['ip']}"
        if text and text not in blob:
            continue
        found.append(item)
    found.reverse()
    start = (page - 1) * page_size
    return {
        "items": found[start : start + page_size],
        "total": len(found),
        "page": page,
        "page_size": page_size,
        "message": "只显示日志文件末尾一段里的成功登录",
    }


def _ssh(line: str) -> dict | None:
    matched = SSH_LINE.search(line)
    if not matched:
        return None
    ip = matched.group("ip")
    try:
        stamp = datetime.fromisoformat(matched.group("ts")).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        stamp = matched.group("ts")[:19].replace("T", " ")
    located = place(ip)
    shown = f"{ip}({located})" if located and located != "未知" else ip
    return {
        "operator": matched.group("user"),
        "type": "SSH登录",
        "details": f"{shown} 成功登录到 SSH",
        "ip": shown,
        "created_at": stamp,
    }


def _filter(lines: list[str], keyword: str) -> list[str]:
    text = _clean_keyword(keyword)
    if not text:
        return lines
    return [line for line in lines if text in line]


def _clean_keyword(keyword: str) -> str:
    return " ".join(str(keyword or "").split())[:40]
