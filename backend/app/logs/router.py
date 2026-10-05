"""日志查询。写操作只有清空，而且必须再次输入管理员密码。"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.deps import current_user
from app.auth.security import TokenError, decode_token, verify_password
from app.auth.store import load_admin
from app.config import settings
from app.logs import files, store

router = APIRouter()
router.dependencies.append(Depends(current_user))


class ClearIn(BaseModel):
    section: str = Field(pattern=r"^(operation|login)$")
    password: str = Field(min_length=1, max_length=72)
    confirm: str = Field(min_length=1, max_length=20)


def _run_error(exc: Exception) -> HTTPException:
    if isinstance(exc, store.LogError):
        return HTTPException(status_code=exc.status, detail=exc.message)
    raise exc


async def _run(func):
    try:
        return await anyio.to_thread.run_sync(func)
    except store.LogError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@router.get("/operations")
async def operations(page: int = 1, page_size: int = 10, q: str = "", log_type: str = "") -> dict:
    return await _run(lambda: store.query_logs("operation", page, page_size, q, log_type))


@router.get("/logins")
async def logins(page: int = 1, page_size: int = 10, q: str = "") -> dict:
    return await _run(lambda: store.query_logs("login", page, page_size, q))


@router.get("/audit")
async def audit(page: int = 1, page_size: int = 10, q: str = "") -> dict:
    return await _run(lambda: store.query_logs("audit", page, page_size, q))


@router.get("/runtime")
async def runtime(limit: int = 200, q: str = "") -> dict:
    limit = _tail_limit(limit)
    return await _run(lambda: files.runtime_lines(limit, q))


@router.get("/tasks")
async def tasks(limit: int = 200, q: str = "") -> dict:
    limit = _tail_limit(limit)
    return await _run(lambda: files.task_lines(limit, q))


@router.get("/sites")
async def sites(file: str = "access.log", limit: int = 200, q: str = "") -> dict:
    limit = _tail_limit(limit)
    return await _run(lambda: files.site_lines(file, limit, q))


@router.get("/site-files")
async def site_files() -> dict:
    return await _run(files.site_files)


@router.get("/ssh")
async def ssh(page: int = 1, page_size: int = 10, q: str = "") -> dict:
    return await _run(lambda: files.ssh_page(page, page_size, q))


@router.get("/software")
async def software(name: str = "nginx", limit: int = 200, q: str = "") -> dict:
    limit = _tail_limit(limit)
    return await _run(lambda: files.software_lines(name, limit, q))


@router.get("/export")
async def export(section: str = "operation", form: str = "csv", q: str = "", log_type: str = ""):
    if section not in {"operation", "login", "audit"} or form not in {"csv", "txt"}:
        raise HTTPException(status_code=400, detail="导出参数无效")
    rows = await _run(lambda: store.export_rows(section, q, log_type))
    filename = f"panel-logs.{form}"
    if form == "txt":
        body = "\n".join(
            f"{row['created_at']}\t{row['operator']}\t{row['type']}\t{row['ip']}\t{row['details']}" for row in rows
        )
        data = body.encode("utf-8")
        media = "text/plain; charset=utf-8"
    else:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["时间", "用户", "操作类型", "IP", "详情"])
        for row in rows:
            writer.writerow([row["created_at"], row["operator"], row["type"], row["ip"], row["details"]])
        data = buffer.getvalue().encode("utf-8")
        media = "text/csv; charset=utf-8"
    return StreamingResponse(
        iter([data]),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/clear")
async def clear(body: ClearIn, request: Request, username: str = Depends(current_user)) -> dict:
    if body.confirm != "清空日志":
        raise HTTPException(status_code=400, detail="请输入清空日志以确认")
    admin = load_admin()
    if username != admin["username"] or not verify_password(body.password, admin["password_hash"]):
        raise HTTPException(status_code=403, detail="密码错误，没有清空")
    deleted = await _run(lambda: store.clear_section(body.section))
    host = request.client.host if request.client else "unknown"
    label = "操作日志" if body.section == "operation" else "登录日志"
    await _run(lambda: store.write_log(username, "日志管理", f"清空了{label} {deleted} 条", host))
    return {"deleted": deleted}


def _tail_limit(limit: int) -> int:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="最多读取最后 500 行")
    return limit


ws_router = APIRouter()


@ws_router.websocket("/ws/logs")
async def logs_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=8)
        if len(raw) > 8192:
            raise ValueError("认证消息过长")
        message = json.loads(raw)
        token = message.get("token") if isinstance(message, dict) else None
        source = message.get("source") if isinstance(message, dict) else ""
        payload = decode_token(token, "access")
        if payload["sub"] != load_admin()["username"]:
            raise TokenError("用户不匹配")
        path = files.stream_path(str(source or ""))
    except (asyncio.TimeoutError, json.JSONDecodeError, TokenError, TypeError, ValueError, store.LogError):
        await websocket.close(code=4401)
        return

    deadline = time.monotonic() + settings.access_token_minutes * 60
    try:
        lines = await anyio.to_thread.run_sync(lambda: files.tail_lines(path, 100))
        await websocket.send_json({"type": "lines", "lines": lines})
        offset = path.stat().st_size if path.is_file() else 0
        while time.monotonic() < deadline:
            fresh, offset = await anyio.to_thread.run_sync(lambda: files.read_after(path, offset))
            if fresh:
                await websocket.send_json({"type": "append", "lines": fresh})
            await asyncio.sleep(1)
        await websocket.close(code=4401)
    except WebSocketDisconnect:
        return
