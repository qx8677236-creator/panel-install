"""备份任务接口。查询用 GET，创建、执行、开关和删除都用 POST。"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.deps import current_user
from app.backup import scheduler, store
from app.backup.store import BackupError

router = APIRouter()
router.dependencies.append(Depends(current_user))


class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    kind: str = Field(min_length=1, max_length=16)
    engine: str = ""
    target: str = ""
    cron: str = Field(min_length=1, max_length=80)
    keep: int = Field(ge=1, le=30)
    enabled: bool = True


class IdIn(BaseModel):
    id: int = Field(ge=1, le=100000)


class DeleteIn(BaseModel):
    id: int = Field(ge=1, le=100000)
    confirm: str = ""


class StatusIn(BaseModel):
    id: int = Field(ge=1, le=100000)
    enabled: bool


async def _run(func):
    try:
        return await anyio.to_thread.run_sync(func)
    except BackupError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@router.get("")
async def listing() -> dict:
    return await _run(store.list_public)


@router.get("/options")
async def options() -> dict:
    def load():
        sites, databases = store.catalogs()
        return {"sites": sites, "databases": databases, "paths": store.list_public()["paths"]}

    return await _run(load)


@router.post("/save")
async def save(body: TaskIn) -> dict:
    result = await _run(lambda: store.add_task(body.model_dump()))
    scheduler.sync_jobs(scheduler.get_scheduler() or scheduler.start_scheduler())
    return result


@router.post("/delete")
async def remove(body: DeleteIn) -> dict:
    if body.confirm != "确认":
        raise HTTPException(status_code=400, detail="请输入确认")
    result = await _run(lambda: store.delete_task(body.id))
    current = scheduler.get_scheduler()
    if current is not None:
        scheduler.sync_jobs(current)
    return result


@router.post("/toggle")
async def toggle(body: StatusIn) -> dict:
    result = await _run(lambda: store.set_status(body.id, body.enabled))
    current = scheduler.get_scheduler()
    if current is not None:
        scheduler.sync_jobs(current)
    return result


@router.post("/run")
async def run(body: IdIn) -> dict:
    def start():
        scheduler.enqueue(body.id)
        return {"started": True, "id": body.id}

    return await _run(start)


@router.get("/logs")
async def logs(id: int, limit: int = 100) -> dict:
    return await _run(lambda: store.read_logs(id, limit))
