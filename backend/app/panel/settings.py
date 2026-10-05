"""面板名称、终端开关、登录密码和授权 IP。不改监听端口。"""

from __future__ import annotations

import ipaddress
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


def normalize_ip(value: str) -> str:
    """把地址收成唯一写法。IPv4 映射的 IPv6 记成 IPv4。"""
    if not isinstance(value, str):
        raise SiteError("IP 地址无效")
    text = value.strip()
    if not text or len(text) > 64 or "/" in text:
        raise SiteError("IP 地址无效")
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        raise SiteError(f"IP 地址无效：{text}") from None
    if getattr(parsed, "ipv4_mapped", None) is not None:
        parsed = parsed.ipv4_mapped
    if parsed.is_unspecified:
        raise SiteError("不能使用 0.0.0.0 或 :: 作为授权 IP")
    return str(parsed)


def parse_allow_ips(raw: str) -> list[str]:
    """逗号分隔。空名单表示不限制。"""
    if raw is None:
        return []
    if not isinstance(raw, str):
        raise SiteError("授权 IP 无效")
    if len(raw) > 2000:
        raise SiteError("授权 IP 过多")
    pieces = re.split(r"[,，;\s]+", raw.strip())
    found = []
    seen = set()
    for piece in pieces:
        if not piece:
            continue
        ip = normalize_ip(piece)
        if ip in seen:
            continue
        seen.add(ip)
        found.append(ip)
    if len(found) > 50:
        raise SiteError("授权 IP 最多 50 个")
    return found


def stored_allow_ips() -> list[str]:
    raw = _load().get("allow_ips") or []
    if isinstance(raw, str):
        try:
            return parse_allow_ips(raw)
        except SiteError:
            return []
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw:
        if not isinstance(item, str):
            continue
        try:
            ip = normalize_ip(item)
        except SiteError:
            continue
        if ip not in cleaned:
            cleaned.append(ip)
    return cleaned


def ip_permitted(client_ip: str, allowed: list[str] | None = None) -> bool:
    """名单为空时放行。名单有内容时，只放行写明的地址。"""
    ips = stored_allow_ips() if allowed is None else allowed
    if not ips:
        return True
    try:
        current = normalize_ip(client_ip or "")
    except SiteError:
        return False
    return current in ips


def save_allow_ips(raw: str, client_ip: str) -> dict:
    ips = parse_allow_ips(raw or "")
    if ips and not ip_permitted(client_ip, ips):
        raise SiteError("当前访问 IP 不在授权列表里，不能保存，否则这台电脑会进不了面板")
    data = _load()
    data["allow_ips"] = ips
    _save(data)
    return panel_settings()


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
        "allow_ips": ",".join(stored_allow_ips()),
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
