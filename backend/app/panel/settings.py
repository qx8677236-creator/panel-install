"""面板名称、终端开关和登录密码。不改监听端口。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.auth.security import hash_password, verify_password
from app.auth.store import load_admin, update_password
from app.config import settings as app_settings
from app.nginx.security import SiteError
from app.security.ssh import read_config

_TITLE = re.compile(r"^[\w\u4e00-\u9fff ·]{1,20}$")
_PASSWORD = re.compile(r"^[^\s\x00-\x1f]{8,72}$")


def _path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "panel" / "settings.json"


def _load() -> dict:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def terminal_config() -> dict:
    data = _load()
    return {
        "use_completion": bool(data.get("use_completion")),
        "terminal_theme": bool(data.get("terminal_theme")),
        "ai_shell": False,
        "ai_shell_analyze": False,
    }


def save_terminal(use_completion: bool, terminal_theme: bool) -> dict:
    data = _load()
    data["use_completion"] = bool(use_completion)
    data["terminal_theme"] = bool(terminal_theme)
    _save(data)
    return terminal_config()


def panel_settings() -> dict:
    data = _load()
    ssh = read_config().get("config") or {}
    admin = load_admin()
    return {
        "title": str(data.get("title") or "删库跑路快捷助手"),
        "username": admin.get("username") or "admin",
        "port": app_settings.port,
        "port_editable": False,
        "password": "yes" if str(ssh.get("passwordauthentication", "")).lower() == "yes" else "no",
        "pubkey": "yes" if str(ssh.get("pubkeyauthentication", "")).lower() == "yes" else "no",
        "rsa_auth": "yes" if str(ssh.get("pubkeyauthentication", "")).lower() == "yes" else "no",
        **terminal_config(),
    }


def save_title(title: str) -> dict:
    if not isinstance(title, str) or not _TITLE.fullmatch(title.strip()):
        raise SiteError("面板名称只能使用中文、字母、数字和空格")
    data = _load()
    data["title"] = title.strip()
    _save(data)
    return panel_settings()


def change_password(old_password: str, new_password: str) -> dict:
    admin = load_admin()
    if not verify_password(old_password, admin.get("password_hash") or ""):
        raise SiteError("当前密码不正确", 403)
    if not isinstance(new_password, str) or not _PASSWORD.fullmatch(new_password):
        raise SiteError("新密码需要 8 到 72 位，且不能包含空格")
    if new_password == old_password:
        raise SiteError("新密码不能和当前密码相同")
    update_password(hash_password(new_password))
    return {"changed": True}
