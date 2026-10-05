"""只读取 sshd 配置并给出检查结果，不修改、不重启 SSH。"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_KEYS = {
    "port",
    "permitrootlogin",
    "passwordauthentication",
    "pubkeyauthentication",
    "permitemptypasswords",
    "challengeresponseauthentication",
    "kbdinteractiveauthentication",
    "x11forwarding",
    "maxauthtries",
}
_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9]+)\s+(\S+)")
_INCLUDE = re.compile(r"^Include\s+(\S+)")


def read_config() -> dict:
    values, notes = _from_helper()
    if values is None:
        values, notes = _collect(Path("/etc/ssh/sshd_config"), set())
    findings = _findings(values, notes)
    shown = {key: values.get(key, "未设置") for key in sorted(_KEYS)}
    return {"config": shown, "items": findings, "writable": False}


def _from_helper():
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/local/sbin/panel-ssh-audit"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, []
    if result.returncode != 0 or not result.stdout.strip().startswith("{"):
        return None, []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, []
    values = data.get("config") if isinstance(data.get("config"), dict) else {}
    notes = data.get("notes") if isinstance(data.get("notes"), list) else []
    return {str(key): str(value) for key, value in values.items()}, [str(item) for item in notes]


def _collect(path: Path, seen: set[str]) -> tuple[dict, list[str]]:
    values: dict[str, str] = {}
    notes: list[str] = []
    resolved = str(path)
    if resolved in seen:
        return values, notes
    seen.add(resolved)
    if not path.is_file() or path.is_symlink() or not str(path.resolve()).startswith("/etc/ssh/"):
        notes.append(f"无法读取 {path.name}")
        return values, notes
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        notes.append(f"无法读取 {path.name}")
        return values, notes
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        include = _INCLUDE.match(line)
        if include:
            child_values, child_notes = _include(include.group(1), seen)
            values.update(child_values)
            notes.extend(child_notes)
            continue
        matched = _LINE.match(line)
        if not matched:
            continue
        key = matched.group(1).lower()
        if key in _KEYS:
            values[key] = matched.group(2)
    return values, notes


def _include(pattern: str, seen: set[str]) -> tuple[dict, list[str]]:
    values: dict[str, str] = {}
    notes: list[str] = []
    if ".." in pattern or not pattern.startswith("/etc/ssh/"):
        return values, notes
    folder = Path(pattern).parent
    name = Path(pattern).name
    if not folder.is_dir():
        return values, notes
    for child in sorted(folder.glob(name)):
        child_values, child_notes = _collect(child, seen)
        values.update(child_values)
        notes.extend(child_notes)
    return values, notes


def _findings(values: dict, notes: list[str]) -> list[dict]:
    items = [{"level": "low", "item": note, "message": "这个文件没有参与检查"} for note in notes]
    if values.get("permitrootlogin", "").lower() == "yes":
        items.append({"level": "high", "item": "PermitRootLogin", "message": "允许 root 直接登录"})
    if values.get("permitemptypasswords", "").lower() == "yes":
        items.append({"level": "high", "item": "PermitEmptyPasswords", "message": "允许空密码登录"})
    if values.get("passwordauthentication", "").lower() == "yes":
        items.append({"level": "medium", "item": "PasswordAuthentication", "message": "允许使用密码登录"})
    if values.get("pubkeyauthentication", "").lower() == "no":
        items.append({"level": "high", "item": "PubkeyAuthentication", "message": "关闭了密钥登录"})
    if not items:
        items.append({"level": "ok", "item": "sshd", "message": "没有发现需要处理的 SSH 配置"})
    return items
