"""面板设置接口。端口只展示，不提供修改。"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.deps import current_user
from app.nginx.security import SiteError
from app.panel import settings

router = APIRouter()
router.dependencies.append(Depends(current_user))
xterm_router = APIRouter()
xterm_router.dependencies.append(Depends(current_user))


class TitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=20)


class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)


class TerminalIn(BaseModel):
    use_completion: bool = False
    terminal_theme: bool = False
    ai_shell: bool = False


class AllowIpsIn(BaseModel):
    allow_ips: str = Field(default="", max_length=2000)


def _client_ip(request: Request) -> str:
    if request.client is None:
        return ""
    return request.client.host or ""


def _with_client(data: dict, request: Request) -> dict:
    shown = dict(data)
    host = _client_ip(request)
    try:
        shown["client_ip"] = settings.normalize_ip(host) if host else ""
    except SiteError:
        shown["client_ip"] = ""
    return shown


async def _run(func):
    try:
        return await anyio.to_thread.run_sync(func)
    except SiteError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    data = await _run(settings.panel_settings)
    return _with_client(data, request)


@router.post("/title")
async def save_title(body: TitleIn) -> dict:
    return await _run(lambda: settings.save_title(body.title))


@router.post("/allow-ips")
async def save_allow_ips(body: AllowIpsIn, request: Request) -> dict:
    data = await _run(lambda: settings.save_allow_ips(body.allow_ips, _client_ip(request)))
    return _with_client(data, request)


@router.post("/password")
async def password(body: PasswordIn) -> dict:
    return await _run(lambda: settings.change_password(body.old_password, body.new_password))


@xterm_router.get("/config")
async def xterm_config() -> dict:
    return await _run(settings.terminal_config)


@xterm_router.post("/config")
async def save_xterm(body: TerminalIn) -> dict:
    if body.ai_shell:
        raise HTTPException(status_code=400, detail="未接入 AI Shell，也不会从外部下载插件")
    return await _run(lambda: settings.save_terminal(body.use_completion, body.terminal_theme))
