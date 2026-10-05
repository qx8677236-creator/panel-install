"""给 HTTP 响应补安全响应头。WebSocket 原样放过。"""

from starlette.datastructures import MutableHeaders


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Content-Security-Policy"] = "frame-ancestors 'none'"
                headers["Cache-Control"] = "no-store"
                # 开发环境走 HTTP，不能下发 HSTS，否则浏览器会把 localhost 钉死成 HTTPS。
            await send(message)

        await self.app(scope, receive, send_wrapper)
