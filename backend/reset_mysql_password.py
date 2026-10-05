#!/usr/bin/env python3
"""测试保险库里的 MySQL root 密码。只有认证失败且传入 --password 时才重置。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import pymysql
from pymysql.err import MySQLError

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.databases.security import DatabaseError, root_password
from app.vault import read_secret_file, write_secret_file

ADMIN = "/usr/local/sbin/panel-db-admin"
SECRET = ROOT / "data" / "mysql" / "root.secret"


def _connect(password: str):
    return pymysql.connect(
        host="127.0.0.1",
        port=3306,
        user="root",
        password=password,
        connect_timeout=5,
        charset="utf8mb4",
    )


def vault_password() -> str:
    if not SECRET.is_file():
        return ""
    try:
        return read_secret_file(SECRET)
    except Exception:
        print("VAULT_UNREADABLE", file=sys.stderr)
        return ""


def authenticates(password: str) -> bool:
    if not password:
        return False
    try:
        connection = _connect(password)
    except MySQLError:
        return False
    connection.close()
    return True


def reset_in_container(password: str) -> None:
    completed = subprocess.run(
        ["/usr/bin/sudo", "-n", ADMIN, "reset-mysql", password],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        text = ((completed.stderr or "") + "\n" + (completed.stdout or "")).strip()
        line = text.splitlines()[-1] if text else "重置失败"
        if password:
            line = line.replace(password, "******")
        print(line, file=sys.stderr)
        raise SystemExit(completed.returncode or 1)


def wait_auth(password: str) -> bool:
    for _ in range(30):
        if authenticates(password):
            return True
        time.sleep(1)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="测试或重置面板 MySQL root 密码")
    parser.add_argument("--password")
    args = parser.parse_args()
    supplied = args.password
    if supplied is not None:
        try:
            supplied = root_password(supplied)
        except DatabaseError as exc:
            print(exc.message, file=sys.stderr)
            raise SystemExit(2) from None
    if authenticates(vault_password()):
        print("RECOVERED_EXISTING")
        raise SystemExit(0)
    if supplied is None:
        print("AUTH_FAILED", file=sys.stderr)
        raise SystemExit(1)
    reset_in_container(supplied)
    if not wait_auth(supplied):
        print("AUTH_FAILED", file=sys.stderr)
        raise SystemExit(1)
    write_secret_file(SECRET, supplied)
    print("RESET_OK")


if __name__ == "__main__":
    main()
