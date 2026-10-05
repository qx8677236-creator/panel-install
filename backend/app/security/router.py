"""SSH 安全只读接口，以及本机站点扫描。"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import current_user
from app.nginx.scan import scan_sites
from app.security.firewall import FirewallError, read_status
from app.security.ssh import read_config

ssh_router = APIRouter()
ssh_router.dependencies.append(Depends(current_user))
warning_router = APIRouter()
warning_router.dependencies.append(Depends(current_user))
firewall_router = APIRouter()
firewall_router.dependencies.append(Depends(current_user))


@ssh_router.get("/config")
async def config() -> dict:
    return await anyio.to_thread.run_sync(read_config)


@ssh_router.post("/scan")
async def scan() -> dict:
    return await anyio.to_thread.run_sync(read_config)


@warning_router.get("")
async def warning(domain: str = "") -> dict:
    return await anyio.to_thread.run_sync(lambda: scan_sites(domain))


@firewall_router.get("")
async def firewall() -> dict:
    try:
        return await anyio.to_thread.run_sync(read_status)
    except FirewallError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
