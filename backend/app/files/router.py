"""文件管理接口。每个处理函数都走路径监狱，磁盘读写放在线程里，不占用事件循环。"""

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.deps import current_user
from app.files.security import FileOpError, PathJailError, recent_alerts, set_actor
from app.files import service

router = APIRouter()


async def _actor(username: str = Depends(current_user)) -> str:
    set_actor(username)
    return username


router.dependencies.append(Depends(_actor))


class NameIn(BaseModel):
    directory: str = ""
    name: str = Field(min_length=1, max_length=255)


class PathsIn(BaseModel):
    paths: list = Field(min_length=1, max_length=50)


class RenameIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    new_name: str = Field(min_length=1, max_length=255)


class ChmodIn(BaseModel):
    paths: list = Field(min_length=1, max_length=50)
    mode: str = Field(min_length=3, max_length=4)


class CompressIn(BaseModel):
    paths: list = Field(min_length=1, max_length=50)
    dest: str = Field(min_length=1, max_length=4096)


class ExtractIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    dest: str = ""


class ContentIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=1_000_000)


class UploadInitIn(BaseModel):
    directory: str = ""
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0, le=service.MAX_UPLOAD)
    total_chunks: int = Field(ge=1, le=10000)


class UploadCompleteIn(BaseModel):
    upload_id: str = Field(min_length=32, max_length=32)


async def _run(func):
    try:
        return await anyio.to_thread.run_sync(func)
    except PathJailError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": exc.code, "reason": exc.reason, "message": exc.message},
        ) from None
    except FileOpError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@router.get("/list")
async def list_dir(path: str = "", page: int = 1, page_size: int = 100, q: str = "") -> dict:
    return await _run(lambda: service.list_dir(path, page=page, page_size=page_size, query=q))


@router.get("/tree")
async def list_tree(path: str = "") -> dict:
    return await _run(lambda: service.list_tree(path))


@router.get("/alerts")
def alerts() -> dict:
    return {"alerts": recent_alerts()}


@router.post("/mkdir")
async def make_dir(body: NameIn) -> dict:
    return await _run(lambda: service.make_dir(body.directory, body.name))


@router.post("/create")
async def make_file(body: NameIn) -> dict:
    return await _run(lambda: service.make_file(body.directory, body.name))


@router.post("/delete")
async def remove_paths(body: PathsIn) -> dict:
    return await _run(lambda: service.remove_paths(body.paths))


@router.post("/rename")
async def rename_path(body: RenameIn) -> dict:
    return await _run(lambda: service.rename_path(body.path, body.new_name))


@router.post("/chmod")
async def change_mode(body: ChmodIn) -> dict:
    return await _run(lambda: service.change_mode(body.paths, body.mode))


@router.get("/content")
async def read_text(path: str) -> dict:
    return await _run(lambda: service.read_text(path))


@router.post("/content")
async def write_text(body: ContentIn) -> dict:
    return await _run(lambda: service.write_text(body.path, body.content))


@router.get("/download")
async def download(path: str):
    target = await _run(lambda: service.download_file(path))
    return StreamingResponse(
        service.iter_chunks(target),
        media_type="application/octet-stream",
        headers=service.download_headers(target),
    )


@router.post("/compress")
async def compress_paths(body: CompressIn) -> dict:
    return await _run(lambda: service.compress_paths(body.paths, body.dest))


@router.post("/extract")
async def extract_archive(body: ExtractIn) -> dict:
    return await _run(lambda: service.extract_archive(body.path, body.dest))


@router.post("/upload/init")
async def init_upload(body: UploadInitIn) -> dict:
    return await _run(
        lambda: service.init_upload(body.directory, body.filename, body.size, body.total_chunks)
    )


@router.post("/upload/chunk")
async def upload_chunk(
    upload_id: str = Form(...),
    index: int = Form(...),
    file: UploadFile = File(...),
) -> dict:
    data = await file.read(service.MAX_CHUNK + 1)
    return await _run(lambda: service.save_chunk(upload_id, index, data))


@router.post("/upload/complete")
async def complete_upload(body: UploadCompleteIn) -> dict:
    return await _run(lambda: service.complete_upload(body.upload_id))
