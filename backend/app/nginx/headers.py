"""站点安全响应头。值只允许白名单，避免把输入写进 Nginx 配置。"""

from __future__ import annotations

from app.nginx.security import SiteError

_FRAMES = {"", "SAMEORIGIN", "DENY"}
_REFERRERS = {"", "no-referrer", "same-origin", "strict-origin-when-cross-origin"}


def normalize_security_headers(payload) -> dict:
    if not isinstance(payload, dict):
        raise SiteError("安全响应头参数无效")
    frame = str(payload.get("x_frame_options") or "")
    referrer = str(payload.get("referrer") or "")
    if frame not in _FRAMES or referrer not in _REFERRERS:
        raise SiteError("安全响应头包含不支持的值")
    return {
        "enabled": bool(payload.get("enabled")),
        "x_frame_options": frame,
        "nosniff": bool(payload.get("nosniff")),
        "xss": bool(payload.get("xss")),
        "referrer": referrer,
        "hsts": bool(payload.get("hsts")),
    }


def present_headers(payload) -> dict:
    try:
        return normalize_security_headers(payload or {"enabled": False})
    except SiteError:
        return normalize_security_headers({"enabled": False})


def security_header_lines(payload, https: bool = False) -> list[str]:
    if not isinstance(payload, dict) or not payload.get("enabled"):
        return []
    item = normalize_security_headers(payload)
    lines = []
    if item["x_frame_options"]:
        lines.append(f'add_header X-Frame-Options "{item["x_frame_options"]}" always;')
    if item["nosniff"]:
        lines.append('add_header X-Content-Type-Options "nosniff" always;')
    if item["xss"]:
        lines.append('add_header X-XSS-Protection "1; mode=block" always;')
    if item["referrer"]:
        lines.append(f'add_header Referrer-Policy "{item["referrer"]}" always;')
    if item["hsts"] and https:
        lines.append('add_header Strict-Transport-Security "max-age=31536000" always;')
    return lines
