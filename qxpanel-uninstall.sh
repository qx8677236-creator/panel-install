#!/bin/bash
# QxPanel 一键卸载。
#   curl -fsSL https://raw.githubusercontent.com/qx8677236-creator/panel-install/main/qxpanel-uninstall.sh | sudo bash
# 会停止面板容器、删掉面板数据，并删除面板为网站写的 Nginx 配置。
# 不停止、不修改系统已经在 80/443 上运行的 Nginx。
# 网站文件默认保留。连同网站文件一起删除：sudo bash uninstall-qxpanel.sh --purge
set -e

QX_NAME="${QX_NAME:-qxpanel}"
ASSUME_YES=0
PURGE_SITES=0
PANEL_PORT="${PANEL_PORT:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --yes)
            ASSUME_YES=1
            ;;
        --purge)
            PURGE_SITES=1
            ;;
        --name)
            QX_NAME="$2"
            shift
            ;;
        --name=*)
            QX_NAME="${1#*=}"
            ;;
        --port)
            PANEL_PORT="$2"
            shift
            ;;
        --port=*)
            PANEL_PORT="${1#*=}"
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

if ! command -v docker >/dev/null 2>&1; then
    echo "没有找到 Docker，无法定位 QxPanel 容器。"
    exit 1
fi

container_exists() {
    docker inspect "$QX_NAME" >/dev/null 2>&1
}

container_running() {
    docker inspect -f '{{.State.Running}}' "$QX_NAME" 2>/dev/null | grep -q true
}

protected_port() {
    case "$1" in
        22|80|443|8000|3306|21)
            return 0
            ;;
    esac
    return 1
}

WORK=$(mktemp -d)
cleanup() {
    rm -rf "$WORK"
}
trap cleanup EXIT

echo "即将卸载 QxPanel（容器名 ${QX_NAME}）"
echo "- 停止并删除面板容器"
echo "- 停止只服务于面板站点的 Nginx，并删除这些网站的 Nginx 配置"
echo "- 删除面板数据卷 qxpanel-data（登录信息、站点记录）"
if [ "$PURGE_SITES" = "1" ]; then
    echo "- 同时删除网站文件卷 qxpanel-www"
else
    echo "- 网站文件卷 qxpanel-www 保留。要一并删除请加 --purge"
fi
echo "系统 80/443 上的 Nginx、8000 端口和 3306 数据库不会被停止。"
echo ""

if [ "$ASSUME_YES" != "1" ]; then
    if [ ! -r /dev/tty ]; then
        echo "当前没有交互终端。确认卸载请追加 --yes"
        exit 1
    fi
    printf '确认卸载请输入 uninstall: '
    read -r answer < /dev/tty
    if [ "$answer" != "uninstall" ]; then
        echo "已取消"
        exit 0
    fi
fi

if container_exists; then
    detected=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$QX_NAME" 2>/dev/null | sed -n 's/^PANEL_PORT=//p' | head -n 1 || true)
    if [ -z "$PANEL_PORT" ] && [ -n "$detected" ]; then
        PANEL_PORT="$detected"
    fi
    docker cp "$QX_NAME:/www/server/nginx/conf/qx-sites.conf" "$WORK/qx-sites.conf" >/dev/null 2>&1 || true
    docker cp "$QX_NAME:/www/server/panel/vhost/nginx/." "$WORK/vhost/" >/dev/null 2>&1 || true
fi
if [ -z "$PANEL_PORT" ]; then
    PANEL_PORT=7800
fi

PORT_LIST="$WORK/ports.txt"
: > "$PORT_LIST"
if [ -f "$WORK/qx-sites.conf" ] || [ -d "$WORK/vhost" ]; then
    grep -RhoE 'listen[[:space:]]+(\[::\]:)?[0-9]+' "$WORK" 2>/dev/null | grep -Eo '[0-9]+' | sort -u >> "$PORT_LIST" || true
fi
echo "$PANEL_PORT" >> "$PORT_LIST"

echo "正在停止面板创建的站点 Nginx..."
for pid_path in /proc/[0-9]*; do
    pid=${pid_path#/proc/}
    [ -r "$pid_path/cmdline" ] || continue
    cmd=$(tr '\0' ' ' < "$pid_path/cmdline" 2>/dev/null || true)
    case "$cmd" in
        *qx-sites.conf*)
            echo "停止进程 ${pid}"
            kill -TERM "$pid" 2>/dev/null || true
            ;;
    esac
done

if container_running; then
    docker exec "$QX_NAME" sh -c '
        pid_file=/www/server/nginx/logs/qx-sites.pid
        if [ -f "$pid_file" ]; then
            pid=$(tr -cd "0-9" < "$pid_file")
            if [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ]; then
                cmd=$(tr "\0" " " < "/proc/$pid/cmdline")
                case "$cmd" in
                    *qx-sites.conf*)
                        kill -TERM "$pid" 2>/dev/null || true
                        ;;
                esac
            fi
        fi
        rm -f /www/server/nginx/conf/qx-sites.conf
        rm -f /www/server/nginx/logs/qx-sites.pid
        rm -f /www/server/nginx/logs/qx-sites.error.log
        if [ -d /www/server/panel/vhost/nginx ]; then
            find /www/server/panel/vhost/nginx -name "*.conf" -type f -delete
        fi
        rm -rf /www/server/panel/vhost/nginx/proxy \
            /www/server/panel/vhost/nginx/redirect \
            /www/server/panel/vhost/nginx/dir_auth \
            /www/server/panel/vhost/rewrite \
            /www/server/panel/vhost/apache \
            /www/server/panel/vhost/open_basedir \
            /www/server/panel/vhost/openlitespeed
    ' || true
fi

if [ -f /www/server/nginx/conf/qx-sites.conf ] && grep -q 'qx-sites.pid' /www/server/nginx/conf/qx-sites.conf; then
    rm -f /www/server/nginx/conf/qx-sites.conf
    echo "已删除宿主机上的站点监听配置 qx-sites.conf"
fi
rm -f /www/server/nginx/logs/qx-sites.pid /www/server/nginx/logs/qx-sites.error.log 2>/dev/null || true

sleep 1
left=0
for pid_path in /proc/[0-9]*; do
    pid=${pid_path#/proc/}
    [ -r "$pid_path/cmdline" ] || continue
    cmd=$(tr '\0' ' ' < "$pid_path/cmdline" 2>/dev/null || true)
    case "$cmd" in
        *qx-sites.conf*)
            kill -KILL "$pid" 2>/dev/null || true
            left=1
            ;;
    esac
done
if [ "$left" = "1" ]; then
    echo "已强制结束仍占用站点端口的 Nginx 进程"
fi

if container_exists; then
    docker rm -f "$QX_NAME" >/dev/null
    echo "已删除容器 ${QX_NAME}"
else
    echo "容器 ${QX_NAME} 不存在，跳过删除容器"
fi

close_port() {
    port="$1"
    if ! echo "$port" | grep -Eq '^[0-9]+$'; then
        return 0
    fi
    if protected_port "$port"; then
        echo "保留系统端口 ${port}"
        return 0
    fi
    if command -v ufw >/dev/null 2>&1; then
        ufw --force delete allow "${port}/tcp" >/dev/null 2>&1 || true
    fi
    if command -v firewall-cmd >/dev/null 2>&1; then
        firewall-cmd --zone=public --remove-port="${port}/tcp" --permanent >/dev/null 2>&1 || true
    fi
    if command -v iptables >/dev/null 2>&1; then
        n=0
        while [ "$n" -lt 30 ]; do
            iptables -D INPUT -p tcp --dport "$port" -j ACCEPT >/dev/null 2>&1 || break
            n=$((n + 1))
        done
    fi
    echo "已移除端口 ${port} 的防火墙放行"
}

sort -u "$PORT_LIST" | while read -r port; do
    [ -n "$port" ] || continue
    close_port "$port"
done
if command -v firewall-cmd >/dev/null 2>&1; then
    firewall-cmd --reload >/dev/null 2>&1 || true
fi

if docker volume inspect qxpanel-data >/dev/null 2>&1; then
    docker volume rm qxpanel-data >/dev/null
    echo "已删除面板数据卷 qxpanel-data"
fi

if [ "$PURGE_SITES" = "1" ]; then
    if docker volume inspect qxpanel-www >/dev/null 2>&1; then
        docker volume rm qxpanel-www >/dev/null
        echo "已删除网站文件卷 qxpanel-www"
    fi
else
    if docker volume inspect qxpanel-www >/dev/null 2>&1; then
        echo "网站文件仍在数据卷 qxpanel-www 中，当前已经没有 Nginx 配置继续对外提供这些网站。"
    fi
fi

echo ""
echo "QxPanel 已卸载。"
echo "云厂商安全组里手工放行的端口需要在云控制台自行关闭。"
echo "Docker 镜像未删除，以后可以重新安装。"
