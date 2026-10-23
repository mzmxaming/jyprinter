#!/usr/bin/env bash
# =============================================================================
# jyprinter 网络打印服务 —— Linux / 银河麒麟 一键安装脚本
#
# 用法（需要 root，脚本会自动通过 sudo 提权）：
#   sudo bash tools/install_linux.sh                    # 源码模式安装（推荐）
#   sudo bash tools/install_linux.sh --port 8080        # 同时指定服务端口
#   sudo bash install_linux.sh                          # 二进制模式（与 jyprinter 可执行文件同目录）
#
# 常用选项：
#   --dir <路径>          安装目录（默认 /opt/jyprinter）
#   --port <端口>         服务端口，写入 config.json（默认沿用 5000）
#   --autostart <方式>    systemd（默认，开机即运行，无需登录）| desktop（登录后带托盘）| none
#   --user <用户名>       服务与自启所属用户（默认为调用 sudo 的原始用户）
#   --skip-deps           跳过系统依赖安装（离线/已自行准备依赖时使用）
#   -h, --help            显示帮助
#
# 安装内容：
#   1. 系统依赖：CUPS 打印组件、LibreOffice、中文字体、python3-venv/tk/gi、NetworkManager 工具
#   2. 程序本体 → /opt/jyprinter
#   3. 开机启动：systemd 系统服务 或 桌面自启（二选一，可重复执行本脚本切换）
#   4. 桌面图标与开始菜单项（/usr/share/applications、hicolor 图标主题）
#
# 设计说明：
#   · 系统服务以「调用 sudo 的原始用户」身份运行，避免以 root 常驻；
#   · venv 使用 --system-site-packages，以便复用系统自带的 PyGObject(pystray 依赖) 与 tkinter；
#   · 全部步骤幂等，可重复执行用于升级；
#   · 脚本不修改任何网络配置，静态 IP 请在网页端/托盘菜单中操作。
# =============================================================================

set -euo pipefail

APP_NAME="jyprinter"
APP_DESC="网络打印服务"
DEFAULT_DIR="/opt/${APP_NAME}"
STATE_FILE="/etc/${APP_NAME}-install.conf"
SERVICE_NAME="${APP_NAME}.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
DESKTOP_NAME="${APP_NAME}.desktop"
APPLICATIONS_DIR="/usr/share/applications"
ICON_THEME_DIR="/usr/share/icons/hicolor"

# --------------------------------------------------------------------------- 输出
log()  { printf '\033[1;34m[安装]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[完成]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[警告]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
jyprinter 网络打印服务 —— Linux / 银河麒麟 安装脚本

用法：sudo bash tools/install_linux.sh [选项]

选项：
  --dir <路径>          安装目录（默认 /opt/jyprinter）
  --port <端口>         服务端口，写入 config.json（默认沿用 5000）
  --autostart <方式>    systemd（默认，开机即运行、无需登录）
                        desktop（登录后带托盘运行）| none（不设置）
  --user <用户名>       服务与自启所属用户（默认为调用 sudo 的原始用户）
  --skip-deps           跳过系统依赖安装（离线环境使用）
  -h, --help            显示本帮助

示例：
  sudo bash tools/install_linux.sh
  sudo bash tools/install_linux.sh --port 8080 --autostart desktop
USAGE
    exit 0
}

# --------------------------------------------------------------------------- 参数
INSTALL_DIR="${DEFAULT_DIR}"
PORT=""
AUTOSTART=""
SERVICE_USER=""
SKIP_DEPS=0
SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
# 先保存原始参数：下面的解析会 shift 掉全部参数，提权重执行时要用原始参数
ORIG_ARGS=("$@")

while [ $# -gt 0 ]; do
    case "$1" in
        --dir)        INSTALL_DIR="${2:?--dir 需要参数}"; shift 2 ;;
        --port)       PORT="${2:?--port 需要参数}"; shift 2 ;;
        --autostart)  AUTOSTART="${2:?--autostart 需要参数}"; shift 2 ;;
        --user)       SERVICE_USER="${2:?--user 需要参数}"; shift 2 ;;
        --skip-deps)  SKIP_DEPS=1; shift ;;
        -h|--help)    usage ;;
        *)            die "未知参数：$1（-h 查看帮助）" ;;
    esac
done

case "${AUTOSTART}" in
    ""|systemd|desktop|none) ;;
    *) die "--autostart 只支持 systemd / desktop / none" ;;
esac

# --------------------------------------------------------------------------- 提权
if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || die "需要 root 权限，且当前系统没有 sudo，请以 root 登录后执行"
    log "需要管理员权限，正在通过 sudo 重新执行……"
    if [ "${#ORIG_ARGS[@]}" -gt 0 ]; then
        exec sudo -E bash "${SCRIPT_PATH}" "${ORIG_ARGS[@]}"
    fi
    exec sudo -E bash "${SCRIPT_PATH}"
fi

# 记录调用 sudo 的原始用户（服务与自启均归属该用户，避免以 root 常驻）
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
    ORIG_USER="${SUDO_USER}"
else
    ORIG_USER="$(id -un)"
fi
[ -n "${SERVICE_USER}" ] || SERVICE_USER="${ORIG_USER}"
id "${SERVICE_USER}" >/dev/null 2>&1 || die "用户 ${SERVICE_USER} 不存在"
ORIG_HOME="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
[ -n "${ORIG_HOME}" ] || die "无法确定用户 ${SERVICE_USER} 的家目录"
SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"

# --------------------------------------------------------------------------- 来源识别
SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
SRC_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BINARY_MODE=0
DIST_DIR=""
if [ -f "${SRC_ROOT}/main.py" ]; then
    BINARY_MODE=0
elif [ -x "${SRC_ROOT}/${APP_NAME}" ]; then
    BINARY_MODE=1
    DIST_DIR="${SRC_ROOT}"
elif [ -x "${SCRIPT_DIR}/${APP_NAME}" ]; then
    BINARY_MODE=1
    DIST_DIR="${SCRIPT_DIR}"
else
    die "未找到 main.py 或 ${APP_NAME} 可执行文件，无法确定安装来源（请把脚本放在程序源码或发行包内运行）"
fi

log "运行平台：$( (grep -E '^PRETTY_NAME=' /etc/os-release 2>/dev/null || echo 'PRETTY_NAME=未知 Linux') | cut -d= -f2- | tr -d '"')"
log "系统架构：$(uname -m)"
log "安装方式：$([ "${BINARY_MODE}" -eq 1 ] && echo '二进制发行包' || echo '源码')"
log "安装目录：${INSTALL_DIR}"
log "运行用户：${SERVICE_USER}（家目录 ${ORIG_HOME}）"

# --------------------------------------------------------------------------- 系统依赖
install_with_manager() {
    # 逐个安装，缺失的包只警告不中断（不同发行版包名差异较大）
    local pkgs=("$@")
    local pkg
    if [ "${#pkgs[@]}" -eq 0 ]; then
        return 0
    fi
    log "安装系统依赖：${pkgs[*]}"
    if "${PKG_INSTALL_ALL[@]}" "${pkgs[@]}"; then
        return 0
    fi
    warn "批量安装未全部成功，改为逐个安装"
    for pkg in "${pkgs[@]}"; do
        "${PKG_INSTALL_ONE[@]}" "${pkg}" >/dev/null 2>&1 || warn "跳过不可用的包：${pkg}"
    done
    return 0
}

detect_package_manager() {
    if command -v apt-get >/dev/null 2>&1; then
        PM="apt"
        export DEBIAN_FRONTEND=noninteractive
        PKG_UPDATE=(apt-get update -qq)
        PKG_INSTALL_ALL=(apt-get install -y --no-install-recommends)
        PKG_INSTALL_ONE=(apt-get install -y --no-install-recommends)
        PKG_BASE=(cups cups-client cups-bsd python3 python3-venv python3-pip python3-tk python3-gi gir1.2-gtk-3.0 iproute2 network-manager xclip fonts-noto-cjk)
        PKG_OFFICE=(libreoffice-writer libreoffice-calc libreoffice-impress)
    elif command -v dnf >/dev/null 2>&1; then
        PM="dnf"
        PKG_UPDATE=(dnf makecache -q)
        PKG_INSTALL_ALL=(dnf install -y)
        PKG_INSTALL_ONE=(dnf install -y)
        PKG_BASE=(cups cups-client python3 python3-pip python3-tkinter python3-gobject gtk3 iproute NetworkManager xclip google-noto-sans-cjk-fonts)
        PKG_OFFICE=(libreoffice-writer libreoffice-calc libreoffice-impress)
    elif command -v yum >/dev/null 2>&1; then
        PM="yum"
        PKG_UPDATE=(yum makecache -q)
        PKG_INSTALL_ALL=(yum install -y)
        PKG_INSTALL_ONE=(yum install -y)
        PKG_BASE=(cups cups-client python3 python3-pip python3-tkinter python3-gobject gtk3 iproute NetworkManager xclip wqy-zenhei-fonts)
        PKG_OFFICE=(libreoffice-writer libreoffice-calc libreoffice-impress)
    elif command -v zypper >/dev/null 2>&1; then
        PM="zypper"
        PKG_UPDATE=(zypper --non-interactive refresh)
        PKG_INSTALL_ALL=(zypper --non-interactive install --no-recommends)
        PKG_INSTALL_ONE=(zypper --non-interactive install --no-recommends)
        PKG_BASE=(cups cups-client python3 python3-pip python3-tk python3-gobject gtk3 iproute2 NetworkManager xclip noto-sans-sc-fonts)
        PKG_OFFICE=(libreoffice-writer libreoffice-calc libreoffice-impress)
    else
        die "未识别的包管理器，请手动安装 CUPS、LibreOffice、中文字体与 python3-venv 后重新执行（可加 --skip-deps）"
    fi
}

if [ "${SKIP_DEPS}" -eq 0 ]; then
    detect_package_manager
    log "检测到包管理器：${PM}"
    "${PKG_UPDATE[@]}" >/dev/null 2>&1 || warn "软件源刷新失败（离线环境可忽略，或改用 --skip-deps）"
    install_with_manager "${PKG_BASE[@]}"
    # LibreOffice 体积较大且仅影响办公格式，安装失败不中断
    install_with_manager "${PKG_OFFICE[@]}" || warn "LibreOffice 安装未完成，Office/WPS 格式将不可用（其余格式不受影响）"
else
    log "已跳过系统依赖安装（--skip-deps）"
fi

# --------------------------------------------------------------------------- 安装程序本体
log "部署程序到 ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"

if [ "${BINARY_MODE}" -eq 1 ]; then
    install -m 0755 "${DIST_DIR}/${APP_NAME}" "${INSTALL_DIR}/${APP_NAME}"
    if [ -f "${DIST_DIR}/assets/jyprinter.png" ]; then
        install -D -m 0644 "${DIST_DIR}/assets/jyprinter.png" "${INSTALL_DIR}/assets/jyprinter.png"
    fi
    if [ -f "${DIST_DIR}/assets/jyprinter.ico" ]; then
        install -D -m 0644 "${DIST_DIR}/assets/jyprinter.ico" "${INSTALL_DIR}/assets/jyprinter.ico"
    fi
    # 安装后仍可在安装目录执行卸载/自检脚本
    if [ -d "${DIST_DIR}/tools" ]; then
        mkdir -p "${INSTALL_DIR}/tools"
        cp -f "${DIST_DIR}/tools/"*.sh "${DIST_DIR}/tools/"*.py "${INSTALL_DIR}/tools/" 2>/dev/null || true
        chmod 0755 "${INSTALL_DIR}/tools/"*.sh 2>/dev/null || true
    fi
else
    # 源码模式：按清单复制必要内容（避免把虚拟环境、缓存、构建产物与数据一起搬过去）
    cp -a "${SRC_ROOT}/core" "${SRC_ROOT}/web" "${SRC_ROOT}/tools" "${SRC_ROOT}/assets" \
          "${SRC_ROOT}/main.py" "${INSTALL_DIR}/"
    for extra in "${SRC_ROOT}"/*.txt "${SRC_ROOT}"/*.spec "${SRC_ROOT}"/*.md "${SRC_ROOT}/config.json"; do
        if [ -e "${extra}" ]; then
            cp -a "${extra}" "${INSTALL_DIR}/"
        fi
    done
    # 清理源码目录里可能带过来的缓存
    find "${INSTALL_DIR}" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    find "${INSTALL_DIR}" -name '*.pyc' -type f -delete 2>/dev/null || true
fi

mkdir -p "${INSTALL_DIR}/data/logs" "${INSTALL_DIR}/data/uploads"

if [ "${BINARY_MODE}" -eq 0 ]; then
    VENV_DIR="${INSTALL_DIR}/.venv"
    if [ ! -x "${VENV_DIR}/bin/python" ]; then
        log "创建虚拟环境 ${VENV_DIR}"
        # --system-site-packages：复用系统 python3-gi（pystray 托盘后端）与 tkinter
        python3 -m venv --system-site-packages "${VENV_DIR}" \
            || die "创建虚拟环境失败，请先安装 python3-venv（Debian/Ubuntu/银河麒麟）或 python3-virtualenv（RPM 系）"
    fi
    log "安装 Python 依赖（离线环境请参考 requirements-linux.txt 顶部说明）"
    "${VENV_DIR}/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "${VENV_DIR}/bin/python" -m pip install -r "${INSTALL_DIR}/requirements-linux.txt" \
        || die "Python 依赖安装失败：请检查网络，或按 requirements-linux.txt 说明使用本地 wheels 离线安装"
    RUN_CMD="\"${VENV_DIR}/bin/python\" \"${INSTALL_DIR}/main.py\""
else
    RUN_CMD="\"${INSTALL_DIR}/${APP_NAME}\""
fi

# --------------------------------------------------------------------------- 配置文件
CONFIG_FILE="${INSTALL_DIR}/config.json"
if [ -n "${PORT}" ]; then
    case "${PORT}" in
        ''|*[!0-9]*) die "--port 必须是数字" ;;
    esac
    [ "${PORT}" -ge 1024 ] && [ "${PORT}" -le 65535 ] || die "--port 范围应为 1024-65535"
    PY_FOR_CONFIG="$(command -v python3 || true)"
    [ -n "${PY_FOR_CONFIG}" ] || die "未找到 python3，无法写入配置文件"
    "${PY_FOR_CONFIG}" - "${CONFIG_FILE}" "${PORT}" <<'PYEOF'
import json
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
port = int(sys.argv[2])
data = {}
if target.exists():
    try:
        data = json.loads(target.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        data = {}
data["port"] = port
target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已写入服务端口：{port}")
PYEOF
elif [ ! -f "${CONFIG_FILE}" ]; then
    log "未指定 --port，首次启动将按默认端口 5000 生成 config.json"
fi

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}"
if [ "${BINARY_MODE}" -eq 0 ]; then
    # 保证程序目录对运行用户可读写（数据、日志、转换缓存都写在这里）
    chmod -R u+rwX "${INSTALL_DIR}"
fi

# --------------------------------------------------------------------------- 开机启动
systemd_ready() {
    command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]
}

install_systemd_service() {
    local unit_exec="${RUN_CMD} --no-tray"
    log "写入 systemd 服务：${SERVICE_PATH}"
    cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=${APP_DESC}（${APP_NAME} 网络静默打印）
Documentation=file://${INSTALL_DIR}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${unit_exec}
Restart=always
RestartSec=5
# 交由 journald 统一收集，查看日志：journalctl -u ${APP_NAME} -f
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
Environment=HOME=${ORIG_HOME}

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}" >/dev/null
    systemctl restart "${SERVICE_NAME}"
    sleep 2
    if systemctl is-active --quiet "${SERVICE_NAME}"; then
        ok "systemd 服务已启动并设为开机自启"
    else
        warn "服务启动失败，请查看日志：journalctl -u ${APP_NAME} -n 50 --no-pager"
    fi
}

install_desktop_autostart() {
    local autostart_dir="${ORIG_HOME}/.config/autostart"
    local exec_line="${RUN_CMD}"
    mkdir -p "${autostart_dir}" "${APPLICATIONS_DIR}"
    log "写入桌面自启：${autostart_dir}/${DESKTOP_NAME}"

    cat > "${autostart_dir}/${DESKTOP_NAME}" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=${APP_DESC}
Name[en]=Network Print Service
Comment=局域网文件静默打印服务
Comment[en]=Silent printing service for LAN
Exec=${exec_line}
Icon=${APP_NAME}
Terminal=false
StartupNotify=false
Categories=Utility;Office;Printing;
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
EOF
    chmod 0644 "${autostart_dir}/${DESKTOP_NAME}"
    chown "${SERVICE_USER}:${SERVICE_GROUP}" "${autostart_dir}/${DESKTOP_NAME}"
    ok "已设置登录后自动启动（桌面自启，托盘常驻）"
}

install_desktop_entry() {
    mkdir -p "${APPLICATIONS_DIR}"
    cat > "${APPLICATIONS_DIR}/${DESKTOP_NAME}" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=${APP_DESC}
Name[en]=Network Print Service
Comment=局域网文件静默打印服务
Exec=${RUN_CMD}
Icon=${APP_NAME}
Terminal=true
StartupNotify=false
Categories=Utility;Office;Printing;
EOF
    chmod 0644 "${APPLICATIONS_DIR}/${DESKTOP_NAME}"
}

install_icon() {
    local src=""
    for candidate in "${INSTALL_DIR}/assets/jyprinter.png" "${SRC_ROOT}/assets/jyprinter.png" "${SCRIPT_DIR}/jyprinter.png"; do
        if [ -f "${candidate}" ]; then
            src="${candidate}"
            break
        fi
    done
    if [ -z "${src}" ]; then
        warn "未找到 assets/jyprinter.png，桌面图标将使用系统默认图标（可执行 tools/make_icon.py 生成）"
        return 0
    fi
    install -D -m 0644 "${src}" "${ICON_THEME_DIR}/256x256/apps/${APP_NAME}.png"
    install -D -m 0644 "${src}" "${ICON_THEME_DIR}/128x128/apps/${APP_NAME}.png"
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f "${ICON_THEME_DIR}" >/dev/null 2>&1 || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q "${APPLICATIONS_DIR}" >/dev/null 2>&1 || true
    fi
    ok "已安装桌面图标（jyprinter.png）"
}

if [ -z "${AUTOSTART}" ]; then
    if systemd_ready; then
        AUTOSTART="systemd"
    else
        AUTOSTART="desktop"
        warn "当前系统未使用 systemd，自动改用桌面自启方式"
    fi
fi

install_icon
install_desktop_entry

case "${AUTOSTART}" in
    systemd)
        install_systemd_service
        # 二者同时启用会争抢端口，安装系统服务时清理桌面自启副本
        legacy="${ORIG_HOME}/.config/autostart/${DESKTOP_NAME}"
        if [ -f "${legacy}" ]; then
            rm -f "${legacy}"
            log "已移除原有桌面自启条目（避免与系统服务争抢端口）"
        fi
        ;;
    desktop)
        if [ -f "${SERVICE_PATH}" ]; then
            systemctl disable --now "${SERVICE_NAME}" >/dev/null 2>&1 || true
            rm -f "${SERVICE_PATH}"
            systemctl daemon-reload >/dev/null 2>&1 || true
            log "已移除原有 systemd 服务（避免与桌面自启争抢端口）"
        fi
        install_desktop_autostart
        ;;
    none)
        log "已按 --autostart none 跳过开机启动配置"
        ;;
esac

# --------------------------------------------------------------------------- 状态记录
cat > "${STATE_FILE}" <<EOF
# 由 install_linux.sh 生成，供 uninstall_linux.sh 使用
INSTALL_DIR=${INSTALL_DIR}
SERVICE_USER=${SERVICE_USER}
AUTOSTART=${AUTOSTART}
MODE=$([ "${BINARY_MODE}" -eq 1 ] && echo binary || echo source)
EOF
chmod 0644 "${STATE_FILE}"

# --------------------------------------------------------------------------- 依赖自检
log "环境自检"
[ -x "$(command -v lpstat 2>/dev/null || echo /nonexistent)" ] && ok "CUPS 打印组件：可用" || warn "未检测到 lpstat，打印功能不可用（请安装 cups-client）"
if command -v soffice >/dev/null 2>&1 || [ -x /usr/lib/libreoffice/program/soffice ]; then
    ok "LibreOffice：可用（支持 Office/WPS 共 9 种格式）"
else
    warn "未检测到 LibreOffice，Office/WPS 格式不可用（其余 11 种格式不受影响）"
fi
if fc-list 2>/dev/null | grep -qiE 'noto.*cjk|wqy|source han|zenhei'; then
    ok "中文字体：已安装"
else
    warn "未检测到中文字体，打印中文内容会失败（请安装 fonts-noto-cjk / wqy-zenhei-fonts）"
fi

# --------------------------------------------------------------------------- 完成
PORT_SHOW="${PORT:-5000}"
IP_SHOW="$( (ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1) || true)"
[ -n "${IP_SHOW}" ] || IP_SHOW="<本机IP>"

echo
ok "安装完成"
echo "  访问地址   : http://${IP_SHOW}:${PORT_SHOW}"
echo "  其他设备   : 在同一网络内的浏览器打开上述地址即可上传并打印"
echo "  安装目录   : ${INSTALL_DIR}"
echo "  运行用户   : ${SERVICE_USER}"
case "${AUTOSTART}" in
    systemd)
        echo "  开机启动   : 已开启（systemd 服务 ${SERVICE_NAME}，开机即运行、无需登录）"
        echo "  查看日志   : journalctl -u ${APP_NAME} -f"
        echo "  重启服务   : sudo systemctl restart ${SERVICE_NAME}"
        ;;
    desktop)
        echo "  开机启动   : 已开启（桌面自启，登录后带托盘图标运行）"
        echo "  查看日志   : 托盘菜单「打开打印日志」，或终端启动 ${RUN_CMD}"
        ;;
    none)
        echo "  开机启动   : 未设置（可用 --autostart systemd 重新执行本脚本）"
        ;;
esac
echo "  卸载       : sudo bash tools/uninstall_linux.sh --purge"
echo "  环境自检   : bash tools/selfcheck_linux.sh"
echo
echo "  管理员账号 : admin（初始密码 admin123，登录后请立即修改）"
