#!/bin/sh
# 新服务器卸载：
#   wget -O uninstall.sh https://raw.githubusercontent.com/qx8677236-creator/panel-install/main/uninstall.sh && sudo bash uninstall.sh
set -eu

INSTALL_DIR=/opt/panel-assistant
BACKEND_IMAGE="${DOCKERHUB_USER:-xiaoqiang001}/panel-backend:${PANEL_TAG:-1.0}"
FRONTEND_IMAGE="${DOCKERHUB_USER:-xiaoqiang001}/panel-frontend:${PANEL_TAG:-1.0}"

die() {
    echo "$1" >&2
    exit 1
}

if [ "$(id -u)" -ne 0 ]; then
    die "请用 root 运行此脚本。"
fi

if [ -f /etc/systemd/system/panel-assistant.service ] && [ "${ALLOW_ON_PANEL_HOST:-}" != "yes" ]; then
    die "这台机器上有 systemd 面板服务，拒绝卸载，以免删掉正在使用的网站和备份。"
fi

echo "此操作将永久清空服务器上的所有网站文件、数据库容器及面板本地数据，不可逆转，请谨慎操作。"
echo "确认后请输入：确认卸载"
if [ "${CONFIRM_UNINSTALL:-}" != "确认卸载" ]; then
    printf '请输入确认文字: '
    read -r typed
    if [ "$typed" != "确认卸载" ]; then
        die "未输入确认卸载，已停止。"
    fi
fi

if command -v docker >/dev/null 2>&1; then
    if [ -f "${INSTALL_DIR}/docker-compose.yml" ]; then
        docker compose -f "${INSTALL_DIR}/docker-compose.yml" down -v --rmi all \
            || die "停止并删除面板容器失败。"
    fi
    docker rmi "$BACKEND_IMAGE" "$FRONTEND_IMAGE" 2>/dev/null || true
else
    echo "未找到 Docker，跳过容器和镜像删除。"
fi

rm -rf "$INSTALL_DIR" || die "删除 ${INSTALL_DIR} 失败。"
rm -rf /www/wwwroot /www/backup /www/server/data || die "删除 /www 下的面板数据失败。"

echo
echo "============================================================"
echo "  面板已卸载"
echo "  已删除容器、镜像、面板数据卷，以及："
echo "  /www/wwwroot"
echo "  /www/backup"
echo "  /www/server/data"
echo "  /opt/panel-assistant"
echo "  Docker 程序本身和 /etc/nginx 未删除。"
echo "============================================================"
echo
