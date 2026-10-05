"""从站点 Nginx 配置原文提取表单字段。没有写到文件里的项保持空，不补默认值。"""

from __future__ import annotations

import re
from pathlib import Path

from app.nginx.headers import normalize_security_headers
from app.nginx.proxyparse import parse_proxy_rules
from app.nginx.security import SiteError, parse_domain_port, rewrite_templates

_LISTEN = re.compile(r"^listen\s+(\d+)(?:\s+ssl)?\s*$", re.I)
_CONN = re.compile(r"limit_conn\s+\S+\s+(\d+)\s*;")
_RATE = re.compile(r"rate=(\d+)r/s")
_AUTH = re.compile(r'^auth_basic\s+(?:"([^"]+)"|([^\s;]+))\s*$')
_INDEX = re.compile(r"^index\s+(.+)$", re.I)
_HEADER = re.compile(r'add_header\s+([A-Za-z0-9-]+)\s+"([^"]*)"\s+always\s*;')
_REDIRECT = re.compile(
    r"^rewrite\s+\^(?P<pattern>/.*?)\(\.\*\)\$\s+(?P<target>https?://\S+?)/\$1\s+(?P<flag>permanent|redirect)\s*;$"
)
_REWRITE_HEADS = ("rewrite", "try_files", "return", "index", "break")
_FRAMES = {"SAMEORIGIN", "DENY"}
_REFERRERS = {"no-referrer", "same-origin", "strict-origin-when-cross-origin"}


def parse_site_files(
    text: str,
    *,
    primary: str,
    primary_root: str,
    rewrite_text: str = "",
    password_text: str = "",
    file_root: str = "",
) -> dict:
    primary_name = _safe_name(primary)
    servers, preamble = _servers(text or "")
    content = [item for item in servers if item["root"] and primary_name and primary_name in item["names"]]
    main = content[0] if content else None
    domains = _domains(servers, primary_name)
    bindings = _bindings(servers, primary_name, primary_root)
    rewrite_body = _rewrite_body(rewrite_text)
    if not rewrite_body and main is not None:
        rewrite_body = _rewrite_body(main["location_root"])
    preset, custom = _preset(rewrite_body)
    auth = _auth(main, password_text)
    limit_text = (main["raw"] if main else "") + "\n" + preamble
    return {
        "domains": domains,
        "bindings": bindings,
        "proxies": parse_proxy_rules(text or ""),
        "rewrite_body": rewrite_body,
        "rewrite_custom": custom,
        "preset": preset,
        "limit": {
            "conn": _first_int(_CONN, limit_text),
            "rate": _first_int(_RATE, limit_text),
        },
        "auth": auth,
        "hotlink": _hotlink(main["raw"] if main else ""),
        "redirects": _redirects(main["raw"] if main else ""),
        "ssl": any(item["ssl"] for item in servers),
        "force_https": any(item["force_https"] for item in servers),
        "security_headers": _headers(main["raw"] if main else ""),
        "index_files": _index(main["statements"] if main else []),
        "root": _display_root(main["root"] if main else "", file_root),
    }


def _safe_name(primary: str) -> str:
    try:
        return parse_domain_port(primary)["domain"]
    except SiteError:
        return ""


def _servers(text: str) -> tuple[list[dict], str]:
    servers = []
    preamble = []
    for kind, head, body in _scan(text):
        if kind == "stmt":
            preamble.append(head)
            continue
        if kind != "block" or not head.startswith("server"):
            continue
        statements = []
        location_root = ""
        root = ""
        names = []
        listens = []
        ssl = False
        force_https = False
        raw_lines = []
        for inner_kind, inner_head, inner_body in _scan(body):
            if inner_kind == "stmt":
                statements.append(inner_head)
                raw_lines.append(inner_head + ";")
                if inner_head.startswith("root "):
                    root = _unquote(inner_head.split(None, 1)[1])
                elif inner_head.startswith("server_name "):
                    names = inner_head.split()[1:]
                elif inner_head.startswith("listen "):
                    matched = _LISTEN.match(inner_head)
                    if matched:
                        listens.append(int(matched.group(1)))
                    if re.search(r"\bssl\b", inner_head):
                        ssl = True
                elif inner_head.startswith("ssl_certificate "):
                    ssl = True
                elif "return 301 https://" in inner_head:
                    force_https = True
            elif inner_kind == "block" and inner_head.startswith("location"):
                path = inner_head.split(None, 1)[1].strip() if " " in inner_head else ""
                if path == "/":
                    location_root = inner_body
                raw_lines.append(inner_head + " { " + inner_body + " }")
        servers.append(
            {
                "root": root,
                "names": names,
                "listens": listens,
                "ssl": ssl,
                "force_https": force_https,
                "statements": statements,
                "location_root": location_root,
                "raw": "\n".join(raw_lines),
            }
        )
    return servers, "\n".join(preamble)


def _scan(text: str) -> list[tuple]:
    items = []
    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        if text[index] == "#":
            end = text.find("\n", index)
            index = length if end < 0 else end + 1
            continue
        start = index
        while index < length and text[index] not in "{;":
            index += 1
        head = " ".join(text[start:index].split())
        if index < length and text[index] == "{":
            depth = 1
            index += 1
            body_start = index
            while index < length and depth:
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                index += 1
            items.append(("block", head, text[body_start : index - 1]))
            continue
        if index < length and text[index] == ";":
            index += 1
        if head:
            items.append(("stmt", head, ""))
    return items


def _domains(servers: list[dict], primary_name: str) -> list[dict]:
    found = []
    seen = set()
    for server in servers:
        if primary_name and primary_name not in server["names"]:
            continue
        if server["root"] == "" and not server["force_https"] and not server["ssl"]:
            continue
        if not server["listens"]:
            continue
        for port in server["listens"]:
            for name in server["names"]:
                item = _domain_item(name, port)
                if item is None:
                    continue
                key = (item["domain"], item["port"])
                if key in seen:
                    continue
                seen.add(key)
                found.append(item)
    return found


def _bindings(servers: list[dict], primary_name: str, primary_root: str) -> list[dict]:
    base = primary_root.rstrip("/")
    if not base:
        return []
    found = []
    seen = set()
    for server in servers:
        if not server["root"] or (primary_name and primary_name in server["names"]):
            continue
        root = server["root"].rstrip("/")
        if not root.startswith(base + "/"):
            continue
        subdir = root[len(base) + 1 :]
        if not subdir or ".." in subdir.split("/"):
            continue
        for name in server["names"]:
            if not server["listens"]:
                continue
            item = _domain_item(name, server["listens"][0])
            if item is None:
                continue
            key = (item["domain"], subdir)
            if key in seen:
                continue
            seen.add(key)
            found.append({"domain": item["domain"], "subdir": subdir})
    return found


def _domain_item(name: str, port: int) -> dict | None:
    try:
        return parse_domain_port({"domain": name, "port": port})
    except SiteError:
        return None


def _rewrite_body(text: str) -> str:
    if not text or "根路径已交给反向代理" in text:
        return ""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("location") or line == "}":
            continue
        head = line.split(None, 1)[0].lower()
        if head in _REWRITE_HEADS and line.endswith(";"):
            lines.append(line)
    return "\n".join(lines)


def _preset(body: str) -> tuple[str, str]:
    if not body:
        return "", ""
    for name, template in rewrite_templates().items():
        if body == template:
            return name, ""
    return "custom", body


def _auth(server: dict | None, password_text: str) -> dict:
    realm = ""
    enabled = False
    if server is not None:
        for line in server["statements"]:
            matched = _AUTH.match(line)
            if matched:
                enabled = True
                realm = matched.group(1) or matched.group(2) or ""
    users = []
    if enabled:
        for line in (password_text or "").splitlines():
            name = line.split(":", 1)[0].strip()
            if name and name not in users:
                users.append(name)
    if not enabled:
        realm = ""
        users = []
    return {"enabled": enabled, "realm": realm, "users": users}


def _hotlink(text: str) -> dict:
    for line in text.splitlines():
        stripped = line.strip().rstrip(";")
        if not stripped.startswith("valid_referers "):
            continue
        domains = []
        for token in stripped.split()[1:]:
            if token in {"none", "blocked", "server_names"}:
                continue
            item = _domain_item(token, 80)
            if item is not None and item["domain"] not in domains:
                domains.append(item["domain"])
        return {"enabled": True, "domains": domains}
    return {"enabled": False, "domains": []}


def _redirects(text: str) -> list[dict]:
    found = []
    seen = set()
    for line in text.splitlines():
        matched = _REDIRECT.match(line.strip())
        if not matched:
            continue
        pattern = matched.group("pattern")
        path = "/" if pattern in {"/", "/?"} else pattern.removesuffix("/?").removesuffix("/")
        if not path.startswith("/"):
            path = "/" + path
        code = 301 if matched.group("flag") == "permanent" else 302
        if path in seen:
            continue
        seen.add(path)
        found.append({"path": path, "target": matched.group("target"), "code": code})
    return found


def _headers(text: str) -> dict:
    payload = {
        "enabled": False,
        "x_frame_options": "",
        "nosniff": False,
        "xss": False,
        "referrer": "",
        "hsts": False,
    }
    for name, value in _HEADER.findall(text):
        if name == "X-Frame-Options" and value in _FRAMES:
            payload["x_frame_options"] = value
            payload["enabled"] = True
        elif name == "X-Content-Type-Options" and value == "nosniff":
            payload["nosniff"] = True
            payload["enabled"] = True
        elif name == "X-XSS-Protection":
            payload["xss"] = True
            payload["enabled"] = True
        elif name == "Referrer-Policy" and value in _REFERRERS:
            payload["referrer"] = value
            payload["enabled"] = True
        elif name == "Strict-Transport-Security":
            payload["hsts"] = True
            payload["enabled"] = True
    return normalize_security_headers(payload)


def _index(statements: list[str]) -> str:
    for line in statements:
        matched = _INDEX.match(line)
        if matched:
            return " ".join(matched.group(1).split())
    return ""


def _display_root(root: str, file_root: str) -> str:
    if not root:
        return ""
    if not file_root:
        return root
    try:
        relative = Path(root).resolve().relative_to(Path(file_root).resolve()).as_posix()
    except (OSError, ValueError):
        return root
    return "" if relative == "." else relative


def _first_int(pattern: re.Pattern, text: str) -> int:
    matched = pattern.search(text or "")
    if not matched:
        return 0
    return int(matched.group(1))


def _unquote(text: str) -> str:
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value
