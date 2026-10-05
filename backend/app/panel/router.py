"""面板设置接口。端口只展示，不提供修改。"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException
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


async def _run(func):
    try:
        return await anyio.to_thread.run_sync(func)
    except SiteError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@router.get("/settings")
async def get_settings() -> dict:
    return await _run(settings.panel_settings)


@router.post("/title")
async def save_title(body: TitleIn) -> dict:
    return await _run(lambda: settings.save_title(body.title))


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
