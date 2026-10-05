"""可逆加密。密钥文件只在本机 data 目录，不写入日志。"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import DATA_DIR

PREFIX = "enc:v1:"


def _key_path(directory: Path | None = None) -> Path:
    return (directory or DATA_DIR) / "secret.key"


def ensure_secret_key(directory: Path | None = None) -> None:
    """没有密钥文件时用 secrets.token_hex 生成，权限 600。"""
    base = directory or DATA_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = _key_path(base)
    if not path.exists():
        material = secrets.token_hex(32)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            fd = None
        else:
            try:
                os.write(fd, material.encode("ascii"))
            finally:
                os.close(fd)
    os.chmod(path, 0o600)


def _fernet(directory: Path | None = None) -> Fernet:
    ensure_secret_key(directory)
    material = _key_path(directory).read_text(encoding="utf-8").strip()
    try:
        raw = bytes.fromhex(material)
    except ValueError as exc:
        raise RuntimeError("密钥文件无效") from exc
    if len(raw) != 32:
        raise RuntimeError("密钥文件无效")
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_text(plain: str, directory: Path | None = None) -> str:
    token = _fernet(directory).encrypt(plain.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt_text(stored: str, directory: Path | None = None) -> str:
    stored = stored.strip()
    if not stored.startswith(PREFIX):
        return stored
    token = stored[len(PREFIX) :].encode("ascii")
    try:
        return _fernet(directory).decrypt(token).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("无法解密") from exc


def write_secret_file(path: Path, plain: str, directory: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (encrypt_text(plain, directory=directory) + "\n").encode("ascii")
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def read_secret_file(path: Path, directory: Path | None = None) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith(PREFIX):
        return decrypt_text(text, directory=directory)
    write_secret_file(path, text, directory=directory)
    return text
