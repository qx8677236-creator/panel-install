"""站点参数校验。

表单里的域名、目录、伪静态和反代都先收成固定格式。
手写整份配置时，只允许改这一份站点文件，并且路径不能跳出网站目录和面板自己的配置目录。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN = re.compile(rf"^(?:\*\.)?{_LABEL}(?:\.{_LABEL})+$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}$")
_LOCATION = re.compile(r"^/(?:[A-Za-z0-9._~-]+/)*[A-Za-z0-9._~-]*$")
_HOST = (
    r"(?:localhost|[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"|(?:\d{1,3}\.){3}\d{1,3})"
)
_UPSTREAM = re.compile(rf"^https?://{_HOST}(?::(\d{{1,5}}))?(?:/[A-Za-z0-9._~%/-]*)?$")
_REWRITE_HEAD = ("rewrite", "try_files", "return", "index", "break")
_REWRITE_VARS = {
    "$uri",
    "$request_uri",
    "$request_filename",
    "$args",
    "$query_string",
    "$host",
    "$scheme",
    "$document_root",
    "$is_args",
}
_PRESETS = {
    "none": "try_files $uri $uri/ /index.html;",
    "wordpress": "try_files $uri $uri/ /index.php?$args;",
    "laravel": "try_files $uri $uri/ /index.php?$query_string;",
    "thinkphp": "try_files $uri $uri/ /index.php?s=$uri&$args;",
}


class SiteError(Exception):
    def __init__(self, message: str, status: int = 400, log: str = ""):
        self.message = message
        self.status = status
        self.log = log
        super().__init__(message)


def _ipv4(text: str) -> bool:
    parts = text.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or len(part) > 3:
            return False
        number = int(part)
        if number > 255:
            return False
    return True


def normalize_domain(raw: str) -> str:
    if not isinstance(raw, str):
        raise SiteError("域名无效")
    text = raw.strip().lower().rstrip(".")
    if _ipv4(text):
        return text
    if len(text) > 253 or not _DOMAIN.fullmatch(text):
        raise SiteError("域名无效。可以填写域名或 IP，例如 www.example.com、20.187.70.213，也可以写成 20.187.70.213:9999")
    return text


def conf_stem(domain: str) -> str:
    """配置文件名只保留安全字符。地址里写了端口时，文件名带上端口，避免同一 IP 互相覆盖。"""
    parsed = parse_domain_port(domain)
    name = parsed["domain"]
    stem = "wildcard." + name[2:] if name.startswith("*.") else name
    raw = str(domain).strip().lower().rstrip(".")
    label = str(parsed.get("port_text") or parsed["port"])
    if raw.endswith(":" + label) and not (parsed["port"] == 80 and label == "80"):
        stem = f"{stem}.{label}"
    if ".." in stem or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", stem):
        raise SiteError("域名无效")
    return stem


def default_root_name(domain: str) -> str:
    domain = normalize_domain(domain)
    return domain[2:] if domain.startswith("*") else domain


def normalize_email(raw: str) -> str:
    if not isinstance(raw, str):
        raise SiteError("邮箱无效")
    text = raw.strip()
    if not _EMAIL.fullmatch(text):
        raise SiteError("邮箱无效")
    return text


def validate_rewrite(text: str) -> str:
    """只留下伪静态常用指令。每一行都要能单独放进 location 块。"""
    if not isinstance(text, str):
        raise SiteError("伪静态内容无效")
    if len(text) > 4000:
        raise SiteError("伪静态内容过长")
    kept = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if any(char in line for char in "{}\r`'\"\\#"):
            raise SiteError("伪静态包含不允许的字符")
        if not line.endswith(";"):
            raise SiteError("伪静态每一行都要以分号结尾")
        if len(line) > 240:
            raise SiteError("伪静态单行过长")
        head = line.split(None, 1)[0].lower()
        if head not in _REWRITE_HEAD:
            raise SiteError("伪静态只允许 rewrite、try_files、return、index、break")
        for var in re.findall(r"\$[A-Za-z_][A-Za-z0-9_]*", line):
            if var not in _REWRITE_VARS:
                raise SiteError("伪静态包含不允许的变量")
        stripped = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "", line)
        stripped = re.sub(r"\$\d+", "", stripped)
        # 剩下的 $ 只能是正则结尾，例如 ^(.*)$ ，后面必须是空白或分号。
        if re.search(r"\$[^\s;]", stripped):
            raise SiteError("伪静态包含不允许的变量")
        kept.append(line)
    if len(kept) > 40:
        raise SiteError("伪静态行数过多")
    return "\n".join(kept)


def compile_rewrite(preset: str, custom: str) -> tuple:
    if preset not in _PRESETS and preset != "custom":
        raise SiteError("不支持的伪静态方案")
    if preset == "custom":
        body = validate_rewrite(custom or "")
        if not body:
            raise SiteError("自定义伪静态不能为空")
        return "custom", body, body
    stored = validate_rewrite(custom) if (custom or "").strip() else ""
    return preset, _PRESETS[preset], stored


def rewrite_templates() -> dict:
    return dict(_PRESETS)


def parse_domain_port(item, default_port: int = 80) -> dict:
    """域名可以写成 example.com，也可以写成 example.com:88。IP 和 *.example.com 同样支持。"""
    port = default_port
    if isinstance(item, dict):
        domain = normalize_domain(str(item.get("domain", "")))
        raw_port = item.get("port", default_port)
    else:
        text = str(item).strip().lower().rstrip(".")
        raw_port = default_port
        matched = re.fullmatch(r"(.+):(\d{1,5})", text)
        if matched:
            text = matched.group(1).rstrip(".")
            raw_port = matched.group(2)
        domain = normalize_domain(text)
    if isinstance(raw_port, bool) or raw_port is None:
        raise SiteError("端口无效")
    label = str(raw_port).strip()
    if not re.fullmatch(r"\d{1,5}", label):
        raise SiteError("端口无效")
    try:
        port = int(label)
    except (TypeError, ValueError):
        raise SiteError("端口无效") from None
    if not 1 <= port <= 65535:
        raise SiteError("端口无效")
    return {"domain": domain, "port": port, "port_text": label}


def validate_domains(primary: str, domains) -> list:
    primary_parsed = parse_domain_port(primary)
    primary_name = primary_parsed["domain"]
    if not isinstance(domains, list):
        raise SiteError("域名列表无效")
    cleaned = []
    seen = set()
    for item in domains:
        parsed = parse_domain_port(item)
        label = str(parsed.get("port_text") or parsed["port"])
        key = (parsed["domain"], label)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"domain": parsed["domain"], "port": label})
    if not any(item["domain"] == primary_name for item in cleaned):
        cleaned.insert(0, {"domain": primary_name, "port": str(primary_parsed.get("port_text") or primary_parsed["port"])})
    if len(cleaned) > 20:
        raise SiteError("绑定域名过多")
    return cleaned


def validate_binding(item: dict, primary: str) -> dict:
    if not isinstance(item, dict):
        raise SiteError("子目录绑定无效")
    domain = normalize_domain(str(item.get("domain", "")))
    if domain == parse_domain_port(primary)["domain"]:
        raise SiteError("子目录绑定不能使用站点主域名")
    subdir = str(item.get("subdir", "")).strip().strip("/")
    if not subdir or ".." in subdir.split("/") or not re.fullmatch(r"[A-Za-z0-9._/-]+", subdir):
        raise SiteError("子目录无效")
    if any(char in subdir for char in " ;{}#'\"\\`$\n\r"):
        raise SiteError("子目录无效")
    return {"domain": domain, "subdir": subdir}


def validate_bindings(items, primary: str) -> list:
    if not isinstance(items, list):
        raise SiteError("子目录绑定无效")
    if len(items) > 20:
        raise SiteError("子目录绑定过多")
    cleaned = [validate_binding(item, primary) for item in items]
    domains = [item["domain"] for item in cleaned]
    if len(domains) != len(set(domains)):
        raise SiteError("子目录绑定的域名重复")
    overlap = set(domains) & {item["domain"] for item in validate_domains(primary, [])}
    if overlap:
        raise SiteError("子目录绑定的域名和主站域名重复")
    return cleaned


def validate_proxy(item: dict) -> dict:
    if not isinstance(item, dict):
        raise SiteError("反向代理参数无效")
    path = str(item.get("path", "")).strip() or "/"
    upstream = str(item.get("target_url") or item.get("upstream") or "").strip()
    name = str(item.get("name", "")).replace("\n", " ").replace("\r", " ").strip()
    name = re.sub(r"[#\"';{}]", "", name)[:40] or "proxy"
    if path != "/" and not _LOCATION.fullmatch(path):
        raise SiteError("代理路径无效。请使用 / 或 /api 这种路径")
    parts = [part for part in path.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise SiteError("代理路径无效")
    matched = _UPSTREAM.fullmatch(upstream)
    if not matched or any(char in upstream for char in " ;{}#'\"\\`\n\r"):
        raise SiteError("代理目标无效。只接受 http 或 https 地址")
    port = matched.group(1)
    if port is not None and not 1 <= int(port) <= 65535:
        raise SiteError("代理目标端口无效")
    host = upstream.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", host):
        if any(int(piece) > 255 for piece in host.split(".")):
            raise SiteError("代理目标地址无效")
    flag = item.get("forward_ip", True)
    if isinstance(flag, str):
        forward_ip = flag.strip().lower() not in {"0", "false", "no", "off"}
    else:
        forward_ip = bool(flag)
    return {
        "name": name,
        "path": path,
        "upstream": upstream,
        "target_url": upstream,
        "forward_ip": forward_ip,
    }


def validate_proxies(items) -> list:
    if not isinstance(items, list):
        raise SiteError("反向代理参数无效")
    if len(items) > 20:
        raise SiteError("反向代理数量过多")
    cleaned = [validate_proxy(item) for item in items]
    paths = [item["path"] for item in cleaned]
    if len(paths) != len(set(paths)):
        raise SiteError("反向代理路径重复")
    return cleaned


def validate_limit(item: dict) -> dict:
    if not isinstance(item, dict):
        raise SiteError("流量限制参数无效")
    try:
        conn = int(item.get("conn") or 0)
        rate = int(item.get("rate") or 0)
    except (TypeError, ValueError):
        raise SiteError("流量限制必须是整数") from None
    if not 0 <= conn <= 500 or not 0 <= rate <= 500:
        raise SiteError("并发和每秒请求数需要在 0 到 500 之间，0 表示不限制")
    return {"conn": conn, "rate": rate}


def validate_hotlink(item: dict) -> dict:
    if not isinstance(item, dict):
        raise SiteError("防盗链参数无效")
    domains = item.get("domains") or []
    if isinstance(domains, str):
        domains = [line.strip() for line in domains.splitlines() if line.strip()]
    if not isinstance(domains, list) or len(domains) > 20:
        raise SiteError("防盗链域名过多")
    cleaned = []
    for domain in domains:
        normalized = normalize_domain(str(domain))
        if normalized not in cleaned:
            cleaned.append(normalized)
    return {"enabled": bool(item.get("enabled")), "domains": cleaned}


def validate_redirect(item: dict) -> dict:
    if not isinstance(item, dict):
        raise SiteError("重定向参数无效")
    path = str(item.get("path", "/")).strip() or "/"
    if path != "/" and not _LOCATION.fullmatch(path):
        raise SiteError("重定向路径无效")
    if any(part in {".", ".."} for part in path.split("/")):
        raise SiteError("重定向路径无效")
    target = str(item.get("target", "")).strip().rstrip("/")
    matched = _UPSTREAM.fullmatch(target)
    if not matched or any(char in target for char in " ;{}#'\"\\`$\n\r"):
        raise SiteError("重定向目标无效。只接受 http 或 https 地址")
    try:
        code = int(item.get("code"))
    except (TypeError, ValueError):
        raise SiteError("重定向状态码无效") from None
    if code not in (301, 302):
        raise SiteError("重定向只支持 301 或 302")
    return {"path": path, "target": target, "code": code}


def validate_redirects(items) -> list:
    if not isinstance(items, list):
        raise SiteError("重定向参数无效")
    if len(items) > 20:
        raise SiteError("重定向数量过多")
    cleaned = [validate_redirect(item) for item in items]
    paths = [item["path"] for item in cleaned]
    if len(paths) != len(set(paths)):
        raise SiteError("重定向路径重复")
    return cleaned


def validate_auth_name(name: str) -> str:
    text = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", text):
        raise SiteError("访问限制的用户名无效")
    return text


def validate_realm(realm: str) -> str:
    text = str(realm or "Restricted").strip() or "Restricted"
    if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,40}", text):
        raise SiteError("认证提示文字无效")
    return text


def apr1_hash(password: str, salt: str = "") -> str:
    """生成 Nginx auth_basic 能识别的 APR1 密码哈希。"""
    if not isinstance(password, str) or not password or len(password) > 64:
        raise SiteError("访问密码无效")
    if any(char in password for char in "\n\r\x00:"):
        raise SiteError("访问密码包含不能使用的字符")
    cleaned = re.sub(r"[^A-Za-z0-9./]", "", salt)[:8]
    if not cleaned:
        cleaned = os.urandom(4).hex()[:8]
    pw = password.encode("utf-8")
    salt_b = cleaned.encode("ascii")
    digest = hashlib.md5(pw + b"$apr1$" + salt_b)
    alt = hashlib.md5(pw + salt_b + pw).digest()
    size = len(pw)
    while size > 0:
        digest.update(alt[:16] if size >= 16 else alt[:size])
        size -= 16
    index = len(pw)
    while index:
        digest.update(b"\x00" if index & 1 else pw[:1])
        index >>= 1
    final = digest.digest()
    for round_index in range(1000):
        block = hashlib.md5()
        block.update(pw if round_index & 1 else final)
        if round_index % 3:
            block.update(salt_b)
        if round_index % 7:
            block.update(pw)
        block.update(final if round_index & 1 else pw)
        final = block.digest()
    alphabet = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    def pack(value, count):
        chars = []
        while count > 0:
            chars.append(alphabet[value & 0x3F])
            value >>= 6
            count -= 1
        return "".join(chars)

    encoded = (
        pack((final[0] << 16) | (final[6] << 8) | final[12], 4)
        + pack((final[1] << 16) | (final[7] << 8) | final[13], 4)
        + pack((final[2] << 16) | (final[8] << 8) | final[14], 4)
        + pack((final[3] << 16) | (final[9] << 8) | final[15], 4)
        + pack((final[4] << 16) | (final[10] << 8) | final[5], 4)
        + pack(final[11], 2)
    )
    return f"$apr1${cleaned}${encoded}"


def validate_pem(certificate: str, key: str) -> tuple:
    certificate = str(certificate or "").strip() + "\n"
    key = str(key or "").strip() + "\n"
    if "-----BEGIN CERTIFICATE-----" not in certificate or "-----END CERTIFICATE-----" not in certificate:
        raise SiteError("证书内容需要是 PEM 格式")
    if not any(mark in key for mark in ("-----BEGIN PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN EC PRIVATE KEY-----")):
        raise SiteError("私钥内容需要是 PEM 格式")
    if len(certificate) > 100000 or len(key) > 100000:
        raise SiteError("证书内容过长")
    for text in (certificate, key):
        if "\x00" in text:
            raise SiteError("证书内容无效")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("-----"):
                continue
            if not re.fullmatch(r"[A-Za-z0-9+/=]+", stripped):
                raise SiteError("证书内容里只能有 PEM 文本")
    return certificate, key


def audit_raw_config(text: str, layout) -> str:
    """手写配置仍只能引用网站目录和这块面板自己的配置目录。"""
    if not isinstance(text, str):
        raise SiteError("配置内容无效")
    if "\x00" in text or len(text) > 65536:
        raise SiteError("配置内容无效或过长")
    lowered = text.lower()
    if any(token in lowered for token in ("lua_", "perl ", "load_module", "eval ")):
        raise SiteError("配置包含不允许的指令")
    assigned = re.findall(
        r"(?m)^\s*(root|alias|include|ssl_certificate_key|ssl_certificate|auth_basic_user_file|access_log|error_log)\s+(\S+)",
        text,
    )
    if not assigned and "server" not in lowered:
        raise SiteError("配置里没有站点内容")
    for kind, raw_path in assigned:
        path = raw_path.strip().rstrip(";").strip("'\"")
        if raw_path.strip().startswith("$"):
            continue
        if any(char in path for char in "\n\r;{}#`'"):
            raise SiteError("配置里的路径无效")
        _assert_config_path(kind, path, layout)
    return text if text.endswith("\n") else text + "\n"


def _assert_config_path(kind: str, path: str, layout) -> None:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise SiteError("配置里的路径必须是绝对路径")
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        raise SiteError("配置里的路径无效") from None
    file_root = layout.file_root.resolve()
    prefix = layout.prefix.resolve()
    lets = Path("/etc/letsencrypt")
    if kind in {"root", "alias"}:
        if not candidate.exists() or not candidate.is_dir() or not inside(resolved, file_root):
            raise SiteError("网站目录必须位于文件根目录内")
        return
    if kind in {"ssl_certificate", "ssl_certificate_key"}:
        bases = [layout.certs.resolve()]
        if lets.is_dir():
            bases.append(lets.resolve())
        if not candidate.is_file() or not any(inside(resolved, base) for base in bases):
            raise SiteError("证书路径不在允许的目录内")
        return
    if kind == "include":
        allowed = [prefix / "rewrite", prefix / "pending", prefix / "sites-test", layout.sites_enabled]
        if layout.system_mode:
            allowed.append(Path("/etc/nginx"))
        if not any(_inside_base(resolved, base) for base in allowed):
            raise SiteError("include 路径不在允许的目录内")
        return
    if kind == "auth_basic_user_file":
        if not _inside_base(resolved, prefix / "passwords"):
            raise SiteError("密码文件路径无效")
        return
    if kind in {"access_log", "error_log"}:
        if not _inside_base(resolved, layout.logs):
            raise SiteError("日志路径必须位于面板的日志目录")
        return


def _inside_base(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def nginx_quote(path: str) -> str:
    """路径写入配置时加引号，并拒绝能截断指令的字符。"""
    if not isinstance(path, str) or not path or any(char in path for char in "\n\r;{}#'\""):
        raise SiteError("路径包含不能写入 Nginx 配置的字符")
    return '"' + path + '"'


def inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def confirm_cert(path: Path, cert_root: Path) -> Path:
    """证书只能来自面板证书目录或 Let's Encrypt 的固定目录。"""
    if not path.is_file():
        raise SiteError("证书文件不存在")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise SiteError("证书文件无法读取") from None
    bases = [cert_root.resolve()]
    lets = Path("/etc/letsencrypt")
    if lets.is_dir():
        bases.append(lets.resolve())
    if not any(inside(resolved, base) for base in bases):
        raise SiteError("证书文件超出允许的目录")
    text = path.as_posix()
    if any(char in text for char in "\n\r;{}#'\""):
        raise SiteError("证书路径包含非法字符")
    return path

