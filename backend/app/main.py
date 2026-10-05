"""应用入口：挂上认证、监控、WebSocket 和安全中间件。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.auth.router import router as auth_router
from app.auth.security import ensure_jwt_secret
from app.errors import unhandled_exception
from app.vault import ensure_secret_key
from app.auth.store import create_admin_if_needed
from app.config import settings
from app.files.router import router as files_router
from app.files.security import ensure_file_root
from app.middleware.security import SecurityHeadersMiddleware
from app.monitor.collector import metrics_loop
from app.monitor.router import router as monitor_router
from app.nginx.router import alias_router as site_alias_router
from app.nginx.router import router as nginx_router
from app.databases.router import router as database_router
from app.databases.manage import router as database_manage_router
from app.logs.files import setup_runtime_log
from app.logs.middleware import OperationLogMiddleware
from app.logs.router import router as log_router
from app.logs.router import ws_router as log_ws_router
from app.logs.store import connect as connect_logs
from app.crontab.router import router as crontab_router
from app.backup.router import router as backup_router
from app.backup.scheduler import start_scheduler, stop_scheduler
from app.databases.accounts import router as database_accounts_router
from app.security.router import firewall_router, ssh_router, warning_router
from app.panel.router import router as panel_router
from app.panel.router import xterm_router
from app.panel.terminal import router as terminal_router
from app.nginx.service import ensure_nginx_layout
from app.websocket.metrics import router as ws_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("panel")


def _optional_startup(step: str, action) -> None:
    """目录、Nginx 或备份调度失败时只记日志，不能挡住登录和健康检查。"""
    try:
        action()
    except Exception:
        logger.exception("%s失败，已跳过，登录接口继续启动", step)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_jwt_secret()
    ensure_secret_key()
    create_admin_if_needed()
    _optional_startup("文件根目录", ensure_file_root)
    _optional_startup("Nginx 布局", ensure_nginx_layout)
    setup_runtime_log()
    connect_logs().close()
    task = asyncio.create_task(metrics_loop())
    _optional_startup("备份调度器", start_scheduler)
    yield
    try:
        stop_scheduler()
    except Exception:
        logger.exception("备份调度器停止失败")
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(
    title=settings.app_name,
    description="安全底座、系统监控、文件管理与 Nginx 站点",
    version="0.3.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_exception_handler(Exception, unhandled_exception)

# 后添加的中间件在最外层。CORS 放最外，预检失败时也能带上来源头。
app.add_middleware(OperationLogMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["认证"])
app.include_router(monitor_router, prefix="/api/monitor", tags=["监控"])
app.include_router(files_router, prefix="/api/files", tags=["文件"])
app.include_router(nginx_router, prefix="/api/sites", tags=["站点"])
app.include_router(site_alias_router, prefix="/api/site", tags=["站点"])
app.include_router(database_router, prefix="/api/databases", tags=["数据库"])
app.include_router(database_manage_router, prefix="/api/database", tags=["数据库安装"])
app.include_router(log_router, prefix="/api/logs", tags=["日志"])
app.include_router(crontab_router, prefix="/api/crontab", tags=["计划任务"])
app.include_router(backup_router, prefix="/api/backup", tags=["定时备份"])
app.include_router(database_accounts_router, prefix="/api/database", tags=["数据库账号"])
app.include_router(ssh_router, prefix="/api/ssh_security", tags=["SSH安全"])
app.include_router(warning_router, prefix="/api/warning", tags=["安全扫描"])
app.include_router(firewall_router, prefix="/api/firewall", tags=["防火墙"])
app.include_router(panel_router, prefix="/api/panel", tags=["面板设置"])
app.include_router(xterm_router, prefix="/api/xterm", tags=["终端"])
app.include_router(terminal_router)
app.include_router(log_ws_router)
app.include_router(ws_router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "name": settings.app_name}


def _frontend_file(relative: str) -> Path | None:
    """只返回构建目录里的真实文件，避免路径跳出 dist。"""
    root = _FRONTEND_DIST.resolve()
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    if candidate.is_file():
        return candidate
    return None


def _mount_frontend() -> None:
    """有前端构建产物时，由同一个端口提供页面。没有 dist 时保持开发模式。"""
    index = _FRONTEND_DIST / "index.html"
    if not index.is_file():
        return

    @app.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_asset(full_path: str) -> FileResponse:
        found = _frontend_file(full_path)
        if found is not None:
            return FileResponse(found)
        return FileResponse(index)


# backend/app/main.py 的上两级是仓库根目录。
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_mount_frontend()
