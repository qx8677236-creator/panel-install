"""数据库名称、账号和密码只允许安全字符，避免把输入拼进 SQL 或命令。"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
PASSWORD = re.compile(r"^[A-Za-z0-9_]{8,64}$")
IPV4 = re.compile(r"^(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}$")
NOTE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff ]{0,40}$")
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
ENGINES = {"mysql", "sqlserver", "mongodb", "redis", "pgsql", "sqlite"}
SYSTEM_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}


class DatabaseError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status
        super().__init__(message)


def engine_name(value: str) -> str:
    if not isinstance(value, str) or value not in ENGINES:
        raise DatabaseError("不支持的数据库类型")
    return value


def identifier(value: str, label: str = "名称") -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise DatabaseError(f"{label}只能使用字母、数字和下划线，并且以字母开头")
    if value.lower() in SYSTEM_DATABASES or value.lower() == "root":
        raise DatabaseError(f"不能使用保留{label}")
    return value


def password(value: str) -> str:
    if not isinstance(value, str) or not PASSWORD.fullmatch(value):
        raise DatabaseError("密码只能使用 8 到 64 位字母、数字和下划线")
    return value


def strong_password(value: str) -> str:
    raw = password(value)
    if not (re.search(r"[A-Z]", raw) and re.search(r"[a-z]", raw) and re.search(r"\d", raw)):
        raise DatabaseError("密码需要同时包含大写字母、小写字母和数字")
    return raw


EXAMPLE_ROOT_PASSWORD = "Admin123!@#"


def root_password(value: str) -> str:
    """安装和 root 口令。必须由调用方传入，没有默认值。"""
    if not isinstance(value, str) or value == "":
        raise DatabaseError("请填写 root 密码")
    if value == EXAMPLE_ROOT_PASSWORD:
        raise DatabaseError("不能使用示例密码")
    if len(value) < 8 or len(value) > 64:
        raise DatabaseError("密码长度需要在 8 到 64 位之间")
    if any(char.isspace() or char in "'\";\x00" for char in value):
        raise DatabaseError("密码不能包含引号、分号或空白")
    if not (
        re.search(r"[A-Z]", value)
        and re.search(r"[a-z]", value)
        and re.search(r"\d", value)
        and re.search(r"[^A-Za-z0-9]", value)
    ):
        raise DatabaseError("密码需要同时包含大写字母、小写字母、数字和特殊字符")
    return value


def pymysql_sql(query: str) -> str:
    """字面量 %（例如主机 '%'）要写成 %%，否则 pymysql 会把它当成格式符。"""
    return query.replace("%", "%%").replace("%%s", "%s")


def generated_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    while True:
        raw = "".join(secrets.choice(alphabet) for _ in range(15)) + "_"
        if re.search(r"[A-Z]", raw) and re.search(r"[a-z]", raw) and re.search(r"\d", raw):
            return raw


def note(value: str) -> str:
    text = str(value or "")
    if not NOTE.fullmatch(text):
        raise DatabaseError("备注只能使用字母、数字、下划线、空格和中文")
    return text.strip()


def account_host(access: str, ip: str = "") -> str:
    if access == "local":
        return "localhost"
    if access == "all":
        return "%"
    if access == "ip" and isinstance(ip, str) and IPV4.fullmatch(ip):
        return ip
    raise DatabaseError("访问权限无效")


def quote_ident(value: str, label: str = "名称") -> str:
    """先按标识符校验，再加反引号。反引号加倍，避免拼进语句。"""
    safe = identifier(value, label).replace("`", "``")
    return "`" + safe + "`"


def saved_host(host: str) -> str:
    if host in {"localhost", "%"} or (isinstance(host, str) and IPV4.fullmatch(host)):
        return host
    raise DatabaseError("记录中的访问权限无效")


def quote_host(access: str, ip: str = "") -> str:
    host = account_host(access, ip)
    if host != "%" and host != "localhost" and not IPV4.fullmatch(host):
        raise DatabaseError("访问权限无效")
    return "'" + host.replace("'", "''") + "'"


def resolve_sqlite_file(raw: str, roots: list[Path]) -> Path:
    if not isinstance(raw, str) or "\x00" in raw or not raw.startswith("/"):
        raise DatabaseError("请填写数据库文件的绝对路径")
    candidate = Path(raw)
    if candidate.exists() and candidate.is_symlink():
        raise DatabaseError("不能打开符号链接")
    resolved = candidate.resolve(strict=candidate.exists())
    allowed = False
    for root in roots:
        base = root.resolve()
        if resolved == base or base in resolved.parents:
            allowed = True
            break
    if not allowed:
        raise DatabaseError("这个路径不在允许的目录里")
    if resolved.suffix.lower() not in SQLITE_SUFFIXES:
        raise DatabaseError("只允许打开 .db、.sqlite 或 .sqlite3 文件")
    return resolved
