"""授权 IP 名单。名单为空时不限制；有名单时，其他地址不能打开面板。"""

from __future__ import annotations

from app.panel.settings import ip_permitted, normalize_ip
from app.nginx.security import SiteError


def _client_host(scope) -> str:
    client = scope.get("client")
    if not client:
        return ""
    host = client[0] if isinstance(client, (list, tuple)) else ""
    return str(host or "")


class IpAllowMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        host = _client_host(scope)
        if ip_permitted(host):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send(
                {
                    "type": "websocket.http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json; charset=utf-8")],
                }
            )
            await send(
                {
                    "type": "websocket.http.response.body",
                    "body": '{"detail":"当前 IP 不在授权列表里"}'.encode("utf-8"),
                }
            )
            return
        body = '{"detail":"当前 IP 不在授权列表里"}'.encode("utf-8")
        path = scope.get("path") or ""
        if not path.startswith("/api"):
            try:
                shown = normalize_ip(host)
            except SiteError:
                shown = "未知"
            body = f"当前 IP {shown} 不在面板授权列表里".encode("utf-8")
            content_type = b"text/plain; charset=utf-8"
        else:
            content_type = b"application/json; charset=utf-8"
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", content_type),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
