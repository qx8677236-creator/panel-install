#!/bin/sh
# 新服务器一条命令：
#   wget -O install.sh <这个文件的公网地址> && bash install.sh
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

if [ -z "$DOCKERHUB_USER" ]; then
    die "缺少 Docker Hub 用户名。"
fi

BACKEND_IMAGE="${DOCKERHUB_USER}/panel-backend:${PANEL_TAG}"
FRONTEND_IMAGE="${DOCKERHUB_USER}/panel-frontend:${PANEL_TAG}"

port_busy() {
    port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltn | grep -q ":${port} "
        return
    fi
    netstat -ltn 2>/dev/null | grep -q ":${port} "
}

if port_busy 8000; then
    die "8000 端口已被占用，停止安装。不要在已经运行面板的机器上执行本脚本。"
fi
if port_busy 8888; then
    die "8888 端口已被占用，停止安装。"
fi

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
        dnf install -y dnf-plugins-core || die "安装 dnf 插件失败。"
        dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo \
            || die "添加 Docker 软件源失败。"
        dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin \
            || die "安装 Docker 失败。"
    elif command -v yum >/dev/null 2>&1; then
        yum install -y yum-utils || die "安装 yum-utils 失败。"
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

systemctl enable --now docker || die "启动 Docker 服务失败。"

mkdir -p /www/wwwroot /www/backup /www/server/data "$INSTALL_DIR" \
    || die "创建目录失败。请检查磁盘是否已满。"

cat > "${INSTALL_DIR}/docker-compose.yml" << EOF
services:
  backend:
    image: ${BACKEND_IMAGE}
    network_mode: host
    privileged: true
    restart: unless-stopped
    environment:
      FILE_ROOT: /www/wwwroot
      NGINX_SYSTEM_MODE: "true"
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

docker pull "$BACKEND_IMAGE" || die "拉取 ${BACKEND_IMAGE} 失败。请确认镜像已公开推送到 Docker Hub。"
docker pull "$FRONTEND_IMAGE" || die "拉取 ${FRONTEND_IMAGE} 失败。请确认镜像已公开推送到 Docker Hub。"

(
    cd "$INSTALL_DIR"
    docker compose up -d
) || die "启动容器失败。"

ready=0
i=0
while [ "$i" -lt 60 ]; do
    code=$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health || true)
    if [ "$code" = "200" ]; then
        ready=1
        break
    fi
    i=$((i + 1))
    sleep 1
done
if [ "$ready" != "1" ]; then
    die "后端 60 秒内没有就绪，安装中止。请执行 docker compose -f ${INSTALL_DIR}/docker-compose.yml logs backend 查看原因。"
fi

password=""
i=0
while [ "$i" -lt 20 ]; do
    password=$(docker compose -f "${INSTALL_DIR}/docker-compose.yml" logs backend 2>/dev/null | sed -n 's/.*密码:[[:space:]]*//p' | tail -n 1 || true)
    password=$(printf '%s' "$password" | tr -d '\r')
    if [ -n "$password" ]; then
        break
    fi
    i=$((i + 1))
    sleep 1
done

lan_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$lan_ip" ]; then
    lan_ip="127.0.0.1"
fi
public_ip=$(curl -fsS -m 5 https://api64.ipify.org || curl -fsS -m 5 https://ifconfig.me || true)
public_ip=$(printf '%s' "$public_ip" | tr -d '[:space:]')
case "$public_ip" in
    *[!0-9a-fA-F.:]*|"") public_ip="请使用您的服务器公网 IP" ;;
esac

echo
echo "============================================================"
echo "  面板安装完成"
echo "  公网访问地址: http://${public_ip}:8888"
echo "  内网访问地址: http://${lan_ip}:8888"
echo "  用户名:       admin"
if [ -n "$password" ]; then
    echo "  密码:         ${password}"
    echo "  该密码只在首次初始化时生成，请用公网地址登录并立即修改。"
else
    echo "  密码:         数据卷里已有管理员，本次没有生成新密码。"
    echo "  请使用原先的 admin 密码，从公网地址登录。"
fi
echo "============================================================"
echo
