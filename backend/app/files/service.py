"""文件列表、增删改、上传下载、压缩解压、文本读写。不调用 shell。"""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional
from uuid import uuid4

from app.config import DATA_DIR
from app.files.security import FileOpError, display_path, get_file_root, locate, raise_jail

MAX_TEXT = 1_000_000
MAX_UPLOAD = 512 * 1024 * 1024
MAX_CHUNK = 8 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_UNCOMPRESSED = 512 * 1024 * 1024
LIST_PAGE_SIZE = 100
LIST_PAGE_MAX = 200
PREVIEW_LIMIT = 50 * 1024
PREVIEW_LINES = 200
STREAM_CHUNK = 8 * 1024

TEXT_SUFFIXES = {
    ".txt", ".md", ".html", ".htm", ".css", ".scss", ".less", ".js", ".mjs", ".cjs",
    ".ts", ".tsx", ".jsx", ".vue", ".json", ".py", ".xml", ".yml", ".yaml", ".ini",
    ".conf", ".log", ".csv", ".sql", ".php", ".sh", ".toml", ".svg",
}
TEXT_NAMES = {".env", ".gitignore", ".htaccess", "dockerfile", "makefile", "license", "readme"}



def list_dir(
    raw: str,
    *,
    root: Optional[Path] = None,
    page: int = 1,
    page_size: int = LIST_PAGE_SIZE,
    query: str = "",
) -> dict:
    """只统计当前页需要的属性。目录扫描在调用方的线程里完成，不占用事件循环。"""
    base = _root(root)
    folder = locate(raw, root=base, follow_final=True)
    if not folder.is_dir():
        raise FileOpError("路径不是目录", 400)
    try:
        page = int(page)
        page_size = int(page_size)
    except (TypeError, ValueError):
        raise FileOpError("分页参数无效", 400) from None
    if page < 1 or page_size < 1 or page_size > LIST_PAGE_MAX:
        raise FileOpError("分页参数无效", 400)
    needle = str(query or "").strip().lower()
    if len(needle) > 80:
        raise FileOpError("搜索关键词过长", 400)
    ranked = []
    try:
        scanner = os.scandir(folder)
    except OSError as exc:
        raise FileOpError("无法读取目录", 400) from exc
    with scanner:
        for item in scanner:
            name = item.name
            if needle and needle not in name.lower():
                continue
            try:
                is_link = item.is_symlink()
                is_dir = item.is_dir(follow_symlinks=False) and not is_link
            except OSError:
                continue
            ranked.append((0 if is_dir else 1, name.lower(), name, is_dir, is_link))
    ranked.sort()
    total = len(ranked)
    start = (page - 1) * page_size
    window = ranked[start:start + page_size]
    entries = []
    for _rank, _lower, name, is_dir, is_link in window:
        item = folder / name
        try:
            st = item.lstat()
        except OSError:
            continue
        entries.append(
            {
                "name": name,
                "is_dir": is_dir,
                "is_link": is_link,
                "size": 0 if is_dir else st.st_size,
                "mtime": st.st_mtime,
                "mode": format(stat.S_IMODE(st.st_mode), "03o"),
                "mode_text": stat.filemode(st.st_mode),
                "editable": _editable(name, st),
            }
        )
    shown = display_path(folder, base)
    absolute = base.as_posix() if not shown else f"{base.as_posix()}/{shown}"
    return {
        "path": shown,
        "root": base.as_posix(),
        "absolute": absolute,
        "entries": entries,
        "page": page,
        "page_size": page_size,
        "total": total,
        "query": needle,
    }



def list_tree(raw: str, *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    folder = locate(raw, root=base, follow_final=True)
    if not folder.is_dir():
        raise FileOpError("路径不是目录", 400)
    names = []
    try:
        scanner = os.scandir(folder)
    except OSError as exc:
        raise FileOpError("无法读取目录", 400) from exc
    with scanner:
        for item in scanner:
            try:
                if item.is_symlink() or not item.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            names.append(item.name)
    names.sort(key=str.lower)
    shown = names[:800]
    children = [{"name": name, "path": display_path(folder / name, base)} for name in shown]
    return {
        "path": display_path(folder, base),
        "directories": children,
        "truncated": len(names) > len(shown),
    }


def make_dir(directory: str, name: str, *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    parent = locate(directory, root=base, follow_final=True)
    target = _child(parent, name, base, raw=name)
    if target.exists() or target.is_symlink():
        raise FileOpError("同名文件已存在", 409)
    target.mkdir()
    os.chmod(target, 0o755)
    return {"path": display_path(target, base)}


def make_file(directory: str, name: str, *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    parent = locate(directory, root=base, follow_final=True)
    target = _child(parent, name, base, raw=name)
    if target.exists() or target.is_symlink():
        raise FileOpError("同名文件已存在", 409)
    target.write_bytes(b"")
    os.chmod(target, 0o644)
    return {"path": display_path(target, base)}


def remove_paths(paths: list, *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    removed = []
    for raw in paths:
        target = locate(raw, root=base, follow_final=False)
        if target.resolve(strict=False) == base:
            raise FileOpError("不能删除文件根目录", 400)
        if target.is_symlink():
            target.unlink()
        else:
            real = target.resolve(strict=True)
            if not _inside(real, base):
                raise_jail(raw, "outside_jail")
            if real.is_dir():
                shutil.rmtree(real)
            else:
                real.unlink()
        if target.exists() or target.is_symlink():
            raise FileOpError("删除后路径仍然存在", 500)
        removed.append(raw)
    return {"removed": removed}


def rename_path(raw: str, new_name: str, *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    source = locate(raw, root=base, follow_final=False)
    if source.resolve(strict=False) == base:
        raise FileOpError("不能重命名文件根目录", 400)
    safe = _safe_name(new_name)
    target = source.parent / safe
    if not _lexical_inside(target, base):
        raise_jail(new_name, "outside_jail")
    if target.exists() or target.is_symlink():
        raise FileOpError("目标已存在", 409)
    os.rename(source, target)
    return {"path": display_path(target, base)}


def change_mode(paths: list, mode: str, *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    value = _parse_mode(mode)
    changed = []
    for raw in paths:
        # 跟随最终目标。指向监狱外的符号链接会在 locate 里被拦住。
        target = locate(raw, root=base, follow_final=True)
        if target.is_symlink():
            raise FileOpError("不能修改符号链接权限", 400)
        os.chmod(target, value)
        changed.append(display_path(target, base))
    return {"mode": format(value, "03o"), "paths": changed}



def read_text(raw: str, *, root: Optional[Path] = None) -> dict:
    """预览最多 200 行或 50KB，按 8KB 分块读，不把大文件整份读进内存。"""
    base = _root(root)
    target = locate(raw, root=base, follow_final=True)
    if not target.is_file() or target.is_symlink():
        raise FileOpError("只能读取普通文本文件", 400)
    if _archive_kind(target.name):
        raise FileOpError("压缩包不能预览，请下载", 400)
    size = target.stat().st_size
    pieces = []
    pending = b""
    held = 0
    eof = False
    try:
        with target.open("rb") as handle:
            while len(pieces) < PREVIEW_LINES and held < PREVIEW_LIMIT:
                chunk = handle.read(STREAM_CHUNK)
                if not chunk:
                    eof = True
                    break
                if b"\x00" in chunk:
                    raise FileOpError("文件不是可编辑的文本", 400)
                blob = pending + chunk
                parts = blob.split(b"\n")
                pending = parts.pop()
                for part in parts:
                    room = PREVIEW_LIMIT - held
                    if room <= 0 or len(pieces) >= PREVIEW_LINES:
                        eof = False
                        pending = b""
                        break
                    if len(part) + 1 > room:
                        pieces.append(part[:room])
                        held += room
                        eof = False
                        pending = b""
                        break
                    pieces.append(part)
                    held += len(part) + 1
                else:
                    continue
                break
    except FileOpError:
        raise
    except OSError as exc:
        raise FileOpError("无法读取文件", 400) from exc
    if eof and pending and len(pieces) < PREVIEW_LINES and held < PREVIEW_LIMIT:
        room = PREVIEW_LIMIT - held
        pieces.append(pending[:room])
        held += min(len(pending), room)
        if len(pending) > room:
            eof = False
    truncated = not eof or held < size
    if len(pieces) > PREVIEW_LINES:
        pieces = pieces[:PREVIEW_LINES]
        truncated = True
    try:
        content = b"\n".join(pieces).decode("utf-8")
    except UnicodeDecodeError:
        raise FileOpError("文件不是 UTF-8 文本", 400) from None
    message = "文件过大，已截断显示" if truncated else ""
    return {
        "path": display_path(target, base),
        "absolute": f"{base.as_posix()}/{display_path(target, base)}" if display_path(target, base) else base.as_posix(),
        "content": content,
        "size": size,
        "truncated": truncated,
        "message": message,
    }


def write_text(raw: str, content: str, *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_TEXT:
        raise FileOpError("文本内容超出限制", 400)
    if "\x00" in content:
        raise FileOpError("文本内容不合法", 400)
    target = locate(raw, root=base, follow_final=True)
    if not target.is_file() or target.is_symlink():
        raise FileOpError("只能保存普通文本文件", 400)
    if target.stat().st_size > PREVIEW_LIMIT or target.read_bytes().count(b"\n") >= PREVIEW_LINES:
        raise FileOpError("文件过大，已截断显示，不能保存覆盖", 400)
    target.write_text(content, encoding="utf-8")
    return {"path": display_path(target, base), "size": target.stat().st_size}


def download_file(raw: str, *, root: Optional[Path] = None) -> Path:
    base = _root(root)
    target = locate(raw, root=base, follow_final=True)
    if not target.is_file() or target.is_symlink():
        raise FileOpError("只能下载普通文件", 400)
    return target


def iter_chunks(target: Path):
    """下载时按 8KB 向外送，不把整个文件读进内存。"""
    with target.open("rb") as handle:
        while True:
            chunk = handle.read(STREAM_CHUNK)
            if not chunk:
                break
            yield chunk


def download_headers(target: Path) -> dict:
    from urllib.parse import quote

    name = target.name.replace("\r", "").replace("\n", "").replace('"', "")
    if not name:
        name = "download"
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "Content-Length": str(target.stat().st_size),
    }


def compress_paths(paths: list, dest: str, *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    if not paths:
        raise FileOpError("请选择要压缩的文件", 400)
    sources = [locate(raw, root=base, follow_final=True) for raw in paths]
    destination = locate(dest, root=base, follow_final=True, allow_missing=True)
    kind = _archive_kind(destination.name)
    if kind is None:
        raise FileOpError("只支持压缩为 .zip 或 .tar.gz", 400)
    if destination.exists():
        raise FileOpError("压缩包已存在", 409)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if kind == "zip":
        _write_zip(destination, sources, base)
    else:
        _write_tar(destination, sources, base)
    return {"path": display_path(destination, base), "size": destination.stat().st_size}


def extract_archive(raw: str, dest: str = "", *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    archive = locate(raw, root=base, follow_final=True)
    if not archive.is_file():
        raise FileOpError("压缩包不存在", 404)
    kind = _archive_kind(archive.name)
    if kind is None:
        raise FileOpError("只支持解压 .zip 或 .tar.gz", 400)
    if dest:
        folder = locate(dest, root=base, follow_final=True)
    else:
        folder = archive.parent
    if not folder.is_dir():
        raise FileOpError("解压目录不存在", 400)
    if kind == "zip":
        count = _extract_zip(archive, folder, base)
    else:
        count = _extract_tar(archive, folder, base)
    return {"path": display_path(folder, base), "count": count}


def init_upload(directory: str, filename: str, size: int, total_chunks: int, *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    if size < 0 or size > MAX_UPLOAD:
        raise FileOpError("文件大小超出限制", 400)
    expected = 1 if size == 0 else (size + MAX_CHUNK - 1) // MAX_CHUNK
    # 前端按 1MB 分片。允许最后一片更小，但片数必须能覆盖声明的大小。
    if total_chunks < 1 or total_chunks > 10000 or total_chunks < expected:
        raise FileOpError("分片数量不合法", 400)
    parent = locate(directory, root=base, follow_final=True)
    if not parent.is_dir():
        raise FileOpError("上传目录不存在", 400)
    safe = _safe_name(filename)
    target = parent / safe
    if target.exists() or target.is_symlink():
        raise FileOpError("同名文件已存在", 409)
    upload_id = uuid4().hex
    folder = _upload_dir() / upload_id
    folder.mkdir(parents=True)
    meta = {
        "directory": display_path(parent, base),
        "filename": safe,
        "size": size,
        "total_chunks": total_chunks,
        "root": base.as_posix(),
    }
    (folder / "meta.txt").write_text(_dump_meta(meta), encoding="utf-8")
    return {"upload_id": upload_id, "chunk_size": min(MAX_CHUNK, 1024 * 1024)}


def save_chunk(upload_id: str, index: int, data: bytes) -> dict:
    folder = _upload_folder(upload_id)
    meta = _load_meta(folder)
    if index < 0 or index >= int(meta["total_chunks"]):
        raise FileOpError("分片序号超出范围", 400)
    if len(data) > MAX_CHUNK:
        raise FileOpError("分片过大", 400)
    (folder / f"chunk_{index:05d}").write_bytes(data)
    return {"received": index}


def complete_upload(upload_id: str, *, root: Optional[Path] = None) -> dict:
    base = _root(root)
    folder = _upload_folder(upload_id)
    meta = _load_meta(folder)
    if Path(meta["root"]).resolve() != base:
        raise FileOpError("上传任务和当前根目录不一致", 400)
    total = int(meta["total_chunks"])
    pieces = []
    for index in range(total):
        piece = folder / f"chunk_{index:05d}"
        if not piece.is_file():
            raise FileOpError("还有分片没有上传完成", 400)
        pieces.append(piece)
    size = sum(piece.stat().st_size for piece in pieces)
    if size != int(meta["size"]):
        raise FileOpError("上传大小和声明不一致", 400)
    parent = locate(str(meta["directory"]), root=base, follow_final=True)
    target = _child(parent, str(meta["filename"]), base, raw=str(meta["filename"]))
    if target.exists() or target.is_symlink():
        raise FileOpError("同名文件已存在", 409)
    with target.open("wb") as handle:
        for piece in pieces:
            handle.write(piece.read_bytes())
    os.chmod(target, 0o644)
    shutil.rmtree(folder, ignore_errors=True)
    return {"path": display_path(target, base), "size": size}


def _root(root: Optional[Path]) -> Path:
    return (root or get_file_root()).resolve()


def _safe_name(name: str) -> str:
    if not isinstance(name, str):
        raise_jail(repr(name), "illegal_char")
    text = name.strip()
    bad = text in {"", ".", ".."} or any(ch in text for ch in "/\\\x00\r\n") or len(text) > 255
    if bad:
        reason = "traversal" if ".." in text.split("/") or "/" in name or "\\" in name else "illegal_char"
        raise_jail(name, reason)
    return text


def _child(parent: Path, name: str, root: Path, *, raw: str) -> Path:
    safe = _safe_name(name)
    target = parent / safe
    if not _lexical_inside(target, root):
        raise_jail(raw, "outside_jail")
    return target


def _parse_mode(mode: str) -> int:
    if not isinstance(mode, str) or len(mode) not in {3, 4}:
        raise FileOpError("权限格式应为 755 这样的八进制", 400)
    try:
        value = int(mode, 8)
    except ValueError:
        raise FileOpError("权限格式应为 755 这样的八进制", 400)
    if value > 0o777:
        raise FileOpError("不允许 setuid、setgid 或粘滞位", 400)
    return value


def _editable(name: str, st: os.stat_result) -> bool:
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        return False
    if st.st_size > MAX_TEXT:
        return False
    return _is_text_name(name)


def _is_text_name(name: str) -> bool:
    lower = name.lower()
    if lower in TEXT_NAMES:
        return True
    return PurePosixPath(lower).suffix in TEXT_SUFFIXES


def _archive_kind(name: str) -> Optional[str]:
    lower = name.lower()
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return "tar.gz"
    if lower.endswith(".zip"):
        return "zip"
    return None


def _write_zip(destination: Path, sources: list, root: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            for path in _walk_files(source):
                arcname = path.resolve().relative_to(root).as_posix()
                archive.write(path, arcname)


def _write_tar(destination: Path, sources: list, root: Path) -> None:
    with tarfile.open(destination, "w:gz") as archive:
        for source in sources:
            for path in _walk_files(source):
                arcname = path.resolve().relative_to(root).as_posix()
                archive.add(path, arcname=arcname, recursive=False)



def _walk_files(source: Path):
    if source.is_symlink():
        return
    if source.is_file():
        yield source
        return
    stack = [source]
    while stack:
        current = stack.pop()
        try:
            scanner = os.scandir(current)
        except OSError:
            continue
        with scanner:
            children = list(scanner)
        for item in children:
            try:
                if item.is_symlink():
                    continue
                if item.is_dir(follow_symlinks=False):
                    stack.append(Path(item.path))
                elif item.is_file(follow_symlinks=False):
                    yield Path(item.path)
            except OSError:
                continue


def _extract_zip(archive: Path, folder: Path, root: Path) -> int:
    with zipfile.ZipFile(archive) as package:
        infos = package.infolist()
        _check_member_budget(len(infos), sum(info.file_size for info in infos))
        count = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise FileOpError("不支持加密压缩包", 400)
            _check_member_name(info.filename)
            target = _member_target(folder, info.filename, root)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info, "r") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
        return count


def _extract_tar(archive: Path, folder: Path, root: Path) -> int:
    try:
        package = tarfile.open(archive, "r:gz")
    except tarfile.TarError:
        raise FileOpError("无法读取 tar.gz", 400)
    with package:
        members = package.getmembers()
        _check_member_budget(len(members), sum(max(0, item.size) for item in members if item.isfile()))
        count = 0
        for member in members:
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise_jail(member.name, "outside_jail")
            _check_member_name(member.name)
            target = _member_target(folder, member.name, root)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise FileOpError("压缩包包含不支持的条目", 400)
            target.parent.mkdir(parents=True, exist_ok=True)
            src = package.extractfile(member)
            if src is None:
                raise FileOpError("无法读取压缩包条目", 400)
            with src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
        return count


def _check_member_budget(count: int, total: int) -> None:
    if count > MAX_MEMBERS or total > MAX_UNCOMPRESSED:
        raise FileOpError("压缩包体积或文件数超出限制", 400)


def _check_member_name(name: str) -> None:
    if not isinstance(name, str) or "\x00" in name or "\\" in name:
        raise_jail(str(name), "illegal_char")
    posix = PurePosixPath(name)
    if posix.is_absolute() or ".." in posix.parts:
        raise_jail(name, "traversal")


def _member_target(folder: Path, name: str, root: Path) -> Path:
    relative = PurePosixPath(name)
    target = folder.joinpath(*relative.parts).resolve(strict=False)
    if not _inside(target, folder.resolve()) or not _inside(target, root):
        raise_jail(name, "outside_jail")
    return target


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _lexical_inside(path: Path, root: Path) -> bool:
    return _inside(path, root)


def _upload_dir() -> Path:
    path = DATA_DIR / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _upload_folder(upload_id: str) -> Path:
    if not isinstance(upload_id, str) or len(upload_id) != 32 or any(ch not in "0123456789abcdef" for ch in upload_id):
        raise FileOpError("上传任务不存在", 404)
    folder = _upload_dir() / upload_id
    if not folder.is_dir():
        raise FileOpError("上传任务不存在", 404)
    return folder


def _dump_meta(meta: dict) -> str:
    return "\n".join(f"{key}={value}" for key, value in meta.items()) + "\n"


def _load_meta(folder: Path) -> dict:
    meta = {}
    for line in (folder / "meta.txt").read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        meta[key] = value
    required = {"directory", "filename", "size", "total_chunks", "root"}
    if not required <= set(meta):
        raise FileOpError("上传任务已损坏", 400)
    return meta
