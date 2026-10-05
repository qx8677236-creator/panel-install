"""本机站点检查。只读配置和目录权限，不连接外部扫描器。"""

from __future__ import annotations

import os
from pathlib import Path

from app.files.security import FileOpError, PathJailError, locate
from app.nginx.layout import get_layout
from app.nginx.security import conf_stem


def scan_sites(domain: str = "") -> dict:
    from app.nginx import service

    layout = get_layout()
    listed = service.list_sites(layout).get("sites") or []
    items = []
    for site in listed:
        name = str(site.get("domain") or "")
        if domain and name != domain:
            continue
        items.extend(_one(site, layout))
    return {"items": items}


def _one(site: dict, layout) -> list[dict]:
    name = str(site.get("domain") or "")
    found = []

    def add(level: str, item: str, message: str) -> None:
        found.append({"site": name, "level": level, "item": item, "message": message})

    if not site.get("reload_ok"):
        add("medium", "配置加载", "站点配置还没有成功加载")
    root = str(site.get("root") or "")
    if root:
        try:
            path = locate(root, root=layout.file_root)
            mode = path.stat().st_mode
        except (OSError, FileOpError, PathJailError):
            add("medium", "网站目录", "网站目录不存在或无法读取")
        else:
            if mode & 0o002:
                add("high", "目录权限", "网站目录对其他用户可写")
    headers = site.get("security_headers") or {}
    if not headers.get("enabled"):
        add("low", "安全响应头", "还没有开启 X-Frame-Options 等安全响应头")
    if not site.get("ssl"):
        add("low", "HTTPS", "站点还没有启用 HTTPS")
    text = _conf_text(layout, name)
    if "autoindex on" in text:
        add("high", "目录列表", "配置打开了目录浏览")
    if "server_tokens on" in text:
        add("medium", "版本信息", "配置会返回 Nginx 版本号")
    if not found:
        add("ok", "检查完成", "没有发现需要处理的项目")
    return found


def _conf_text(layout, domain: str) -> str:
    path = Path(layout.sites_available) / f"{conf_stem(domain)}.conf"
    if not path.is_file() or path.is_symlink():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
