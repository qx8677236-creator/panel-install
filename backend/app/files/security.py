"""文件根目录监狱。

任何文件操作都要先经过 locate()。
含有 .. 的路径、指向 /etc 或 /root 的绝对路径，以及解析后落到根目录外的路径，
都会被拒绝，并写一条越界报警。符号链接如果指向监狱外面，同样拒绝。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Optional

from app.config import DATA_DIR, settings

logger = logging.getLogger("panel.files")

_actor: ContextVar[str] = ContextVar("file_actor", default="")
_alerts: deque = deque(maxlen=50)
_alert_lock = threading.Lock()
_root: Optional[Path] = None

# 这些位置即使被写成绝对路径，也不能当文件根，也不能被访问。
_SENSITIVE = (
    "/etc",
    "/root",
    "/private/etc",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/System",
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
)

REASON_TEXT = {
    "traversal": "检测到 ../ 路径穿越，已拦截",
    "sensitive_path": "试图访问系统目录（如 /etc、/root），已拦截",
    "outside_jail": "路径超出文件根目录，已拦截",
    "illegal_char": "路径包含非法字符，已拦截",
}


class PathJailError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        self.code = "path_jail"
        self.message = REASON_TEXT.get(reason, "已拦截越界路径访问")
        super().__init__(self.message)


class FileOpError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.status = status
        super().__init__(message)


def set_actor(username: str) -> None:
    _actor.set(username or "")


def recent_alerts() -> list:
    with _alert_lock:
        return list(_alerts)


def clear_alerts() -> None:
    with _alert_lock:
        _alerts.clear()


def raise_jail(raw: str, reason: str) -> None:
    """记录报警并拒绝这次访问。响应里不回显原始路径。"""
    shown = str(raw).replace("\n", " ").replace("\r", " ")[:180]
    logger.warning("【路径越界报警】user=%s reason=%s path=%s", _actor.get() or "-", reason, shown)
    item = {
        "ts": time.time(),
        "reason": reason,
        "username": _actor.get(),
        "message": REASON_TEXT.get(reason, "已拦截越界路径访问"),
    }
    with _alert_lock:
        _alerts.appendleft(item)
    raise PathJailError(reason)


def ensure_file_root() -> Path:
    """准备文件根目录。拒绝把监狱设在系统目录或面板数据目录上。"""
    global _root
    raw = (settings.file_root or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (DATA_DIR.parent / path).resolve()
    else:
        path = DATA_DIR / "wwwroot"
    path = path.resolve()
    if _root_is_forbidden(path):
        raise RuntimeError(f"文件根目录不允许使用系统路径: {path}")
    data_dir = DATA_DIR.resolve()
    if path == data_dir or _is_relative_to(data_dir, path):
        raise RuntimeError("文件根目录不能包含面板的 data 目录")
    path.mkdir(parents=True, exist_ok=True)
    _seed_if_empty(path)
    _root = path
    logger.info("文件根目录: %s", path)
    return path


def get_file_root() -> Path:
    if _root is None:
        return ensure_file_root()
    return _root


def reset_file_root_cache() -> None:
    global _root
    _root = None


def locate(
    raw: str,
    *,
    root: Optional[Path] = None,
    follow_final: bool = True,
    allow_missing: bool = False,
) -> Path:
    """把用户传入的路径收成监狱内的绝对路径。"""
    base = (root or get_file_root()).resolve()
    relative = _normalize(raw, base)
    if relative == "":
        return base

    current = base
    parts = relative.split("/")
    for index, part in enumerate(parts):
        if part in {"", ".", ".."} or len(part) > 255 or "/" in part or "\\" in part:
            raise_jail(raw, "traversal" if part == ".." else "illegal_char")
        is_last = index == len(parts) - 1
        nxt = current / part
        # 父目录已经在监狱内，单个文件名不能靠拼接逃出去。
        # 这里不能 resolve，否则指向外部的符号链接会在“只删除链接本身”之前被拒绝。
        if not _joined_inside(nxt, base):
            raise_jail(raw, "outside_jail")
        is_link = nxt.is_symlink()
        exists = is_link or nxt.exists()
        if not exists:
            if is_last and allow_missing:
                return nxt
            raise FileOpError("路径不存在", 404)
        if is_link:
            if is_last and not follow_final:
                return nxt
            try:
                resolved = nxt.resolve(strict=True)
            except OSError:
                raise_jail(raw, "outside_jail")
            if not _is_relative_to(resolved, base):
                raise_jail(raw, "outside_jail")
            if not is_last and not resolved.is_dir():
                raise FileOpError("路径不是目录", 400)
            current = resolved
            continue
        if not is_last and not nxt.is_dir():
            raise FileOpError("路径不是目录", 400)
        current = nxt
    return current


def display_path(path: Path, root: Optional[Path] = None) -> str:
    base = (root or get_file_root()).resolve()
    relative = path.resolve(strict=False).relative_to(base)
    text = relative.as_posix()
    return "" if text == "." else text


def _normalize(raw: str, root: Path) -> str:
    if not isinstance(raw, str):
        raise_jail(repr(raw), "illegal_char")
    if "\x00" in raw or "\\" in raw:
        raise_jail(raw, "illegal_char")
    if len(raw) > 4096:
        raise_jail(raw[:180], "illegal_char")
    text = re.sub(r"/+", "/", raw.strip())
    if text in {"", "."}:
        return ""
    posix = PurePosixPath(text)
    if ".." in posix.parts:
        raise_jail(text, "traversal")
    if posix.is_absolute():
        root_posix = PurePosixPath(root.as_posix())
        try:
            relative = posix.relative_to(root_posix)
        except ValueError:
            reason = "sensitive_path" if _is_sensitive(posix.as_posix()) else "outside_jail"
            raise_jail(text, reason)
        text = "" if str(relative) == "." else relative.as_posix()
        if text == "":
            return ""
        posix = PurePosixPath(text)
        if ".." in posix.parts:
            raise_jail(raw, "traversal")
    parts = [part for part in posix.parts if part != "."]
    if any(part == ".." for part in parts):
        raise_jail(raw, "traversal")
    return "/".join(parts)


def _is_sensitive(posix: str) -> bool:
    for prefix in _SENSITIVE:
        if posix == prefix or posix.startswith(prefix + "/"):
            return True
    return False


def _root_is_forbidden(path: Path) -> bool:
    posix = path.as_posix().rstrip("/") or "/"
    if posix == "/":
        return True
    return _is_sensitive(posix)


def inside_allowed(path: Path, root: Path, *, allow_root: bool = True) -> bool:
    """用绝对路径和 commonpath 判断目标是不是合法根目录里面的路径。

    目录名里的冒号、短端口和补零都不参与判断。指向根目录外的符号链接拒绝。
    """
    root_abs = os.path.abspath(root.as_posix())
    target_abs = os.path.abspath(path.as_posix())
    try:
        if os.path.commonpath([target_abs, root_abs]) != root_abs:
            return False
    except ValueError:
        return False
    if not allow_root and target_abs == root_abs:
        return False
    try:
        if path.is_symlink() or path.exists():
            resolved = os.path.realpath(target_abs)
            if os.path.commonpath([resolved, root_abs]) != root_abs:
                return False
            if not allow_root and resolved == root_abs:
                return False
    except (OSError, ValueError):
        return False
    return True


def _joined_inside(path: Path, root: Path) -> bool:
    return inside_allowed(path, root, allow_root=True)


def _is_relative_to(path: Path, root: Path) -> bool:
    return inside_allowed(path, root, allow_root=True)


def _seed_if_empty(root: Path) -> None:
    if any(root.iterdir()):
        return
    (root / "index.html").write_text(
        "<!doctype html>\n<html>\n  <body>\n    <h1>wwwroot</h1>\n  </body>\n</html>\n",
        encoding="utf-8",
    )
    (root / "app.js").write_text(
        "function hello() {\n  return '删库跑路快捷助手'\n}\n",
        encoding="utf-8",
    )
    docs = root / "docs"
    docs.mkdir()
    (docs / "readme.txt").write_text("这个目录是文件管理器的根，不能通过 ../ 跳出去。\n", encoding="utf-8")
    os.chmod(root / "index.html", 0o644)
    os.chmod(root / "app.js", 0o644)
    os.chmod(docs, 0o755)
