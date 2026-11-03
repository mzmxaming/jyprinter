#!/usr/bin/env bash
# =============================================================================
# jyprinter —— iOS 客户端构建脚本（Flet）
#
# 重要：iOS 只能在 macOS + Xcode 上构建，交叉编译不被支持。
#
# 用法（在 macOS 上、项目根目录执行）：
#   bash tools/build_ios.sh              # 构建（无签名，产出未签名 IPA）
#   bash tools/build_ios.sh --clean      # 先清理构建目录
#
# 前置条件：
#   · macOS 14+、Xcode 15+（含命令行工具）、CocoaPods（pod --version 可用）
#   · python3 ≥ 3.11（脚本会自动创建 .venv-ios 并安装 requirements-ios.txt）
#   · 首次构建 flet 会下载 Flutter SDK，需要联网，耗时较长
#
# 产物：
#   dist/ios/jyprinter-ios-<版本>-unsigned.ipa     未签名 IPA（用 Sideloadly/AltStore 自签安装）
#   dist/ios/build/ios/archive/*.xcarchive         未签名归档（可在 Xcode 里手动签名导出）
#   若配置了签名环境变量，则额外产出已签名 IPA：
#     IOS_TEAM_ID                  Apple 开发者 Team ID（10 位）
#     IOS_SIGNING_CERTIFICATE      证书名称（钥匙串中的 Common Name，如 "Apple Distribution: xxx"）
#     IOS_PROVISIONING_PROFILE     描述文件名称或 UUID
#     IOS_EXPORT_METHOD            app-store / ad-hoc / development / enterprise
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

APP_NAME="jyprinter"
VENV="${ROOT}/.venv-ios"
PY="${VENV}/bin/python"
FLET="${VENV}/bin/flet"
STAGING="${ROOT}/build/flet-ios"
OUT_DIR="${ROOT}/dist/ios"

log()  { printf '\033[1;34m[构建]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[完成]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

CLEAN=0
[ "${1:-}" = "--clean" ] && CLEAN=1

# ------------------------------------------------------------------ 环境检查
[ "$(uname -s)" = "Darwin" ] || die "本脚本只能在 macOS 上执行（当前：$(uname -s)）。iOS 不支持交叉编译。"
command -v python3 >/dev/null 2>&1 || die "未找到 python3，请安装 Python 3.11 或更高版本"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 版本过低：$(python3 -V 2>&1)，需要 3.11+"
command -v xcodebuild >/dev/null 2>&1 || die "未检测到 Xcode，请安装 Xcode 15+ 后执行：sudo xcode-select -s /Applications/Xcode.app"
command -v pod >/dev/null 2>&1 || warn "未检测到 CocoaPods，iOS 构建可能失败：sudo gem install cocoapods"

if [ "${CLEAN}" -eq 1 ]; then
    log "清理旧构建目录"
    rm -rf "${STAGING}" "${OUT_DIR}"
fi

# ------------------------------------------------------------------ 构建环境
if [ ! -x "${PY}" ]; then
    log "创建构建虚拟环境 ${VENV}"
    python3 -m venv "${VENV}" || die "创建虚拟环境失败"
fi
log "安装构建依赖（requirements-ios.txt）"
"${PY}" -m pip install --upgrade pip >/dev/null 2>&1 || true
"${PY}" -m pip install -r "${ROOT}/requirements-ios.txt" || die "依赖安装失败"
[ -x "${FLET}" ] || die "未找到 flet 命令（${FLET}）"

VERSION="$("${PY}" -c "import sys; sys.path.insert(0, '${ROOT}'); from core import settings; print(settings.VERSION)")"
BUILD_NUMBER="$(date +%Y%m%d%H%M)"
log "客户端版本：${VERSION}，构建号：${BUILD_NUMBER}"

# ------------------------------------------------------------------ 组装 staging
log "组装 staging 目录 ${STAGING}"
rm -rf "${STAGING}"
mkdir -p "${STAGING}/assets"

install -m 0644 "${ROOT}/flet_client.py" "${STAGING}/flet_client.py"
install -m 0644 "${ROOT}/packaging/pyproject.mobile.toml" "${STAGING}/pyproject.toml"
[ -f "${ROOT}/assets/jyprinter.png" ] || die "缺少 assets/jyprinter.png，无法设置应用图标"
install -m 0644 "${ROOT}/assets/jyprinter.png" "${STAGING}/assets/icon.png"

# ------------------------------------------------------------------ 签名参数（可选）
FLET_ARGS=(
    build ipa
    --build-version "${VERSION}"
    --build-number "${BUILD_NUMBER}"
    --output "${OUT_DIR}"
    --yes
)
if [ -n "${IOS_TEAM_ID:-}" ] && [ -n "${IOS_PROVISIONING_PROFILE:-}" ]; then
    log "检测到签名配置，将产出已签名 IPA"
    FLET_ARGS+=(--ios-team-id "${IOS_TEAM_ID}")
    FLET_ARGS+=(--ios-provisioning-profile "${IOS_PROVISIONING_PROFILE}")
    [ -n "${IOS_SIGNING_CERTIFICATE:-}" ] && FLET_ARGS+=(--ios-signing-certificate "${IOS_SIGNING_CERTIFICATE}")
    [ -n "${IOS_EXPORT_METHOD:-}" ] && FLET_ARGS+=(--ios-export-method "${IOS_EXPORT_METHOD}")
else
    warn "未配置 IOS_TEAM_ID / IOS_PROVISIONING_PROFILE：将执行无签名构建（只产出 .xcarchive）"
fi

# ------------------------------------------------------------------ 构建
mkdir -p "${OUT_DIR}"
log "执行 flet build ipa（首次运行需下载 Flutter SDK，请耐心等待）"
(
    cd "${STAGING}"
    "${FLET}" "${FLET_ARGS[@]}"
) || die "flet build ipa 失败，请查看上方日志"

# ------------------------------------------------------------------ 收集产物
IPA="$(find "${OUT_DIR}" -maxdepth 2 -name '*.ipa' -type f 2>/dev/null | head -n1 || true)"

if [ -z "${IPA}" ]; then
    # 无签名构建只产出 .xcarchive，这里把其中的 .app 打成未签名 IPA，
    # 便于在 Windows/Mac 上用 Sideloadly / AltStore 以免费 Apple ID 自签安装。
    APP_PATH="$(find "${STAGING}/build/ios/archive" -maxdepth 8 -path '*Products/Applications/*.app' -type d 2>/dev/null | head -n1 || true)"
    [ -n "${APP_PATH}" ] || APP_PATH="$(find "${OUT_DIR}" -path '*Products/Applications/*.app' -type d 2>/dev/null | head -n1 || true)"
    if [ -n "${APP_PATH}" ]; then
        log "未生成 IPA（无签名构建），正在打包未签名 IPA"
        PKG_DIR="${ROOT}/build/ios-payload"
        rm -rf "${PKG_DIR}"
        mkdir -p "${PKG_DIR}/Payload"
        cp -R "${APP_PATH}" "${PKG_DIR}/Payload/"
        IPA="${OUT_DIR}/${APP_NAME}-ios-${VERSION}-unsigned.ipa"
        rm -f "${IPA}"
        ( cd "${PKG_DIR}" && zip -qry "${IPA}" Payload )
    fi
fi

[ -n "${IPA}" ] && [ -f "${IPA}" ] || die "构建结束但未找到 IPA，请检查 ${OUT_DIR} 与 ${STAGING}/build/ios"

# 已签名构建时 flet 产出的是 <构件名>.ipa（如 jyprinter_client.ipa），统一改成可读名称
if [ "$(basename "${IPA}")" != "${APP_NAME}-ios-${VERSION}-unsigned.ipa" ]; then
    TARGET="${OUT_DIR}/${APP_NAME}-ios-${VERSION}.ipa"
    if [ "${IPA}" != "${TARGET}" ]; then
        mv -f "${IPA}" "${TARGET}"
        IPA="${TARGET}"
    fi
fi

ok "构建完成"
echo "  iOS 安装包 : ${IPA}（$(du -h "${IPA}" | cut -f1)）"
echo
if [[ "$(basename "${IPA}")" == *"-unsigned.ipa" ]]; then
    echo "  安装方式：未签名 IPA，用 Sideloadly（Windows/Mac）或 AltStore 以免费 Apple ID 签名安装，"
    echo "            有效期 7 天，到期重新签名即可。"
else
    echo "  安装方式：已签名 IPA，可用 Apple Configurator、Xcode Devices 或 TestFlight 安装；"
    echo "            目标设备的 UDID 必须在描述文件的 ProvisionedDevices 中。"
fi
echo
echo "  首次打开需允许「本地网络」权限，否则无法连接局域网打印服务。"
