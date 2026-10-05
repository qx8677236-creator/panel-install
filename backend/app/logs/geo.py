"""IP 地理位置只走本地库。查不到时显示未知，不把 IP 发到外部服务。"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

from app.config import DATA_DIR

_DB = DATA_DIR / "logs" / "ip2region.xdb"


def place(ip: str) -> str:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if address.version != 4 or address.is_private or address.is_loopback or address.is_reserved or address.is_link_local:
        return "内网"
    region = _search(ip)
    if not region:
        return "未知"
    parts = [item for item in region.split("|") if item and item != "0"]
    text = " ".join(parts[:3])
    return text or "未知"


@lru_cache(maxsize=1)
def _searcher():
    if not _DB.is_file() or _DB.is_symlink():
        return None
    try:
        import ip2region.searcher as xdb
        import ip2region.util as util
    except ImportError:
        return None
    try:
        return xdb.new_with_file_only(util.IPv4, str(_DB))
    except Exception:
        return None


def _search(ip: str) -> str:
    searcher = _searcher()
    if searcher is None:
        return ""
    try:
        region = searcher.search(ip)
    except Exception:
        return ""
    return region if isinstance(region, str) else ""
