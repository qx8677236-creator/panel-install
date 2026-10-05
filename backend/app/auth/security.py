"""密码哈希与 JWT。密钥只落在本机 data 目录。"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import DATA_DIR

ALGORITHM = "HS256"
_secret: str | None = None
# 用户不存在时也做一次比对，缩小响应时间差。
_DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt(rounds=12)).decode("ascii")


class TokenError(Exception):
    """令牌缺失、伪造或类型不对。"""


def dummy_password_hash() -> str:
    return _DUMMY_HASH


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def ensure_jwt_secret() -> str:
    """首次启动时生成密钥文件。已存在则直接读取，避免重启后全部掉线。"""
    global _secret
    if _secret:
        return _secret

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "jwt_secret"
    if path.exists():
        _secret = path.read_text(encoding="utf-8").strip()
        return _secret

    secret = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        _secret = path.read_text(encoding="utf-8").strip()
        return _secret
    try:
        os.write(fd, secret.encode("ascii"))
    finally:
        os.close(fd)
    _secret = secret
    return secret


def create_token(username: str, token_type: str, expires_delta: timedelta) -> tuple[str, str, int]:
    now = datetime.now(timezone.utc)
    exp = now + expires_delta
    jti = secrets.token_hex(16)
    payload = {
        "sub": username,
        "type": token_type,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": exp,
    }
    token = jwt.encode(payload, ensure_jwt_secret(), algorithm=ALGORITHM)
    return token, jti, int(exp.timestamp())


def decode_token(token: str, expected_type: str) -> dict:
    if not isinstance(token, str) or not token:
        raise TokenError("缺少令牌")
    try:
        payload = jwt.decode(token, ensure_jwt_secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError("令牌无效") from exc
    if payload.get("type") != expected_type or not payload.get("sub") or not payload.get("jti"):
        raise TokenError("令牌类型不匹配")
    return payload
