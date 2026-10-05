"""站点管理接口。配置写入和重载都放在 service 里，这里只做参数接收。"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.auth.deps import current_user
from app.files.security import FileOpError, PathJailError, set_actor
from app.nginx import service
from app.nginx.security import SiteError

router = APIRouter()


def _actor(username: str = Depends(current_user)) -> str:
    set_actor(username)
    return username


router.dependencies.append(Depends(_actor))


class CreateIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    root: str = Field(default="", max_length=4096)
    kind: str = Field(default="html", max_length=16)


class DeleteIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    delete_files: bool = False
    delete_database: bool = False
    confirm: str = Field(default="", max_length=4096)


class ToggleIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    enabled: bool


class RewriteIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    preset: str = Field(min_length=1, max_length=32)
    custom: str = Field(default="", max_length=4000)


class ProxyItem(BaseModel):
    name: str = Field(default="", max_length=40)
    path: str = Field(default="/", min_length=1, max_length=200)
    upstream: str = Field(default="", max_length=300)
    target_url: str = Field(default="", max_length=300)
    forward_ip: bool = True


class ProxyIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    proxies: List[ProxyItem] = Field(default_factory=list, max_length=20)


class SaveProxyIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    rules: List[ProxyItem] = Field(default_factory=list, max_length=20)
    proxies: List[ProxyItem] = Field(default_factory=list, max_length=20)


class SslIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    enabled: bool
    force_https: bool = True


class IssueIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    email: str = Field(min_length=3, max_length=254)


def _call(func):
    try:
        return func()
    except PathJailError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": exc.code, "reason": exc.reason, "message": exc.message},
        ) from None
    except FileOpError as exc:
        raise HTTPException(status_code=exc.status, detail={"message": str(exc), "log": ""}) from None
    except SiteError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"message": exc.message, "log": exc.log},
        ) from None


@router.get("")
def list_sites() -> dict:
    return _call(service.list_sites)


@router.get("/detail")
def detail(domain: str) -> dict:
    return _call(lambda: service.site_detail(domain))


@router.get("/diagnose")
def diagnose(domain: str) -> dict:
    return _call(lambda: service.diagnose_site(domain))


@router.post("/create")
def create_site(body: CreateIn) -> dict:
    return _call(lambda: service.create_site(body.domain, body.root, body.kind))


@router.post("/delete")
def delete_site(body: DeleteIn, request: Request, username: str = Depends(current_user)) -> dict:
    host = request.client.host if request.client else "unknown"
    return _call(
        lambda: service.delete_site(
            body.domain,
            body.delete_files,
            body.delete_database,
            body.confirm,
            username,
            host,
        )
    )


@router.post("/toggle")
def toggle_site(body: ToggleIn) -> dict:
    return _call(lambda: service.set_enabled(body.domain, body.enabled))


@router.post("/rewrite")
def rewrite_site(body: RewriteIn) -> dict:
    return _call(lambda: service.update_rewrite(body.domain, body.preset, body.custom))


@router.post("/proxy")
def proxy_site(body: ProxyIn) -> dict:
    items = [item.model_dump() for item in body.proxies]
    return _call(lambda: service.update_proxies(body.domain, items))


@router.get("/proxy-config")
def proxy_config(domain: str) -> dict:
    return _call(lambda: service.proxy_config(domain))


@router.post("/save-proxy")
def save_proxy(body: SaveProxyIn) -> dict:
    items = [item.model_dump() for item in (body.rules or body.proxies)]
    return _call(lambda: service.update_proxies(body.domain, items))


alias_router = APIRouter()
alias_router.dependencies.append(Depends(_actor))
alias_router.add_api_route("/proxy-config", proxy_config, methods=["GET"])
alias_router.add_api_route("/save-proxy", save_proxy, methods=["POST"])


@alias_router.get("/test-nginx-env")
def test_nginx_env() -> dict:
    return _call(service.nginx_env_report)


@router.post("/ssl")
def ssl_site(body: SslIn) -> dict:
    return _call(lambda: service.set_ssl(body.domain, body.enabled, body.force_https))


@router.post("/ssl/issue")
def issue_site(body: IssueIn) -> dict:
    return _call(lambda: service.issue_certificate(body.domain, body.email))


class DomainItem(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    port: str = Field(default="80")

    @field_validator("port", mode="before")
    @classmethod
    def keep_port_text(cls, value):
        if isinstance(value, bool) or value is None:
            raise ValueError("端口无效")
        text = str(value).strip()
        if not text.isdigit() or not 1 <= len(text) <= 5:
            raise ValueError("端口无效")
        number = int(text)
        if not 1 <= number <= 65535:
            raise ValueError("端口无效")
        return text


class DomainsIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    domains: List[DomainItem] = Field(default_factory=list, max_length=20)


class BindingItem(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    subdir: str = Field(min_length=1, max_length=200)


class BindingsIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    bindings: List[BindingItem] = Field(default_factory=list, max_length=20)


class AccessUser(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    password: str = Field(default="", max_length=64)


class AccessIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    enabled: bool
    realm: str = Field(default="Restricted", max_length=40)
    users: List[AccessUser] = Field(default_factory=list, max_length=20)


class LimitIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    conn: int = Field(default=0, ge=0, le=500)
    rate: int = Field(default=0, ge=0, le=500)


class HotlinkIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    enabled: bool
    domains: List[str] = Field(default_factory=list, max_length=20)


class RedirectItem(BaseModel):
    path: str = Field(default="/", min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=300)
    code: int


class RedirectsIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    redirects: List[RedirectItem] = Field(default_factory=list, max_length=20)


class PemIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    certificate: str = Field(min_length=1, max_length=100000)
    key: str = Field(min_length=1, max_length=100000)
    force_https: bool = True


class RawIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    content: str = Field(min_length=1, max_length=65536)


@router.post("/domains")
def domains_site(body: DomainsIn) -> dict:
    items = [item.model_dump() for item in body.domains]
    return _call(lambda: service.update_domains(body.domain, items))


@router.post("/bindings")
def bindings_site(body: BindingsIn) -> dict:
    items = [item.model_dump() for item in body.bindings]
    return _call(lambda: service.update_bindings(body.domain, items))


@router.post("/access")
def access_site(body: AccessIn) -> dict:
    users = [item.model_dump() for item in body.users]
    return _call(lambda: service.update_access(body.domain, body.enabled, body.realm, users))


@router.post("/limit")
def limit_site(body: LimitIn) -> dict:
    return _call(lambda: service.update_limit(body.domain, {"conn": body.conn, "rate": body.rate}))


@router.post("/hotlink")
def hotlink_site(body: HotlinkIn) -> dict:
    return _call(lambda: service.update_hotlink(body.domain, {"enabled": body.enabled, "domains": body.domains}))


@router.post("/redirects")
def redirects_site(body: RedirectsIn) -> dict:
    items = [item.model_dump() for item in body.redirects]
    return _call(lambda: service.update_redirects(body.domain, items))


@router.post("/ssl/paste")
def paste_certificate(body: PemIn) -> dict:
    return _call(
        lambda: service.save_certificate_text(body.domain, body.certificate, body.key, body.force_https)
    )


@router.post("/config")
def save_config(body: RawIn) -> dict:
    return _call(lambda: service.save_raw_config(body.domain, body.content))


@router.get("/logs")
def site_logs(domain: str, kind: str = "access", offset: int = 0) -> dict:
    return _call(lambda: service.read_site_log(domain, kind, offset))


class SecurityIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    enabled: bool = False
    x_frame_options: str = ""
    nosniff: bool = False
    xss: bool = False
    referrer: str = ""
    hsts: bool = False


class AcmeRecordIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    email: str = Field(min_length=3, max_length=254)


class AcmeRenewIn(BaseModel):
    index: str = Field(min_length=1, max_length=12)


class AcmeDeployIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    ssl_hash: str = Field(min_length=1, max_length=80)


@router.get("/security-headers")
def get_security(domain: str) -> dict:
    detail = _call(lambda: service.site_detail(domain))
    return {"domain": domain, "security_headers": detail.get("security_headers") or {}}


@router.post("/security-headers")
def save_security(body: SecurityIn) -> dict:
    payload = body.model_dump()
    domain = payload.pop("domain")
    return _call(lambda: service.update_security_headers(domain, payload))


@router.get("/acme/orders")
def acme_orders(domain: str = "") -> dict:
    from app.nginx import acme

    return _call(lambda: acme.list_orders(domain))


@router.post("/acme/record")
def acme_record(body: AcmeRecordIn) -> dict:
    from app.nginx import acme

    return _call(lambda: acme.record_order(body.domain, body.email))


@router.post("/acme/renew")
def acme_renew(body: AcmeRenewIn) -> dict:
    from app.nginx import acme

    return _call(lambda: acme.renew_order(body.index))


@router.get("/acme/certs")
def acme_certs() -> dict:
    from app.nginx import acme

    return _call(acme.list_certs)


@router.post("/acme/deploy")
def acme_deploy(body: AcmeDeployIn) -> dict:
    from app.nginx import acme

    return _call(lambda: acme.deploy_cert(body.domain, body.ssl_hash))
