"""用 psutil 采样。采集在后台任务里单点进行，避免多个连接各自算网速。"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import socket
import threading
import time
from collections import deque

import psutil

from app.commands.executor import CommandRejected, run_command

logger = logging.getLogger("panel.monitor")

# 这些文件系统不是业务磁盘，面板上不展示。
_SKIP_FS = {
    "squashfs",
    "devfs",
    "autofs",
    "devtmpfs",
    "overlay",
    "tmpfs",
    "proc",
    "sysfs",
    "nsfs",
    "cgroup",
    "cgroup2",
    "debugfs",
    "tracefs",
    "fusectl",
    "configfs",
    "bpf",
    "ramfs",
}
_LOOPBACK = {"lo", "lo0"}


def _read_platform() -> str:
    """通过白名单命令读取 uname。失败时退回 Python 自带的平台信息。"""
    try:
        result = run_command(["uname", "-s", "-r", "-m"])
    except CommandRejected:
        logger.warning("uname 未通过安全执行器，改用 platform 模块")
        return platform.platform()
    if result.returncode != 0:
        return platform.platform()
    return result.stdout.strip() or platform.platform()


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_nic: dict | None = None
        self._last_ts: float | None = None
        self._latest: dict | None = None
        self._history: deque[dict] = deque(maxlen=120)
        self._platform = _read_platform()
        # interval=None 只建立基线，不阻塞。
        psutil.cpu_percent(interval=None)

    def sample(self, prime: bool = False) -> dict:
        now = time.time()
        # 第一次用很短的阻塞采样，让 CPU 百分比有意义；之后靠两次调用的间隔。
        cpu = psutil.cpu_percent(interval=0.1 if prime else None)
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        snap = {
            "ts": now,
            "cpu_percent": cpu,
            "cpu_count": psutil.cpu_count(logical=True) or 0,
            "load": _load_average(),
            "memory": {
                "total": vm.total,
                "used": vm.used,
                "available": vm.available,
                "percent": vm.percent,
            },
            "swap": {
                "total": swap.total,
                "used": swap.used,
                "percent": swap.percent,
            },
            "disks": _disks(),
            "network": self._network(now),
            "addresses": _ipv4_addresses(),
            "hostname": socket.gethostname(),
            "platform": self._platform,
            "uptime_seconds": max(0.0, now - psutil.boot_time()),
            "boot_time": psutil.boot_time(),
            "process_count": len(psutil.pids()),
        }
        with self._lock:
            self._latest = snap
            self._history.append(snap)
        return snap

    def latest(self) -> dict | None:
        with self._lock:
            return self._latest

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)

    def _network(self, now: float) -> dict:
        counters = psutil.net_io_counters(pernic=True)
        stats = psutil.net_if_stats()
        dt = (now - self._last_ts) if self._last_ts else 0.0
        interfaces = []
        total_up = 0.0
        total_down = 0.0
        previous = self._last_nic or {}
        snapshot = {}
        for name, item in counters.items():
            snapshot[name] = (item.bytes_sent, item.bytes_recv)
            if name in _LOOPBACK:
                continue
            prev = previous.get(name)
            up = down = 0.0
            if prev is not None and dt > 0:
                up = max(0.0, (item.bytes_sent - prev[0]) / dt)
                down = max(0.0, (item.bytes_recv - prev[1]) / dt)
            total_up += up
            total_down += down
            is_up = bool(stats[name].isup) if name in stats else False
            if not is_up and up == 0 and down == 0:
                continue
            interfaces.append(
                {
                    "name": name,
                    "upload_bps": up,
                    "download_bps": down,
                    "bytes_sent": item.bytes_sent,
                    "bytes_recv": item.bytes_recv,
                    "is_up": is_up,
                }
            )
        self._last_nic = snapshot
        self._last_ts = now
        interfaces.sort(key=lambda row: row["download_bps"] + row["upload_bps"], reverse=True)
        return {
            "upload_bps": total_up,
            "download_bps": total_down,
            "interfaces": interfaces[:8],
        }


def _load_average() -> list[float]:
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except OSError:
        return [0.0, 0.0, 0.0]


def _disks() -> list[dict]:
    rows = []
    for part in psutil.disk_partitions(all=False):
        if not part.mountpoint or part.fstype in _SKIP_FS:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        if usage.total <= 0:
            continue
        rows.append(
            {
                "device": part.device,
                "mount": part.mountpoint,
                "fstype": part.fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": usage.percent,
            }
        )
    rows.sort(key=lambda row: (row["mount"] != "/", -row["total"]))
    return rows


def _ipv4_addresses() -> list[dict]:
    found = []
    for name, addresses in psutil.net_if_addrs().items():
        if name in _LOOPBACK:
            continue
        for addr in addresses:
            if addr.family == socket.AF_INET and addr.address:
                found.append({"name": name, "address": addr.address})
    return found


collector = MetricsCollector()


async def metrics_loop() -> None:
    """每秒采样一次。只允许这一处写入速率基线。"""
    first = True
    while True:
        try:
            await asyncio.to_thread(collector.sample, first)
        except Exception:
            logger.exception("采集系统指标失败")
        first = False
        await asyncio.sleep(1)
