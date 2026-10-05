"""敏感写操作完成后记一条面板日志。不读取请求体，避免把密码写进日志。"""

from __future__ import annotations

from starlette.datastructures import Headers

from app.auth.security import TokenError, decode_token
from app.logs.store import write_log

ACTIONS = {
    "/api/files/mkdir": ("文件管理", "新建目录"),
    "/api/files/create": ("文件管理", "新建文件"),
    "/api/files/delete": ("文件管理", "删除文件"),
    "/api/files/rename": ("文件管理", "重命名"),
    "/api/files/chmod": ("文件管理", "修改权限"),
    "/api/files/content": ("文件管理", "保存文件"),
    "/api/files/compress": ("文件管理", "压缩"),
    "/api/files/extract": ("文件管理", "解压"),
    "/api/files/upload/complete": ("文件管理", "上传文件"),
    "/api/sites/create": ("网站管理", "新建站点"),
    "/api/sites/toggle": ("网站管理", "启停站点"),
    "/api/sites/rewrite": ("网站管理", "修改伪静态"),
    "/api/sites/config": ("网站管理", "修改站点配置"),
    "/api/sites/save-proxy": ("网站管理", "修改反向代理"),
    "/api/site/save-proxy": ("网站管理", "修改反向代理"),
    "/api/sites/domains": ("网站管理", "修改域名"),
    "/api/sites/ssl": ("网站管理", "修改证书"),
    "/api/databases/create": ("数据库", "新建数据库"),
    "/api/databases/delete": ("数据库", "删除数据库"),
    "/api/databases/password": ("数据库", "修改数据库密码"),
    "/api/databases/root-password": ("数据库", "修改 root 密码"),
    "/api/databases/backup": ("数据库", "备份数据库"),
    "/api/databases/restore": ("数据库", "还原数据库"),
    "/api/databases/remote": ("数据库", "保存远程数据库"),
    "/api/database/install": ("数据库", "安装数据库"),
    "/api/database/start": ("数据库", "启动数据库"),
    "/api/database/stop": ("数据库", "停止数据库"),
    "/api/database/sqlserver/create": ("数据库", "新建 SQL Server 数据库"),
    "/api/database/sqlserver/delete": ("数据库", "删除 SQL Server 数据库"),
    "/api/database/mongodb/create": ("数据库", "新建 MongoDB 数据库"),
    "/api/database/mongodb/delete": ("数据库", "删除 MongoDB 数据库"),
    "/api/database/redis/flush": ("数据库", "清空 Redis"),
    "/api/database/redis/password": ("数据库", "修改 Redis 密码"),
    "/api/database/pgsql/create": ("数据库", "新建 PostgreSQL 数据库"),
    "/api/database/pgsql/delete": ("数据库", "删除 PostgreSQL 数据库"),
    "/api/database/pgsql/password": ("数据库", "修改 PostgreSQL 密码"),
    "/api/sites/security-headers": ("网站管理", "修改安全响应头"),
    "/api/sites/acme/record": ("网站管理", "记录证书申请"),
    "/api/sites/acme/deploy": ("网站管理", "部署已有证书"),
    "/api/crontab/save": ("计划任务", "添加计划任务"),
    "/api/crontab/delete": ("计划任务", "删除计划任务"),
    "/api/database/mysql/users": ("数据库", "添加 MySQL 用户"),
    "/api/database/mysql/users/delete": ("数据库", "删除 MySQL 用户"),
    "/api/database/redis/config": ("数据库", "修改 Redis 配置"),
    "/api/database/mongodb/users": ("数据库", "添加 MongoDB 用户"),
    "/api/panel/password": ("面板设置", "修改面板密码"),
    "/api/panel/title": ("面板设置", "修改面板名称"),
    "/api/xterm/config": ("终端", "修改终端设置"),
}
SAFE_QUERY = {"path", "name", "engine", "domain", "file"}


class OperationLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        action = ACTIONS.get(path)
        status = 0
        body = bytearray()

        async def wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            elif message["type"] == "http.response.body" and status >= 400 and len(body) < 8000:
                chunk = message.get("body") or b""
                body.extend(chunk[: 8000 - len(body)])
            await send(message)

        try:
            await self.app(scope, receive, wrapper)
        finally:
            if action is None:
                return
            try:
                kind, detail = action
                if status >= 400:
                    detail = f"{detail}失败"
                    reason = _failure_text(bytes(body)) if path.startswith("/api/site") else ""
                    if reason:
                        detail = f"{detail}：{reason}"
                extra = _safe_query(scope.get("query_string") or b"")
                if extra:
                    detail = f"{detail} {extra}"
                write_log(_user(scope), kind, detail, _ip(scope), limit=2000 if status >= 400 else 320)
            except Exception:
                return


def _user(scope) -> str:
    header = Headers(scope=scope).get("authorization") or ""
    if not header.startswith("Bearer "):
        return "未知"
    try:
        payload = decode_token(header[7:].strip(), "access")
    except TokenError:
        return "未知"
    return str(payload.get("sub") or "未知")


def _ip(scope) -> str:
    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0])
    return "unknown"


def _safe_query(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    kept = []
    for part in text.split("&"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key in SAFE_QUERY and value:
            kept.append(f"{key}={value[:80]}")
    return " ".join(kept)[:80]

def _failure_text(raw: bytes) -> str:
    import json

    if not raw:
        return ""
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    parts = []
    if isinstance(detail, str):
        parts.append(detail)
    elif isinstance(detail, dict):
        message = str(detail.get("message") or "").strip()
        log = str(detail.get("log") or "").strip()
        if message:
            parts.append(message)
        if log and log not in message:
            parts.append(log)
    elif isinstance(detail, list):
        parts.append("请求参数无效")
    text = " ".join(part for part in parts if part)
    if "PRIVATE KEY" in text or "BEGIN " in text:
        return ""
    return " ".join(text.split())[:1800]
