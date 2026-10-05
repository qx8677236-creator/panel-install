"""只读取 ufw 状态。不接受参数，不修改规则。"""

from __future__ import annotations

import subprocess

_HELPER = "/usr/local/sbin/panel-ufw-status"
_ACTIONS = {"ALLOW", "DENY", "REJECT", "LIMIT"}


class FirewallError(Exception):
    def __init__(self, message: str, status: int = 500) -> None:
        self.message = message
        self.status = status


def read_status() -> dict:
    try:
        result = subprocess.run(
            ["sudo", "-n", _HELPER],
            capture_output=True,
            text=True,
            timeout=8,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise FirewallError("读取防火墙超时") from None
    except OSError as exc:
        raise FirewallError(str(exc) or "无法执行防火墙状态命令") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "无法读取防火墙").strip()
        raise FirewallError(detail[:500])
    text = result.stdout or ""
    status = "unknown"
    rules = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("status:"):
            status = stripped.split(":", 1)[1].strip()
            continue
        parts = stripped.split()
        if len(parts) >= 4 and parts[1] in _ACTIONS:
            rules.append({"to": parts[0], "action": parts[1], "direction": parts[2], "from": " ".join(parts[3:])})
    return {"tool": "ufw", "status": status, "rules": rules, "text": text, "writable": False}
