"""MySQL 用户、Redis 配置和 MongoDB 用户。账号只允许本机使用。"""

from __future__ import annotations

import logging

import anyio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymysql.err import MySQLError

from app.auth.deps import current_user
from app.databases.engines import _mongo_client, _redis_client
from app.databases.security import DatabaseError, identifier, password, pymysql_sql, quote_ident
from app.databases.service import _connect

logger = logging.getLogger("panel.databases")

router = APIRouter()
router.dependencies.append(Depends(current_user))

_RESERVED_USERS = {"root", "mysql.sys", "mysql.session", "mysql.infoschema", "debian-sys-maint", "panel"}
_HOSTS = {"localhost", "127.0.0.1"}
_POLICIES = {
    "noeviction",
    "allkeys-lru",
    "volatile-lru",
    "allkeys-lfu",
    "volatile-lfu",
    "allkeys-random",
    "volatile-random",
    "volatile-ttl",
}


class MysqlUserIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=64)
    host: str = "localhost"
    db_name: str = ""


class MysqlDropIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    host: str = "localhost"


class RedisConfigIn(BaseModel):
    maxmemory: int = Field(ge=0, le=268435456)
    policy: str = "noeviction"


class MongoUserIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=64)
    db_name: str = Field(min_length=1, max_length=32)


class MongoDropIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    db_name: str = Field(min_length=1, max_length=32)


async def _run(func):
    try:
        return await anyio.to_thread.run_sync(func)
    except DatabaseError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


def _host(value: str) -> str:
    if value not in _HOSTS:
        raise DatabaseError("新账号只允许从本机登录")
    return value


def _mysql_user(value: str) -> str:
    if value.lower() in _RESERVED_USERS:
        raise DatabaseError("不能操作系统账号")
    return identifier(value, "用户名")


def list_mysql_users() -> dict:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT user, host FROM mysql.user ORDER BY user, host LIMIT 100")
            rows = cursor.fetchall()
            items = []
            for user, host in rows:
                grants = _grants(cursor, str(user), str(host))
                items.append({"username": user, "host": host, "grants": grants, "reserved": str(user).lower() in _RESERVED_USERS})
        return {"items": items}
    except MySQLError as exc:
        raise DatabaseError("读取 MySQL 用户失败") from exc
    finally:
        connection.close()


def _grants(cursor, user: str, host: str) -> str:
    if not user or any(char in user + host for char in "\x00'\"\\"):
        return ""
    try:
        cursor.execute(pymysql_sql("SHOW GRANTS FOR %s@%s"), (user, host))
    except MySQLError:
        return ""
    raw = " ".join(str(row[0]).split(" IDENTIFIED BY ", 1)[0] for row in cursor.fetchall())
    upper = raw.upper()
    if "ALL PRIVILEGES" in upper:
        return "全部权限"
    if any(word in upper for word in ("SELECT", "INSERT", "UPDATE", "DELETE")):
        return "读写"
    if raw.strip():
        return "已授权"
    return ""


def add_mysql_user(username: str, raw_password: str, host: str, db_name: str) -> dict:
    user = _mysql_user(username)
    host = _host(host)
    raw_password = password(raw_password)
    database = identifier(db_name, "数据库名") if db_name else ""
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(pymysql_sql("CREATE USER %s@%s IDENTIFIED BY %s"), (user, host, raw_password))
            if database:
                quoted = quote_ident(database, "数据库名")
                cursor.execute(
                    pymysql_sql(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {quoted}.* TO %s@%s"),
                    (user, host),
                )
        connection.commit()
    except MySQLError as exc:
        connection.rollback()
        logger.warning("创建 MySQL 用户失败: %s", str(exc).replace(raw_password, "******")[:500])
        raise DatabaseError("创建 MySQL 用户失败") from exc
    finally:
        connection.close()
    return {"username": user, "host": host, "db_name": database}


def delete_mysql_user(username: str, host: str) -> dict:
    user = _mysql_user(username)
    if host not in _HOSTS and host != "%":
        raise DatabaseError("访问主机无效")
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(pymysql_sql("DROP USER %s@%s"), (user, host))
        connection.commit()
    except MySQLError as exc:
        connection.rollback()
        raise DatabaseError("删除 MySQL 用户失败") from exc
    finally:
        connection.close()
    return {"username": user, "host": host}


def redis_config() -> dict:
    client = _redis_client()
    try:
        keys = ["maxmemory", "maxmemory-policy", "databases", "bind", "timeout"]
        values = {}
        for key in keys:
            found = client.config_get(key) or {}
            values[key] = found.get(key, "")
        return values
    finally:
        client.close()


def save_redis_config(maxmemory: int, policy: str) -> dict:
    if policy not in _POLICIES:
        raise DatabaseError("Redis 内存策略无效")
    client = _redis_client()
    try:
        client.config_set("maxmemory", str(int(maxmemory)))
        client.config_set("maxmemory-policy", policy)
    except Exception as exc:
        raise DatabaseError("保存 Redis 配置失败") from exc
    finally:
        client.close()
    return {"maxmemory": int(maxmemory), "policy": policy}


def list_mongo_users() -> dict:
    with _mongo_client() as client:
        result = client.admin.command({"usersInfo": 1, "showCredentials": False})
    items = []
    for item in result.get("users") or []:
        item.pop("credentials", None)
        roles = [
            {"role": role.get("role"), "db": role.get("db")}
            for role in item.get("roles") or []
            if role.get("role") != "root"
        ]
        items.append({"username": item.get("user"), "db": item.get("db"), "roles": roles})
    return {"items": items}


def add_mongo_user(username: str, raw_password: str, db_name: str) -> dict:
    user = _mysql_user(username)
    database = identifier(db_name, "数据库名")
    raw_password = password(raw_password)
    try:
        with _mongo_client() as client:
            client[database].command(
                "createUser",
                user,
                pwd=raw_password,
                roles=[{"role": "readWrite", "db": database}],
            )
    except DatabaseError:
        raise
    except Exception as exc:
        logger.warning("创建 MongoDB 用户失败: %s", str(exc).replace(raw_password, "******")[:500])
        raise DatabaseError("创建 MongoDB 用户失败") from exc
    return {"username": user, "db_name": database}


def delete_mongo_user(username: str, db_name: str) -> dict:
    user = _mysql_user(username)
    database = identifier(db_name, "数据库名")
    with _mongo_client() as client:
        client[database].command("dropUser", user)
    return {"username": user, "db_name": database}


@router.get("/mysql/users")
async def mysql_users() -> dict:
    return await _run(list_mysql_users)


@router.post("/mysql/users")
async def mysql_user_add(body: MysqlUserIn) -> dict:
    return await _run(lambda: add_mysql_user(body.username, body.password, body.host, body.db_name))


@router.post("/mysql/users/delete")
async def mysql_user_delete(body: MysqlDropIn) -> dict:
    return await _run(lambda: delete_mysql_user(body.username, body.host))


@router.get("/redis/config")
async def redis_get() -> dict:
    return await _run(redis_config)


@router.post("/redis/config")
async def redis_save(body: RedisConfigIn) -> dict:
    return await _run(lambda: save_redis_config(body.maxmemory, body.policy))


@router.get("/mongodb/users")
async def mongo_users() -> dict:
    return await _run(list_mongo_users)


@router.post("/mongodb/users")
async def mongo_user_add(body: MongoUserIn) -> dict:
    return await _run(lambda: add_mongo_user(body.username, body.password, body.db_name))


@router.post("/mongodb/users/delete")
async def mongo_user_delete(body: MongoDropIn) -> dict:
    return await _run(lambda: delete_mongo_user(body.username, body.db_name))
