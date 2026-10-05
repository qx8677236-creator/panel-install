"""ACME 订单只做记录和已有证书部署，不向证书机构发起申请。"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from app.nginx.security import SiteError, conf_stem

_lock = threading.Lock()
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}")
_HASH = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _store() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "nginx" / "acme-orders.json"


def _load() -> list:
    path = _store()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(items: list) -> None:
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def list_orders(domain: str = "") -> dict:
    items = _load()
    if domain:
        items = [item for item in items if item.get("siteName") == domain]
    return {"items": items}


def record_order(domain: str, email: str) -> dict:
    from app.nginx import service

    service.site_detail(domain)
    if not isinstance(email, str) or not _EMAIL.fullmatch(email):
        raise SiteError("邮箱格式无效")
    with _lock:
        items = _load()
        index = str(max((int(item["index"]) for item in items if str(item.get("index", "")).isdigit()), default=0) + 1)
        order = {
            "index": index,
            "siteName": domain,
            "domains": [domain],
            "email": email,
            "auth_type": "http",
            "status": "recorded",
            "message": "申请已记录，尚未向证书机构发起请求",
        }
        items.append(order)
        _save(items)
    return order


def renew_order(index: str) -> dict:
    if not str(index).isdigit():
        raise SiteError("订单编号无效")
    with _lock:
        items = _load()
        for item in items:
            if str(item.get("index")) == str(index):
                item["status"] = "recorded"
                item["message"] = "续签已记录，尚未向证书机构发起请求"
                _save(items)
                return item
    raise SiteError("没有这个证书订单", 404)


def list_certs() -> dict:
    from app.nginx.layout import get_layout

    folder = get_layout().certs
    items = []
    if folder.is_dir():
        for child in sorted(folder.iterdir()):
            if not child.is_dir() or child.is_symlink() or not _HASH.fullmatch(child.name):
                continue
            cert = child / "fullchain.pem"
            key = child / "privkey.pem"
            if cert.is_file() and key.is_file() and not cert.is_symlink() and not key.is_symlink():
                items.append({"ssl_hash": child.name, "siteName": child.name})
    return {"items": items}


def deploy_cert(domain: str, ssl_hash: str) -> dict:
    from app.nginx import service
    from app.nginx.layout import get_layout

    if not isinstance(ssl_hash, str) or not _HASH.fullmatch(ssl_hash):
        raise SiteError("证书编号无效")
    folder = get_layout().certs / ssl_hash
    cert = folder / "fullchain.pem"
    key = folder / "privkey.pem"
    if not cert.is_file() or not key.is_file() or cert.is_symlink() or key.is_symlink():
        raise SiteError("证书夹里还没有这个证书，没有向证书机构发起申请")
    if conf_stem(domain) != ssl_hash and ssl_hash != domain:
        raise SiteError("只能把本站点自己的证书部署回去")
    return service.save_certificate_text(
        domain,
        cert.read_text(encoding="utf-8"),
        key.read_text(encoding="utf-8"),
        True,
    )
