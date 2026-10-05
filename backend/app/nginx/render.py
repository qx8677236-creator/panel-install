"""把已经校验过的站点数据渲染成虚拟主机配置。"""

from __future__ import annotations

from pathlib import Path

from app.files.security import locate
from app.nginx.layout import NginxLayout
from app.nginx.headers import security_header_lines
from app.nginx.listen import safe_listen_token
from app.nginx.security import (
    audit_raw_config,
    confirm_cert,
    conf_stem,
    nginx_quote,
    parse_domain_port,
    validate_bindings,
    validate_domains,
    validate_hotlink,
    validate_limit,
    validate_proxies,
    validate_redirects,
    validate_rewrite,
)


def render_main(layout: NginxLayout, include_dir: Path = None) -> str:
    folder = include_dir or layout.sites_enabled
    error_log = nginx_quote((layout.logs / "error.log").as_posix())
    pid = nginx_quote((layout.logs / "nginx.pid").as_posix())
    access = nginx_quote((layout.logs / "access.log").as_posix())
    include = nginx_quote((folder / "*.conf").as_posix())
    return (
        "worker_processes 1;\n"
        f"error_log {error_log};\n"
        f"pid {pid};\n"
        "events {\n"
        "    worker_connections 128;\n"
        "}\n"
        "http {\n"
        "    default_type application/octet-stream;\n"
        f"    access_log {access};\n"
        f"    include {include};\n"
        "}\n"
    )


def rewrite_path(layout: NginxLayout, domain: str) -> Path:
    return layout.prefix / "rewrite" / f"{conf_stem(domain)}.conf"


def password_path(layout: NginxLayout, domain: str) -> Path:
    return layout.prefix / "passwords" / f"{conf_stem(domain)}.htpasswd"


def rewrite_document(site: dict) -> str:
    proxies = validate_proxies(list(site.get("proxies") or []))
    if any(item["path"] == "/" for item in proxies):
        return "# 根路径已交给反向代理。\n"
    body = validate_rewrite(site.get("rewrite_body") or "try_files $uri $uri/ /index.html;")
    indented = "\n        ".join(body.splitlines())
    return f"location / {{\n        {indented}\n    }}\n"


def render_site(site: dict, layout: NginxLayout, listen_addresses: dict = None) -> str:
    if site.get("raw_mode") and site.get("raw_config"):
        return audit_raw_config(site["raw_config"], layout)
    identity = str(site.get("domain") or "")
    parsed = parse_domain_port(identity)
    domain = parsed["domain"]
    entries = validate_domains(identity, list(site.get("domains") or [identity]))
    bindings = validate_bindings(list(site.get("bindings") or []), domain)
    bound = {binding["domain"] for binding in bindings}
    grouped = {}
    for item in entries:
        if item["domain"] in bound:
            continue
        listen_port = int(item["port"])
        grouped.setdefault(listen_port, [])
        if item["domain"] not in grouped[listen_port]:
            grouped[listen_port].append(item["domain"])
    if not any(domain in group for group in grouped.values()):
        grouped.setdefault(int(parsed["port"]), []).insert(0, domain)
    root = locate(site["root"], root=layout.file_root)
    proxies = validate_proxies(list(site.get("proxies") or []))
    limit = validate_limit(dict(site.get("limit") or {}))
    hotlink = validate_hotlink(dict(site.get("hotlink") or {}))
    redirects = validate_redirects(list(site.get("redirects") or []))
    stem = conf_stem(identity)
    zone = stem.replace(".", "_").replace("-", "_")
    addresses = {int(key): str(value) for key, value in (listen_addresses or {}).items()}
    pieces = [_limit_zones(zone, limit)]
    first = True
    for port in sorted(grouped):
        pieces.append(
            _server_block(
                site,
                layout,
                grouped[port],
                root,
                proxies,
                limit,
                hotlink,
                redirects,
                zone,
                stem,
                listen_port=port,
                primary=first,
                listen_addresses=addresses,
            )
        )
        first = False
    for binding in bindings:
        bound_root = locate(f"{site['root']}/{binding['subdir']}", root=layout.file_root)
        pieces.append(
            _server_block(
                site,
                layout,
                [binding["domain"]],
                bound_root,
                [],
                {"conn": 0, "rate": 0},
                {"enabled": False, "domains": []},
                [],
                zone,
                stem,
                include_rewrite=False,
            )
        )
    return "# 由删库跑路快捷助手生成，请通过面板修改。\n" + "\n".join(piece for piece in pieces if piece)


def _limit_zones(zone: str, limit: dict) -> str:
    lines = []
    if limit["rate"]:
        lines.append(
            f"limit_req_zone $binary_remote_addr zone={zone}_req:1m rate={limit['rate']}r/s;"
        )
    if limit["conn"]:
        lines.append(f"limit_conn_zone $binary_remote_addr zone={zone}_conn:1m;")
    return "\n".join(lines)


def _server_block(site, layout, names, root, proxies, limit, hotlink, redirects, zone, stem, include_rewrite=True, listen_port=80, primary=True, listen_addresses=None) -> str:
    domain = " ".join(names)
    access = nginx_quote((layout.logs / f"{stem}.access.log").as_posix())
    error = nginx_quote((layout.logs / f"{stem}.error.log").as_posix())
    root_text = nginx_quote(root.as_posix())
    body = [
        f"    server_name {domain};",
        f"    root {root_text};",
        "    index index.html index.htm;",
        "    server_tokens off;",
        f"    access_log {access};",
        f"    error_log {error};",
    ]
    if include_rewrite:
        body.append(f"    include {nginx_quote(rewrite_path(layout, site['domain']).as_posix())};")
    for item in redirects:
        body.append(f"    {_redirect_line(item)}")
    if limit["rate"]:
        body.append(f"    limit_req zone={zone}_req burst=20 nodelay;")
    if limit["conn"]:
        body.append(f"    limit_conn {zone}_conn {limit['conn']};")
    auth = site.get("auth") or {}
    if include_rewrite and auth.get("enabled") and auth.get("users"):
        realm = str(auth.get("realm") or "Restricted")
        body.append(f"    auth_basic {nginx_quote(realm)};")
        body.append(f"    auth_basic_user_file {nginx_quote(password_path(layout, site['domain']).as_posix())};")
    if hotlink.get("enabled"):
        names = list(hotlink.get("domains") or [])
        allowed = " ".join(["none", "blocked", "server_names", *names])
        body.append(f"    valid_referers {allowed};")
        body.append("    if ($invalid_referer) {")
        body.append("        return 403;")
        body.append("    }")
    for item in proxies:
        body.append("    " + _proxy_location(item).replace("\n", "\n    "))
    for line in security_header_lines(site.get("security_headers"), bool(site.get("ssl"))):
        body.append("    " + line)
    return _wrap_listen(site, domain, "\n".join(body) + "\n", primary and include_rewrite, listen_port, listen_addresses)


def _listen_line(port: int, addresses: dict, ssl: bool = False) -> str:
    token = safe_listen_token(str((addresses or {}).get(int(port), port)))
    extra = " ssl" if ssl else ""
    return f"    listen {token}{extra};\n"


def _wrap_listen(site: dict, names: str, common: str, primary: bool, listen_port: int = 80, listen_addresses=None) -> str:
    addresses = listen_addresses or {}
    if site.get("ssl") and primary:
        cert = nginx_quote(str(site["cert_path"]))
        key = nginx_quote(str(site["key_path"]))
        https = (
            "server {\n"
            + _listen_line(443, addresses, ssl=True)
            + f"{common}"
            f"    ssl_certificate {cert};\n"
            f"    ssl_certificate_key {key};\n"
            "}\n"
        )
        if site.get("force_https", True):
            return (
                "server {\n"
                + _listen_line(80, addresses)
                + f"    server_name {names};\n"
                "    return 301 https://$host$request_uri;\n"
                "}\n"
                + https
            )
        return "server {\n" + _listen_line(listen_port, addresses) + common + "}\n" + https
    return "server {\n" + _listen_line(listen_port, addresses) + common + "}\n"


def _redirect_line(item: dict) -> str:
    flag = "permanent" if item["code"] == 301 else "redirect"
    if item["path"] == "/":
        pattern = "^/(.*)$"
    else:
        pattern = "^" + item["path"].rstrip("/") + "/?(.*)$"
    return f"rewrite {pattern} {item['target']}/$1 {flag};"


def _location_block(path: str, body: str) -> str:
    indented = "\n        ".join(body.splitlines())
    return f"location {path} {{\n        {indented}\n    }}"


def _proxy_location(item: dict) -> str:
    name = str(item.get("name") or "proxy").replace("\n", " ").strip() or "proxy"
    block = _location_block(item["path"], _proxy_body(item))
    return f"# panel-proxy {name}\n" + block


def _proxy_body(item) -> str:
    if isinstance(item, str):
        item = {"upstream": item, "forward_ip": True}
    upstream = item.get("target_url") or item.get("upstream")
    lines = [
        f"proxy_pass {upstream};",
        "proxy_set_header Host $host;",
    ]
    if item.get("forward_ip", True):
        lines.append("proxy_set_header X-Real-IP $remote_addr;")
        lines.append("proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
    lines.append("proxy_set_header X-Forwarded-Proto $scheme;")
    return "\n".join(lines)


def confirm_site_certs(site: dict, layout: NginxLayout) -> None:
    if not site.get("ssl"):
        return
    cert = confirm_cert(Path(str(site.get("cert_path") or "")), layout.certs)
    key = confirm_cert(Path(str(site.get("key_path") or "")), layout.certs)
    site["cert_path"] = cert.as_posix()
    site["key_path"] = key.as_posix()
