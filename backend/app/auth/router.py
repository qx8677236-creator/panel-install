"""登录、刷新、退出。"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.deps import current_user
from app.auth.security import (
    TokenError,
    create_token,
    decode_token,
    dummy_password_hash,
    verify_password,
)
from app.auth.store import load_admin, remember_refresh, revoke_refresh, rotate_refresh
from app.config import settings
from app.logs.store import write_log

router = APIRouter()
_guard_lock = threading.Lock()
# key -> (失败次数, 锁定截止时间戳)
_failures: dict[str, tuple[int, float]] = {}


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=72)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str


def _client_key(request: Request, username: str) -> str:
    # 不信任 X-Forwarded-For，避免调用方自己伪造来源绕过锁定。
    host = request.client.host if request.client else "unknown"
    return f"{host}:{username}"


def _ensure_not_locked(key: str) -> None:
    with _guard_lock:
        count, until = _failures.get(key, (0, 0.0))
        if count >= settings.max_login_failures and time.time() < until:
            raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试")
        if until and time.time() >= until:
            _failures.pop(key, None)


def _mark_failure(key: str) -> None:
    with _guard_lock:
        count, until = _failures.get(key, (0, 0.0))
        if until and time.time() >= until:
            count = 0
        count += 1
        lock_until = time.time() + settings.lockout_seconds if count >= settings.max_login_failures else 0.0
        _failures[key] = (count, lock_until)


def _clear_failure(key: str) -> None:
    with _guard_lock:
        _failures.pop(key, None)


def _issue_pair(username: str) -> TokenOut:
    access, _, _ = create_token(username, "access", timedelta(minutes=settings.access_token_minutes))
    refresh, refresh_jti, refresh_exp = create_token(
        username, "refresh", timedelta(days=settings.refresh_token_days)
    )
    remember_refresh(refresh_jti, username, refresh_exp)
    return TokenOut(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
        username=username,
    )


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request) -> TokenOut:
    key = _client_key(request, body.username)
    _ensure_not_locked(key)
    admin = load_admin()
    known = body.username == admin["username"]
    password_hash = admin["password_hash"] if known else dummy_password_hash()
    # 先做哈希比对，再看用户名是否存在，避免过早返回。
    ok = verify_password(body.password, password_hash) and known
    host = request.client.host if request.client else "unknown"
    if not ok:
        _mark_failure(key)
        write_log(body.username, "用户登录", "登录失败", host)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _clear_failure(key)
    write_log(admin["username"], "用户登录", "登录成功", host)
    return _issue_pair(admin["username"])


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn) -> TokenOut:
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except TokenError:
        raise HTTPException(status_code=401, detail="刷新令牌无效") from None
    username = str(payload["sub"])
    if username != load_admin()["username"]:
        raise HTTPException(status_code=401, detail="刷新令牌无效")

    access, _, _ = create_token(username, "access", timedelta(minutes=settings.access_token_minutes))
    new_refresh, new_jti, new_exp = create_token(
        username, "refresh", timedelta(days=settings.refresh_token_days)
    )
    # 轮换：旧的刷新令牌用过即废，降低泄漏后的可用窗口。
    if not rotate_refresh(str(payload["jti"]), username, new_jti, new_exp):
        raise HTTPException(status_code=401, detail="刷新令牌无效")
    return TokenOut(
        access_token=access,
        refresh_token=new_refresh,
        expires_in=settings.access_token_minutes * 60,
        username=username,
    )


@router.post("/logout")
def logout(body: RefreshIn, request: Request) -> dict:
    host = request.client.host if request.client else "unknown"
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except TokenError:
        return {"ok": True}
    revoke_refresh(str(payload["jti"]))
    write_log(str(payload.get("sub") or ""), "用户登出", "退出面板", host)
    return {"ok": True}


@router.get("/me")
def me(username: str = Depends(current_user)) -> dict:
    return {"username": username}
