"""统一的数据库安装和各引擎管理接口。只接受 GET 和 POST。"""

import anyio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.deps import current_user
from app.databases import engines
from app.databases import service as mysql
from app.databases.security import DatabaseError

router = APIRouter()
router.dependencies.append(Depends(current_user))


class InstallIn(BaseModel):
    engine: str = ""
    type: str = ""
    password: str = ""
    root_password: str = ""

    def resolved(self) -> str:
        return (self.engine or self.type).strip().lower()

    def resolved_password(self) -> str:
        return self.root_password or self.password


class EngineIn(BaseModel):
    engine: str


class SqlNameIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class ConfirmNameIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    confirm: str = ""


class ConfirmIn(BaseModel):
    confirm: str = ""


def _need_confirm(value: str) -> None:
    if value != "确认":
        raise HTTPException(status_code=400, detail="请输入确认")


class SqlRestoreIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    file: str = Field(min_length=1, max_length=80)


class MongoIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    user: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=64)


class RedisPasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=64)


class PostgresIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    user: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=64)


class PostgresPasswordIn(BaseModel):
    user: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=64)


class SqliteIn(BaseModel):
    file: str = Field(min_length=1, max_length=300)
    table: str = ""
    page: int = 1


async def _run(func):
    try:
        return await anyio.to_thread.run_sync(func)
    except DatabaseError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@router.get("/status")
async def status(engine: str = "") -> dict:
    if engine:
        return await _run(lambda: engines.engine_status(engine))
    return await _run(engines.all_status)


@router.get("/progress")
async def progress(engine: str) -> dict:
    return await _run(lambda: engines.install_progress(engine))


@router.post("/install")
async def install(body: InstallIn) -> dict:
    return await _run(lambda: engines.begin_install(body.resolved(), body.resolved_password()))


@router.post("/start")
async def start(body: EngineIn) -> dict:
    return await _run(lambda: engines.start_engine(body.engine))


@router.post("/stop")
async def stop(body: EngineIn) -> dict:
    return await _run(lambda: engines.stop_engine(body.engine))


@router.get("/sqlserver")
async def sqlserver_list() -> dict:
    return await _run(engines.sqlserver_list)


@router.post("/sqlserver/create")
async def sqlserver_create(body: SqlNameIn) -> dict:
    return await _run(lambda: engines.sqlserver_create(body.name))


@router.post("/sqlserver/delete")
async def sqlserver_delete(body: ConfirmNameIn) -> dict:
    _need_confirm(body.confirm)
    return await _run(lambda: engines.sqlserver_delete(body.name))


@router.post("/sqlserver/backup")
async def sqlserver_backup(body: SqlNameIn) -> dict:
    return await _run(lambda: engines.sqlserver_backup(body.name))


@router.get("/sqlserver/backups")
async def sqlserver_backups(name: str) -> dict:
    return await _run(lambda: engines.sqlserver_backups(name))


@router.post("/sqlserver/restore")
async def sqlserver_restore(body: SqlRestoreIn) -> dict:
    return await _run(lambda: engines.sqlserver_restore(body.name, body.file))


@router.get("/mongodb")
async def mongo_list() -> dict:
    return await _run(engines.mongo_overview)


@router.post("/mongodb/create")
async def mongo_create(body: MongoIn) -> dict:
    return await _run(lambda: engines.mongo_create(body.name, body.user, body.password))


@router.post("/mongodb/delete")
async def mongo_delete(body: ConfirmNameIn) -> dict:
    _need_confirm(body.confirm)
    return await _run(lambda: engines.mongo_delete(body.name))


@router.get("/redis")
async def redis_info() -> dict:
    return await _run(engines.redis_info)


@router.post("/redis/flush")
async def redis_flush(body: ConfirmIn) -> dict:
    _need_confirm(body.confirm)
    return await _run(engines.redis_flush)


@router.post("/redis/password")
async def redis_password(body: RedisPasswordIn) -> dict:
    return await _run(lambda: engines.redis_password(body.password))


@router.get("/pgsql")
async def pgsql_list() -> dict:
    return await _run(engines.postgres_overview)


@router.post("/pgsql/create")
async def pgsql_create(body: PostgresIn) -> dict:
    return await _run(lambda: engines.postgres_create(body.name, body.user, body.password))


@router.post("/pgsql/delete")
async def pgsql_delete(body: ConfirmNameIn) -> dict:
    _need_confirm(body.confirm)
    return await _run(lambda: engines.postgres_delete(body.name))


@router.post("/pgsql/password")
async def pgsql_password(body: PostgresPasswordIn) -> dict:
    return await _run(lambda: engines.postgres_role_password(body.user, body.password))


@router.post("/pgsql/delete-role")
async def pgsql_delete_role(body: ConfirmNameIn) -> dict:
    _need_confirm(body.confirm)
    return await _run(lambda: engines.postgres_delete_role(body.name))


@router.get("/sqlite/files")
async def sqlite_files() -> dict:
    return await _run(engines.sqlite_files)


@router.get("/sqlite/tables")
async def sqlite_tables(file: str) -> dict:
    return await _run(lambda: engines.sqlite_tables(file))


@router.get("/sqlite/rows")
async def sqlite_rows(file: str, table: str, page: int = 1) -> dict:
    return await _run(lambda: engines.sqlite_rows(file, table, page))


@router.get("/mysql")
async def mysql_list(page: int = 1, page_size: int = 10) -> dict:
    return await _run(lambda: mysql.list_databases(page, page_size))
