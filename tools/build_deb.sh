#!/usr/bin/env bash
# =============================================================================
# jyprinter —— 构建 .deb 安装包（deb 系银河麒麟 / UOS / Ubuntu / Debian）
#
# 用法（普通用户即可）：
#   bash tools/build_linux.sh      # 第一步：产出单文件可执行程序 dist/jyprinter
#   bash tools/build_deb.sh        # 第二步：打成 deb，产物 dist/jyprinter_<版本>_<架构>.deb
#
# 前置条件：
#   · 必须在 deb 系机器上构建，且架构与目标机一致（amd64 / arm64，不支持交叉）；
#   · 已安装 dpkg（提供 dpkg-deb；dpkg ≥ 1.19 无需 fakeroot）；
#   · 已执行过 tools/build_linux.sh（本脚本只打包，不重复编译）。
#
# 安装行为（由包内脚本自动完成）：
#   程序     → /opt/jyprinter（root 只读，仅 config.json 归服务用户所有）
#   数据     → /var/lib/jyprinter（专用系统用户 jyprinter，无登录 shell）
#   开机启动 → systemd 服务 jyprinter.service（开机即运行，无需登录）
#   系统依赖 → 由 apt 自动安装 CUPS 与中文字体；LibreOffice 为推荐项
#
# 说明：本包只提供「服务 + 网页端」，不含桌面托盘启动项（托盘方式请用 tar.gz 发行包）。
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

PKG="jyprinter"
APP_DESC="网络打印服务"
DATA_DIR="/var/lib/jyprinter"
SERVICE_NAME="jyprinter.service"
UNIT_SRC="${ROOT}/packaging/${SERVICE_NAME}"

log()  { printf '\033[1;34m[打包]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[完成]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ 前置检查
command -v dpkg-deb >/dev/null 2>&1 || die "未找到 dpkg-deb，请先安装：sudo apt install dpkg"

ARCH="$(uname -m)"
case "${ARCH}" in
    x86_64)              DEB_ARCH="amd64" ;;
    aarch64|arm64)       DEB_ARCH="arm64" ;;
    loongarch64|loong64) DEB_ARCH="loong64" ;;
    *) die "不支持的架构：${ARCH}（仅支持 x86_64 / aarch64 / loongarch64）" ;;
esac

VERSION="$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "${ROOT}/core/settings.py" | head -n1)"
[ -n "${VERSION}" ] || die "无法从 core/settings.py 解析版本号"

BIN="${ROOT}/dist/${PKG}"
if [ ! -f "${BIN}" ]; then
    die "未找到 ${BIN}，请先执行：bash tools/build_linux.sh"
fi

# 校验待打包程序的 ELF 架构：PyInstaller 不支持交叉编译，
# 若把别的架构的二进制打进来，会得到「架构标签正确但根本跑不起来」的包
elf_arch() {
    local machine
    machine="$(od -An -t u2 -j 18 -N 2 "$1" 2>/dev/null | tr -d ' \n')"
    case "${machine}" in
        62)  echo "x86_64" ;;
        183) echo "aarch64" ;;
        258) echo "loongarch64" ;;
        *)   echo "unknown(${machine})" ;;
    esac
}
BIN_ARCH="$(elf_arch "${BIN}")"
[ "${BIN_ARCH}" = "${ARCH}" ] || die "dist/${PKG} 是 ${BIN_ARCH} 架构，与构建机 ${ARCH} 不一致；请在本机重新执行 bash tools/build_linux.sh --clean"

[ -f "${UNIT_SRC}" ] || die "缺少 systemd 单元模板：${UNIT_SRC}"

log "程序版本：${VERSION}　目标架构：${DEB_ARCH}"

# 是否支持 --root-owner-group（dpkg ≥ 1.19），否则退回 fakeroot
ROOT_OWNER_GROUP=0
if dpkg-deb --help 2>&1 | grep -q -- "--root-owner-group"; then
    ROOT_OWNER_GROUP=1
elif ! command -v fakeroot >/dev/null 2>&1; then
    die "当前 dpkg 不支持 --root-owner-group 且未安装 fakeroot，请执行：sudo apt install fakeroot"
fi

# ------------------------------------------------------------------ 组装包内容
STAGE="${ROOT}/build/deb/${PKG}_${VERSION}_${DEB_ARCH}"
log "组装包目录 ${STAGE}"
rm -rf "${STAGE}"
mkdir -p "${STAGE}/DEBIAN" \
         "${STAGE}/opt/${PKG}/assets" \
         "${STAGE}/opt/${PKG}/tools" \
         "${STAGE}/usr/bin" \
         "${STAGE}/usr/lib/systemd/system" \
         "${STAGE}/usr/share/icons/hicolor/256x256/apps" \
         "${STAGE}/usr/share/icons/hicolor/128x128/apps" \
         "${STAGE}/usr/share/doc/${PKG}"

install -m 0755 "${BIN}" "${STAGE}/opt/${PKG}/${PKG}"
install -m 0644 "${ROOT}/assets/jyprinter.png" "${STAGE}/opt/${PKG}/assets/jyprinter.png"
if [ -f "${ROOT}/assets/jyprinter.ico" ]; then
    install -m 0644 "${ROOT}/assets/jyprinter.ico" "${STAGE}/opt/${PKG}/assets/jyprinter.ico"
fi
install -m 0755 "${ROOT}/tools/selfcheck_linux.sh" "${STAGE}/opt/${PKG}/tools/selfcheck_linux.sh"
install -m 0644 "${ROOT}/tools/selftest_linux.py" "${STAGE}/opt/${PKG}/tools/selftest_linux.py"

# 预置空的 config.json：安装脚本会把它交给服务用户，保证首次启动可写入默认配置
printf '{}' > "${STAGE}/opt/${PKG}/config.json"
chmod 0640 "${STAGE}/opt/${PKG}/config.json"

install -D -m 0644 "${UNIT_SRC}" "${STAGE}/usr/lib/systemd/system/${SERVICE_NAME}"
install -m 0644 "${ROOT}/assets/jyprinter.png" "${STAGE}/usr/share/icons/hicolor/256x256/apps/${PKG}.png"
install -m 0644 "${ROOT}/assets/jyprinter-128.png" "${STAGE}/usr/share/icons/hicolor/128x128/apps/${PKG}.png"
# 用相对链接指向程序（不写死绝对路径，便于整包迁移）
ln -sf "../../opt/${PKG}/${PKG}" "${STAGE}/usr/bin/${PKG}"

cat > "${STAGE}/usr/share/doc/${PKG}/README" <<EOF
${APP_DESC}（jyprinter）${VERSION}  ${DEB_ARCH}

一、访问
    在同网络的其他设备浏览器中打开  http://<本机IP>:5000
    初始管理员账号 admin / admin123（登录后请立即修改密码）

二、服务管理
    systemctl status ${SERVICE_NAME}      # 查看状态
    systemctl restart ${SERVICE_NAME}     # 重启
    journalctl -u ${PKG} -f               # 查看日志

三、配置与数据
    配置：/opt/${PKG}/config.json（需 root 修改；也可在网页端修改可调项）
    数据：${DATA_DIR}（上传文件、预览缓存、账号、密钥、日志）

四、自检与排障
    bash /opt/${PKG}/tools/selfcheck_linux.sh              环境自检（只读，不打印）
    bash /opt/${PKG}/tools/selfcheck_linux.sh --live       含真实 lpstat/lpoptions 解析校验
    bash /opt/${PKG}/tools/selfcheck_linux.sh --test-submit 打印管道试跑（不出纸）

五、依赖说明
    必需：CUPS（cups / cups-client）、中文字体（已随包自动安装）
    推荐：LibreOffice（缺失时 Office/WPS 共 9 种格式不可用，其余 11 种不受影响）

六、卸载
    sudo apt remove ${PKG}          # 保留数据目录
    sudo apt purge ${PKG}           # 连同数据与专用用户一并删除

程序开发：陈光明　微信号：cgm10050　联系电话：13923010050
界面图标：Material Design Icons（Apache-2.0）
EOF

# ------------------------------------------------------------------ DEBIAN 控制信息
INSTALLED_KB="$(du -sk "${STAGE}/opt" | cut -f1)"
cat > "${STAGE}/DEBIAN/control" <<EOF
Package: ${PKG}
Version: ${VERSION}
Architecture: ${DEB_ARCH}
Maintainer: 陈光明（微信号 cgm10050，联系电话 13923010050）
Section: utils
Priority: optional
Installed-Size: ${INSTALLED_KB}
Depends: cups, cups-client, fonts-noto-cjk | fonts-wqy-zenhei | fonts-wqy-microhei
Recommends: libreoffice-writer, libreoffice-calc, libreoffice-impress
Description: ${APP_DESC}（局域网文件静默打印）
 在连接打印机的机器上提供网页端：同一网络内的电脑或手机用浏览器上传文件，
 即可静默打印到本机连接的打印机，无需在客户端安装打印机驱动或办公软件。
 支持 PDF、图片、Office/WPS 与文本共 20 种格式，含网页预览、打印队列、
 账号权限与审计日志，可通过内网穿透供手机远程打印。
 .
 程序开发：陈光明　微信号：cgm10050　联系电话：13923010050
EOF

cat > "${STAGE}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
# 安装后：创建专用系统用户与数据目录、注册并启动开机自启服务
set -e

APP_DIR=/opt/jyprinter
DATA_DIR=/var/lib/jyprinter
SERVICE=jyprinter.service

# 1) 专用系统用户（无登录 shell；已存在则跳过）
if ! getent group jyprinter >/dev/null 2>&1; then
    if command -v addgroup >/dev/null 2>&1; then
        addgroup --system jyprinter >/dev/null 2>&1 || groupadd -r jyprinter || true
    else
        groupadd -r jyprinter || true
    fi
fi
if ! getent passwd jyprinter >/dev/null 2>&1; then
    if command -v adduser >/dev/null 2>&1; then
        adduser --system --no-create-home --home "${DATA_DIR}" --shell /usr/sbin/nologin \
                --ingroup jyprinter --gecos "Jyprinter service" jyprinter >/dev/null 2>&1 \
            || useradd -r -g jyprinter -d "${DATA_DIR}" -s /usr/sbin/nologin -c "Jyprinter service" jyprinter || true
    else
        useradd -r -g jyprinter -d "${DATA_DIR}" -s /usr/sbin/nologin -c "Jyprinter service" jyprinter || true
    fi
fi

# 2) 可写数据目录
mkdir -p "${DATA_DIR}"
chown -R jyprinter:jyprinter "${DATA_DIR}" 2>/dev/null || true
chmod 0750 "${DATA_DIR}" 2>/dev/null || true

# 3) 程序目录保持只读，仅 config.json 交给服务用户（首次启动需写入默认配置）
chmod 0755 "${APP_DIR}" 2>/dev/null || true
if [ -f "${APP_DIR}/config.json" ]; then
    chown jyprinter:jyprinter "${APP_DIR}/config.json" 2>/dev/null || true
    chmod 0640 "${APP_DIR}/config.json" 2>/dev/null || true
fi

# 4) 注册并启动服务
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable "${SERVICE}" >/dev/null 2>&1 || true
    systemctl restart "${SERVICE}" >/dev/null 2>&1 || true
fi

echo "网络打印服务已安装完成。"
echo "  服务名  : ${SERVICE}（开机自启已启用）"
echo "  访问地址: http://<本机IP>:5000（初始账号 admin / admin123）"
echo "  自检    : bash ${APP_DIR}/tools/selfcheck_linux.sh"
exit 0
EOF

cat > "${STAGE}/DEBIAN/prerm" <<'EOF'
#!/bin/sh
# 卸载/升级前：停止服务（卸载时同时取消开机自启）
set -e
case "$1" in
    remove|purge|upgrade)
        if command -v systemctl >/dev/null 2>&1; then
            systemctl stop jyprinter.service >/dev/null 2>&1 || true
            if [ "$1" != "upgrade" ]; then
                systemctl disable jyprinter.service >/dev/null 2>&1 || true
            fi
        fi
        ;;
esac
exit 0
EOF

cat > "${STAGE}/DEBIAN/postrm" <<'EOF'
#!/bin/sh
# 卸载后：刷新 systemd；purge 时删除数据目录与专用用户
set -e
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || true
fi

if [ "$1" = "purge" ]; then
    rm -rf /var/lib/jyprinter
    if getent passwd jyprinter >/dev/null 2>&1; then
        if command -v deluser >/dev/null 2>&1; then
            deluser jyprinter >/dev/null 2>&1 || true
        else
            userdel jyprinter >/dev/null 2>&1 || true
        fi
    fi
    if getent group jyprinter >/dev/null 2>&1; then
        if command -v delgroup >/dev/null 2>&1; then
            delgroup jyprinter >/dev/null 2>&1 || true
        else
            groupdel jyprinter >/dev/null 2>&1 || true
        fi
    fi
fi
exit 0
EOF

chmod 0755 "${STAGE}/DEBIAN/postinst" "${STAGE}/DEBIAN/prerm" "${STAGE}/DEBIAN/postrm"

# ------------------------------------------------------------------ 生成 .deb
OUT="${ROOT}/dist/${PKG}_${VERSION}_${DEB_ARCH}.deb"
mkdir -p "${ROOT}/dist"
log "生成安装包 ${OUT}"
if [ "${ROOT_OWNER_GROUP}" -eq 1 ]; then
    dpkg-deb --root-owner-group --build "${STAGE}" "${OUT}"
else
    fakeroot dpkg-deb --build "${STAGE}" "${OUT}"
fi
[ -f "${OUT}" ] || die "dpkg-deb 未生成 ${OUT}，请检查安装包目录是否完整"

ok "打包完成：${OUT}（$(du -h "${OUT}" | cut -f1)）"
echo
echo "  安装：sudo dpkg -i ${PKG}_${VERSION}_${DEB_ARCH}.deb"
echo "        sudo apt -f install        # 若提示缺少依赖，用这条补齐"
echo "  自检：bash /opt/jyprinter/tools/selfcheck_linux.sh --live"
echo "  卸载：sudo apt remove ${PKG}          # 保留数据"
echo "        sudo apt purge ${PKG}           # 连数据与专用用户一并删除"
