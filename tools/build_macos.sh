#!/usr/bin/env bash
# =============================================================================
# jyprinter —— macOS 安装包构建脚本（Flet）
#
# 重要：Flet 只能在 macOS 上构建 macOS 应用，交叉编译不被支持。
#       请在 Mac 上、项目根目录执行本脚本。
#
# 用法：
#   bash tools/build_macos.sh              # 构建 .app 并打包成 .dmg
#   bash tools/build_macos.sh --clean      # 先清理 staging 与旧产物再构建
#
# 前置条件：
#   · macOS 12+，已安装 Xcode 15+（或至少 Command Line Tools：xcode-select --install）
#   · python3 ≥ 3.10（脚本会自动创建 .venv-macos 并安装 requirements-macos.txt）
#   · 首次构建时 flet 会自动下载 Flutter SDK 与随包 Python 运行时，需要联网
#
# 产物：
#   dist/macos/jyprinter.app                     可直接双击运行的应用程序
#   dist/jyprinter-macos-<version>.dmg           拖拽安装镜像（含 Applications 快捷方式）
#
# 说明：
#   · 默认使用 ad-hoc 签名，适合自用/内网分发；首次打开需「右键 → 打开」，
#     或到「系统设置 → 隐私与安全性」点「仍要打开」；
#   · 对外公开分发需 Apple Developer ID 证书 + 公证（notarization），本脚本不处理；
#   · 只复制运行所需文件到 staging，避免把 .venv、build、dist 打进应用包。
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

APP_NAME="jyprinter"
APP_DESC="网络打印服务"
VENV="${ROOT}/.venv-macos"
PY="${VENV}/bin/python"
FLET="${VENV}/bin/flet"
STAGING="${ROOT}/build/flet-macos"
OUT_DIR="${ROOT}/dist/macos"
DMG_ROOT="${ROOT}/build/dmg-root"

log()  { printf '\033[1;34m[构建]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[完成]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

CLEAN=0
if [ "${1:-}" = "--clean" ]; then
    CLEAN=1
fi

# ------------------------------------------------------------------ 环境检查
[ "$(uname -s)" = "Darwin" ] || die "本脚本只能在 macOS 上执行（当前系统：$(uname -s)）。Flet 不支持交叉编译 macOS 应用。"

command -v python3 >/dev/null 2>&1 || die "未找到 python3，请先安装 Python 3.10 或更高版本"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "Python 版本过低：$(python3 -V 2>&1)，需要 3.10 或更高版本"
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

# python.org 安装的 Python 默认不带根证书，未执行 Install Certificates 时，
# pip 的构建后端（用 urllib 访问 HTTPS）会报 CERTIFICATE_VERIFY_FAILED
if ! python3 -c 'import urllib.request; urllib.request.urlopen("https://pypi.org/simple/", timeout=10)' >/dev/null 2>&1; then
    warn "python3 无法验证 HTTPS 证书（python.org 版 Python 的常见问题），可能让依赖安装失败。"
    warn "修复方式一：/Applications/Python ${PY_VER}/Install Certificates.command"
    warn "修复方式二：brew install python@${PY_VER} 后用它重建 .venv-macos"
fi

xcode-select -p >/dev/null 2>&1 \
    || die "未安装 Xcode 命令行工具，请执行：xcode-select --install"
if ! xcodebuild -version >/dev/null 2>&1; then
    warn "未检测到完整 Xcode；若构建失败，请安装 Xcode 15+ 后执行：sudo xcode-select -s /Applications/Xcode.app"
fi

if [ "${CLEAN}" -eq 1 ]; then
    log "清理旧构建目录"
    rm -rf "${STAGING}" "${DMG_ROOT}" "${OUT_DIR}"
fi

# ------------------------------------------------------------------ 构建环境
if [ ! -x "${PY}" ]; then
    log "创建构建虚拟环境 ${VENV}"
    python3 -m venv "${VENV}" || die "创建虚拟环境失败"
fi
log "安装构建依赖（requirements-macos.txt）"
"${PY}" -m pip install --upgrade pip >/dev/null 2>&1 || true
"${PY}" -m pip install -r "${ROOT}/requirements-macos.txt" \
    || die "依赖安装失败。若报 CERTIFICATE_VERIFY_FAILED：先执行 /Applications/Python ${PY_VER}/Install Certificates.command；若报 PyMuPDF 编译失败：确认 requirements-macos.txt 中为 PyMuPDF>=1.24,<2"

[ -x "${FLET}" ] || die "未找到 flet 命令（${FLET}），请检查依赖安装是否成功"

VERSION="$("${PY}" -c "import sys; sys.path.insert(0, '${ROOT}'); from core import settings; print(settings.VERSION)")"
BUILD_NUMBER="$(date +%Y%m%d%H%M)"
log "程序版本：${VERSION}，构建号：${BUILD_NUMBER}"

# ------------------------------------------------------------------ 组装 staging
log "组装 staging 目录 ${STAGING}"
rm -rf "${STAGING}"
mkdir -p "${STAGING}"

rsync -a --exclude '__pycache__' --exclude '*.pyc' \
    "${ROOT}/core" "${ROOT}/web" "${ROOT}/assets" "${STAGING}/"
install -m 0644 "${ROOT}/main.py" "${ROOT}/flet_app.py" "${ROOT}/pyproject.toml" "${STAGING}/"

# flet 会从 assets/icon.png 生成 macOS 全套图标（不允许把图标写进 pyproject）
[ -f "${ROOT}/assets/jyprinter.png" ] || die "缺少 assets/jyprinter.png，无法设置应用图标"
install -m 0644 "${ROOT}/assets/jyprinter.png" "${STAGING}/assets/icon.png"

# ------------------------------------------------------------------ 调用 flet 构建
mkdir -p "${OUT_DIR}"
log "执行 flet build macos（首次运行需下载 Flutter SDK，请耐心等待）"
(
    cd "${STAGING}"
    "${FLET}" build macos \
        --build-version "${VERSION}" \
        --build-number "${BUILD_NUMBER}" \
        --output "${OUT_DIR}" \
        --yes
) || die "flet build macos 失败，请查看上方日志"

APP_PATH="${OUT_DIR}/${APP_NAME}.app"
if [ ! -d "${APP_PATH}" ]; then
    # 兜底：artifact 名与 .app 名不一致时，从产物目录里找第一个 .app
    APP_PATH="$(find "${OUT_DIR}" -maxdepth 2 -name '*.app' -type d -print 2>/dev/null | head -n 1 || true)"
fi
[ -n "${APP_PATH}" ] && [ -d "${APP_PATH}" ] || die "构建未生成 .app，请检查 ${OUT_DIR}"
ok "已生成应用：${APP_PATH}"

# ------------------------------------------------------------------ 生成 dmg
DMG_PATH="${ROOT}/dist/${APP_NAME}-macos-${VERSION}.dmg"
log "制作磁盘镜像 ${DMG_PATH}"
rm -rf "${DMG_ROOT}"
mkdir -p "${DMG_ROOT}"
cp -R "${APP_PATH}" "${DMG_ROOT}/"
ln -s /Applications "${DMG_ROOT}/Applications"

rm -f "${DMG_PATH}"
hdiutil create \
    -volname "${APP_DESC} ${VERSION}" \
    -srcfolder "${DMG_ROOT}" \
    -ov \
    -format UDZO \
    "${DMG_PATH}" >/dev/null || die "hdiutil 制作 dmg 失败"

ok "构建完成"
echo "  应用程序 : ${APP_PATH}（$(du -sh "${APP_PATH}" | cut -f1)）"
echo "  安装镜像 : ${DMG_PATH}（$(du -h "${DMG_PATH}" | cut -f1)）"
echo
echo "  安装与使用："
echo "    1. 双击 ${DMG_PATH##*/}，把「${APP_DESC}」拖到 Applications；"
echo "    2. 首次打开如提示「无法验证开发者」：右键点应用 → 打开，"
echo "       或到「系统设置 → 隐私与安全性」点「仍要打开」；"
echo "    3. 应用启动后会自动开启局域网打印服务，窗口内可复制访问地址；"
echo "    4. 需要在后台长期运行，请打开窗口内「登录时自动启动」。"
