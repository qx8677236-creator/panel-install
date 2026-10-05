"""计划任务保存在面板数据目录，并同步到当前用户的 crontab。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from app.nginx.security import SiteError

_lock = threading.Lock()
_BEGIN = "# panel-crontab-begin"
_END = "# panel-crontab-end"
_NAME = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff ]{1,40}$")
_URL = re.compile(r"^https?://[A-Za-z0-9._:-]+(?:/[A-Za-z0-9._~%/?&=+\-]*)?$")
_TYPES = {"minute", "hour", "day", "week", "month"}
_STYPES = {"toShell", "toUrl", "database"}


class CronError(SiteError):
    pass


def _root() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "crontab"


def _tasks_path() -> Path:
    return _root() / "tasks.json"


def _log_path(task_id: int) -> Path:
    return _root() / "logs" / f"{task_id}.log"


def _tasks_log() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "logs" / "tasks.log"


def _load() -> list:
    path = _tasks_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write(items: list) -> None:
    path = _tasks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _number(value, low: int, high: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise CronError(f"{label}无效") from None
    if number < low or number > high:
        raise CronError(f"{label}超出范围")
    return number


def cron_expr(task: dict) -> str:
    kind = task["type"]
    if kind == "minute":
        step = _number(task.get("where1"), 1, 59, "分钟间隔")
        return f"*/{step} * * * *"
    minute = _number(task.get("minute", 0), 0, 59, "分钟")
    if kind == "hour":
        return f"{minute} * * * *"
    hour = _number(task.get("hour", 0), 0, 23, "小时")
    if kind == "day":
        return f"{minute} {hour} * * *"
    if kind == "week":
        week = _number(task.get("where1"), 0, 7, "星期")
        return f"{minute} {hour} * * {week}"
    day = _number(task.get("where1"), 1, 28, "日期")
    return f"{minute} {hour} {day} * *"


def _check(payload: dict, task_id: int | None = None) -> dict:
    name = str(payload.get("name") or "").strip()
    if not _NAME.fullmatch(name):
        raise CronError("任务名称只能使用中文、字母、数字、空格、下划线和短横线")
    kind = str(payload.get("type") or "")
    style = str(payload.get("sType") or "")
    if kind not in _TYPES or style not in _STYPES:
        raise CronError("任务类型无效")
    body = str(payload.get("sBody") or "")
    target = str(payload.get("sName") or "")
    if "\x00" in body or len(body) > 4000:
        raise CronError("脚本内容无效")
    if style == "toUrl" and not _URL.fullmatch(body):
        raise CronError("URL 无效")
    if style == "database":
        from app.databases.security import DatabaseError, identifier

        try:
            target = identifier(target, "数据库名")
        except DatabaseError as exc:
            raise CronError(exc.message) from exc
        body = ""
    if style == "toShell" and not body.strip():
        raise CronError("请填写要执行的脚本")
    if style == "toUrl":
        target = ""
    task = {
        "id": task_id or 0,
        "name": name,
        "type": kind,
        "where1": str(payload.get("where1") or "1"),
        "hour": str(payload.get("hour") or "0"),
        "minute": str(payload.get("minute") or "0"),
        "sType": style,
        "sBody": body if style != "database" else "",
        "sName": target,
        "enabled": bool(payload.get("enabled", True)),
    }
    task["cron"] = cron_expr(task)
    return task


def _live_ids() -> set[int]:
    """只认当前用户 crontab 里面板标记之间的任务，不看 JSON 里的历史记录。"""
    listed = subprocess.run(["crontab", "-l"], capture_output=True, text=True, shell=False)
    text = listed.stdout if listed.returncode == 0 else ""
    if _BEGIN not in text or _END not in text:
        return set()
    block = text.split(_BEGIN, 1)[1].split(_END, 1)[0]
    found: set[int] = set()
    for line in block.splitlines():
        match = re.search(r"app\.crontab\.runner (\d+)\s*$", line.strip())
        if match:
            found.add(int(match.group(1)))
    return found


def list_tasks() -> dict:
    live = _live_ids()
    items = []
    for item in _load():
        copied = dict(item)
        copied.pop("sBody", None)
        copied["has_body"] = bool(item.get("sBody"))
        enabled = bool(item.get("enabled"))
        copied["live"] = (not enabled) or int(item["id"]) in live
        items.append(copied)
    return {"items": items}


def add_task(payload: dict) -> dict:
    with _lock:
        items = _load()
        if len(items) >= 30:
            raise CronError("最多保存 30 个计划任务")
        task_id = max((int(item["id"]) for item in items), default=0) + 1
        task = _check(payload, task_id)
        items.append(task)
        _store_script(task)
        _commit(items)
    return {"id": task["id"], "cron": task["cron"]}


def update_task(task_id: int, payload: dict) -> dict:
    with _lock:
        items = _load()
        for index, item in enumerate(items):
            if int(item["id"]) == task_id:
                task = _check(payload, task_id)
                task["enabled"] = bool(payload.get("enabled", item.get("enabled", True)))
                items[index] = task
                _store_script(task)
                _commit(items)
                return {"id": task_id}
    raise CronError("没有这个计划任务", 404)


def delete_task(task_id: int) -> dict:
    with _lock:
        items = _load()
        kept = [item for item in items if int(item["id"]) != task_id]
        if len(kept) == len(items):
            raise CronError("没有这个计划任务", 404)
        _commit(kept)
        script = _root() / "scripts" / f"{task_id}.sh"
        if script.is_file():
            script.unlink()
    return {"id": task_id}


def set_status(task_id: int, enabled: bool) -> dict:
    with _lock:
        items = _load()
        for item in items:
            if int(item["id"]) == task_id:
                item["enabled"] = bool(enabled)
                _commit(items)
                return {"id": task_id, "enabled": item["enabled"]}
    raise CronError("没有这个计划任务", 404)


def read_log(task_id: int, limit: int = 200) -> dict:
    path = _log_path(task_id)
    if not path.is_file():
        return {"lines": []}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": lines[-max(1, min(limit, 200)):]}


def start_task(task_id: int) -> dict:
    if not any(int(item["id"]) == task_id for item in _load()):
        raise CronError("没有这个计划任务", 404)
    threading.Thread(target=run_task_by_id, args=(str(task_id),), daemon=True).start()
    return {"started": True, "id": task_id}


def run_task_by_id(raw_id: str) -> None:
    if not str(raw_id).isdigit():
        return
    task_id = int(raw_id)
    task = next((item for item in _load() if int(item["id"]) == task_id), None)
    if task is None:
        return
    status = "ok"
    detail = ""
    try:
        if task["sType"] == "toUrl":
            detail = _curl(task["sBody"])
        elif task["sType"] == "database":
            from app.databases.service import backup_database

            detail = str(backup_database(task["sName"]).get("file") or "已备份")
        else:
            detail = _shell(task_id)
    except Exception as exc:
        status = "error"
        detail = str(exc)[:500]
    _append_log(task, status, detail)


def _curl(url: str) -> str:
    if not _URL.fullmatch(url):
        raise CronError("URL 无效")
    result = subprocess.run(
        ["/usr/bin/curl", "-fsS", "--max-time", "20", "--max-redirs", "0", url],
        capture_output=True,
        text=True,
        timeout=25,
        shell=False,
    )
    if result.returncode != 0:
        raise CronError(result.stderr.strip() or "URL 请求失败")
    return (result.stdout or "请求完成")[:300]


def _shell(task_id: int) -> str:
    script = _root() / "scripts" / f"{task_id}.sh"
    if not script.is_file() or script.is_symlink():
        raise CronError("脚本文件不存在")
    result = subprocess.run(
        ["/bin/bash", script.as_posix()],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=script.parent.as_posix(),
        shell=False,
    )
    output = (result.stdout or result.stderr or "执行完成").strip()
    if result.returncode != 0:
        raise CronError(output[:300] or "脚本执行失败")
    return output[:300]


def _store_script(task: dict) -> None:
    folder = _root() / "scripts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{task['id']}.sh"
    if task["sType"] != "toShell":
        if path.is_file():
            path.unlink()
        return
    path.write_text(task["sBody"].replace("\r\n", "\n"), encoding="utf-8")
    path.chmod(0o700)


def _append_log(task: dict, status: str, detail: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} task={task['id']} name={task['name']} status={status} detail={detail}\n"
    for path in (_log_path(int(task["id"])), _tasks_log()):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def _commit(items: list) -> None:
    previous = _load()
    _write(items)
    try:
        _sync(items)
    except CronError:
        _write(previous)
        raise


def _sync(items: list) -> None:
    python = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
    backend = Path(__file__).resolve().parents[2]
    lines = []
    for task in items:
        if not task.get("enabled"):
            continue
        expr = cron_expr(task)
        lines.append(
            f"{expr} cd {backend.as_posix()} && {python.as_posix()} -m app.crontab.runner {int(task['id'])}"
        )
    block = ""
    if lines:
        block = _BEGIN + "\n" + "\n".join(lines) + "\n" + _END + "\n"
    listed = subprocess.run(["crontab", "-l"], capture_output=True, text=True, shell=False)
    current = listed.stdout if listed.returncode == 0 else ""
    merged = merge_crontab(current, block)
    result = subprocess.run(["crontab", "-"], input=merged, capture_output=True, text=True, shell=False)
    if result.returncode != 0:
        raise CronError(result.stderr.strip() or "无法写入当前用户的计划任务")
    checked = subprocess.run(["crontab", "-l"], capture_output=True, text=True, shell=False)
    text = checked.stdout if checked.returncode == 0 else ""
    missing = [
        str(int(task["id"]))
        for task in items
        if task.get("enabled") and f"app.crontab.runner {int(task['id'])}" not in text
    ]
    if missing or checked.returncode != 0:
        detail = (checked.stderr or result.stderr or "").strip()
        raise CronError(detail or "计划任务写入后，crontab 里没有读回")


def merge_crontab(current: str, block: str) -> str:
    if _BEGIN in current and _END in current:
        pre, rest = current.split(_BEGIN, 1)
        _, post = rest.split(_END, 1)
        body = pre.rstrip()
        tail = post.strip()
        pieces = [piece for piece in (body, block.rstrip(), tail) if piece]
        return "\n".join(pieces) + "\n"
    if not block:
        return current if current.endswith("\n") or not current else current + "\n"
    base = current.rstrip()
    return (base + "\n" if base else "") + block
