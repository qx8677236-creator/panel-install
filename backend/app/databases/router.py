"""数据库管理接口。只接受 GET 和 POST，写操作都在线程里执行。"""

import anyio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.auth.deps import current_user
from app.databases.security import DatabaseError
from app.databases import service

router = APIRouter()
router.dependencies.append(Depends(current_user))


class RemoteIn(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: str = Field(default="3306")
    user: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=64)

    @field_validator("port", mode="before")
    @classmethod
    def keep_port_text(cls, value):
        if isinstance(value, bool) or value is None:
            raise ValueError("端口无效")
        text = str(value).strip()
        if not text.isdigit() or not 1 <= len(text) <= 5:
            raise ValueError("端口无效")
        number = int(text)
        if not 1 <= number <= 65535:
            raise ValueError("端口无效")
        return text


class CreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    user: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=64)
    access: str = Field(pattern=r"^(local|all|ip)$")
    ip: str = ""
    note: str = ""


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class DeleteIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    confirm: str = ""


class PasswordIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=64)


class AccessIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    access: str = Field(pattern=r"^(local|all|ip)$")
    ip: str = ""


class RootIn(BaseModel):
    password: str = Field(min_length=8, max_length=64)


class InstallMysqlIn(BaseModel):
    root_password: str = ""
    password: str = ""

    def resolved_password(self) -> str:
        return self.root_password or self.password


class RestoreIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    file: str = Field(min_length=1, max_length=80)


async def _run(func):
    try:
        return await anyio.to_thread.run_sync(func)
    except DatabaseError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@router.get("/status")
async def status() -> dict:
    return await _run(service.detect)


@router.get("")
async def listing(page: int = 1, page_size: int = 10) -> dict:
    return await _run(lambda: service.list_databases(page, page_size))


@router.post("/install")
async def install(body: InstallMysqlIn) -> dict:
    return await _run(lambda: service.install_mysql(body.resolved_password()))


@router.post("/remote")
async def remote(body: RemoteIn) -> dict:
    return await _run(lambda: service.save_remote(body.host, body.port, body.user, body.password))


@router.post("/create")
async def create(body: CreateIn) -> dict:
    return await _run(
        lambda: service.create_database(body.name, body.user, body.password, body.access, body.ip, body.note)
    )


@router.post("/delete")
async def remove(body: DeleteIn) -> dict:
    if body.confirm != body.name:
        raise HTTPException(status_code=400, detail="请输入数据库名以确认删除")
    return await _run(lambda: service.delete_database(body.name, body.confirm))


@router.post("/password")
async def change_password(body: PasswordIn) -> dict:
    return await _run(lambda: service.change_password(body.name, body.password))


@router.post("/access")
async def change_access(body: AccessIn) -> dict:
    return await _run(lambda: service.change_access(body.name, body.access, body.ip))


@router.post("/root-password")
async def root_password(body: RootIn) -> dict:
    return await _run(lambda: service.change_root_password(body.password))


@router.post("/root-password/show")
async def show_root_password() -> dict:
    return await _run(service.show_root_password)


@router.post("/backup")
async def backup(body: NameIn) -> dict:
    return await _run(lambda: service.begin_backup(body.name))


@router.get("/backup/status")
async def backup_status(job_id: str) -> dict:
    return await _run(lambda: service.job_status(job_id))


@router.get("/backup/download")
async def backup_download(name: str, file: str) -> FileResponse:
    path = await _run(lambda: service.resolve_backup_file(name, file))
    return FileResponse(path, filename=path.name, media_type="application/gzip")


@router.get("/backups")
async def backups(name: str) -> dict:
    return await _run(lambda: service.list_backups(name))


@router.post("/restore")
async def restore(body: RestoreIn) -> dict:
    return await _run(lambda: service.begin_restore(body.name, body.file))
