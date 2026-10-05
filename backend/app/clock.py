"""面板展示用北京时间。服务器时钟保持 UTC，不改系统时区。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")
_WALL = "%Y-%m-%d %H:%M:%S"


def beijing_now() -> datetime:
    return datetime.now(BEIJING)


def beijing_text(when: datetime | None = None) -> str:
    current = beijing_now() if when is None else when
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(BEIJING).strftime(_WALL)


def utc_wall_to_beijing(text: str) -> str:
    """把没有时区的 UTC 墙钟换成北京时间。解析不了就原样返回。"""
    try:
        parsed = datetime.strptime(str(text), _WALL).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return str(text or "")
    return parsed.astimezone(BEIJING).strftime(_WALL)


def server_clock_is_utc() -> bool:
    offset = datetime.now().astimezone().utcoffset()
    return offset == timedelta(0)
