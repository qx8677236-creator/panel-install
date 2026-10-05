"""监控 WebSocket。

令牌放在连接建立后的第一条消息里，不放进 URL，避免出现在访问日志中。
连接只在访问令牌的有效期内保持，到期后关闭，由前端刷新令牌再重连。
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.security import TokenError, decode_token
from app.auth.store import load_admin
from app.config import settings
from app.monitor.collector import collector

router = APIRouter()


@router.websocket("/ws/metrics")
async def metrics_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=8)
        if len(raw) > 8192:
            raise ValueError("认证消息过长")
        message = json.loads(raw)
        token = message.get("token") if isinstance(message, dict) else None
        payload = decode_token(token, "access")
        if payload["sub"] != load_admin()["username"]:
            raise TokenError("用户不匹配")
    except (asyncio.TimeoutError, json.JSONDecodeError, TokenError, TypeError, ValueError):
        await websocket.close(code=4401)
        return

    deadline = time.monotonic() + settings.access_token_minutes * 60
    last_ts = None
    try:
        while time.monotonic() < deadline:
            snap = collector.latest()
            if snap is not None and snap["ts"] != last_ts:
                await websocket.send_json(snap)
                last_ts = snap["ts"]
            await asyncio.sleep(0.3)
        await websocket.close(code=4401)
    except WebSocketDisconnect:
        return
