"""未捕获异常只向浏览器返回固定 JSON，堆栈留在服务端日志。"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("panel")


async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    path = ""
    url = getattr(request, "url", None)
    if url is not None:
        path = str(getattr(url, "path", "") or "")
    logger.exception("未处理的异常 %s", path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})
