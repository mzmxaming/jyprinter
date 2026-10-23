#!/usr/bin/env bash
# =============================================================================
# jyprinter —— Linux / 银河麒麟 二进制打包脚本
#
# 用法（在项目根目录执行，普通用户即可）：
#   bash tools/build_linux.sh              # 打包并生成发行压缩包
#   bash tools/build_linux.sh --clean      # 先删除旧的构建目录再打包
#
# 产物（dist/ 目录下）：
#   dist/jyprinter                                    单文件可执行程序
#   dist/pkg/jyprinter-linux-<arch>-<version>/        发行目录（程序 + 安装/卸载/自检脚本 + 图标）
#   dist/jyprinter-linux-<arch>-<version>.tar.gz      可直接拷贝到麒麟机器上解压安装
#
# 说明：
#   · 使用 requirements-linux.txt 中的版本（兼容 Python 3.7~3.11），需提前安装 python3-venv；
#   · 打包机与目标机应同为 x86_64 或同为 aarch64（PyInstaller 不支持交叉编译）；
#   · 目标机仍需 CUPS 与字体等系统依赖，由 install_linux.sh 负责安装。
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

APP_NAME="jyprinter"
APP_DESC="网络打印服务"
VENV="${ROOT}/.venv-linux"
PY="${VENV}/bin/python"

log()  { printf '\033[1;34m[打包]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[完成]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

CLEAN=0
if [ "${1:-}" = "--clean" ]; then
    CLEAN=1
fi

ARCH="$(uname -m)"
case "${ARCH}" in
    x86_64|aarch64|arm64|loongarch64) ;;
    *) die "不支持的架构：${ARCH}（仅支持 x86_64 / aarch64 / loongarch64）" ;;
esac

command -v python3 >/dev/null 2>&1 || die "未找到 python3，请先安装 python3 与 python3-venv"

if [ "${CLEAN}" -eq 1 ]; then
    log "清理旧构建目录"
    rm -rf "${ROOT}/build" "${ROOT}/dist"
fi

# ------------------------------------------------------------------ 构建环境
if [ ! -x "${PY}" ]; then
    log "创建打包虚拟环境 ${VENV}"
    python3 -m venv "${VENV}" || die "创建虚拟环境失败，请先安装 python3-venv"
fi
log "安装打包依赖（requirements-linux.txt）"
"${PY}" -m pip install --upgrade pip >/dev/null 2>&1 || true
"${PY}" -m pip install -r "${ROOT}/requirements-linux.txt" || die "依赖安装失败"

VERSION="$("${PY}" -c "import sys; sys.path.insert(0, '${ROOT}'); from core import settings; print(settings.VERSION)")"
log "程序版本：${VERSION}，目标架构：${ARCH}"

# ------------------------------------------------------------------ 打包
mkdir -p "${ROOT}/dist"
PYI_LOG="${ROOT}/dist/pyinstaller.log"
log "运行 PyInstaller（完整日志：${PYI_LOG}）"
if ! "${PY}" -m PyInstaller --clean --noconfirm "${ROOT}/jyprinter-linux.spec" >"${PYI_LOG}" 2>&1; then
    tail -n 30 "${PYI_LOG}"
    die "PyInstaller 打包失败，完整日志见 ${PYI_LOG}"
fi

BIN="${ROOT}/dist/${APP_NAME}"
[ -f "${BIN}" ] || die "打包失败：未生成 ${BIN}"
chmod 0755 "${BIN}"
ok "已生成可执行程序：${BIN}（$(du -h "${BIN}" | cut -f1)）"

# ------------------------------------------------------------------ 组装发行目录
PKG_NAME="${APP_NAME}-linux-${ARCH}-${VERSION}"
PKG_DIR="${ROOT}/dist/pkg/${PKG_NAME}"
log "组装发行目录 ${PKG_DIR}"
rm -rf "${PKG_DIR}"
mkdir -p "${PKG_DIR}/tools" "${PKG_DIR}/assets"

install -m 0755 "${BIN}" "${PKG_DIR}/${APP_NAME}"
install -m 0644 "${ROOT}/assets/jyprinter.png" "${PKG_DIR}/assets/jyprinter.png"
install -m 0644 "${ROOT}/assets/jyprinter.ico" "${PKG_DIR}/assets/jyprinter.ico"
install -m 0755 "${ROOT}/tools/install_linux.sh" "${PKG_DIR}/tools/install_linux.sh"
install -m 0755 "${ROOT}/tools/uninstall_linux.sh" "${PKG_DIR}/tools/uninstall_linux.sh"
install -m 0755 "${ROOT}/tools/selfcheck_linux.sh" "${PKG_DIR}/tools/selfcheck_linux.sh"
install -m 0644 "${ROOT}/tools/selftest_linux.py" "${PKG_DIR}/tools/selftest_linux.py"

cat > "${PKG_DIR}/安装说明.txt" <<EOF
jyprinter ${APP_DESC}（Linux / 银河麒麟）v${VERSION}  ${ARCH}

一、安装
    sudo bash tools/install_linux.sh
    （默认安装到 /opt/jyprinter，并设置为开机自启的 systemd 服务）
    常用参数：
        --port 8080            指定服务端口
        --autostart desktop    改为登录后带托盘启动
        --skip-deps            跳过系统依赖安装

二、使用
    在同网络的其他设备浏览器中打开  http://<本机IP>:5000
    管理员账号 admin / admin123（登录后请立即修改密码）

三、自检与排障
    bash tools/selfcheck_linux.sh              环境自检（只读，不打印）
    bash tools/selfcheck_linux.sh --test-submit 打印管道试跑（作业保持挂起后立即取消，不出纸）
    journalctl -u jyprinter -f                 查看服务日志

四、卸载
    sudo bash tools/uninstall_linux.sh              保留程序目录
    sudo bash tools/uninstall_linux.sh --purge      连同数据一并删除

五、重要提示
    程序依赖系统 CUPS（打印）、LibreOffice（Office/WPS 格式转换）与中文字体，
    请确保已安装：cups cups-client libreoffice fonts-noto-cjk。
EOF

TARBALL="${ROOT}/dist/${PKG_NAME}.tar.gz"
log "生成发行压缩包"
tar -C "${ROOT}/dist/pkg" -czf "${TARBALL}" "${PKG_NAME}"

ok "打包完成"
echo "  可执行程序 : ${BIN}"
echo "  发行目录   : ${PKG_DIR}"
echo "  压缩包     : ${TARBALL}（$(du -h "${TARBALL}" | cut -f1)）"
echo
echo "  部署到麒麟机器："
echo "    tar -xzf ${PKG_NAME}.tar.gz && cd ${PKG_NAME} && sudo bash tools/install_linux.sh"
