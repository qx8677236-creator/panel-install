"""把面板里的周期收成标准 5 段 Cron，并给出中文说明。时间按北京时间。"""

from __future__ import annotations

import re

from app.backup.store import BackupError

_CRON = re.compile(r"^[0-9*/,\- ]+$")
_WEEKS = ("周日", "周一", "周二", "周三", "周四", "周五", "周六")


def panel_zone():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return None


def validate_cron(expr: str) -> str:
    text = " ".join(str(expr or "").split())
    if not text or not _CRON.fullmatch(text):
        raise BackupError("Cron 只能使用数字、星号、斜线、逗号和短横线")
    parts = text.split(" ")
    if len(parts) != 5:
        raise BackupError("Cron 需要 5 段：分 时 日 月 周")
    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(text, timezone=panel_zone())
    except Exception as exc:
        raise BackupError("Cron 表达式无法解析") from exc
    return text


def describe(expr: str) -> str:
    parts = expr.split(" ")
    if len(parts) != 5:
        return expr
    minute, hour, day, month, week = parts
    if month != "*":
        return expr
    if minute.startswith("*/") and minute[2:].isdigit() and hour == day == week == "*":
        return f"每 {int(minute[2:])} 分钟"
    if hour == day == week == "*" and minute.isdigit():
        return f"每小时第 {int(minute)} 分"
    if day == week == "*" and minute.isdigit() and hour.isdigit():
        return f"每天 {int(hour):02d}:{int(minute):02d}"
    if day == "*" and week.isdigit() and minute.isdigit() and hour.isdigit():
        index = int(week)
        if index > 6:
            return expr
        return f"每周{_WEEKS[index][1:]} {int(hour):02d}:{int(minute):02d}"
    if week == "*" and day.isdigit() and minute.isdigit() and hour.isdigit():
        return f"每月 {int(day)} 日 {int(hour):02d}:{int(minute):02d}"
    return expr
