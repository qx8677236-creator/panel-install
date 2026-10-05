"""选择站点 listen 地址。

宿主机 Nginx 直接读取 FILE_ROOT（这台机器是 /var/www/panel）。
站点根目录留在这个目录下，不改写成 /usr/share/nginx/html 或 /www/wwwroot，也不额外挂载卷。
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Optional

from app.nginx.security import SiteError

_WILDCARD = {"", "0.0.0.0", "*", "::", "[::]"}
_LOOPBACK = {"127.0.0.1", "::1"}
_TOKEN = re.compile(r"^(?:\d{1,5}|(?:\d{1,3}\.){3}\d{1,3}:\d{1,5})$")
_LISTEN = re.compile(r"^\s*listen\s+([^;]+);", re.I | re.M)
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


def parse_listeners(text: str) -> list:
    """解析 ss -lnt 表格。返回 (地址, 端口)。不打开新的监听。"""
    found = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] != "LISTEN":
            continue
        host, port = _split_endpoint(parts[3])
        if port:
            found.append((host, port))
    return found


def parse_route_ipv4(text: str) -> str:
    matched = re.search(r"\bsrc\s+(\d{1,3}(?:\.\d{1,3}){3})\b", text or "")
    if not matched:
        return ""
    ip = matched.group(1)
    if ip.startswith("127.") or not _ipv4_ok(ip):
        return ""
    return ip


def listen_tokens(text: str) -> list:
    return [item.group(1).strip() for item in _LISTEN.finditer(text or "")]


def split_listen(token: str) -> tuple:
    parts = (token or "").split()
    if not parts:
        raise SiteError("监听地址无效")
    host, port = _split_endpoint(parts[0])
    if not port:
        raise SiteError("监听地址无效")
    ssl = any(part.lower() == "ssl" for part in parts[1:])
    return host, port, ssl


def own_site_ports(ports) -> set:
    """站点自己的监听端口。反代只改 location，这些端口不能再当成冲突。"""
    owned = set()
    for port in ports or ():
        try:
            number = int(port)
        except (TypeError, ValueError):
            continue
        if not 1 <= number <= 65535:
            continue
        for wild in _WILDCARD:
            owned.add((wild, number))
    return owned


def listen_owns(text: str) -> set:
    """当前这份配置已经占用的监听，重新发布时不把它当成别人的冲突。"""
    owned = set()
    for token in listen_tokens(text):
        try:
            host, port, _ssl = split_listen(token)
        except SiteError:
            continue
        if host in _WILDCARD:
            for wild in _WILDCARD:
                owned.add((wild, port))
        else:
            owned.add((host, port))
    return owned


def decide_listen(port: int, listeners: Iterable, public_ip: str, own: Optional[Iterable] = None) -> str:
    """端口空闲时返回纯端口。只被回环占用时返回公网 IPv4。通配或公网 IP 已被占用则拒绝。"""
    port = int(port)
    if not 1 <= port <= 65535:
        raise SiteError(f"端口 {port} 无效", 400)
    owned = set(own or ())
    hosts = []
    for host, bound in listeners:
        if int(bound) != port:
            continue
        if (host, port) in owned:
            continue
        hosts.append(host)
    conflict = [host for host in hosts if host in _WILDCARD or (public_ip and host == public_ip)]
    if conflict:
        where = conflict[0] or "0.0.0.0"
        raise SiteError(f"端口 {port} 已被 {where} 占用，站点没有创建。", 400)
    others = [host for host in hosts if host not in _LOOPBACK]
    if hosts and not others:
        if not _ipv4_ok(public_ip) or public_ip.startswith("127."):
            raise SiteError(f"端口 {port} 只绑定在 127.0.0.1，但没有可用的公网地址，站点没有创建。", 400)
        return f"{public_ip}:{port}"
    if others:
        raise SiteError(f"端口 {port} 已被 {others[0]} 占用，站点没有创建。", 400)
    return str(port)


def assert_raw_listens(text: str, listeners, public_ip: str, own=None) -> None:
    tokens = listen_tokens(text)
    if not tokens:
        raise SiteError("站点配置没有 listen，站点没有创建。", 400)
    for token in tokens:
        host, port, _ssl = split_listen(token)
        chosen = decide_listen(port, listeners, public_ip, own)
        if ":" not in chosen:
            continue
        wanted = chosen.split(":", 1)[0]
        if host != wanted:
            raise SiteError(
                f"端口 {port} 已在 127.0.0.1 监听，请使用 {chosen}，不能监听 0.0.0.0。站点没有创建。",
                400,
            )


def safe_listen_token(token: str) -> str:
    if not isinstance(token, str) or not _TOKEN.fullmatch(token) or token.startswith("0.0.0.0"):
        raise SiteError("监听地址无效")
    return token


def probe_url(conf_text: str) -> str:
    """用配置里的 listen 拼出只含该站点地址的探测 URL。"""
    for token in listen_tokens(conf_text):
        try:
            host, port, ssl = split_listen(token)
        except SiteError:
            continue
        if host in _WILDCARD:
            host = "127.0.0.1"
        elif host == "::1":
            host = "[::1]"
        if not re.fullmatch(r"[0-9A-Fa-f:.\[\]]+", host):
            continue
        scheme = "https" if ssl else "http"
        return f"{scheme}://{host}:{port}/"
    return ""


def parse_http_status(output: str) -> int:
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if not lines or not re.fullmatch(r"\d{3}", lines[-1]):
        return 0
    value = int(lines[-1])
    if 100 <= value <= 599:
        return value
    return 0


def accepting(ip: str, port: int, timeout: float = 2.0) -> bool:
    import socket

    if not ip or int(port) <= 0:
        return False
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def port_visible(listeners, port: int, token: str, public_ip: str) -> bool:
    hosts = [host for host, bound in listeners if int(bound) == int(port)]
    if ":" in token:
        return token.split(":", 1)[0] in hosts
    return any(host in _WILDCARD or host == public_ip for host in hosts)


def ss_argv(fake: bool = False) -> list:
    if fake:
        return ["/usr/bin/ss", "-lnt"]
    for candidate in ("/usr/bin/ss", "/bin/ss", "/usr/sbin/ss"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return [candidate, "-lnt"]
    raise SiteError("无法读取端口监听状态", 400)


def ip_route_argv(fake: bool = False) -> list:
    if fake:
        return ["/usr/sbin/ip", "-4", "route", "get", "1.1.1.1"]
    for candidate in ("/usr/sbin/ip", "/sbin/ip", "/bin/ip"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return [candidate, "-4", "route", "get", "1.1.1.1"]
    return []


def _split_endpoint(endpoint: str) -> tuple:
    text = (endpoint or "").strip()
    if text.startswith("["):
        host, _, port_text = text.rpartition(":")
        host = host.strip("[]")
    elif ":" in text:
        host, _, port_text = text.rpartition(":")
    elif text.isdigit():
        port = int(text)
        if 1 <= port <= 65535:
            return "", port
        return "", 0
    else:
        return "", 0
    if not port_text.isdigit():
        return "", 0
    port = int(port_text)
    if not 1 <= port <= 65535:
        return "", 0
    return host, port


def _ipv4_ok(ip: str) -> bool:
    if not _IPV4.fullmatch(ip or ""):
        return False
    return all(0 <= int(part) <= 255 for part in ip.split("."))
