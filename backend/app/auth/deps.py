"""需要登录的接口共用这个依赖。"""

from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import TokenError, decode_token
from app.auth.store import load_admin

_bearer = HTTPBearer(auto_error=False)


async def current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="未登录或令牌缺失")
    try:
        payload = decode_token(creds.credentials, "access")
    except TokenError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录") from None
    admin = load_admin()
    if payload["sub"] != admin["username"]:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return str(payload["sub"])
