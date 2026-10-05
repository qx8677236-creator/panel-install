"""从站点 Nginx 配置里读出反向代理，而不是返回写死的本地地址。"""

from __future__ import annotations

import re

_LOCATION = re.compile(r"^[ \t]*location[ \t]+(?P<path>/[^\s{]*)[ \t]*\{(?P<body>[^{}]*)\}", re.M)
_PASS = re.compile(r"proxy_pass[ \t]+(https?://[^;\s]+)[ \t]*;")
_NAME = re.compile(r"#\s*panel-proxy[ \t]+([^\r\n#]{1,40})\s*$")
_UPSTREAM = re.compile(r"^https?://[A-Za-z0-9._:-]+(?:/[A-Za-z0-9._~%/-]*)?$")
_PATH = re.compile(r"^/(?:[A-Za-z0-9._~-]+/)*[A-Za-z0-9._~-]*$")


def parse_proxy_rules(text: str) -> list[dict]:
    if not isinstance(text, str) or len(text) > 200_000:
        return []
    rules = []
    seen = set()
    for match in _LOCATION.finditer(text):
        body = match.group("body")
        passed = _PASS.search(body)
        if not passed:
            continue
        upstream = passed.group(1)
        path = match.group("path")
        if not _UPSTREAM.fullmatch(upstream):
            continue
        if path != "/" and not _PATH.fullmatch(path):
            continue
        if path in seen:
            continue
        seen.add(path)
        prefix = text[max(0, match.start() - 160) : match.start()]
        named = _NAME.search(prefix)
        name = named.group(1).strip() if named else "proxy"
        rules.append(
            {
                "name": name[:40],
                "path": path,
                "upstream": upstream,
                "target_url": upstream,
                "forward_ip": "X-Real-IP" in body and "$remote_addr" in body,
                "preserve_host": "proxy_set_header Host" in body,
            }
        )
    return rules
