"""监控数据的 HTTP 查询。实时推送走 WebSocket。"""

from fastapi import APIRouter, Depends

from app.auth.deps import current_user
from app.monitor.collector import collector

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("/latest")
def latest() -> dict:
    return {"point": collector.latest()}


@router.get("/history")
def history() -> dict:
    return {"points": collector.history()}
