"""Nginx 目录布局。

默认把站点配置放在 data/nginx，并用独立的主配置做 nginx -t。
只有显式打开系统模式，才会写 /etc/nginx 并给系统主进程发 reload。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import DATA_DIR, settings
from app.files.security import get_file_root
from app.nginx.security import SiteError, inside


@dataclass(frozen=True)
class NginxLayout:
    prefix: Path
    sites_available: Path
    sites_enabled: Path
    logs: Path
    certs: Path
    file_root: Path
    system_mode: bool


def get_layout() -> NginxLayout:
    file_root = get_file_root()
    prefix = DATA_DIR / "nginx"
    system_mode = bool(settings.nginx_system_mode)
    if system_mode:
        available = Path(settings.nginx_sites_available or "/etc/nginx/sites-available")
        enabled = Path(settings.nginx_sites_enabled or "/etc/nginx/sites-enabled")
        if not available.is_absolute() or not enabled.is_absolute():
            raise SiteError("系统 Nginx 目录必须是绝对路径")
    else:
        available = prefix / "sites-available"
        enabled = prefix / "sites-enabled"
    for folder in (available, enabled, prefix):
        resolved = folder.resolve() if folder.exists() else folder.absolute()
        if resolved == Path("/") or inside(resolved, file_root.resolve()):
            raise SiteError("Nginx 配置目录不能放在网站根目录或系统根目录")
    return NginxLayout(
        prefix=prefix,
        sites_available=available,
        sites_enabled=enabled,
        logs=prefix / "logs",
        certs=prefix / "certs",
        file_root=file_root,
        system_mode=system_mode,
    )
