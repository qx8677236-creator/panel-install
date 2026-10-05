"""管理员账号与刷新令牌的本地存储。"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from app.auth.security import hash_password
from app.config import DATA_DIR

_lock = threading.Lock()
_admin: dict | None = None


def create_admin_if_needed() -> None:
    """没有管理员文件时创建 admin，并把随机密码打到控制台。

    密码只打印这一次，不写进日志文件，也不写进数据文件的明文。
    """
    global _admin
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "admin.json"
    if path.exists():
        return

    supplied = os.environ.get("PANEL_INIT_PASSWORD", "").strip()
    password = supplied or secrets.token_urlsafe(12)
    payload = {
        "username": "admin",
        "password_hash": hash_password(password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    _admin = payload
    if supplied:
        return
    # 没有外部指定密码时，只在这次初始化把随机密码打到控制台。不要改成 logger。
    print(
        "\n"
        "============================================================\n"
        "  删库跑路快捷助手 · 初始管理员账号已创建\n"
        "  用户名: admin\n"
        f"  密码:   {password}\n"
        "  该密码只在本次初始化时显示，请立即保存。\n"
        "============================================================\n",
        flush=True,
    )


def load_admin() -> dict:
    global _admin
    if _admin is not None:
        return _admin
    path = DATA_DIR / "admin.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    _admin = data
    return data


def update_password(password_hash: str) -> None:
    """只写入新的密码哈希，调用方负责校验旧密码。"""
    global _admin
    with _lock:
        admin = dict(load_admin())
        admin["password_hash"] = password_hash
        path = DATA_DIR / "admin.json"
        raw = json.dumps(admin, ensure_ascii=False, indent=2).encode("utf-8")
        temporary = path.with_suffix(".json.tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
        os.replace(temporary, path)
        _admin = admin


def remember_refresh(jti: str, username: str, exp: int) -> None:
    with _lock:
        data = _load_tokens()
        data[jti] = {"sub": username, "exp": exp}
        _save_tokens(data)


def rotate_refresh(old_jti: str, username: str, new_jti: str, new_exp: int) -> bool:
    """确认旧刷新令牌属于该用户，然后换成新的。旧令牌立即失效。"""
    with _lock:
        data = _load_tokens()
        row = data.get(old_jti)
        if not row or row.get("sub") != username or int(row.get("exp", 0)) <= int(time.time()):
            data.pop(old_jti, None)
            _save_tokens(data)
            return False
        data.pop(old_jti, None)
        data[new_jti] = {"sub": username, "exp": new_exp}
        _save_tokens(data)
        return True


def revoke_refresh(jti: str) -> None:
    with _lock:
        data = _load_tokens()
        data.pop(jti, None)
        _save_tokens(data)


def _token_path():
    return DATA_DIR / "refresh_tokens.json"


def _load_tokens() -> dict:
    path = _token_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    now = int(time.time())
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if int(value.get("exp", 0)) > now}


def _save_tokens(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _token_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
