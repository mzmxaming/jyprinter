#!/usr/bin/env bash
# =============================================================================
# jyprinter 网络打印服务 —— Linux / 银河麒麟 环境自检脚本
#
# 用法（普通用户即可，无需 root；加 sudo 可看到更多服务状态）：
#   bash tools/selfcheck_linux.sh                    # 只读自检，不做任何打印
#   bash tools/selfcheck_linux.sh --test-submit      # 追加「打印管道试跑」（不出纸，见下）
#   bash tools/selfcheck_linux.sh --python /opt/jyprinter/.venv/bin/python
#
# --test-submit 说明（重要）：
#   该检查会在线生成一页测试 PDF，并通过 CUPS 以「保持挂起（-H hold）」方式提交，
#   作业不会送达打印机、不会出纸，随后立即 cancel 取消。
#   因此它可在生产打印机上安全用于验证「渲染 → 合成 PDF → CUPS 提交」整条链路，
#   且符合「禁止真机出纸测试」的约束。需要显式指定才会执行。
# =============================================================================

set -uo pipefail

PASS=0
FAIL=0
WARN=0

c_ok()   { printf '  \033[1;32m[通过]\033[0m %s\n' "$*"; PASS=$((PASS + 1)); }
c_bad()  { printf '  \033[1;31m[失败]\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
c_warn() { printf '  \033[1;33m[警告]\033[0m %s\n' "$*"; WARN=$((WARN + 1)); }
title()  { printf '\n\033[1;34m== %s ==\033[0m\n' "$*"; }

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "${SCRIPT_PATH}")"
SRC_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_FILE="/etc/jyprinter-install.conf"

PYTHON=""
DO_SUBMIT=0
DO_LIVE=0
BINARY_MODE=0
SUBMIT_PRINTER=""

usage() {
    cat <<'USAGE'
jyprinter 环境自检脚本

用法：bash tools/selfcheck_linux.sh [选项]

选项：
  --test-submit [打印机名]   追加打印管道试跑（作业挂起后立即取消，不出纸）
  --live                     解析自检同时使用目标机真实命令输出校验
  --python <解释器路径>      指定用于自检的 Python（默认自动探测 venv）
  -h, --help                 显示本帮助
USAGE
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --test-submit)
            DO_SUBMIT=1
            shift
            if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
                SUBMIT_PRINTER="$1"
                shift
            fi
            ;;
        --live) DO_LIVE=1; shift ;;
        --python) PYTHON="${2:?--python 需要参数}"; shift 2 ;;
        -h|--help) usage ;;
        *) printf '[错误] 未知参数：%s（-h 查看帮助）\n' "$1" >&2; exit 2 ;;
    esac
done

# ------------------------------------------------------------------ 定位程序目录
INSTALL_DIR=""
if [ -f "${STATE_FILE}" ]; then
    # shellcheck disable=SC1090
    . "${STATE_FILE}"
fi
# 优先检查「已安装位置」（存在安装记录时），否则使用脚本所在目录，
# 便于安装后自检与安装前预检两种场景
if [ -n "${INSTALL_DIR}" ] && [ -f "${INSTALL_DIR}/main.py" ]; then
    PROGRAM_DIR="${INSTALL_DIR}"
    BINARY_MODE=0
elif [ -n "${INSTALL_DIR}" ] && [ -x "${INSTALL_DIR}/jyprinter" ]; then
    PROGRAM_DIR="${INSTALL_DIR}"
    BINARY_MODE=1
elif [ -f "${SRC_ROOT}/main.py" ]; then
    PROGRAM_DIR="${SRC_ROOT}"
    BINARY_MODE=0
elif [ -x "${SRC_ROOT}/jyprinter" ]; then
    PROGRAM_DIR="${SRC_ROOT}"
    BINARY_MODE=1
else
    printf '[错误] 未找到 jyprinter 程序目录（请把脚本放在程序目录内运行）\n' >&2
    exit 2
fi

# ------------------------------------------------------------------ 定位 Python
if [ -z "${PYTHON}" ]; then
    for candidate in "${PROGRAM_DIR}/.venv/bin/python" "${SRC_ROOT}/.venv/bin/python"; do
        if [ -x "${candidate}" ]; then
            PYTHON="${candidate}"
            break
        fi
    done
fi
if [ -n "${PYTHON}" ] && [ -x "${PYTHON}" ]; then
    PY_MODE="虚拟环境"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
    PY_MODE="系统 Python（未找到 venv，源码模式安装请先执行 install_linux.sh）"
else
    PYTHON=""
    PY_MODE=""
fi

# 判断该解释器能否导入项目依赖（二进制模式无 venv 时通常不能，此时跳过依赖相关检查）
PY_OK=0
if [ -n "${PYTHON}" ] && "${PYTHON}" -c "import flask, PIL, qrcode" >/dev/null 2>&1; then
    PY_OK=1
fi

# ------------------------------------------------------------------ 1. 系统信息
title "系统信息"
if [ -f /etc/os-release ]; then
    distro="$(grep -E '^PRETTY_NAME=' /etc/os-release | cut -d= -f2- | tr -d '"')"
    c_ok "发行版：${distro}"
else
    c_warn "未读取到 /etc/os-release，无法确认发行版"
fi
c_ok "内核：$(uname -sr)　架构：$(uname -m)"
case "$(uname -m)" in
    x86_64|aarch64|arm64|loongarch64) c_ok "架构受支持" ;;
    *) c_warn "架构 $(uname -m) 未在官方验证范围内" ;;
esac

# ------------------------------------------------------------------ 2. Python 运行时
title "Python 运行时"
if [ "${BINARY_MODE}" -eq 1 ] && [ "${PY_OK}" -eq 0 ]; then
    c_ok "二进制发行版：运行时与依赖已内置，无需系统 Python"
    c_warn "本机没有可导入项目依赖的 Python，自检中的「解析自检」「打印管道试跑」会跳过；如需完整自检请改用源码模式"
elif [ -n "${PYTHON}" ]; then
    c_ok "$("${PYTHON}" -c 'import sys; print("Python %s（%s）" % (sys.version.split()[0], sys.executable))')"
    c_ok "来源：${PY_MODE}"
    case "$("${PYTHON}" -c 'import sys; print(sys.version_info >= (3, 7))')" in
        True) c_ok "版本满足要求（需 ≥ 3.7）" ;;
        *) c_bad "Python 版本过低，程序需要 3.7 及以上" ;;
    esac
else
    c_bad "未找到可用的 Python 解释器"
fi

# ------------------------------------------------------------------ 3. Python 依赖
title "Python 依赖"
if [ "${BINARY_MODE}" -eq 1 ] && [ "${PY_OK}" -eq 0 ]; then
    c_ok "跳过：依赖已打进可执行文件"
elif [ -n "${PYTHON}" ]; then
    for spec in "flask:Flask（网页服务）" "PIL:Pillow（图像处理）" "qrcode:qrcode（扫码访问）"; do
        module="${spec%%:*}"
        label="${spec#*:}"
        if "${PYTHON}" -c "import ${module}" >/dev/null 2>&1; then
            c_ok "${label}"
        else
            c_bad "${label} 未安装（pip install -r requirements-linux.txt）"
        fi
    done
    if "${PYTHON}" -c "import pymupdf" >/dev/null 2>&1 || "${PYTHON}" -c "import fitz" >/dev/null 2>&1; then
        c_ok "PyMuPDF（PDF 渲染与合成）"
    else
        c_bad "PyMuPDF 未安装（打印管道不可用）"
    fi
    if "${PYTHON}" -c "import pystray" >/dev/null 2>&1; then
        if "${PYTHON}" -c "import gi" >/dev/null 2>&1 || "${PYTHON}" -c "import Xlib" >/dev/null 2>&1; then
            c_ok "pystray（托盘常驻，后端可用）"
        else
            c_warn "pystray 已安装但缺少图形后端（gi / Xlib），托盘不可用，后台服务不受影响"
        fi
    else
        c_warn "pystray 未安装：无法显示托盘图标（后台服务不受影响）"
    fi
fi

# ------------------------------------------------------------------ 4. 打印后端（CUPS）
title "打印后端（CUPS）"
if command -v lpstat >/dev/null 2>&1 && command -v lp >/dev/null 2>&1; then
    c_ok "lp / lpstat（$(command -v lp)）"
    if lpstat -r >/dev/null 2>&1; then
        c_ok "CUPS 调度服务运行中（$(lpstat -r 2>/dev/null | head -n1)）"
    else
        c_bad "CUPS 调度服务未运行：sudo systemctl enable --now cups"
    fi

    printers="$(lpstat -p 2>/dev/null | sed -n 's/^printer \([^ ]*\).*/\1/p')"
    if [ -n "${printers}" ]; then
        c_ok "已添加打印机 $(printf '%s\n' "${printers}" | wc -l) 台：$(printf '%s ' ${printers})"
    else
        c_bad "系统中没有打印机，请先在系统设置中添加打印机"
    fi

    default_printer="$(lpstat -d 2>/dev/null | sed -n 's/.*: *//p')"
    if [ -n "${default_printer}" ]; then
        c_ok "默认打印机：${default_printer}"
    else
        c_warn "未设置默认打印机（可在网页端指定打印机，或在系统设置中设为默认）"
    fi
else
    c_bad "未检测到 CUPS 客户端（lp/lpstat）：请安装 cups cups-client 后重试"
fi

# ------------------------------------------------------------------ 5. 纸张与双面能力
title "纸张与双面能力"
probe_printer="${SUBMIT_PRINTER:-${default_printer:-}}"
if [ -n "${probe_printer}" ] && command -v lpoptions >/dev/null 2>&1; then
    options="$(lpoptions -p "${probe_printer}" -l 2>/dev/null)"
    if [ -n "${options}" ]; then
        c_ok "已读取 ${probe_printer} 的驱动选项"
        if printf '%s\n' "${options}" | grep -qiE '^PageSize|^media'; then
            papers="$(printf '%s\n' "${options}" | grep -iE '^PageSize|^media' | head -n1 | sed 's/.*: *//' | tr -d '*')"
            c_ok "支持纸张：$(printf '%s' "${papers}" | cut -c1-100)"
        else
            c_warn "驱动未上报纸张列表，程序将按 A4 提交"
        fi
        if printf '%s\n' "${options}" | grep -qi 'two-sided-long-edge'; then
            c_ok "支持双面打印（双面长边翻转）"
        else
            c_warn "驱动未上报双面能力，网页端仍可提交双面参数（由驱动决定是否生效）"
        fi
    else
        c_warn "无法读取 ${probe_printer} 的驱动选项（部分通用驱动不支持，不影响打印）"
    fi
else
    c_warn "跳过：未确定用于探测的打印机"
fi

# ------------------------------------------------------------------ 6. 办公文档转换
title "办公文档转换（Office/WPS）"
if command -v soffice >/dev/null 2>&1 || [ -x /usr/lib/libreoffice/program/soffice ]; then
    version="$( (soffice --version 2>/dev/null || /usr/lib/libreoffice/program/soffice --version 2>/dev/null) | head -n1)"
    c_ok "LibreOffice 可用：${version:-版本未知}"
    c_ok "可支持 .doc .docx .xls .xlsx .ppt .pptx .wps .et .dps（共 9 种）"
else
    c_warn "未检测到 LibreOffice：Office/WPS 共 9 种格式不可用，其余 11 种格式不受影响"
    c_warn "安装：sudo apt install libreoffice（Debian/Ubuntu/银河麒麟）或 sudo yum install libreoffice"
fi

# ------------------------------------------------------------------ 7. 中文字体
title "中文字体"
if command -v fc-list >/dev/null 2>&1; then
    if fc-list 2>/dev/null | grep -qiE 'noto.*cjk|wqy|source han|zenhei|simsun|msyh'; then
        c_ok "已安装中文字体：$(fc-list 2>/dev/null | grep -iE 'noto.*cjk|wqy|source han|zenhei' | head -n1 | cut -d: -f2- | cut -c1-70)"
    else
        c_bad "缺少中文字体，打印中文内容会被拒绝：sudo apt install fonts-noto-cjk"
    fi
else
    c_warn "未找到 fc-list（fontconfig），无法确认字体情况：请安装 fonts-noto-cjk"
fi

# ------------------------------------------------------------------ 8. 图形环境
title "图形环境（托盘 / 对话框）"
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    c_ok "检测到图形会话（DISPLAY=${DISPLAY:-未设置} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-未设置}）"
    if [ -n "${PYTHON}" ] && "${PYTHON}" -c "import tkinter" >/dev/null 2>&1; then
        c_ok "tkinter 可用（托盘菜单中的配置对话框可用）"
    else
        c_warn "tkinter 不可用：托盘菜单中的弹窗将退回为终端输出（sudo apt install python3-tk）"
    fi
else
    c_warn "当前无图形会话（headless）：托盘不可用，程序会自动以后台服务方式运行"
fi

# ------------------------------------------------------------------ 9. 开机启动
title "开机启动"
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    c_ok "系统使用 systemd"
    if [ -f /etc/systemd/system/jyprinter.service ]; then
        c_ok "已安装开机启动服务：jyprinter.service"
        if systemctl is-enabled --quiet jyprinter.service 2>/dev/null; then
            c_ok "开机自启：已启用"
        else
            c_warn "开机自启：未启用（sudo systemctl enable jyprinter）"
        fi
        if systemctl is-active --quiet jyprinter.service 2>/dev/null; then
            c_ok "服务状态：运行中"
        else
            c_warn "服务状态：未运行，查看原因：journalctl -u jyprinter -n 50 --no-pager"
        fi
    else
        c_warn "未安装 systemd 服务（可用 sudo bash tools/install_linux.sh 安装并设为开机自启）"
    fi
else
    c_warn "系统未使用 systemd，开机启动请使用桌面自启方式"
fi

autostart_files=""
for home in /root /home/*; do
    [ -f "${home}/.config/autostart/jyprinter.desktop" ] && autostart_files="${autostart_files} ${home}/.config/autostart/jyprinter.desktop"
done
if [ -n "${autostart_files}" ]; then
    c_ok "已安装桌面自启：${autostart_files}"
    if [ -f /etc/systemd/system/jyprinter.service ]; then
        c_warn "同时存在系统服务与桌面自启，二者会争抢端口，建议只保留一种"
    fi
else
    c_warn "未发现桌面自启条目（无需登录即运行可选择 systemd 方式）"
fi

# ------------------------------------------------------------------ 10. 程序目录与端口
title "程序目录与端口"
if [ -w "${PROGRAM_DIR}/data" ] || [ -w "${PROGRAM_DIR}" ]; then
    c_ok "程序目录可写：${PROGRAM_DIR}"
else
    c_bad "程序目录不可写：${PROGRAM_DIR}（数据与日志无法落盘）"
fi
if [ -f "${PROGRAM_DIR}/config.json" ]; then
    c_ok "配置文件：${PROGRAM_DIR}/config.json"
else
    c_warn "尚未生成 config.json（首次启动时自动创建）"
fi
port="5000"
if [ -f "${PROGRAM_DIR}/config.json" ] && command -v python3 >/dev/null 2>&1; then
    detected="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8')).get('port',5000))" "${PROGRAM_DIR}/config.json" 2>/dev/null || true)"
    [ -n "${detected}" ] && port="${detected}"
fi
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${port} "; then
    c_ok "服务端口 ${port} 已在监听（服务正在运行）"
else
    c_warn "服务端口 ${port} 未在监听（服务可能未启动）"
fi

# ------------------------------------------------------------------ 11. 解析自检
title "打印输出解析自检"
if [ "${PY_OK}" -eq 1 ] && [ -f "${SCRIPT_DIR}/selftest_linux.py" ]; then
    test_log="$(mktemp /tmp/jyprinter-selftest-XXXXXX.log)"
    live_arg=""
    [ "${DO_LIVE}" -eq 1 ] && live_arg="--live"
    if "${PYTHON}" "${SCRIPT_DIR}/selftest_linux.py" ${live_arg} >"${test_log}" 2>&1; then
        cases="$(sed -n 's/.*通过 \([0-9][0-9]*\) 项.*/\1/p' "${test_log}" | tail -n1)"
        c_ok "lpstat / lpoptions / 网络信息解析全部通过（${cases:-若干} 项用例）"
        if [ "${DO_LIVE}" -eq 1 ]; then
            grep -E '^  (默认打印机|纸张选项|双面支持|打印机状态|队列作业数|网卡|LibreOffice)：' "${test_log}" | sed 's/^/        /'
        fi
        rm -f "${test_log}"
    else
        c_bad "解析自检存在失败项（输出如下）"
        sed 's/^/        /' "${test_log}"
        rm -f "${test_log}"
    fi
elif [ "${BINARY_MODE}" -eq 1 ]; then
    c_warn "跳过：二进制模式下需系统 Python 与项目依赖才能执行解析自检（源码模式可完整执行）"
else
    c_warn "跳过：未找到 tools/selftest_linux.py 或 Python 依赖不完整"
fi

# ------------------------------------------------------------------ 12. 打印管道试跑
title "打印管道试跑"
if [ "${DO_SUBMIT}" -eq 0 ]; then
    printf '  \033[1;33m[跳过]\033[0m 未启用（加 --test-submit 可执行「不出纸」的端到端提交测试）\n'
elif [ "${PY_OK}" -eq 0 ]; then
    c_warn "跳过：需要可导入项目依赖的 Python 才能生成测试页（二进制模式请改用源码环境自检）"
elif ! command -v lp >/dev/null 2>&1; then
    c_bad "缺少 lp 命令，无法提交测试作业"
else
    target_printer="${SUBMIT_PRINTER:-${default_printer:-}}"
    if [ -z "${target_printer}" ]; then
        c_bad "未确定目标打印机（可用 --test-submit <打印机名> 指定）"
    else
        tmp_dir="$(mktemp -d /tmp/jyprinter-selfcheck-XXXXXX)"
        tmp_pdf="${tmp_dir}/selfcheck.pdf"

        cat > "${tmp_dir}/render_test.py" <<'PYEOF'
"""自检用：按真实渲染链路生成一页 PDF（验证字体、PIL、PyMuPDF 与 PDF 合成）。"""
import os
import sys
from pathlib import Path

root, output = sys.argv[1], Path(sys.argv[2])
sys.path.insert(0, root)

from core import engines_linux, printers_linux, settings  # noqa: E402

source = Path(settings.DATA_DIR) / "selfcheck_page.txt"
source.parent.mkdir(parents=True, exist_ok=True)
source.write_text(
    "jyprinter 打印管道自检\n\n"
    "本页由 selfcheck_linux.sh 自动生成，用于验证渲染与合成链路。\n"
    "中文字体渲染检查：永 恒 打 印 服 务\n",
    encoding="utf-8",
)

factory, _converted = engines_linux.build_page_factory(
    str(source), "txt", engines_linux.RENDER_DPI
)
pdf_path, pages = engines_linux._compose_pdf(factory, printers_linux.DEFAULT_PAPER)
output.write_bytes(Path(pdf_path).read_bytes())
try:
    os.remove(pdf_path)
except OSError:
    pass
print("生成页数：%d" % pages)
PYEOF

        if "${PYTHON}" "${tmp_dir}/render_test.py" "${PROGRAM_DIR}" "${tmp_pdf}" >"${tmp_dir}/render.log" 2>&1; then
            size="$(stat -c %s "${tmp_pdf}" 2>/dev/null || echo 0)"
            if [ "${size}" -gt 0 ]; then
                c_ok "渲染链路正常（测试页已合成 PDF：${size} 字节）"
            else
                c_bad "渲染链路异常：生成的 PDF 为空，详见 ${tmp_dir}/render.log"
            fi
        else
            c_bad "渲染链路异常（测试页生成失败），详见 ${tmp_dir}/render.log"
            sed 's/^/        /' "${tmp_dir}/render.log" 2>/dev/null | tail -n 12
        fi

        if [ -s "${tmp_pdf}" ]; then
            # -H hold：作业保持挂起，不会送达打印机、不出纸；提交后立即取消
            submit_out="$(lp -d "${target_printer}" -H hold -t "jyprinter 自检（不会出纸）" "${tmp_pdf}" 2>&1)"
            submit_code=$?
            if [ "${submit_code}" -ne 0 ]; then
                c_bad "CUPS 提交失败：${submit_out}"
            else
                job_id="$(printf '%s' "${submit_out}" | sed -n 's/.*request id is \([^ ]*\).*/\1/p')"
                if [ -z "${job_id}" ]; then
                    c_bad "CUPS 提交返回异常：${submit_out}"
                else
                    c_ok "CUPS 提交成功（作业 ${job_id}，已保持挂起，未送往打印机）"
                    if lpstat -o "${target_printer}" 2>/dev/null | grep -q "${job_id}"; then
                        c_ok "作业已进入队列并可被程序跟踪"
                    else
                        c_warn "未在队列中看到该作业（部分驱动不列出挂起作业，不影响正常打印）"
                    fi
                    if cancel "${job_id}" >/dev/null 2>&1; then
                        c_ok "测试作业已取消（未消耗纸张）"
                    else
                        c_warn "测试作业取消失败，请手动执行：cancel ${job_id}"
                    fi
                fi
            fi
        fi
        rm -rf "${tmp_dir}"
    fi
fi

# ------------------------------------------------------------------ 汇总
printf '\n\033[1;34m== 自检结果 ==\033[0m\n'
printf '  通过 %d 项　警告 %d 项　失败 %d 项\n' "${PASS}" "${WARN}" "${FAIL}"
if [ "${FAIL}" -gt 0 ]; then
    printf '  \033[1;31m存在失败项，请按上方提示处理后重新自检。\033[0m\n'
    exit 1
fi
if [ "${WARN}" -gt 0 ]; then
    printf '  \033[1;33m核心功能可用，警告项按需处理。\033[0m\n'
    exit 0
fi
printf '  \033[1;32m全部检查通过。\033[0m\n'
exit 0
