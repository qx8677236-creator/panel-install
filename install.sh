#!/bin/sh
# 新服务器：
#   wget -O install.sh https://raw.githubusercontent.com/qx8677236-creator/panel-install/main/install.sh && sudo bash install.sh
set -eu

DOCKERHUB_USER="${DOCKERHUB_USER:-xiaoqiang001}"
PANEL_TAG="${PANEL_TAG:-1.0}"
INSTALL_DIR=/opt/panel-assistant

die() {
    echo "$1" >&2
    exit 1
}

if [ "$(id -u)" -ne 0 ]; then
    die "请用 root 运行此脚本。"
fi

while true; do
    printf '请输入您想设置的面板初始管理员密码: '
    read -r PANEL_PASSWORD
    if [ -n "$PANEL_PASSWORD" ]; then
        break
    fi
    echo "密码不能为空"
done

BACKEND_IMAGE="${DOCKERHUB_USER}/panel-backend:${PANEL_TAG}"
FRONTEND_IMAGE="${DOCKERHUB_USER}/panel-frontend:${PANEL_TAG}"

if [ -f /etc/os-release ]; then
    . /etc/os-release
else
    die "无法识别操作系统。"
fi

install_docker_debian() {
    apt-get update || die "apt-get update 失败，安装中止。"
    apt-get install -y ca-certificates curl gnupg || die "安装 Docker 前置包失败。"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${ID}/gpg" \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
        || die "下载 Docker 签名密钥失败。"
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update || die "更新 Docker 软件源失败。"
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin \
        || die "安装 Docker 失败。"
}

install_docker_rpm() {
    if command -v dnf >/dev/null 2>&1; then
        dnf install -y dnf-plugins-core curl || die "安装 dnf 插件失败。"
        dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo \
            || die "添加 Docker 软件源失败。"
        dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin \
            || die "安装 Docker 失败。"
    elif command -v yum >/dev/null 2>&1; then
        yum install -y yum-utils curl || die "安装 yum-utils 失败。"
        yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo \
            || die "添加 Docker 软件源失败。"
        yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin \
            || die "安装 Docker 失败。"
    else
        die "没有找到 apt、dnf 或 yum，无法安装 Docker。"
    fi
}

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    case "${ID:-}" in
        ubuntu|debian)
            install_docker_debian
            ;;
        centos|rhel|rocky|almalinux|fedora)
            install_docker_rpm
            ;;
        *)
            die "当前系统 ${ID:-unknown} 不在 Ubuntu、Debian、CentOS 支持范围内。"
            ;;
    esac
fi

if ! command -v curl >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update && apt-get install -y curl || die "安装 curl 失败。"
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y curl || die "安装 curl 失败。"
    elif command -v yum >/dev/null 2>&1; then
        yum install -y curl || die "安装 curl 失败。"
    else
        die "系统没有 curl。"
    fi
fi

systemctl enable --now docker || die "启动 Docker 服务失败。"

if [ -f "${INSTALL_DIR}/docker-compose.yml" ]; then
    docker compose -f "${INSTALL_DIR}/docker-compose.yml" down || true
fi

port_busy() {
    port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltn | grep -q ":${port} "
        return
    fi
    netstat -ltn 2>/dev/null | grep -q ":${port} "
}

if port_busy 8000; then
    die "8000 端口已被占用，停止安装。"
fi
if port_busy 8888; then
    die "8888 端口已被占用，停止安装。"
fi

mkdir -p /www/wwwroot /www/backup /www/server/data "$INSTALL_DIR" \
    || die "创建目录失败。请检查磁盘是否已满。"

umask 077
printf 'PANEL_INIT_PASSWORD=%s\nFILE_ROOT=/www/wwwroot\nNGINX_SYSTEM_MODE=true\n' "$PANEL_PASSWORD" \
    > "${INSTALL_DIR}/panel.env" || die "写入初始密码失败。"
chmod 600 "${INSTALL_DIR}/panel.env"

cat > "${INSTALL_DIR}/docker-compose.yml" << EOF
services:
  backend:
    image: ${BACKEND_IMAGE}
    network_mode: host
    privileged: true
    restart: unless-stopped
    env_file:
      - ${INSTALL_DIR}/panel.env
    volumes:
      - /etc/nginx:/etc/nginx
      - /www:/www
      - /var/run/docker.sock:/var/run/docker.sock
      - panel-data:/app/data

  frontend:
    image: ${FRONTEND_IMAGE}
    network_mode: host
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  panel-data:
EOF

docker pull "$BACKEND_IMAGE" || die "拉取 ${BACKEND_IMAGE} 失败。请确认镜像已公开。"
docker pull "$FRONTEND_IMAGE" || die "拉取 ${FRONTEND_IMAGE} 失败。请确认镜像已公开。"

(
    cd "$INSTALL_DIR"
    docker compose up -d
) || die "启动容器失败。"

lan_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$lan_ip" ]; then
    lan_ip="127.0.0.1"
fi
public_ip=$(curl -s --connect-timeout 5 https://ipify.org || curl -s --connect-timeout 5 https://ifconfig.me || true)
public_ip=$(printf '%s' "$public_ip" | tr -d '[:space:]')
case "$public_ip" in
    *[!0-9a-fA-F.:]*|"") public_ip="请使用您的服务器公网IP" ;;
esac

echo
echo "============================================================"
echo "  面板安装完成"
echo "  面板公网访问地址: http://${public_ip}:8888"
echo "  面板内网访问地址: http://${lan_ip}:8888"
echo "  初始账号: admin"
echo "  初始密码: ${PANEL_PASSWORD}"
echo "  请使用公网地址的 8888 端口登录。"
echo "  若这台机器以前装过面板，已有管理员不会被这次输入覆盖。"
echo "============================================================"
echo
