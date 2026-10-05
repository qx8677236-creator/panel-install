"""站点的创建、开关、伪静态、反代和证书。

每次写入配置后先执行 nginx -t。检查失败就把文件和启用链接恢复回去，
不会把一台机器上的 Web 服务重载到错误配置上。
"""

from __future__ import annotations

import copy
import html
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

from app.files.security import FileOpError, PathJailError, display_path, locate, raise_jail
from app.nginx.layout import NginxLayout, get_layout
from app.nginx.listen import (
    accepting,
    assert_raw_listens,
    decide_listen,
    ip_route_argv,
    listen_owns,
    own_site_ports,
    parse_http_status,
    parse_listeners,
    parse_route_ipv4,
    port_visible,
    probe_url,
    ss_argv,
)
from app.nginx.render import (
    confirm_site_certs,
    password_path,
    render_main,
    render_site,
    rewrite_document,
    rewrite_path,
)
from app.nginx.runner import (
    build_certbot_argv,
    find_certbot,
    find_nginx,
    install_public_site,
    nginx_reload,
    nginx_test,
    prepare_system_nginx,
    run_command,
    using_fake_runner,
)
from app.nginx.confparse import parse_site_files
from app.nginx.proxyparse import parse_proxy_rules
from app.nginx.headers import normalize_security_headers, present_headers
from app.nginx.security import (
    SiteError,
    apr1_hash,
    audit_raw_config,
    compile_rewrite,
    confirm_cert,
    conf_stem,
    default_root_name,
    inside,
    normalize_domain,
    parse_domain_port,
    normalize_email,
    rewrite_templates,
    validate_auth_name,
    validate_bindings,
    validate_domains,
    validate_hotlink,
    validate_limit,
    validate_pem,
    validate_proxies,
    validate_realm,
    validate_redirects,
)

logger = logging.getLogger("panel.nginx")
_lock = threading.Lock()


def ensure_nginx_layout() -> None:
    with _lock:
        _ensure(get_layout())


def list_sites(layout: Optional[NginxLayout] = None) -> dict:
    with _lock:
        layout = layout or get_layout()
        _ensure(layout)
        state = _load(layout)
        sites = []
        for key in sorted(state):
            view = _present(state[key])
            view["traffic"] = _traffic_text(layout, key)
            sites.append(view)
        binary = find_nginx()
        version = "nginx"
        if binary:
            code, output = run_command([binary, "-v"], timeout=5)
            if code == 0 and output:
                version = output.replace("nginx version:", "").strip() or "nginx"
        return {
            "sites": sites,
            "config_dir": str(layout.sites_available),
            "system_mode": layout.system_mode,
            "nginx_found": binary is not None,
            "nginx_version": version,
            "templates": rewrite_templates(),
        }


def _traffic_text(layout: NginxLayout, domain: str) -> str:
    path = layout.logs / f"{conf_stem(domain)}.access.log"
    if not path.is_file() or path.is_symlink():
        return "0 B"
    size = path.stat().st_size
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _server_name(site: dict) -> str:
    for item in site.get("domains") or []:
        if isinstance(item, dict) and item.get("domain"):
            return normalize_domain(str(item["domain"]))
    return parse_domain_port(site.get("domain") or "")["domain"]


def _listen_ports(site: dict) -> set:
    ports = set()
    for item in site.get("domains") or []:
        if isinstance(item, dict) and item.get("port"):
            ports.add(int(item["port"]))
    if not ports:
        ports.add(parse_domain_port(site.get("domain") or "")["port"])
    return ports


def _resolve(state: dict, raw: str) -> str:
    """同一个 IP 的不同端口是不同站点。没写端口时，只有一条记录才直接命中。"""
    text = str(raw).strip().lower().rstrip(".")
    if text in state:
        return text
    parsed = parse_domain_port(text)
    label = str(parsed.get("port_text") or parsed["port"])
    key = parsed["domain"] if parsed["port"] == 80 and label == "80" else f"{parsed['domain']}:{label}"
    if key in state:
        return key
    matches = [stored for stored, site in state.items() if _server_name(site) == parsed["domain"]]
    explicit = ":" in text and text.rsplit(":", 1)[-1].isdigit()
    if explicit:
        by_port = [stored for stored in matches if parsed["port"] in _listen_ports(state[stored])]
        if len(by_port) == 1:
            return by_port[0]
        if not by_port:
            raise SiteError("站点不存在", 404)
    if len(matches) == 1 and not explicit:
        return matches[0]
    if not matches:
        raise SiteError("站点不存在", 404)
    raise SiteError("这个地址有多个端口，请带上端口", 404)


def site_detail(domain: str, layout: Optional[NginxLayout] = None) -> dict:
    with _lock:
        layout = layout or get_layout()
        _ensure(layout)
        state = _load(layout)
        domain = _resolve(state, domain)
        site = state.get(domain)
        if not site:
            raise SiteError("站点不存在", 404)
        view = _present(site)
        path = _available(layout, domain)
        if path.is_file() and not path.is_symlink():
            view["config"] = path.read_text(encoding="utf-8")[-20000:]
        else:
            view["config"] = ""
        _overlay_live(view, _live(layout, site))
        view["error_log"] = _read_error_log(layout, domain)
        return view


def diagnose_site(domain: str, layout: Optional[NginxLayout] = None) -> dict:
    """检查这一份站点：nginx -t、本机 listen 地址的响应，以及它自己的错误日志。"""
    try:
        with _lock:
            layout = layout or get_layout()
            _ensure(layout)
            state = _load(layout)
            key = _resolve(state, domain)
            if key not in state:
                raise SiteError("站点不存在", 404)
            conf = _read_public_conf(key)
            if not conf:
                path = _available(layout, key)
                if path.is_file() and not path.is_symlink():
                    conf = path.read_text(encoding="utf-8", errors="replace")
            code, output = nginx_test(layout, str((layout.prefix / "nginx.conf").resolve()))
            status = 0
            target = probe_url(conf)
            if target:
                status = _curl_status(target)
            return {
                "ok": code == 0 and status > 0,
                "nginx_test": _safe_text(output),
                "http_status": status,
                "error_log": _diagnose_log(layout, key, conf),
            }
    except SiteError:
        raise
    except Exception:
        raise SiteError("诊断失败", 400) from None


def create_site(domain: str, root: str = "", kind: str = "html", layout: Optional[NginxLayout] = None) -> dict:
    parsed = parse_domain_port(domain)
    name = parsed["domain"]
    port = parsed["port"]
    label = str(parsed.get("port_text") or port)
    plain = port == 80 and label == "80"
    key = name if plain else f"{name}:{label}"
    allowed = {"php", "java", "node", "go", "python", "net", "proxy", "html", "other"}
    kind = kind if kind in allowed else "html"
    with _lock:
        layout = layout or get_layout()
        _ensure(layout)
        state = _load(layout)
        for existing in state.values():
            if _server_name(existing) == name and port in _listen_ports(existing):
                raise SiteError("站点已存在", 409)
        if key in state:
            raise SiteError("站点已存在", 409)
        fallback = default_root_name(name)
        if not plain:
            fallback = f"{fallback}_{label}"
        relative = _ensure_web_root(layout, root.strip() or fallback, name if plain else f"{name}:{label}")
        site = {
            "domain": key,
            "root": relative,
            "enabled": True,
            "ssl": False,
            "preset": "none",
            "rewrite_custom": "",
            "rewrite_body": "try_files $uri $uri/ /index.html;",
            "proxies": [],
            "domains": [{"domain": name, "port": label}],
            "kind": kind,
            "note": "",
            "bindings": [],
            "force_https": True,
            "auth": {"enabled": False, "realm": "Restricted", "users": []},
            "limit": {"conn": 0, "rate": 0},
            "hotlink": {"enabled": False, "domains": []},
            "redirects": [],
            "raw_mode": False,
            "raw_config": "",
            "cert_path": "",
            "key_path": "",
            "backup_at": "",
            "expires_at": "",
            "created_at": _now(),
            "last_log": "",
            "checked": False,
            "reload_ok": False,
        }
        _publish(layout, site)
        state[key] = site
        _save(layout, state)
        return _present(site)


def set_enabled(domain: str, enabled: bool, layout: Optional[NginxLayout] = None) -> dict:
    """启用时把配置链入 sites-enabled，停止时移走链接，配置原文仍留在 sites-available。"""

    def change(site, _layout):
        site["enabled"] = bool(enabled)

    return _mutate(domain, change, layout)


def update_rewrite(domain: str, preset: str, custom: str, layout: Optional[NginxLayout] = None) -> dict:
    chosen, body, stored = compile_rewrite(preset, custom or "")

    def change(site, _layout):
        if any((item.get("path") or "/") == "/" for item in site.get("proxies") or []):
            raise SiteError("根路径已经交给反向代理，伪静态不会写入配置")
        site["raw_mode"] = False
        site["preset"] = chosen
        site["rewrite_body"] = body
        site["rewrite_custom"] = stored

    view = _mutate(domain, change, layout)
    return _echo(
        view["domain"],
        lambda live: (live.get("rewrite_body") or "") == body,
        "伪静态已写入，但回读结果和保存内容不一致",
        layout=layout,
    )


def update_proxies(domain: str, proxies, layout: Optional[NginxLayout] = None) -> dict:
    cleaned = validate_proxies(proxies)

    def change(site, _layout):
        site["raw_mode"] = False
        site["proxies"] = cleaned
        site["_keep_listen"] = True

    view = _mutate(domain, change, layout)
    return _echo(
        view["domain"],
        lambda live: _proxy_signature(cleaned) == _proxy_signature(live.get("proxies") or []),
        "反向代理已写入，但回读结果和保存内容不一致",
        layout=layout,
    )


def proxy_config(domain: str, layout: Optional[NginxLayout] = None) -> dict:
    with _lock:
        layout = layout or get_layout()
        _ensure(layout)
        state = _load(layout)
        domain = _resolve(state, domain)
        if domain not in state:
            raise SiteError("站点不存在", 404)
        rules = _live(layout, state[domain]).get("proxies") or []
        return {"domain": domain, "rules": rules}


def _proxy_signature(items) -> list:
    signed = []
    for item in items:
        signed.append(
            (
                item.get("name") or "proxy",
                item.get("path") or "/",
                item.get("target_url") or item.get("upstream") or "",
                bool(item.get("forward_ip", True)),
            )
        )
    return signed


def _rules_from_available(domain: str) -> list:
    layout = get_layout()
    path = _available(layout, domain)
    if not path.is_file() or path.is_symlink():
        return []
    return parse_proxy_rules(path.read_text(encoding="utf-8"))


def _rules_from_files(layout: NginxLayout, domain: str) -> list:
    available = _rules_from_available(domain)
    if available:
        return available
    stem = conf_stem(domain)
    public = Path("/etc/nginx/conf.d") / f"panel-{stem}.conf"
    try:
        if public.is_file() and not public.is_symlink() and public.resolve().parent == Path("/etc/nginx/conf.d"):
            return parse_proxy_rules(public.read_text(encoding="utf-8"))
    except OSError:
        return []
    return []


def _support_text(path: Path) -> str:
    try:
        if path.is_file() and not path.is_symlink():
            return path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return ""


def _conf_text(layout: NginxLayout, domain: str) -> str:
    public = Path("/etc/nginx/conf.d") / f"panel-{conf_stem(domain)}.conf"
    for path in (public, _available(layout, domain)):
        try:
            if not path.is_file() or path.is_symlink():
                continue
            if path == public and path.resolve().parent != Path("/etc/nginx/conf.d"):
                continue
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


def _live(layout: NginxLayout, site: dict) -> dict:
    try:
        root = locate(site.get("root") or "", root=layout.file_root).as_posix()
    except (OSError, SiteError, FileOpError, PathJailError):
        root = ""
    return parse_site_files(
        _conf_text(layout, site["domain"]),
        primary=site["domain"],
        primary_root=root,
        rewrite_text=_support_text(rewrite_path(layout, site["domain"])),
        password_text=_support_text(password_path(layout, site["domain"])),
        file_root=layout.file_root.as_posix(),
    )


def _absorb(site: dict, live: dict) -> None:
    """保存前先用配置文件里的值覆盖内存，避免改一项时把文件里的其他指令写丢。"""
    if live.get("domains"):
        site["domains"] = live["domains"]
    site["bindings"] = live.get("bindings") or []
    site["proxies"] = live.get("proxies") or []
    site["limit"] = live.get("limit") or {"conn": 0, "rate": 0}
    site["hotlink"] = live.get("hotlink") or {"enabled": False, "domains": []}
    site["redirects"] = live.get("redirects") or []
    site["security_headers"] = live.get("security_headers") or {"enabled": False}
    if live.get("rewrite_body"):
        site["rewrite_body"] = live["rewrite_body"]
        site["preset"] = live.get("preset") or "custom"
        site["rewrite_custom"] = live.get("rewrite_custom") or ""


def _overlay_live(view: dict, live: dict) -> None:
    for key in (
        "domains",
        "bindings",
        "proxies",
        "rewrite_body",
        "rewrite_custom",
        "preset",
        "limit",
        "auth",
        "hotlink",
        "redirects",
        "ssl",
        "force_https",
        "security_headers",
        "index_files",
    ):
        view[key] = live[key]
    if live.get("root"):
        view["root"] = live["root"]


def _auth_same(live: dict, saved: dict) -> bool:
    if bool(live.get("enabled")) != bool(saved.get("enabled")):
        return False
    if not saved.get("enabled"):
        return not live.get("realm") and not live.get("users")
    names = [item["name"] if isinstance(item, dict) else item for item in saved.get("users") or []]
    return live.get("realm") == saved.get("realm") and list(live.get("users") or []) == names


def _headers_same(live: dict, saved: dict) -> bool:
    got = dict(live.get("security_headers") or {})
    if not saved.get("enabled"):
        return not got.get("enabled")
    expected = dict(saved)
    if not live.get("ssl"):
        expected["hsts"] = False
    return got == expected


def set_ssl(domain: str, enabled: bool, force_https=None, layout: Optional[NginxLayout] = None) -> dict:
    def change(site, current):
        site["raw_mode"] = False
        if force_https is not None:
            site["force_https"] = bool(force_https)
        if enabled:
            cert, key = resolve_certs(current, site["domain"])
            site["ssl"] = True
            site["cert_path"] = cert.as_posix()
            site["key_path"] = key.as_posix()
            site["expires_at"] = _cert_expiry(cert)
            return
        site["ssl"] = False
        site["cert_path"] = ""
        site["key_path"] = ""
        site["expires_at"] = ""

    view = _mutate(domain, change, layout)
    return _echo(
        view["domain"],
        lambda live: bool(live.get("ssl")) == bool(enabled),
        "HTTPS 已写入，但回读结果和保存内容不一致",
        layout=layout,
    )


def _echo(domain: str, check, message: str, layout: Optional[NginxLayout] = None) -> dict:
    live = site_detail(domain, layout)
    live["rules"] = live.get("proxies") or []
    if not check(live):
        raise SiteError(message)
    return live


def update_domains(domain: str, domains, layout: Optional[NginxLayout] = None) -> dict:
    chosen_box: dict = {}

    def change(site, _layout):
        site["raw_mode"] = False
        chosen = validate_domains(site["domain"], domains)
        taken = {item["domain"] for item in site.get("bindings") or []}
        if taken & {item["domain"] for item in chosen}:
            raise SiteError("域名已经用于子目录绑定")
        chosen_box["items"] = chosen
        site["domains"] = chosen

    view = _mutate(domain, change, layout)
    expected = {(item["domain"], int(item["port"])) for item in chosen_box["items"]}
    return _echo(
        view["domain"],
        lambda live: {(item["domain"], int(item["port"])) for item in live.get("domains") or []} == expected,
        "域名已写入，但回读的 server_name 和保存内容不一致",
        layout=layout,
    )


def update_bindings(domain: str, bindings, layout: Optional[NginxLayout] = None) -> dict:
    chosen_box: dict = {}

    def change(site, current):
        site["raw_mode"] = False
        cleaned = validate_bindings(bindings, site["domain"])
        names = {
            item["domain"] if isinstance(item, dict) else str(item)
            for item in (site.get("domains") or [{"domain": site["domain"]}])
        }
        if names & {item["domain"] for item in cleaned}:
            raise SiteError("这个域名已经绑定在主站上")
        for item in cleaned:
            _ensure_web_root(current, f"{site['root']}/{item['subdir']}", item["domain"])
        site["bindings"] = cleaned
        chosen_box["items"] = cleaned

    view = _mutate(domain, change, layout)
    expected = {(item["domain"], item["subdir"]) for item in chosen_box["items"]}
    return _echo(
        view["domain"],
        lambda live: {(item["domain"], item["subdir"]) for item in live.get("bindings") or []} == expected,
        "子目录绑定已写入，但回读结果和保存内容不一致",
        layout=layout,
    )


def update_access(domain: str, enabled: bool, realm: str, users, layout: Optional[NginxLayout] = None) -> dict:
    chosen_box: dict = {}

    def change(site, _layout):
        site["raw_mode"] = False
        old = {user["name"]: user["hash"] for user in (site.get("auth") or {}).get("users") or []}
        stored = []
        seen = set()
        for item in users or []:
            name = validate_auth_name(item.get("name"))
            if name in seen:
                raise SiteError("访问限制的用户名重复")
            seen.add(name)
            password = str(item.get("password") or "")
            if password:
                stored.append({"name": name, "hash": apr1_hash(password)})
            elif name in old:
                stored.append({"name": name, "hash": old[name]})
            else:
                raise SiteError("新用户必须设置密码")
        if enabled and not stored:
            raise SiteError("开启访问限制前请先添加用户")
        site["auth"] = {"enabled": bool(enabled), "realm": validate_realm(realm) if enabled else "", "users": stored}
        chosen_box["auth"] = site["auth"]

    view = _mutate(domain, change, layout)
    return _echo(view["domain"], lambda live: _auth_same(live.get("auth") or {}, chosen_box["auth"]), "访问限制已写入，但回读结果和保存内容不一致", layout=layout)


def update_limit(domain: str, limit, layout: Optional[NginxLayout] = None) -> dict:
    cleaned = validate_limit(limit or {})

    def change(site, _layout):
        site["raw_mode"] = False
        site["limit"] = cleaned

    view = _mutate(domain, change, layout)
    return _echo(
        view["domain"],
        lambda live: (int(live["limit"]["conn"]), int(live["limit"]["rate"])) == (cleaned["conn"], cleaned["rate"]),
        "流量限制已写入，但回读结果和保存内容不一致",
        layout=layout,
    )


def update_hotlink(domain: str, hotlink, layout: Optional[NginxLayout] = None) -> dict:
    cleaned = validate_hotlink(hotlink or {})

    def change(site, _layout):
        site["raw_mode"] = False
        site["hotlink"] = cleaned

    view = _mutate(domain, change, layout)
    return _echo(
        view["domain"],
        lambda live: (not cleaned["enabled"] and not live["hotlink"]["enabled"])
        or (bool(live["hotlink"]["enabled"]) and list(live["hotlink"]["domains"]) == cleaned["domains"]),
        "防盗链已写入，但回读结果和保存内容不一致",
        layout=layout,
    )


def update_security_headers(domain: str, headers, layout: Optional[NginxLayout] = None) -> dict:
    cleaned = normalize_security_headers(headers or {})

    def change(site, _layout):
        site["raw_mode"] = False
        site["security_headers"] = cleaned

    view = _mutate(domain, change, layout)
    return _echo(
        view["domain"],
        lambda live: _headers_same(live, cleaned),
        "安全响应头已写入，但回读结果和保存内容不一致",
        layout=layout,
    )


def update_redirects(domain: str, redirects, layout: Optional[NginxLayout] = None) -> dict:
    cleaned = validate_redirects(redirects or [])

    def change(site, _layout):
        site["raw_mode"] = False
        site["redirects"] = cleaned

    view = _mutate(domain, change, layout)
    return _echo(
        view["domain"],
        lambda live: live.get("redirects") == cleaned,
        "重定向已写入，但回读结果和保存内容不一致",
        layout=layout,
    )


def save_certificate_text(
    domain: str,
    certificate: str,
    key: str,
    force_https: bool = True,
    layout: Optional[NginxLayout] = None,
) -> dict:
    certificate, key = validate_pem(certificate, key)

    def change(site, current):
        site["raw_mode"] = False
        folder = current.certs / conf_stem(site["domain"])
        folder.mkdir(parents=True, exist_ok=True)
        cert = folder / "fullchain.pem"
        private = folder / "privkey.pem"
        cert.write_text(certificate, encoding="utf-8")
        private.write_text(key, encoding="utf-8")
        os.chmod(private, 0o600)
        site["ssl"] = True
        site["force_https"] = bool(force_https)
        site["cert_path"] = cert.as_posix()
        site["key_path"] = private.as_posix()
        site["expires_at"] = _cert_expiry(cert)

    return _mutate(domain, change, layout)


def save_raw_config(domain: str, text: str, layout: Optional[NginxLayout] = None) -> dict:
    def change(site, current):
        site["raw_config"] = audit_raw_config(text, current)
        site["raw_mode"] = True

    return _mutate(domain, change, layout)


def read_site_log(domain: str, kind: str, offset: int = 0, layout: Optional[NginxLayout] = None) -> dict:
    if kind not in {"access", "error"}:
        raise SiteError("日志类型无效")
    with _lock:
        layout = layout or get_layout()
        _ensure(layout)
        domain = _resolve(_load(layout), domain)
        path = layout.logs / f"{conf_stem(domain)}.{kind}.log"
        if not path.is_file() or path.is_symlink():
            return {"text": "", "offset": 0, "size": 0}
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return {"text": "", "offset": 0, "size": 0}
        if not inside(resolved, layout.logs.resolve()):
            raise SiteError("日志路径无效")
        size = path.stat().st_size
        start = offset if 0 <= offset <= size else max(0, size - 16000)
        with path.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read(16000)
        return {"text": chunk.decode("utf-8", errors="replace"), "offset": start + len(chunk), "size": size}


def delete_site(
    domain: str,
    delete_files: bool = False,
    delete_database: bool = False,
    confirm: str = "",
    operator: str = "",
    ip: str = "",
    layout: Optional[NginxLayout] = None,
) -> dict:
    """彻底删除一个站点。确认串必须等于站点键，路径不合法时什么都不改。"""
    with _lock:
        layout = layout or get_layout()
        _ensure(layout)
        state = _load(layout)
        requested = str(domain).strip()
        if requested == _STUCK_SITE_KEY and requested in state:
            key = requested
        else:
            key = _resolve(state, domain)
        site = state.get(key)
        if not site:
            raise SiteError("站点不存在", 404)
        if confirm != key and not (key == _STUCK_SITE_KEY and confirm.strip() == _STUCK_SITE_KEY):
            raise SiteError("请输入站点域名", 400)
        notes = []
        if delete_files:
            root_text = str(site.get("root") or "")
            try:
                assert_deletable_site_root(root_text, layout.file_root)
            except PathJailError:
                if key != _STUCK_SITE_KEY:
                    raise
                delete_files = False
                notes.append("站点目录不在网站根目录内，已跳过目录删除")
        _remove_nginx_for_delete(layout, key, notes)
        if delete_files:
            _delete_site_files(site, layout)
        _delete_site_logs(layout, key)
        if delete_database:
            binding = _bound_database(site)
            if binding is None:
                notes.append("没有关联数据库")
            else:
                try:
                    _drop_bound_database(binding)
                except Exception as exc:
                    logger.warning("站点 %s 的数据库删除失败，保留面板记录: %s", key, exc)
                    raise SiteError("nginx 已移除但数据库删除失败，站点记录保留", 400) from None
        state.pop(key, None)
        _save(layout, state)
        try:
            from app.backup.store import forget_site_tasks

            forget_site_tasks(key)
        except Exception:
            logger.exception("清理站点备份任务失败")
        try:
            _delete_site_certs(layout, key, site)
        except OSError:
            logger.exception("清理站点证书失败")
        from app.logs.store import write_log

        write_log(
            operator or "管理员",
            "网站管理",
            f"管理员彻底删除了站点 [{key}]，并清理了相关残留",
            ip or "unknown",
        )
        message = "站点已删除"
        if notes:
            message = message + "。" + "。".join(notes)
        return {"domain": key, "deleted": True, "message": message, "notes": notes}


def issue_certificate(domain: str, email: str, layout: Optional[NginxLayout] = None) -> dict:
    email = normalize_email(email)
    with _lock:
        layout = layout or get_layout()
        _ensure(layout)
        state = _load(layout)
        domain = _resolve(state, domain)
        current = state.get(domain)
        if not current:
            raise SiteError("站点不存在", 404)
        binary = find_certbot()
        if not binary:
            raise SiteError("未找到 certbot，无法申请证书")
        webroot = locate(current["root"], root=layout.file_root)
        code, output = run_command(build_certbot_argv(binary, _server_name(current), webroot, email), timeout=180)
        if code != 0:
            raise SiteError("证书申请失败", 400, log=output)
        try:
            cert, key = resolve_certs(layout, domain)
        except SiteError:
            raise SiteError(
                "证书命令已结束，但没有在允许的目录里找到证书文件。",
                400,
                log=output,
            ) from None
        updated = copy.deepcopy(current)
        updated["raw_mode"] = False
        updated["ssl"] = True
        updated["force_https"] = True
        updated["cert_path"] = cert.as_posix()
        updated["key_path"] = key.as_posix()
        updated["expires_at"] = _cert_expiry(cert)
        try:
            _publish(layout, updated)
        except SiteError as exc:
            _remember_failure(layout, state, domain, current, exc)
            raise
        updated["last_log"] = (output + "\n" + (updated.get("last_log") or "")).strip()
        state[domain] = updated
        _save(layout, state)
        return _present(updated)


def resolve_certs(layout: NginxLayout, domain: str):
    parsed = parse_domain_port(domain)
    stem = conf_stem(domain)
    live_name = parsed["domain"][2:] if parsed["domain"].startswith("*.") else parsed["domain"]
    pairs = [
        (layout.certs / stem / "fullchain.pem", layout.certs / stem / "privkey.pem"),
        (
            Path("/etc/letsencrypt/live") / live_name / "fullchain.pem",
            Path("/etc/letsencrypt/live") / live_name / "privkey.pem",
        ),
    ]
    for cert, key in pairs:
        try:
            present = cert.is_file() and key.is_file()
        except OSError:
            continue
        if present:
            return confirm_cert(cert, layout.certs), confirm_cert(key, layout.certs)
    raise SiteError("还没有可用的证书。可以先申请，或把 fullchain.pem 和 privkey.pem 放到面板的证书目录。")


def _mutate(domain: str, change, layout: Optional[NginxLayout]) -> dict:
    with _lock:
        layout = layout or get_layout()
        _ensure(layout)
        state = _load(layout)
        domain = _resolve(state, domain)
        current = state.get(domain)
        if not current:
            raise SiteError("站点不存在", 404)
        updated = copy.deepcopy(current)
        _absorb(updated, _live(layout, updated))
        change(updated, layout)
        try:
            _publish(layout, updated)
        except SiteError as exc:
            _remember_failure(layout, state, domain, current, exc)
            raise
        state[domain] = updated
        _save(layout, state)
        return _present(updated)


def _remember_failure(layout, state, domain, current, exc: SiteError) -> None:
    if not exc.log:
        return
    current["last_log"] = exc.log
    state[domain] = current
    _save(layout, state)


def _existing_conf_text(layout: NginxLayout, domain: str) -> str:
    chunks = []
    available = _available(layout, domain)
    if available.is_file() and not available.is_symlink():
        chunks.append(available.read_text(encoding="utf-8", errors="replace"))
    public = _read_public_conf(domain)
    if public:
        chunks.append(public)
    return "\n".join(chunks)


def _public_conf_path(domain: str) -> Path:
    return Path("/etc/nginx/conf.d") / f"panel-{conf_stem(domain)}.conf"


def _read_public_conf(domain: str) -> str:
    path = _public_conf_path(domain)
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        if path.resolve().parent != Path("/etc/nginx/conf.d"):
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _listen_plan(site: dict, previous_text: str, keep_listen: bool = False) -> dict:
    if not site.get("enabled"):
        return {}
    fake = using_fake_runner()
    code, output = run_command(ss_argv(fake), 5)
    if code != 0:
        raise SiteError("无法读取端口监听状态，站点没有创建。", 400, log=_safe_text(output))
    listeners = parse_listeners(output)
    public_ip = ""
    route = ip_route_argv(fake)
    if route:
        ip_code, ip_out = run_command(route, 5)
        if ip_code == 0:
            public_ip = parse_route_ipv4(ip_out)
    owned = listen_owns(previous_text)
    ports = set(_listen_ports(site))
    if site.get("ssl"):
        ports.add(443)
        if site.get("force_https", True):
            ports.add(80)
    if not ports:
        ports.add(parse_domain_port(site.get("domain") or "")["port"])
    if keep_listen:
        owned |= own_site_ports(ports)
    if site.get("raw_mode") and site.get("raw_config"):
        assert_raw_listens(site["raw_config"], listeners, public_ip, owned)
        return {}
    return {port: decide_listen(port, listeners, public_ip, owned) for port in sorted(ports)}


def _confirm_public(plan: dict) -> None:
    route = ip_route_argv(using_fake_runner())
    public_ip = ""
    if route:
        ip_code, ip_out = run_command(route, 5)
        if ip_code == 0:
            public_ip = parse_route_ipv4(ip_out)
    if not public_ip:
        raise SiteError("发布后无法确认公网地址，已回滚。", 400)
    pending = []
    for _attempt in range(5):
        code, output = run_command(ss_argv(using_fake_runner()), 5)
        if code != 0:
            raise SiteError("发布后无法读取端口监听状态，已回滚。", 400, log=_safe_text(output))
        listeners = parse_listeners(output)
        pending = []
        for port, token in plan.items():
            connect_ip = token.split(":", 1)[0] if ":" in str(token) else public_ip
            if not port_visible(listeners, port, str(token), public_ip) or not accepting(connect_ip, port):
                pending.append(port)
        if not pending:
            return
        time.sleep(0.2)
    names = "、".join(str(port) for port in pending)
    raise SiteError(f"端口 {names} 没有在公网地址 {public_ip} 上接受连接，已回滚。", 400)


def _rollback_public(layout: NginxLayout, domain: str, available: Path, old_conf: Optional[str], snapshot: str) -> None:
    try:
        if snapshot:
            available.parent.mkdir(parents=True, exist_ok=True)
            available.write_text(snapshot, encoding="utf-8")
            os.chmod(available, 0o644)
            install_public_site(layout, domain, True)
            if old_conf is None:
                if available.is_file() and not available.is_symlink():
                    available.unlink()
            else:
                available.write_text(old_conf, encoding="utf-8")
            return
        install_public_site(layout, domain, False)
    except Exception:
        logger.exception("回滚系统 Nginx 失败")


def _curl_status(url: str) -> int:
    binary = "/usr/bin/curl" if os.path.isfile("/usr/bin/curl") else ""
    if not binary:
        return 0
    _code, output = run_command(
        [binary, "-I", "-sS", "--max-time", "3", "-o", "/dev/null", "-w", "%{http_code}", url],
        5,
    )
    return parse_http_status(output)


def _safe_text(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        if "PRIVATE KEY" in line or "BEGIN " in line:
            continue
        if "Traceback (most recent call last)" in line:
            break
        lines.append(line)
    return "\n".join(lines[-20:])


def _diagnose_log(layout: NginxLayout, domain: str, conf_text: str) -> str:
    stem = conf_stem(domain)
    paths = [layout.logs / f"{stem}.error.log"]
    for matched in re.finditer(r"error_log\s+([^;]+);", conf_text or ""):
        raw = matched.group(1).strip().strip("\"'")
        if raw:
            paths.append(Path(raw))
    for path in paths:
        text = _allowed_log(layout, path, stem)
        if text:
            return "\n".join(_safe_text(text).splitlines()[-20:])
    return ""


def _allowed_log(layout: NginxLayout, path: Path, stem: str) -> str:
    if not path.is_file() or path.is_symlink():
        return ""
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return ""
    if resolved.suffix in {".pem", ".key"}:
        return ""
    name = resolved.name
    in_panel = inside(resolved, layout.logs.resolve()) and stem in name and name.endswith(".error.log")
    dedicated = stem in name and "error" in name and name.endswith(".log")
    if not in_panel and not dedicated:
        return ""
    return path.read_bytes()[-16000:].decode("utf-8", errors="replace")


def _settle_host_panel_confs(layout: NginxLayout) -> None:
    """清掉宿主机上已经没有面板记录的 panel- 站点配置。

    只处理 /etc/nginx/conf.d/panel-*.conf。其他配置文件不动。
    仍在账本里的站点如果缺了伪静态文件，补一个空文件，避免 nginx -t 被旧引用卡住。
    """
    if not layout.system_mode or os.geteuid() != 0:
        return
    conf_d = Path("/etc/nginx/conf.d")
    try:
        if not conf_d.is_dir() or conf_d.is_symlink():
            return
        if conf_d.resolve() != Path("/etc/nginx/conf.d").resolve():
            return
    except OSError:
        return
    try:
        state = _load(layout)
    except SiteError:
        state = {}
    live = set()
    for key in state:
        try:
            live.add(conf_stem(str(key)))
        except SiteError:
            continue
    texts = []
    for item in list(conf_d.iterdir()):
        name = item.name
        if not name.startswith("panel-") or not name.endswith(".conf"):
            continue
        if item.is_symlink() or not item.is_file():
            continue
        stem = name[len("panel-"):-len(".conf")]
        if not stem or stem in {".", ".."} or "/" in stem or ".." in stem:
            continue
        try:
            if item.parent.resolve() != conf_d.resolve():
                continue
        except OSError:
            continue
        if stem not in live:
            item.unlink()
            backup = conf_d / (name + ".bak")
            if backup.is_file() and not backup.is_symlink():
                try:
                    if backup.parent.resolve() == conf_d.resolve():
                        backup.unlink()
                except OSError:
                    pass
            continue
        try:
            texts.append(item.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    if texts:
        _materialize_rewrite_includes(layout, texts)


def _publish(layout: NginxLayout, site: dict) -> None:
    """先把候选配置写到临时文件并 nginx -t。通过之后才覆盖正式文件并重载。"""
    _ensure(layout)
    _ensure_nginx_dirs(layout)
    _fill(site)
    keep_listen = bool(site.pop("_keep_listen", False))
    domain = str(site["domain"]).strip().lower().rstrip(".")
    parse_domain_port(domain)
    _settle_host_panel_confs(layout)
    if site.get("ssl"):
        confirm_site_certs(site, layout)
    available = _available(layout, domain)
    previous_text = _existing_conf_text(layout, domain)
    plan = _listen_plan(site, previous_text, keep_listen)
    text = render_site(site, layout, plan)
    enabled = _enabled(layout, domain)
    if available.is_symlink():
        raise SiteError("站点配置不能是符号链接")
    if enabled.exists() and not enabled.is_symlink():
        raise SiteError("启用目录里已有同名文件，无法切换站点")
    rewrite = rewrite_path(layout, domain)
    passwords = password_path(layout, domain)
    old_rewrite = rewrite.read_text(encoding="utf-8") if rewrite.is_file() else None
    old_passwords = passwords.read_text(encoding="utf-8") if passwords.is_file() else None
    old_conf = available.read_text(encoding="utf-8") if available.is_file() else None
    previous_link = os.readlink(enabled) if enabled.is_symlink() else None
    staged = layout.prefix / "pending" / available.name
    installed = False
    wrote_support = False
    public_installed = False
    public_snapshot = _read_public_conf(domain)
    try:
        if not site.get("raw_mode"):
            _write_support(site, rewrite, passwords)
            wrote_support = True
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(text, encoding="utf-8")
        test_main = _prepare_test_config(layout, domain, staged)
        code, output = nginx_test(layout, str(test_main))
        if code != 0:
            logger.warning("Nginx 配置检查失败，正式配置保持不变 domain=%s", domain)
            raise SiteError("Nginx 配置检查失败，已回滚。原来的配置仍然有效。", 400, log=output)
        if old_conf is not None:
            _backup(available).write_text(old_conf, encoding="utf-8")
        available.write_text(text, encoding="utf-8")
        installed = True
        os.chmod(available, 0o644)
        if enabled.is_symlink():
            enabled.unlink()
        if site.get("enabled"):
            os.symlink(str(available.resolve()), enabled)
        if layout.system_mode:
            system_code, system_out = nginx_test(layout)
            if system_code not in (0, None):
                raise SiteError("Nginx 配置检查失败，已回滚。原来的配置仍然有效。", 400, log=system_out)
        site["checked"] = True
        public_code, public_out = install_public_site(layout, domain, bool(site.get("enabled")))
        if public_code != 0:
            raise SiteError("系统 Nginx 没有加载这个站点，其他服务的端口没有改动。", 400, log=f"{output}\n{public_out}".strip())
        public_installed = True
        if site.get("enabled") and plan and not using_fake_runner():
            _confirm_public(plan)
        site["reload_ok"] = True
        if old_conf is not None:
            site["backup_at"] = _now()
        site["last_log"] = (output or "nginx: the configuration file syntax is ok")
    except Exception:
        if wrote_support:
            _restore_text(rewrite, old_rewrite)
            _restore_text(passwords, old_passwords)
        if installed:
            try:
                _restore(available, enabled, old_conf, previous_link)
            except OSError:
                logger.exception("回滚 Nginx 配置失败")
        if public_installed:
            _rollback_public(layout, domain, available, old_conf, public_snapshot)
        _drop_failed_site_files(
            layout,
            domain,
            drop_rewrite=bool(wrote_support and old_rewrite is None and not installed),
        )
        raise
    finally:
        _cleanup_test(layout, staged)


def _write_support(site: dict, rewrite: Path, passwords: Path) -> None:
    rewrite.parent.mkdir(parents=True, exist_ok=True)
    rewrite.write_text(rewrite_document(site), encoding="utf-8")
    auth = site.get("auth") or {}
    if auth.get("enabled") and auth.get("users"):
        passwords.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(passwords.parent, 0o711)
        lines = [f"{user['name']}:{user['hash']}\n" for user in auth["users"]]
        passwords.write_text("".join(lines), encoding="utf-8")
        # nginx worker 以 www-data 读取，目录 711 不可列举
        os.chmod(passwords, 0o644)
        return
    if passwords.exists() and not passwords.is_symlink():
        passwords.unlink()


def _ensure_nginx_dirs(layout: NginxLayout) -> None:
    """预检会 include 这些目录。目录不存在时，nginx -t 会把后面的站点一起判失败。"""
    for folder in (
        layout.prefix / "rewrite",
        layout.prefix / "sites-test",
        layout.prefix / "sites-available",
        layout.prefix / "pending",
        layout.sites_available,
    ):
        folder.mkdir(parents=True, exist_ok=True)


def _materialize_rewrite_includes(layout: NginxLayout, texts) -> None:
    """配置里引用了伪静态文件、但文件还没生成时，先放一个空文件，避免 nginx -t 报找不到路径。"""
    root = (layout.prefix / "rewrite").resolve()
    root.mkdir(parents=True, exist_ok=True)
    for text in texts:
        for matched in re.finditer(r"(?m)^\s*include\s+([^;]+);", text or ""):
            raw = matched.group(1).strip().strip("\"'")
            if not raw.startswith("/") or "*" in raw or ".." in raw:
                continue
            path = Path(raw)
            if path.parent.resolve() != root:
                continue
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.conf", path.name):
                continue
            if path.is_symlink() or path.exists():
                continue
            path.write_text("# default rewrite\n", encoding="utf-8")
            os.chmod(path, 0o644)


def _drop_failed_site_files(layout: NginxLayout, domain: str, drop_rewrite: bool) -> None:
    stem = conf_stem(domain)
    leftover = layout.prefix / "sites-test" / f"{stem}.conf"
    if leftover.is_symlink() or leftover.is_file():
        leftover.unlink()
    pending = layout.prefix / "pending" / f"{stem}.conf"
    if pending.is_file() and not pending.is_symlink():
        pending.unlink()
    if not drop_rewrite:
        return
    rewrite = rewrite_path(layout, domain)
    if rewrite.is_file() and not rewrite.is_symlink():
        rewrite.unlink()


def _prepare_test_config(layout: NginxLayout, domain: str, staged: Path) -> Path:
    """用一份临时主配置加载候选站点，正式的 sites-enabled 先不动。"""
    _ensure_nginx_dirs(layout)
    folder = layout.prefix / "sites-test"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    (folder / "00-empty.conf").write_text("# 语法检查占位。\n", encoding="utf-8")
    name = staged.name
    if layout.sites_enabled.is_dir():
        for item in layout.sites_enabled.iterdir():
            if not item.name.endswith(".conf") or item.name in {name, "00-empty.conf"}:
                continue
            target = item.resolve() if item.is_symlink() else item
            if target.is_file():
                os.symlink(str(target), folder / item.name)
    os.symlink(str(staged.resolve()), folder / name)
    texts = []
    for item in folder.iterdir():
        if not item.name.endswith(".conf"):
            continue
        try:
            texts.append(item.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    _materialize_rewrite_includes(layout, texts)
    test_main = layout.prefix / "nginx.test.conf"
    test_main.write_text(render_main(layout, folder), encoding="utf-8")
    return test_main


def _cleanup_test(layout: NginxLayout, staged: Path) -> None:
    if staged.is_symlink() or staged.exists():
        staged.unlink()
    folder = layout.prefix / "sites-test"
    if folder.exists():
        shutil.rmtree(folder)
    test_main = layout.prefix / "nginx.test.conf"
    if test_main.exists():
        test_main.unlink()

def _restore_text(path: Path, old: Optional[str]) -> None:
    if old is None:
        if path.is_symlink() or path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(old, encoding="utf-8")


def _fill(site: dict) -> None:
    site.setdefault("domains", [site["domain"]])
    site.setdefault("bindings", [])
    site.setdefault("force_https", True)
    site.setdefault("auth", {"enabled": False, "realm": "Restricted", "users": []})
    site.setdefault("limit", {"conn": 0, "rate": 0})
    site.setdefault("hotlink", {"enabled": False, "domains": []})
    site.setdefault("redirects", [])
    site.setdefault("raw_mode", False)
    site.setdefault("raw_config", "")
    site.setdefault("rewrite_body", "try_files $uri $uri/ /index.html;")


def _restore(available: Path, enabled: Path, old: Optional[str], previous_link: Optional[str]) -> None:
    if old is None:
        if available.is_symlink() or available.exists():
            available.unlink()
    else:
        available.write_text(old, encoding="utf-8")
    if enabled.is_symlink():
        enabled.unlink()
    if previous_link is not None:
        os.symlink(previous_link, enabled)


def _ensure(layout: NginxLayout) -> None:
    layout.prefix.mkdir(parents=True, exist_ok=True)
    layout.logs.mkdir(parents=True, exist_ok=True)
    layout.certs.mkdir(parents=True, exist_ok=True)
    (layout.prefix / "rewrite").mkdir(parents=True, exist_ok=True)
    (layout.prefix / "passwords").mkdir(parents=True, exist_ok=True)
    try:
        layout.file_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SiteError(f"网站根目录无法创建：{exc}") from None
    if layout.system_mode:
        try:
            layout.sites_available.mkdir(parents=True, exist_ok=True)
            layout.sites_enabled.mkdir(parents=True, exist_ok=True)
            Path("/etc/nginx/conf.d").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SiteError(f"Nginx 配置目录无法创建：{exc}") from None
        prepare_system_nginx()
        return
    layout.sites_available.mkdir(parents=True, exist_ok=True)
    layout.sites_enabled.mkdir(parents=True, exist_ok=True)
    (layout.prefix / "nginx.conf").write_text(render_main(layout), encoding="utf-8")
    placeholder = layout.sites_enabled / "00-empty.conf"
    if not placeholder.exists():
        placeholder.write_text("# 占位文件，保证配置目录可被加载。\n", encoding="utf-8")


def _ensure_web_root(layout: NginxLayout, raw: str, domain: str) -> str:
    if not isinstance(raw, str):
        raise SiteError("网站目录无效")
    text = raw.strip()
    if not text or text in {".", "/"}:
        raise SiteError("网站根目录不能是文件根本身")
    if any(char in text for char in " ;{}#'\"\\`$\n\r"):
        raise SiteError("网站目录包含不能用于 Nginx 配置的字符")
    base = layout.file_root
    if text.startswith("/"):
        # 绝对路径先交给路径监狱，不能把 /etc 剥成相对目录。
        locate(text, root=base, allow_missing=True)
        relative = _absolute_relative(text, base)
    else:
        relative = text.strip("/")
    if not relative or any(part in {"", "."} for part in relative.split("/")):
        raise SiteError("网站目录无效")
    acc = ""
    for part in relative.split("/"):
        acc = f"{acc}/{part}" if acc else part
        path = locate(acc, root=base, allow_missing=True)
        if path.exists() and not path.is_dir():
            raise SiteError("网站根目录不是文件夹")
        if not path.exists():
            path.mkdir(mode=0o755)
            os.chmod(path, 0o755)
    final = locate(relative, root=base)
    index = final / "index.html"
    if not index.exists():
        title = html.escape(domain)
        index.write_text(
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<title>{title}</title></head><body><h1>{title}</h1></body></html>\n",
            encoding="utf-8",
        )
        os.chmod(index, 0o644)
    _chmod_site_tree(final)
    return display_path(final, base)


def _chmod_site_tree(root: Path) -> None:
    """新站点目录给 Nginx 工作进程读。只改这一棵目录，不跟随符号链接。"""
    if root.is_symlink() or not root.is_dir():
        return
    os.chmod(root, 0o755)
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        folder = Path(current)
        if folder.is_symlink():
            dirnames.clear()
            continue
        os.chmod(folder, 0o755)
        kept = []
        for name in dirnames:
            path = folder / name
            if path.is_symlink():
                continue
            os.chmod(path, 0o755)
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = folder / name
            if path.is_symlink() or not path.is_file():
                continue
            os.chmod(path, 0o755)


def nginx_env_report() -> dict:
    """临时诊断：当前用户、Nginx 目录是否可写、网站根目录是否能落文件。"""
    layout = get_layout()
    who = "unknown"
    for candidate in ("/usr/bin/whoami", "/bin/whoami"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            code, output = run_command([candidate], 5)
            if code == 0 and output.strip():
                who = output.strip().splitlines()[-1][:64]
                break
    conf = Path("/etc/nginx/conf.d")
    nginx_error = ""
    try:
        conf.mkdir(parents=True, exist_ok=True)
        probe = conf / ".panel-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        nginx_writable = True
    except OSError as exc:
        nginx_writable = False
        nginx_error = str(exc)[:300]
    root = layout.file_root
    write_ok = False
    write_error = ""
    try:
        root.mkdir(parents=True, exist_ok=True)
        sample = root / "test_perm.txt"
        sample.write_text("panel-perm-ok\n", encoding="utf-8")
        os.chmod(sample, 0o644)
        write_ok = sample.read_text(encoding="utf-8").strip() == "panel-perm-ok"
    except OSError as exc:
        write_error = str(exc)[:300]
    binary = find_nginx() or ""
    test_code, test_out = nginx_test(layout) if binary else (None, "未找到 nginx")
    return {
        "user": who,
        "euid": os.geteuid(),
        "nginx_conf_dir": str(conf),
        "nginx_conf_readable": os.access(conf, os.R_OK) if conf.exists() else False,
        "nginx_conf_writable": nginx_writable,
        "nginx_conf_error": nginx_error,
        "file_root": str(root),
        "write_test": "ok" if write_ok else "failed",
        "write_error": write_error,
        "nginx": binary,
        "apply_helper": os.path.isfile("/usr/local/sbin/panel-nginx-apply"),
        "nginx_test_code": test_code,
        "nginx_test": _safe_text(test_out or "")[:1500],
    }


def _absolute_relative(text: str, base: Path) -> str:
    found = locate(text, root=base, allow_missing=True)
    if found.exists():
        relative = display_path(found, base)
        if not relative:
            raise SiteError("网站根目录不能是文件根本身")
        return relative
    return found.resolve(strict=False).relative_to(base.resolve()).as_posix()


def _present(site: dict) -> dict:
    copied = dict(site)
    if copied.get("ssl") and copied.get("cert_path"):
        expiry = _cert_expiry(Path(copied["cert_path"]))
        if expiry:
            copied["expires_at"] = expiry
    enabled = bool(copied.get("enabled"))
    return {
        "domain": copied["domain"],
        "enabled": enabled,
        "status": "运行中" if enabled else "已停止",
        "backup": "已备份" if copied.get("backup_at") else "无备份",
        "backup_at": copied.get("backup_at") or "",
        "expires_at": copied.get("expires_at") or "",
        "root": copied.get("root") or "",
        "ssl": bool(copied.get("ssl")),
        "checked": bool(copied.get("checked")),
        "reload_ok": bool(copied.get("reload_ok")),
        "preset": copied.get("preset") or "none",
        "rewrite_custom": copied.get("rewrite_custom") or "",
        "rewrite_body": copied.get("rewrite_body") or "",
        "proxies": [
            {
                "name": item.get("name") or "proxy",
                "path": item.get("path") or "/",
                "upstream": item.get("target_url") or item.get("upstream") or "",
                "target_url": item.get("target_url") or item.get("upstream") or "",
                "forward_ip": bool(item.get("forward_ip", True)),
            }
            for item in copied.get("proxies") or []
        ],
        "domains": validate_domains(copied["domain"], list(copied.get("domains") or [copied["domain"]])),
        "bindings": list(copied.get("bindings") or []),
        "force_https": bool(copied.get("force_https", True)),
        "auth": {
            "enabled": bool((copied.get("auth") or {}).get("enabled")),
            "realm": (copied.get("auth") or {}).get("realm") or "Restricted",
            "users": [user.get("name") for user in (copied.get("auth") or {}).get("users") or []],
        },
        "limit": {
            "conn": int((copied.get("limit") or {}).get("conn") or 0),
            "rate": int((copied.get("limit") or {}).get("rate") or 0),
        },
        "hotlink": {
            "enabled": bool((copied.get("hotlink") or {}).get("enabled")),
            "domains": list((copied.get("hotlink") or {}).get("domains") or []),
        },
        "security_headers": present_headers(copied.get("security_headers")),
        "redirects": list(copied.get("redirects") or []),
        "raw_mode": bool(copied.get("raw_mode")),
        "last_log": copied.get("last_log") or "",
        "created_at": copied.get("created_at") or "",
        "kind": copied.get("kind") or "html",
        "note": copied.get("note") or "",
        "php": "未安装",
        "port": str((validate_domains(copied["domain"], list(copied.get("domains") or [copied["domain"]]))[0]["port"])),
    }


def _read_error_log(layout: NginxLayout, domain: str) -> str:
    path = layout.logs / f"{conf_stem(domain)}.error.log"
    if not path.is_file() or path.is_symlink():
        return ""
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return ""
    if not inside(resolved, layout.logs.resolve()):
        return ""
    text = path.read_bytes()[-64000:].decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-200:])


def _cert_expiry(path: Path) -> str:
    binary = "/usr/bin/openssl"
    if not os.access(binary, os.X_OK):
        return ""
    try:
        completed = subprocess.run(
            [binary, "x509", "-in", str(path), "-noout", "-enddate"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    import re

    matched = re.search(r"notAfter=(.+)", completed.stdout or "")
    if not matched:
        return ""
    try:
        when = parsedate_to_datetime(matched.group(1).strip())
    except (TypeError, ValueError, OverflowError):
        return ""
    return when.strftime("%Y-%m-%d %H:%M:%S")


def _available(layout: NginxLayout, domain: str) -> Path:
    return layout.sites_available / f"{conf_stem(domain)}.conf"


def _enabled(layout: NginxLayout, domain: str) -> Path:
    return layout.sites_enabled / f"{conf_stem(domain)}.conf"


def _backup(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _load(layout: NginxLayout) -> dict:
    path = layout.prefix / "sites.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SiteError("站点数据损坏", 500) from None
    if not isinstance(data, dict):
        raise SiteError("站点数据损坏", 500)
    return data


def _save(layout: NginxLayout, data: dict) -> None:
    path = layout.prefix / "sites.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


_DELETE_EXACT = {
    "/",
    "/etc",
    "/root",
    "/proc",
    "/sys",
    "/boot",
    "/usr",
    "/var",
    "/www",
    "/www/wwwroot",
    "/home",
}


def _collapse_path(text: str) -> str:
    collapsed = text.strip()
    while "//" in collapsed:
        collapsed = collapsed.replace("//", "/")
    if len(collapsed) > 1 and collapsed.endswith("/"):
        collapsed = collapsed.rstrip("/")
    return collapsed or "/"


def _blocked_delete_path(text: str) -> bool:
    collapsed = _collapse_path(text)
    if collapsed in _DELETE_EXACT:
        return True
    return collapsed == "/etc" or collapsed.startswith("/etc/")


def _strict_child(path: Path, parent: Path) -> bool:
    try:
        relative = path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except (ValueError, OSError):
        return False
    return bool(relative.parts)


def _inside_web_root(absolute: str, file_root: Path) -> bool:
    """网站目录的绝对路径必须真正落在网站主根目录里面。端口短、目录名带冒号都不影响。"""
    from app.files.security import inside_allowed

    return inside_allowed(Path(absolute), file_root, allow_root=False)


def assert_deletable_site_root(raw: str, file_root: Path) -> Path:
    """站点根必须是 FILE_ROOT 的严格子目录。不合法时抛 PathJailError，调用方应中止整个请求。"""
    if not isinstance(raw, str) or "\x00" in raw or not raw.strip():
        raise_jail("" if not isinstance(raw, str) else raw[:180], "illegal_char")
    text = raw.strip()
    if _blocked_delete_path(text):
        raise_jail(text, "sensitive_path")
    jail = file_root.resolve()
    if jail.as_posix() == "/":
        raise_jail(text, "sensitive_path")
    found = locate(text, root=jail, follow_final=False, allow_missing=True)
    if found.is_symlink():
        raise_jail(text, "outside_jail")
    try:
        resolved = found.resolve(strict=False)
    except OSError:
        raise_jail(text, "outside_jail")
    if resolved == jail or not _strict_child(resolved, jail):
        raise_jail(text, "outside_jail")
    absolute = os.path.abspath(resolved.as_posix())
    if not _inside_web_root(absolute, jail):
        raise_jail(text, "outside_jail")
    if _blocked_delete_path(absolute):
        raise_jail(text, "sensitive_path")
    if found.exists() and not found.is_dir():
        raise_jail(text, "outside_jail")
    if found.is_dir() and _tree_has_outside_symlink(found, jail):
        raise_jail(text, "outside_jail")
    return found


def _tree_has_outside_symlink(directory: Path, jail: Path) -> bool:
    if directory.is_symlink():
        return True
    if not directory.is_dir():
        return False
    jail_resolved = jail.resolve()
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        for name in list(dirnames) + list(filenames):
            path = Path(current) / name
            if not path.is_symlink():
                continue
            try:
                target = path.resolve(strict=False)
            except OSError:
                return True
            if not _strict_child(target, jail_resolved) and target != jail_resolved:
                try:
                    target.relative_to(jail_resolved)
                except ValueError:
                    return True
            try:
                target.resolve(strict=False).relative_to(jail_resolved)
            except ValueError:
                return True
    return False


_STUCK_SITE_KEY = "104.214.187.79:1"


def _nginx_conf_missing(layout: NginxLayout, domain: str) -> bool:
    stem = conf_stem(domain)
    names = (
        layout.sites_available / f"{stem}.conf",
        layout.sites_enabled / f"{stem}.conf",
        Path("/etc/nginx/conf.d") / f"panel-{stem}.conf",
    )
    return not any(path.exists() for path in names)


def _remove_nginx_for_delete(layout: NginxLayout, key: str, notes: list) -> None:
    """先删掉这个站点自己的 Nginx 配置，再重载。主配置文件保留。"""
    try:
        note = _retire_nginx(layout, key)
    except SiteError:
        if key == _STUCK_SITE_KEY:
            _force_drop_stuck_nginx(layout, key)
            notes.append("已按站点名清除该站点的 Nginx 配置")
            return
        if not _nginx_conf_missing(layout, key):
            raise
        try:
            _drop_local_nginx(layout, key)
        except OSError:
            logger.exception("清理本地站点配置失败")
        notes.append("Nginx 配置已经不存在，已继续清除面板记录")
        return
    if note:
        notes.append(note)


def _force_drop_stuck_nginx(layout: NginxLayout, domain: str) -> None:
    """只处理已经写脏的这一个站点名，卸掉它自己的配置文件，不碰其他路径。"""
    if domain != _STUCK_SITE_KEY:
        raise SiteError("站点不存在", 404)
    stem = conf_stem(domain)
    if stem != "104.214.187.79.1":
        raise SiteError("站点不存在", 404)
    removed = layout.prefix / "pending" / f"{stem}.removed.conf"
    public = Path("/etc/nginx/conf.d") / f"panel-{stem}.conf"
    backup = None
    expected = Path("/etc/nginx/conf.d") / "panel-104.214.187.79.1.conf"
    if (
        public == expected
        and public.is_file()
        and not public.is_symlink()
        and public.parent.resolve() == expected.parent.resolve()
    ):
        backup = public.read_bytes()
        public.unlink()
    try:
        test_main = _prepare_removal_test(layout, domain)
        code, output = nginx_test(layout, str(test_main))
        if code != 0:
            logger.warning("站点 %s 的配置已删除，Nginx 检查未通过: %s", domain, output)
        elif public.parent.is_dir() and os.geteuid() == 0:
            reload_code, reload_out = nginx_reload(layout)
            if reload_code != 0:
                logger.warning("站点 %s 的配置已删除，Nginx 重载失败: %s", domain, reload_out)
    finally:
        _cleanup_test(layout, removed)
        if backup is not None and public.is_file() and not public.is_symlink():
            public.unlink()
    _drop_local_nginx(layout, domain)


def _retire_nginx(layout: NginxLayout, domain: str) -> str:
    """删掉这个站点自己的配置，然后重载。Nginx 主配置保留。

    和宝塔删站一样：站点配置先从磁盘去掉。后面的检查失败也不再把这份配置写回去。
    """
    _settle_host_panel_confs(layout)
    _drop_local_nginx(layout, domain)
    _rewrite_private_main(layout)
    code, output = install_public_site(layout, domain, False, discard=True)
    if code != 0:
        logger.warning("站点 %s 的 Nginx 配置已删除，系统 Nginx 未重载: %s", domain, output)
        return "该站点的 Nginx 配置已删除，系统 Nginx 没有重载"
    return ""


def _rewrite_private_main(layout: NginxLayout) -> None:
    """面板自己的主配置只保留 include。删站后重写这一份，不删除它。"""
    if layout.system_mode:
        return
    path = layout.prefix / "nginx.conf"
    path.write_text(render_main(layout), encoding="utf-8")


def _prepare_removal_test(layout: NginxLayout, domain: str) -> Path:
    """候选主配置只带其他站点，不改正在使用的 sites-enabled。"""
    folder = layout.prefix / "sites-test"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    (folder / "00-empty.conf").write_text("# 语法检查占位。\n", encoding="utf-8")
    skip = f"{conf_stem(domain)}.conf"
    prefix = layout.prefix.resolve()
    if layout.sites_enabled.is_dir():
        for item in layout.sites_enabled.iterdir():
            if not item.name.endswith(".conf") or item.name in {skip, "00-empty.conf"}:
                continue
            target = item.resolve() if item.is_symlink() else item
            if not target.is_file() or target.is_symlink():
                continue
            try:
                target.resolve().relative_to(prefix)
            except ValueError:
                continue
            os.symlink(str(target), folder / item.name)
    test_main = layout.prefix / "nginx.test.conf"
    test_main.write_text(render_main(layout, folder), encoding="utf-8")
    return test_main


def _drop_local_nginx(layout: NginxLayout, domain: str) -> None:
    stem = conf_stem(domain)
    if not stem or "/" in stem or stem in {".", ".."}:
        raise SiteError("域名无效")
    available = _available(layout, domain)
    _unlink_inside(available, layout.sites_available)
    _unlink_inside(_backup(available), layout.sites_available)
    _unlink_inside(_enabled(layout, domain), layout.sites_enabled, symlink_ok=True)
    _unlink_inside(rewrite_path(layout, domain), layout.prefix / "rewrite")
    _unlink_inside(password_path(layout, domain), layout.prefix / "passwords")


def _unlink_inside(path: Path, parent: Path, *, symlink_ok: bool = False) -> None:
    if path.is_symlink():
        if not symlink_ok:
            return
        try:
            if path.parent.resolve() != parent.resolve():
                return
        except OSError:
            return
        path.unlink()
        return
    if not path.exists():
        return
    try:
        resolved = path.resolve(strict=True)
        parent_resolved = parent.resolve()
    except OSError:
        return
    if resolved == parent_resolved or not _strict_child(resolved, parent_resolved):
        return
    if resolved.is_dir():
        return
    resolved.unlink()


def _delete_site_files(site: dict, layout: NginxLayout) -> None:
    root = assert_deletable_site_root(str(site.get("root") or ""), layout.file_root)
    if not root.exists():
        return
    jail = layout.file_root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise_jail(str(site.get("root") or ""), "outside_jail")
    if _tree_has_outside_symlink(root, jail):
        raise_jail(str(site.get("root") or ""), "outside_jail")
    resolved = root.resolve()
    if resolved == jail or not _strict_child(resolved, jail):
        raise_jail(str(site.get("root") or ""), "outside_jail")
    shutil.rmtree(root)


def _delete_site_logs(layout: NginxLayout, domain: str) -> None:
    log_dir = layout.logs
    if log_dir is None or not log_dir.is_dir() or log_dir.is_symlink():
        return
    try:
        root = log_dir.resolve(strict=True)
    except OSError:
        return
    if root.as_posix() in {"/", "/var/log", "/www/wwwlogs"} or root.as_posix().startswith("/var/log/"):
        return
    stem = conf_stem(domain)
    for kind in ("access", "error"):
        name = f"{stem}.{kind}.log"
        if "/" in name or name in {".", ".."}:
            continue
        path = log_dir / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved == root or resolved.name != name or not _strict_child(resolved, root):
            continue
        resolved.unlink()


def _bound_database(site: dict) -> Optional[dict]:
    """只认站点记录里写明的库。没有这个字段时，不用域名去猜。"""
    for field in ("database", "db_name", "database_name", "bound_database"):
        if field not in site:
            continue
        value = site.get(field)
        if isinstance(value, str) and value.strip():
            return {"engine": "mysql", "name": value.strip()}
        if isinstance(value, dict):
            name = str(value.get("name") or value.get("database") or "").strip()
            engine = str(value.get("engine") or "mysql").strip() or "mysql"
            if name:
                return {"engine": engine, "name": name}
    return None


def _drop_bound_database(binding: dict) -> None:
    engine = binding.get("engine") or "mysql"
    name = binding.get("name") or ""
    if engine == "mysql":
        from app.databases.service import delete_database

        delete_database(name)
        return
    if engine == "mongodb":
        from app.databases.engines import mongo_delete

        mongo_delete(name)
        return
    if engine == "pgsql":
        from app.databases.engines import postgres_delete

        postgres_delete(name)
        return
    if engine == "sqlserver":
        from app.databases.engines import sqlserver_delete

        sqlserver_delete(name)
        return
    raise RuntimeError("没有可调用的数据库删除函数")


def _delete_site_certs(layout: NginxLayout, domain: str, site: dict) -> None:
    stem = conf_stem(domain)
    if not stem or "/" in stem or stem in {".", ".."}:
        return
    root = layout.certs
    if not root.is_dir() or root.is_symlink():
        return
    root_resolved = root.resolve()
    if "letsencrypt" in root_resolved.as_posix():
        return
    folder = root / stem
    if folder.is_dir() and not folder.is_symlink():
        try:
            folder_resolved = folder.resolve(strict=True)
        except OSError:
            folder_resolved = None
        if folder_resolved and folder_resolved.name == stem and _strict_child(folder_resolved, root_resolved):
            for child in list(folder_resolved.iterdir()):
                if child.is_symlink() or not child.is_file() or not child.name.endswith(".pem"):
                    continue
                try:
                    resolved = child.resolve(strict=True)
                except OSError:
                    continue
                if _strict_child(resolved, folder_resolved):
                    resolved.unlink()
            try:
                folder_resolved.rmdir()
            except OSError:
                pass
    for field in ("cert_path", "key_path"):
        raw = str(site.get(field) or "")
        if not raw or "letsencrypt" in raw:
            continue
        path = Path(raw)
        if path.is_symlink() or not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if not resolved.name.endswith(".pem") or not _strict_child(resolved, root_resolved):
            continue
        if stem not in resolved.parts:
            continue
        try:
            resolved.unlink()
        except OSError:
            continue


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
