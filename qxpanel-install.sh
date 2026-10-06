#!/bin/bash
# QxPanel 一键安装。拉取已发布的私有镜像 xiaoqiang001/qxpanel:latest。
#   curl -fsSL https://raw.githubusercontent.com/qx8677236-creator/panel-install/main/qxpanel-install.sh | sudo bash
# 镜像是私有的，新机器需要先登录：docker login -u xiaoqiang001
# 已在运行时再次执行不会重建容器，也不会重置登录密码。
# 要用当前目录源码自己构建时：sudo bash install.sh --build
# 卸载：sudo bash uninstall-qxpanel.sh
# 卸载会删除面板为网站写的 Nginx 配置，不会停止系统 80/443 上的 Nginx。
set -e

PANEL_PORT="${PANEL_PORT:-7800}"
QX_NAME="${QX_NAME:-qxpanel}"
QX_IMAGE="${QX_IMAGE:-}"
QX_PUBLISHED_IMAGE="xiaoqiang001/qxpanel:latest"
RECREATE=0
BUILD_LOCAL=0

while [ $# -gt 0 ]; do
    case "$1" in
        --recreate)
            RECREATE=1
            ;;
        --build)
            BUILD_LOCAL=1
            ;;
        --port)
            PANEL_PORT="$2"
            shift
            ;;
        --port=*)
            PANEL_PORT="${1#*=}"
            ;;
        --image)
            QX_IMAGE="$2"
            shift
            ;;
        --image=*)
            QX_IMAGE="${1#*=}"
            ;;
        *)
            ;;
    esac
    shift
done

if [ "$(id -u)" != "0" ]; then
    echo "请用 root 执行：sudo bash $0"
    exit 1
fi

if ! echo "$PANEL_PORT" | grep -Eq '^[0-9]+$' || [ "$PANEL_PORT" -lt 1 ] || [ "$PANEL_PORT" -gt 65535 ]; then
    echo "面板端口必须是 1 到 65535 之间的整数"
    exit 1
fi

SOURCE_FILE="${BASH_SOURCE[0]:-$0}"
if [ -f "$SOURCE_FILE" ]; then
    SCRIPT_DIR=$(cd "$(dirname "$SOURCE_FILE")" && pwd)
else
    SCRIPT_DIR=""
fi
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/Dockerfile" ]; then
    ROOT="$SCRIPT_DIR"
elif [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../Dockerfile" ]; then
    ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
else
    ROOT=""
fi

link_host_nginx() {
    mkdir -p /www/server/nginx/sbin /www/server/nginx/conf /www/server/nginx/logs
    real_nginx=$(command -v nginx 2>/dev/null || true)
    if [ -z "$real_nginx" ] && [ -x /usr/sbin/nginx ]; then
        real_nginx="/usr/sbin/nginx"
    fi
    if [ -n "$real_nginx" ] && [ -f "$real_nginx" ]; then
        ln -sfn "$real_nginx" /www/server/nginx/sbin/nginx
    fi
    if [ -f /etc/nginx/nginx.conf ] && [ ! -e /www/server/nginx/conf/nginx.conf ]; then
        ln -sfn /etc/nginx/nginx.conf /www/server/nginx/conf/nginx.conf
    fi
    if [ -d /var/log/nginx ] && [ ! -L /www/server/nginx/logs ]; then
        rm -rf /www/server/nginx/logs
        ln -sfn /var/log/nginx /www/server/nginx/logs
    fi
}

install_docker() {
    if command -v docker >/dev/null 2>&1; then
        return 0
    fi
    echo "正在安装 Docker..."
    if [ -f /etc/debian_version ]; then
        apt-get update
        apt-get install -y ca-certificates curl
    elif [ -f /etc/redhat-release ]; then
        yum install -y ca-certificates curl || dnf install -y ca-certificates curl
    else
        echo "无法识别系统，请先安装 Docker 后再执行本脚本"
        exit 1
    fi
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker >/dev/null 2>&1 || true
}

open_panel_port() {
    if command -v ufw >/dev/null 2>&1; then
        if ufw status 2>/dev/null | grep -q "Status: active"; then
            ufw allow "${PANEL_PORT}/tcp" || true
        fi
    fi
    if command -v firewall-cmd >/dev/null 2>&1; then
        if systemctl is-active firewalld >/dev/null 2>&1; then
            firewall-cmd --zone=public --add-port="${PANEL_PORT}/tcp" --permanent || true
            firewall-cmd --reload || true
        fi
    fi
}

panel_http_code() {
    curl -s -o /dev/null -m 3 -w '%{http_code}' "http://127.0.0.1:${PANEL_PORT}/bt" 2>/dev/null || echo 000
}

container_running() {
    docker inspect -f '{{.State.Running}}' "$QX_NAME" 2>/dev/null | grep -q true
}

panel_ip() {
    meta=$(curl -4 -fsS -m 3 -H "Metadata:true" \
        "http://169.254.169.254/metadata/instance/network/interface/0/ipv4/ipAddress/0/publicIpAddress?api-version=2021-02-01&format=text" \
        2>/dev/null || true)
    if echo "$meta" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        echo "$meta"
        return
    fi
    pub=$(curl -4 -fsS -m 4 https://api.ipify.org 2>/dev/null || true)
    if echo "$pub" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        echo "$pub"
        return
    fi
    hostname -I 2>/dev/null | awk '{print $1}'
}

read_login_file() {
    docker exec "$QX_NAME" sh -c 'cat /www/server/panel/data/default.pl 2>/dev/null' || true
}

print_login() {
    ip_addr=$(panel_ip)
    if [ -z "$ip_addr" ]; then
        ip_addr="服务器IP"
    fi
    cred=""
    i=0
    while [ "$i" -lt 20 ]; do
        cred=$(read_login_file)
        if printf '%s\n' "$cred" | grep -q '^password:'; then
            break
        fi
        i=$((i + 1))
        sleep 1
    done
    if ! printf '%s\n' "$cred" | grep -q '^password:'; then
        cred=$(docker logs "$QX_NAME" 2>&1 | sed -n 's/^QxPanel username: /username: /p; s/^QxPanel password: /password: /p' || true)
    fi
    login_user=$(printf '%s\n' "$cred" | sed -n 's/^username: //p' | tail -n 1)
    login_pass=$(printf '%s\n' "$cred" | sed -n 's/^password: //p' | tail -n 1)
    echo ""
    echo "QxPanel 已可使用"
    echo "登录地址: http://${ip_addr}:${PANEL_PORT}/bt"
    if [ -n "$login_user" ] && [ -n "$login_pass" ]; then
        echo "账号: ${login_user}"
        echo "密码: ${login_pass}"
    else
        echo "没有读到登录账号，请执行: sudo docker logs ${QX_NAME}"
    fi
    echo "如果这是云服务器，请在云厂商安全组放行 TCP ${PANEL_PORT}，以及你以后新建站点使用的自定义端口。"
    echo ""
}

wait_ready() {
    echo "正在等待面板启动..."
    i=0
    while [ "$i" -lt 40 ]; do
        code=$(panel_http_code)
        if [ "$code" != "000" ]; then
            return 0
        fi
        i=$((i + 1))
        sleep 2
    done
    echo "面板在 80 秒内没有监听 ${PANEL_PORT}。最近日志："
    docker logs --tail 40 "$QX_NAME" 2>&1 || true
    exit 1
}

install_docker
link_host_nginx
open_panel_port

if [ "$RECREATE" != "1" ] && container_running; then
    code=$(panel_http_code)
    if [ "$code" != "000" ]; then
        echo "面板已在运行，未重建容器，登录密码保持不变。"
        print_login
        exit 0
    fi
fi

if [ -z "$QX_IMAGE" ]; then
    if [ "$BUILD_LOCAL" = "1" ]; then
        if [ -z "$ROOT" ] || [ ! -f "$ROOT/Dockerfile" ]; then
            echo "当前目录没有 Dockerfile，无法本地构建"
            exit 1
        fi
        QX_IMAGE="qxpanel:local"
        echo "正在用当前源码构建镜像 ${QX_IMAGE}，第一次会比较久..."
        docker build -t "$QX_IMAGE" "$ROOT"
    else
        QX_IMAGE="$QX_PUBLISHED_IMAGE"
        echo "正在拉取已发布的镜像 ${QX_IMAGE}"
        if ! docker pull "$QX_IMAGE"; then
            echo "拉取失败。该镜像是私有仓库，请先登录 Docker Hub："
            echo "  docker login -u xiaoqiang001"
            echo "登录成功后重新执行：sudo bash install.sh"
            exit 1
        fi
    fi
fi

docker rm -f "$QX_NAME" >/dev/null 2>&1 || true

run_args="--name $QX_NAME --restart unless-stopped --privileged --network host -e PANEL_PORT=${PANEL_PORT} -e PANEL_FOREGROUND=1"
if [ -S /var/run/docker.sock ]; then
    run_args="$run_args -v /var/run/docker.sock:/var/run/docker.sock"
fi
if [ -x /usr/bin/docker ]; then
    run_args="$run_args -v /usr/bin/docker:/usr/bin/docker"
fi
if [ -d /usr/libexec/docker ]; then
    run_args="$run_args -v /usr/libexec/docker:/usr/libexec/docker"
fi
run_args="$run_args -v qxpanel-data:/www/server/panel/data -v qxpanel-www:/www/wwwroot"

# shellcheck disable=SC2086
docker run -d $run_args "$QX_IMAGE"

if [ -x /usr/libexec/docker/cli-plugins/docker-compose ]; then
    docker exec "$QX_NAME" ln -sfn /usr/libexec/docker/cli-plugins/docker-compose /usr/bin/docker-compose || true
fi

wait_ready
print_login
