"""计划任务接口。字段名沿用官方 action 的 name、type、sType、sBody。"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.deps import current_user
from app.crontab import service
from app.nginx.security import SiteError

router = APIRouter()
router.dependencies.append(Depends(current_user))


class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    type: str = Field(min_length=1, max_length=16)
    where1: str = "1"
    hour: str = "0"
    minute: str = "0"
    sType: str = Field(min_length=1, max_length=16)
    sBody: str = ""
    sName: str = ""
    enabled: bool = True


class IdIn(BaseModel):
    id: int = Field(ge=1, le=100000)


class DeleteIn(BaseModel):
    id: int = Field(ge=1, le=100000)
    confirm: str = ""


class StatusIn(BaseModel):
    id: int = Field(ge=1, le=100000)
    enabled: bool


class ActionIn(BaseModel):
    action: str = Field(min_length=1, max_length=40)
    id: int = 0
    name: str = ""
    type: str = ""
    where1: str = "1"
    hour: str = "0"
    minute: str = "0"
    sType: str = ""
    sBody: str = ""
    sName: str = ""
    status: str = ""


async def _run(func):
    try:
        return await anyio.to_thread.run_sync(func)
    except SiteError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@router.get("")
async def listing() -> dict:
    return await _run(service.list_tasks)


@router.post("/save")
async def save(body: TaskIn) -> dict:
    return await _run(lambda: service.add_task(body.model_dump()))


@router.post("/delete")
async def remove(body: DeleteIn) -> dict:
    if body.confirm != "确认":
        raise HTTPException(status_code=400, detail="请输入确认")
    return await _run(lambda: service.delete_task(body.id))


@router.post("/toggle")
async def toggle(body: StatusIn) -> dict:
    return await _run(lambda: service.set_status(body.id, body.enabled))


@router.post("/run")
async def run(body: IdIn) -> dict:
    return await _run(lambda: service.start_task(body.id))


@router.get("/logs")
async def logs(id: int, limit: int = 200) -> dict:
    return await _run(lambda: service.read_log(id, limit))


@router.post("/action")
async def action(body: ActionIn) -> dict:
    payload = body.model_dump()
    name = body.action
    if name == "GetCrontab":
        return await _run(service.list_tasks)
    if name == "AddCrontab":
        return await _run(lambda: service.add_task(payload))
    if name == "DelCrontab":
        return await _run(lambda: service.delete_task(body.id))
    if name == "GetLogs":
        return await _run(lambda: service.read_log(body.id))
    if name == "set_cron_status":
        return await _run(lambda: service.set_status(body.id, body.status == "1"))
    if name == "set_execute_script":
        return await _run(lambda: service.start_task(body.id))
    raise HTTPException(status_code=400, detail="不支持的计划任务操作")
