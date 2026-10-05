"""只运行固定的 nginx / certbot 参数列表。

这里不走通用命令白名单，也不打开 shell。
调用方传入的域名和路径必须已经在校验函数里收干净。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from app.config import settings
from app.nginx.layout import NginxLayout
from app.nginx.security import SiteError, conf_stem, normalize_domain, normalize_email

_PREFIXES = (
    "/usr/sbin/",
    "/usr/bin/",
    "/sbin/",
    "/bin/",
    "/usr/local/sbin/",
    "/usr/local/bin/",
    "/usr/local/opt/",
    "/opt/homebrew/bin/",
    "/opt/homebrew/sbin/",
    "/opt/homebrew/opt/",
    "/opt/homebrew/Cellar/",
)

_runner: Optional[Callable] = None
_nginx_binary = ""
_certbot_binary = ""


def using_fake_runner() -> bool:
    return _runner is not None


def set_command_runner(
    runner: Optional[Callable],
    nginx_binary: str = "/usr/sbin/nginx",
    certbot_binary: str = "/usr/bin/certbot",
) -> None:
    """测试时替换真正的子进程。生产路径不会调用它。"""
    global _runner, _nginx_binary, _certbot_binary
    _runner = runner
    if runner is None:
        _nginx_binary = ""
        _certbot_binary = ""
        return
    _nginx_binary = nginx_binary
    _certbot_binary = certbot_binary


def find_nginx() -> Optional[str]:
    if _runner is not None:
        return _nginx_binary or None
    return _find("nginx", settings.nginx_bin)


def find_certbot() -> Optional[str]:
    if _runner is not None:
        return _certbot_binary or None
    return _find("certbot", settings.certbot_bin)


def nginx_test_argv(layout: NginxLayout, binary: str) -> list:
    if layout.system_mode:
        return [binary, "-t"]
    config = str(layout.prefix / "nginx.conf")
    return [binary, "-t", "-p", str(layout.prefix), "-c", config]


def nginx_reload_argv(layout: NginxLayout, binary: str) -> list:
    if layout.system_mode:
        return [binary, "-s", "reload"]
    config = str(layout.prefix / "nginx.conf")
    return [binary, "-s", "reload", "-p", str(layout.prefix), "-c", config]


def nginx_test(layout: NginxLayout, config: str = "") -> tuple:
    """返回 (退出码, 输出)。机器上没有 nginx 时退出码为 None，不算检查通过。

    普通用户不能绑定 80。检查放在独立的网络命名空间里，不占用宿主机已经在听的端口。
    """
    binary = find_nginx()
    if not binary:
        return None, "未找到 nginx，正式配置没有改动。"
    wrapper = "/usr/local/sbin/panel-nginx-test"
    if config and os.path.isfile(wrapper) and os.access(wrapper, os.X_OK):
        argv = ["/usr/bin/sudo", "-n", wrapper, str(layout.prefix), config]
    elif config:
        argv = [binary, "-t", "-p", str(layout.prefix), "-c", config]
    else:
        argv = nginx_test_argv(layout, binary)
    code, output = run_command(argv, timeout=15)
    return code, output.strip()


_APPLY_WRAPPER = "/usr/local/sbin/panel-nginx-apply"
_CONF_D = Path("/etc/nginx/conf.d")
_RESERVED_PORTS = {22, 53, 5000, 8000, 8888, 18080}
_SITE_NAME = __import__("re").compile(r"^[A-Za-z0-9._-]+$")


def prepare_system_nginx() -> None:
    """新机器没有面板的系统安装程序时，补齐 Nginx 目录和一份最小主配置。

    生产机上 /usr/local/sbin/panel-nginx-apply 存在，这里直接返回，不改现有主配置。
    """
    if os.path.isfile(_APPLY_WRAPPER) or os.geteuid() != 0:
        return
    _CONF_D.mkdir(parents=True, exist_ok=True)
    Path("/var/log/nginx").mkdir(parents=True, exist_ok=True)
    config = Path("/etc/nginx/nginx.conf")
    if config.is_symlink() or (config.is_file() and config.stat().st_size > 0):
        return
    user_line = "user root;\n"
    try:
        import pwd

        for name in ("www-data", "nginx"):
            try:
                pwd.getpwnam(name)
            except KeyError:
                continue
            user_line = f"user {name};\n"
            break
    except Exception:
        user_line = "user root;\n"
    mime = ""
    if Path("/etc/nginx/mime.types").is_file():
        mime = "    include /etc/nginx/mime.types;\n"
    config.write_text(
        user_line
        + "worker_processes auto;\n"
        + "pid /run/nginx.pid;\n"
        + "error_log /var/log/nginx/error.log warn;\n"
        + "events {\n"
        + "    worker_connections 1024;\n"
        + "}\n"
        + "http {\n"
        + mime
        + "    default_type application/octet-stream;\n"
        + "    sendfile on;\n"
        + "    server_tokens off;\n"
        + "    include /etc/nginx/conf.d/*.conf;\n"
        + "}\n",
        encoding="utf-8",
    )
    os.chmod(config, 0o644)


def install_public_site(layout: NginxLayout, domain: str, enabled: bool) -> tuple:
    """把这一个站点交给系统 Nginx。只新增或删除 panel- 开头的配置。"""
    name = conf_stem(domain)
    if os.path.isfile(_APPLY_WRAPPER):
        if enabled:
            source = layout.sites_available / f"{name}.conf"
            argv = ["/usr/bin/sudo", "-n", _APPLY_WRAPPER, "install", name, str(source)]
        else:
            argv = ["/usr/bin/sudo", "-n", _APPLY_WRAPPER, "remove", name]
        return run_command(argv, timeout=20)
    if os.geteuid() != 0:
        return 1, "没有找到系统 Nginx 安装程序，当前用户也不能直接写 Nginx 配置"
    return _install_public_direct(layout, name, enabled)


def _install_public_direct(layout: NginxLayout, name: str, enabled: bool) -> tuple:
    if not _SITE_NAME.fullmatch(name) or ".." in name:
        return 1, "站点名无效"
    prepare_system_nginx()
    source = (layout.sites_available / f"{name}.conf").resolve()
    allowed_dir = layout.sites_available.resolve()
    if source.parent != allowed_dir:
        return 1, "只接受面板生成的站点配置"
    dest = _CONF_D / f"panel-{name}.conf"
    if dest.parent != _CONF_D:
        return 1, "目标路径无效"
    if dest.is_symlink() or source.is_symlink():
        return 1, "目标配置是符号链接，已停止"
    backup = dest.with_name(dest.name + ".bak")
    if not enabled:
        return _remove_public_direct(dest, backup)
    if not source.is_file():
        return 1, "只接受面板生成的站点配置"
    text = source.read_text(encoding="utf-8")
    blocked = [str(port) for port in _listen_ports(text) if port in _RESERVED_PORTS]
    if blocked:
        return 1, "端口 " + ",".join(blocked) + " 已被其他服务使用，没有改动"
    had_file = dest.is_file()
    if had_file:
        shutil.copy2(dest, backup)
    dest.write_text(text, encoding="utf-8")
    os.chmod(dest, 0o644)
    code, output = _system_nginx_test()
    if code != 0:
        _restore_public(dest, backup, had_file)
        return 1, output or "Nginx 配置检查失败，已回滚"
    code, reload_out = _system_nginx_reload_or_start()
    if code != 0:
        _restore_public(dest, backup, had_file)
        _system_nginx_test()
        if _nginx_master_alive():
            _system_nginx_reload()
        return 1, reload_out or output or "Nginx 重载失败，已回滚"
    if backup.exists() and not backup.is_symlink():
        backup.unlink()
    return 0, output or "站点已加载到系统 Nginx"


def _remove_public_direct(dest: Path, backup: Path) -> tuple:
    had_file = dest.is_file()
    if had_file:
        shutil.copy2(dest, backup)
        dest.unlink()
    code, output = _system_nginx_test()
    if code != 0:
        _restore_public(dest, backup, had_file)
        return 1, output or "Nginx 配置检查失败，已停止加载"
    if _nginx_master_alive():
        code, reload_out = _system_nginx_reload()
        if code != 0:
            _restore_public(dest, backup, had_file)
            _system_nginx_test()
            _system_nginx_reload()
            return 1, reload_out or "Nginx 重载失败，已回滚"
    if had_file and backup.is_file() and not backup.is_symlink():
        backup.unlink()
    return 0, output or "已从系统 Nginx 卸下该站点"


def _restore_public(dest: Path, backup: Path, had_file: bool) -> None:
    if backup.is_file() and not backup.is_symlink():
        shutil.copy2(backup, dest)
        return
    if dest.exists() and not dest.is_symlink() and not had_file:
        dest.unlink()


def _listen_ports(text: str) -> list:
    import re

    ports = []
    for line in text.splitlines():
        matched = re.search(r"\blisten\s+([^;]+);", line)
        if not matched:
            continue
        token = matched.group(1).split()[0]
        port = token.rsplit(":", 1)[-1]
        if port.isdigit():
            ports.append(int(port))
    return ports


def _nginx_master_alive() -> bool:
    pid_file = Path("/run/nginx.pid")
    try:
        text = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not text.isdigit():
        return False
    return Path(f"/proc/{text}").exists()


def _system_nginx_test() -> tuple:
    binary = find_nginx()
    if not binary:
        return 1, "未找到 nginx"
    return run_command([binary, "-t"], timeout=20)


def _system_nginx_reload() -> tuple:
    binary = find_nginx()
    if not binary:
        return 1, "未找到 nginx"
    return run_command([binary, "-s", "reload"], timeout=20)


def _system_nginx_reload_or_start() -> tuple:
    if _nginx_master_alive():
        return _system_nginx_reload()
    binary = find_nginx()
    if not binary:
        return 1, "未找到 nginx"
    err_path = Path("/tmp/panel-nginx-start.err")
    try:
        if err_path.exists():
            err_path.unlink()
        with err_path.open("w", encoding="utf-8") as handle:
            subprocess.Popen(
                [binary, "-g", "daemon off;"],
                stdout=subprocess.DEVNULL,
                stderr=handle,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                cwd="/",
            )
        for _ in range(15):
            if _nginx_master_alive():
                return 0, "Nginx 已启动"
            time.sleep(0.2)
        detail = ""
        try:
            detail = err_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            detail = ""
        return 1, detail or "Nginx 没有启动"
    finally:
        try:
            if err_path.exists():
                err_path.unlink()
        except OSError:
            pass


def nginx_reload(layout: NginxLayout) -> tuple:
    binary = find_nginx()
    if not binary:
        return None, "未找到 nginx，配置已保存，未重载。"
    if not layout.system_mode:
        pid = layout.prefix / "logs" / "nginx.pid"
        pid_text = ""
        if pid.is_file() and not pid.is_symlink():
            pid_text = pid.read_text(encoding="utf-8", errors="replace").strip()
        if not pid_text.isdigit():
            return 0, "配置已写入面板目录，没有改动系统 Nginx 的端口。"
    code, output = run_command(nginx_reload_argv(layout, binary), timeout=15)
    return code, output.strip()


def build_certbot_argv(binary: str, domain: str, webroot, email: str) -> list:
    """只用 webroot 申请证书，不用 certbot 的 nginx 插件，避免它绕过语法检查改配置。"""
    domain = normalize_domain(domain)
    if domain.startswith("*."):
        raise SiteError("通配符域名不能用网站目录申请证书")
    email = normalize_email(email)
    root = str(webroot)
    if not os.path.isabs(root) or not os.path.isdir(root):
        raise SiteError("网站目录无效")
    if any(char in root for char in "\n\r;{}`'\"$"):
        raise SiteError("网站目录包含不能用于命令的字符")
    if os.path.basename(binary) != "certbot":
        raise SiteError("证书程序无效")
    cert_name = conf_stem(domain)
    return [
        binary,
        "certonly",
        "--webroot",
        "-w",
        root,
        "-d",
        domain,
        "--cert-name",
        cert_name,
        "--non-interactive",
        "--agree-tos",
        "--email",
        email,
        "--keep-until-expiring",
    ]


def run_command(argv: list, timeout: int) -> tuple:
    if not argv or not os.path.isabs(argv[0]):
        raise SiteError("命令路径无效")
    if any((not isinstance(arg, str)) or ("\x00" in arg) or ("\n" in arg) or ("\r" in arg) for arg in argv):
        raise SiteError("命令参数无效")
    if _runner is not None:
        code, output = _runner(list(argv))
        return int(code), str(output)[-16000:]
    try:
        # shell=False：参数列表不会再被外壳拆开。
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/",
        )
    except subprocess.TimeoutExpired:
        return 1, "命令超时"
    except OSError as exc:
        return 1, str(exc)
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return completed.returncode, output[-16000:]


def _find(name: str, override: str) -> Optional[str]:
    # 环境变量里指定的程序由管理员决定，只核对文件名。
    # 自动搜到的程序必须落在固定目录里，避免 PATH 被塞进同名文件。
    if override:
        return _explicit(name, override)
    candidate = shutil.which(name) or ""
    if not candidate:
        return None
    resolved = _explicit(name, candidate)
    if resolved is None:
        return None
    if not (_allowed(candidate) or _allowed(resolved)):
        return None
    return resolved


def _explicit(name: str, candidate: str) -> Optional[str]:
    path = os.path.abspath(candidate)
    if os.path.basename(path) != name or not os.path.isfile(path):
        return None
    resolved = os.path.realpath(path)
    if os.path.basename(resolved) != name or not os.access(resolved, os.X_OK):
        return None
    return resolved


def _allowed(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _PREFIXES)
