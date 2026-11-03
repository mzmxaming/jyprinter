#!/usr/bin/env bash
# =============================================================================
# jyprinter —— 构建 .rpm 安装包（rpm 系银河麒麟 / 中标麒麟 / RHEL / CentOS）
#
# 用法（普通用户即可，不需 root）：
#   bash tools/build_linux.sh      # 第一步：产出单文件可执行程序 dist/jyprinter
#   bash tools/build_rpm.sh        # 第二步：打成 rpm，产物 dist/jyprinter-<版本>-1.<架构>.rpm
#
# 前置条件：
#   · 必须在 rpm 系机器上构建，且架构与目标机一致（x86_64 / aarch64，不支持交叉）；
#   · 已安装 rpm-build（提供 rpmbuild）：sudo yum install rpm-build 或 sudo dnf install rpm-build；
#   · 已执行过 tools/build_linux.sh（本脚本只打包，不重复编译）。
#
# 安装行为（由包内脚本自动完成）：
#   程序     → /opt/jyprinter（root 只读，仅 config.json 归服务用户）
#   数据     → /var/lib/jyprinter（专用系统用户 jyprinter，无登录 shell）
#   开机启动 → systemd 服务 jyprinter.service（开机即运行，无需登录）
#
# 说明：本包只提供「服务 + 网页端」，不含桌面托盘启动项（托盘方式请用 tar.gz 发行包）。
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

PKG="jyprinter"
APP_DESC="网络打印服务"
SERVICE_NAME="jyprinter.service"
UNIT_SRC="${ROOT}/packaging/${SERVICE_NAME}"

log()  { printf '\033[1;34m[打包]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[完成]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ 前置检查
command -v rpmbuild >/dev/null 2>&1 || die "未找到 rpmbuild，请先安装：sudo yum install rpm-build（或 sudo dnf install rpm-build）"

ARCH="$(uname -m)"
case "${ARCH}" in
    x86_64|aarch64|loongarch64) ;;
    *) die "不支持的架构：${ARCH}（仅支持 x86_64 / aarch64 / loongarch64）" ;;
esac

VERSION="$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "${ROOT}/core/settings.py" | head -n1)"
[ -n "${VERSION}" ] || die "无法从 core/settings.py 解析版本号"

BIN="${ROOT}/dist/${PKG}"
if [ ! -f "${BIN}" ]; then
    die "未找到 ${BIN}，请先执行：bash tools/build_linux.sh"
fi

[ -f "${UNIT_SRC}" ] || die "缺少 systemd 单元模板：${UNIT_SRC}"

log "程序版本：${VERSION}　目标架构：${ARCH}"

# ------------------------------------------------------------------ 构建树
TOP="${ROOT}/build/rpm"
SRCDIR="${TOP}/SOURCES/${PKG}-${VERSION}"
log "准备源码目录 ${SRCDIR}"
rm -rf "${TOP}"
mkdir -p "${SRCDIR}/assets" "${SRCDIR}/tools" \
         "${TOP}/SPECS" "${TOP}/SOURCES" "${TOP}/BUILD" "${TOP}/BUILDROOT" "${TOP}/RPMS"

install -m 0755 "${BIN}" "${SRCDIR}/${PKG}"
install -m 0644 "${ROOT}/assets/jyprinter.png" "${SRCDIR}/assets/jyprinter.png"
# %install 阶段需要 128×128 图标，必须一并放入源码目录，否则 rpmbuild 在 %install 报 bad exit status
install -m 0644 "${ROOT}/assets/jyprinter-128.png" "${SRCDIR}/assets/jyprinter-128.png"
if [ -f "${ROOT}/assets/jyprinter.ico" ]; then
    install -m 0644 "${ROOT}/assets/jyprinter.ico" "${SRCDIR}/assets/jyprinter.ico"
fi
install -m 0755 "${ROOT}/tools/selfcheck_linux.sh" "${SRCDIR}/tools/selfcheck_linux.sh"
install -m 0644 "${ROOT}/tools/selftest_linux.py" "${SRCDIR}/tools/selftest_linux.py"

tar -C "${TOP}/SOURCES" -czf "${TOP}/SOURCES/${PKG}-${VERSION}.tar.gz" "${PKG}-${VERSION}"
install -m 0644 "${UNIT_SRC}" "${TOP}/SOURCES/${SERVICE_NAME}"

# ------------------------------------------------------------------ spec
SPEC="${TOP}/SPECS/${PKG}.spec"
log "生成 spec：${SPEC}"
cat > "${SPEC}" <<EOF
# jyprinter（${APP_DESC}）—— 由 tools/build_rpm.sh 自动生成，请勿直接手工修改
%global debug_package %{nil}
%define _unitdir /usr/lib/systemd/system

Name:           ${PKG}
Version:        ${VERSION}
Release:        1%{?dist}
Summary:        ${APP_DESC}（局域网文件静默打印）
License:        Proprietary
Group:          Applications/System

Requires:       cups
Requires:       cups-client
# 中文字体与办公文档转换引擎为软依赖：缺失时程序会给出明确提示，不影响其余格式
Recommends:     fonts-noto-cjk
Recommends:     wqy-zenhei-fonts
Recommends:     libreoffice-writer
Recommends:     libreoffice-calc
Recommends:     libreoffice-impress

Source0:        %{name}-%{version}.tar.gz
Source1:        ${SERVICE_NAME}

%description
在连接打印机的机器上提供网页端：同一网络内的电脑或手机用浏览器上传文件，
即可静默打印到本机连接的打印机，无需在客户端安装打印机驱动或办公软件。
支持 PDF、图片、Office/WPS 与文本共 20 种格式，含网页预览、打印队列、
账号权限与审计日志，可通过内网穿透供手机远程打印。

安装后浏览器访问 http://<本机IP>:5000，初始账号 admin / admin123。
服务管理：systemctl status ${PKG} / journalctl -u ${PKG} -f
环境自检：bash /opt/${PKG}/tools/selfcheck_linux.sh --live

程序开发：陈光明　微信号：cgm10050　联系电话：13923010050
界面图标：Material Design Icons（Apache-2.0）

%prep
%setup -q

%build

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/opt/${PKG}/assets %{buildroot}/opt/${PKG}/tools \\
         %{buildroot}%{_bindir} %{buildroot}%{_unitdir} \\
         %{buildroot}/var/lib/${PKG} \\
         %{buildroot}%{_datadir}/icons/hicolor/256x256/apps \\
         %{buildroot}%{_datadir}/icons/hicolor/128x128/apps

install -m 0755 ${PKG} %{buildroot}/opt/${PKG}/${PKG}
cp -a assets %{buildroot}/opt/${PKG}/assets
cp -a tools %{buildroot}/opt/${PKG}/tools
printf '{}' > %{buildroot}/opt/${PKG}/config.json
install -m 0644 %{SOURCE1} %{buildroot}%{_unitdir}/${SERVICE_NAME}
install -m 0644 assets/jyprinter.png %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/${PKG}.png
install -m 0644 assets/jyprinter-128.png %{buildroot}%{_datadir}/icons/hicolor/128x128/apps/${PKG}.png
ln -sf ../../opt/${PKG}/${PKG} %{buildroot}%{_bindir}/${PKG}

%pre
# 专用系统用户（无登录 shell）；已存在则跳过
getent group ${PKG} >/dev/null 2>&1 || groupadd -r ${PKG} 2>/dev/null || :
getent passwd ${PKG} >/dev/null 2>&1 || useradd -r -g ${PKG} -d /var/lib/${PKG} -s /sbin/nologin -c "Jyprinter service" ${PKG} 2>/dev/null || :

%post
mkdir -p /var/lib/${PKG}
chown ${PKG}:${PKG} /var/lib/${PKG} 2>/dev/null || :
chmod 0750 /var/lib/${PKG} 2>/dev/null || :
# 程序目录保持只读，仅 config.json 交给服务用户（首次启动需写入默认配置）
chmod 0755 /opt/${PKG} 2>/dev/null || :
if [ -f /opt/${PKG}/config.json ]; then
    chown ${PKG}:${PKG} /opt/${PKG}/config.json 2>/dev/null || :
    chmod 0640 /opt/${PKG}/config.json 2>/dev/null || :
fi
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || :
    systemctl enable ${SERVICE_NAME} >/dev/null 2>&1 || :
    systemctl restart ${SERVICE_NAME} >/dev/null 2>&1 || :
fi
echo "网络打印服务已安装完成，访问 http://<本机IP>:5000（初始账号 admin / admin123）"

%preun
# \$1=0 表示最终卸载（而非升级）
if [ "\$1" -eq 0 ]; then
    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop ${SERVICE_NAME} >/dev/null 2>&1 || :
        systemctl disable ${SERVICE_NAME} >/dev/null 2>&1 || :
    fi
fi

%postun
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || :
fi

%files
%dir /opt/${PKG}
/opt/${PKG}/${PKG}
/opt/${PKG}/assets
/opt/${PKG}/tools
%config(noreplace) /opt/${PKG}/config.json
%{_bindir}/${PKG}
%{_unitdir}/${SERVICE_NAME}
%{_datadir}/icons/hicolor/256x256/apps/${PKG}.png
%{_datadir}/icons/hicolor/128x128/apps/${PKG}.png
# 属主由 %post 设置为服务用户（构建机上通常没有该用户，故这里不写死用户/组）
%attr(0750,-,-) %dir /var/lib/${PKG}

%changelog
* $(LC_ALL=C date '+%a %b %d %Y') 陈光明 - ${VERSION}-1
- 构建版本 ${VERSION}
EOF

# ------------------------------------------------------------------ 生成 .rpm
log "运行 rpmbuild"
RPM_LOG="${ROOT}/dist/rpmbuild.log"
mkdir -p "${ROOT}/dist"
if ! rpmbuild --define "_topdir ${TOP}" --define "_unitdir /usr/lib/systemd/system" \
              -bb "${SPEC}" >"${RPM_LOG}" 2>&1; then
    tail -n 30 "${RPM_LOG}"
    die "rpmbuild 失败，完整日志见 ${RPM_LOG}"
fi

RPM_FILE="$(find "${TOP}/RPMS" -name "*.rpm" -type f | head -n1)"
[ -n "${RPM_FILE}" ] || die "未找到生成的 rpm，请查看 ${RPM_LOG}"

OUT="${ROOT}/dist/$(basename "${RPM_FILE}")"
cp -f "${RPM_FILE}" "${OUT}"

ok "打包完成：${OUT}（$(du -h "${OUT}" | cut -f1)）"
echo
echo "  安装：sudo rpm -ivh $(basename "${OUT}")"
echo "        sudo yum localinstall $(basename "${OUT}")   # 自动补齐依赖"
echo "  自检：bash /opt/jyprinter/tools/selfcheck_linux.sh --live"
echo "  卸载：sudo rpm -e ${PKG}                     # 保留数据目录"
echo "        sudo rm -rf /var/lib/${PKG} && sudo userdel ${PKG}   # 彻底清理"
