"""到点只负责把任务编号交给进程池。压缩本身不在 Web 进程里做。"""

from __future__ import annotations

import logging
import multiprocessing
import re
import threading
import time
from datetime import datetime

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.executors.pool import ProcessPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from concurrent.futures.process import BrokenProcessPool

from app.backup.schedule import panel_zone
from app.clock import beijing_text
from app.backup.store import append_log, load_tasks
from app.backup.worker import execute_task

logger = logging.getLogger("panel.backup")
_lock = threading.Lock()
_scheduler: BackgroundScheduler | None = None
_JOB = re.compile(r"^backup-(?:now-)?(\d+)")


def _noop() -> None:
    return None


def _boot_context():
    """forkserver 必须在出现其他线程之前拉起，后面的工作进程才不会继承 Web 服务的锁。"""
    if multiprocessing.parent_process() is not None:
        return multiprocessing.get_context("fork")
    try:
        context = multiprocessing.get_context("forkserver")
        context.set_forkserver_preload(["app.backup.worker"])
        if threading.active_count() == 1:
            probe = context.Process(target=_noop)
            probe.start()
            probe.join(timeout=15)
            if probe.is_alive():
                probe.terminate()
        return context
    except Exception:
        logger.exception("forkserver 启动失败，改用 fork")
        return multiprocessing.get_context("fork")


_CONTEXT = _boot_context()


class BackupProcessExecutor(ProcessPoolExecutor):
    """只把任务编号送进进程池，不序列化整个调度对象。"""

    def _do_submit_job(self, job, run_times):
        task_id = int(job.args[0])

        def callback(future):
            exc = future.exception()
            if exc:
                self._run_job_error(job.id, exc, getattr(exc, "__traceback__", None))
            else:
                self._run_job_success(job.id, None)

        try:
            future = self._pool.submit(execute_task, task_id)
        except BrokenProcessPool:
            self._pool = self._pool.__class__(self._pool._max_workers, **self.pool_kwargs)
            future = self._pool.submit(execute_task, task_id)
        future.add_done_callback(callback)


def _record_crash(event) -> None:
    match = _JOB.match(getattr(event, "job_id", "") or "")
    if not match:
        return
    message = str(getattr(event, "exception", "") or "备份进程异常").replace("\n", " ")[:400]
    stamp = beijing_text()
    try:
        append_log(
            int(match.group(1)),
            started_at=stamp,
            finished_at=stamp,
            duration_ms=0,
            status="error",
            size_bytes=0,
            filename="",
            message=f"备份进程异常：{message}",
        )
    except Exception:
        logger.exception("写备份失败日志失败")


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(
        executors={
            "backup": BackupProcessExecutor(max_workers=1, pool_kwargs={"mp_context": _CONTEXT}),
        },
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
        timezone=panel_zone(),
    )
    scheduler.add_listener(_record_crash, EVENT_JOB_ERROR)
    return scheduler


def sync_jobs(scheduler: BackgroundScheduler) -> None:
    zone = panel_zone()
    wanted = {}
    for task in load_tasks():
        if task.get("enabled"):
            wanted[f"backup-{int(task['id'])}"] = task
    for job in list(scheduler.get_jobs()):
        if job.id.startswith("backup-") and not job.id.startswith("backup-now-") and job.id not in wanted:
            scheduler.remove_job(job.id)
    for job_id, task in wanted.items():
        scheduler.add_job(
            execute_task,
            CronTrigger.from_crontab(task["cron"], timezone=zone),
            id=job_id,
            args=[int(task["id"])],
            replace_existing=True,
            executor="backup",
        )


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    with _lock:
        if _scheduler is not None and _scheduler.running:
            sync_jobs(_scheduler)
            return _scheduler
        scheduler = build_scheduler()
        pool = scheduler._executors["backup"]._pool
        pool.submit(execute_task, 0).result(timeout=30)
        scheduler.start()
        sync_jobs(scheduler)
        _scheduler = scheduler
    logger.info("备份调度器已启动")
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    with _lock:
        scheduler = _scheduler
        _scheduler = None
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def enqueue(task_id: int) -> None:
    from app.backup.store import BackupError, get_task

    if get_task(task_id) is None:
        raise BackupError("没有这个备份任务", 404)
    scheduler = get_scheduler()
    if scheduler is None or not scheduler.running:
        raise BackupError("备份调度器没有运行", 500)
    zone = panel_zone()
    scheduler.add_job(
        execute_task,
        "date",
        run_date=datetime.now(zone) if zone is not None else datetime.now(),
        args=[int(task_id)],
        id=f"backup-now-{int(task_id)}-{time.time_ns()}",
        executor="backup",
        misfire_grace_time=120,
    )
