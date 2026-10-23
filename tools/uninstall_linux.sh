#!/usr/bin/env bash
# =============================================================================
# jyprinter 网络打印服务 —— Linux / 银河麒麟 卸载脚本
#
# 用法（需要 root）：
#   sudo bash tools/uninstall_linux.sh             # 停止服务、移除开机启动与图标，保留程序目录
#   sudo bash tools/uninstall_linux.sh --purge     # 连同程序目录与数据一起删除
#   sudo bash tools/uninstall_linux.sh --autostart-only   # 仅取消开机启动
#
# 说明：
#   · 不指定 --purge 时保留安装目录（config.json、日志与数据留存，便于重装后继续使用）；
#   · 会把所有用户家目录下的自启条目一并清理，避免遗留开机弹出项；
#   · 不卸载系统依赖（CUPS/LibreOffice 等可能被其他程序使用）。
# =============================================================================

set -euo pipefail

APP_NAME="jyprinter"
APP_DESC="网络打印服务"
STATE_FILE="/etc/${APP_NAME}-install.conf"
SERVICE_NAME="${APP_NAME}.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
DESKTOP_NAME="${APP_NAME}.desktop"
APPLICATIONS_DIR="/usr/share/applications"
ICON_THEME_DIR="/usr/share/icons/hicolor"
DEFAULT_DIR="/opt/${APP_NAME}"

log()  { printf '\033[1;34m[卸载]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[完成]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[警告]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

PURGE=0
AUTOSTART_ONLY=0
SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
# 先保存原始参数：下面的解析会 shift 掉全部参数，提权重执行时要用原始参数
ORIG_ARGS=("$@")

usage() {
    cat <<'USAGE'
jyprinter 网络打印服务 —— Linux / 银河麒麟 卸载脚本

用法：sudo bash tools/uninstall_linux.sh [选项]

选项：
  --purge            连同程序目录与数据一并删除（默认保留安装目录）
  --autostart-only   仅取消开机启动，保留程序与图标
  -h, --help         显示本帮助
USAGE
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --purge)          PURGE=1; shift ;;
        --autostart-only) AUTOSTART_ONLY=1; shift ;;
        -h|--help)        usage ;;
        *)                die "未知参数：$1（-h 查看帮助）" ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || die "需要 root 权限，且当前系统没有 sudo"
    log "需要管理员权限，正在通过 sudo 重新执行……"
    if [ "${#ORIG_ARGS[@]}" -gt 0 ]; then
        exec sudo -E bash "${SCRIPT_PATH}" "${ORIG_ARGS[@]}"
    fi
    exec sudo -E bash "${SCRIPT_PATH}"
fi

INSTALL_DIR="${DEFAULT_DIR}"
if [ -f "${STATE_FILE}" ]; then
    # 读取安装时记录的信息（安装目录、方式等），存在即以记录为准
    # shellcheck disable=SC1090
    . "${STATE_FILE}"
fi

# ------------------------------------------------------------------ 停止并移除服务
if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files "${SERVICE_NAME}" >/dev/null 2>&1; then
        log "停止并禁用 systemd 服务：${SERVICE_NAME}"
        systemctl disable --now "${SERVICE_NAME}" >/dev/null 2>&1 || true
    fi
fi
if [ -f "${SERVICE_PATH}" ]; then
    rm -f "${SERVICE_PATH}"
    command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload >/dev/null 2>&1 || true
    ok "已移除 systemd 服务文件"
else
    log "未发现 systemd 服务文件，跳过"
fi

# ------------------------------------------------------------------ 清理桌面自启与菜单项
removed_autostart=0
for home in /root /home/*; do
    candidate="${home}/.config/autostart/${DESKTOP_NAME}"
    if [ -f "${candidate}" ]; then
        rm -f "${candidate}"
        removed_autostart=1
        log "已移除自启条目：${candidate}"
    fi
done
[ "${removed_autostart}" -eq 1 ] || log "未发现桌面自启条目"

if [ "${AUTOSTART_ONLY}" -eq 1 ]; then
    ok "已取消开机启动（程序与图标保留）"
    exit 0
fi

if [ -f "${APPLICATIONS_DIR}/${DESKTOP_NAME}" ]; then
    rm -f "${APPLICATIONS_DIR}/${DESKTOP_NAME}"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "${APPLICATIONS_DIR}" >/dev/null 2>&1 || true
    ok "已移除开始菜单项"
fi

for size in 256x256 128x128; do
    icon="${ICON_THEME_DIR}/${size}/apps/${APP_NAME}.png"
    if [ -f "${icon}" ]; then
        rm -f "${icon}"
    fi
done
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f "${ICON_THEME_DIR}" >/dev/null 2>&1 || true

# ------------------------------------------------------------------ 程序目录
if [ "${PURGE}" -eq 1 ]; then
    if [ -d "${INSTALL_DIR}" ]; then
        log "删除程序目录：${INSTALL_DIR}"
        rm -rf "${INSTALL_DIR}"
        ok "程序目录已删除（含配置与数据）"
    fi
    rm -f "${STATE_FILE}"
else
    log "保留程序目录：${INSTALL_DIR}（如需彻底删除：sudo bash tools/uninstall_linux.sh --purge）"
fi

echo
ok "卸载完成"
echo "  程序目录   : ${INSTALL_DIR} $([ "${PURGE}" -eq 1 ] && echo '（已删除）' || echo '（保留）')"
echo "  系统依赖   : 未卸载（CUPS/LibreOffice 等可能被其他程序使用）"
echo "  残留检查   : ls /etc/systemd/system/${SERVICE_NAME} ${APPLICATIONS_DIR}/${DESKTOP_NAME} 2>/dev/null"
