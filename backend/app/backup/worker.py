"""在进程池里跑的入口。只接收任务编号，避免把路径或密码传进子进程参数。"""

from __future__ import annotations

import fcntl
import os
import time
from datetime import datetime

from app.backup.schedule import panel_zone
from app.backup.store import append_log, get_task, mark_result, meta_root


def _now() -> datetime:
    zone = panel_zone()
    current = datetime.now(zone) if zone is not None else datetime.now()
    return current.replace(tzinfo=None)


def _message(exc: Exception) -> str:
    text = getattr(exc, "message", None) or str(exc)
    return str(text).replace("\n", " ").replace("\r", " ").strip()[:400] or "备份失败"


def execute_task(task_id: int) -> None:
    """APScheduler 的进程池会按这个函数的路径重新导入它。"""
    try:
        os.nice(10)
    except OSError:
        pass
    started = time.perf_counter()
    started_at = _now().strftime("%Y-%m-%d %H:%M:%S")
    task = get_task(int(task_id))
    if task is None:
        return
    locks = meta_root() / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / f"{int(task_id)}.lock").open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            append_log(
                int(task_id),
                started_at=started_at,
                finished_at=started_at,
                duration_ms=0,
                status="skipped",
                size_bytes=0,
                filename="",
                message="已有一次备份在执行，本次跳过",
            )
            return
        with (locks / "pack.lock").open("a+", encoding="utf-8") as pack:
            try:
                fcntl.flock(pack, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                append_log(
                    int(task_id),
                    started_at=started_at,
                    finished_at=started_at,
                    duration_ms=0,
                    status="skipped",
                    size_bytes=0,
                    filename="",
                    message="已有打包任务在执行，本次跳过",
                )
                return
            _execute_locked(int(task_id), task, started, started_at)


def _execute_locked(task_id: int, task: dict, started: float, started_at: str) -> None:
    status = "ok"
    size = 0
    filename = ""
    message = "开始备份"
    append_log(
        int(task_id),
        started_at=started_at,
        finished_at=started_at,
        duration_ms=0,
        status="running",
        size_bytes=0,
        filename="",
        message=message,
    )
    try:
        from app.backup.runner import perform

        archive, message = perform(task)
        size = archive.stat().st_size
        filename = archive.name
    except Exception as exc:
        status = "error"
        message = _message(exc)
    finished_at = _now().strftime("%Y-%m-%d %H:%M:%S")
    duration_ms = int((time.perf_counter() - started) * 1000)
    append_log(
        int(task_id),
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        status=status,
        size_bytes=size,
        filename=filename,
        message=message,
    )
    mark_result(task_id, status, size, filename, finished_at)
