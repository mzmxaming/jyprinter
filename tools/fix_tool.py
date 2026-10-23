"""运行环境检测与修复工具（独立脚本，只依赖 Python 标准库）。

用途：当程序无法启动或打印异常时，先运行本工具定位环境问题。
用法：
    python tools/fix_tool.py           检测并给出修复建议
    python tools/fix_tool.py --fix     在检测后尝试自动修复可安全修复的项（需管理员权限）

检测项：
    1. 管理员权限         6. Office / WPS / LibreOffice 可用性
    2. 安装路径是否含中文  7. VC++ 运行库
    3. 打印后台服务状态    8. 服务端口占用
    4. 已安装打印机        9. Python 依赖（源码运行时）
    5. 默认打印机
"""

from __future__ import annotations

import ctypes
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

# 源码运行时需要的依赖（打包为 exe 后无需检查）
REQUIRED_MODULES = ["flask", "win32print", "win32ui", "win32gui", "PIL", "pystray", "pymupdf", "qrcode"]
VC_RUNTIME_DLLS = ["vcruntime140.dll", "msvcp140.dll"]
OFFICE_PROGIDS = ["Word.Application", "Excel.Application", "PowerPoint.Application", "KWPS.Application", "KET.Application", "KWPP.Application"]
DEFAULT_PORT = 5000

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def check_admin() -> Tuple[bool, str]:
    """检测管理员权限。"""
    try:
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        admin = False
    return admin, "已是管理员权限" if admin else "当前为普通用户（托盘网络配置、修复服务等操作将不可用）"


def check_path() -> Tuple[bool, str]:
    """检测安装路径是否包含中文或空格。"""
    target = Path(__file__).resolve().parent.parent
    text = str(target)
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return False, f"安装路径含中文：{text}（建议改为纯英文路径，如 E:\\jyprinter）"
    if " " in text:
        return True, f"安装路径含空格：{text}（一般可用，如异常请更换为无空格路径）"
    return True, f"安装路径正常：{text}"


def check_spooler() -> Tuple[bool, str]:
    """检测打印后台服务（Print Spooler）。"""
    try:
        result = subprocess.run(
            ["sc", "query", "Spooler"],
            capture_output=True,
            timeout=15,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"无法查询打印服务：{exc}"
    output = _decode(result.stdout)
    if "RUNNING" in output:
        return True, "打印后台服务运行中"
    return False, "打印后台服务未运行（可执行 net start Spooler 启动）"


def fix_spooler() -> Tuple[bool, str]:
    """尝试启动打印后台服务。"""
    try:
        result = subprocess.run(
            ["net", "start", "Spooler"],
            capture_output=True,
            timeout=30,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"启动失败：{exc}"
    if result.returncode == 0:
        return True, "已启动打印后台服务"
    return False, f"启动失败：{_decode(result.stderr or result.stdout).strip()[:120]}"


def check_printers() -> Tuple[bool, str]:
    """列出已安装打印机并检测默认打印机。"""
    try:
        import win32print
    except ImportError:
        return False, "未安装 pywin32，无法读取打印机列表"

    try:
        printers = win32print.EnumPrinters(2 | 4, None, 2)
    except Exception as exc:
        return False, f"枚举打印机失败：{exc}"
    if not printers:
        return False, "系统未安装任何打印机"

    names = [item.get("pPrinterName", "") for item in printers]
    try:
        default = win32print.GetDefaultPrinter()
    except Exception:
        default = ""
    if not default:
        return False, f"未设置默认打印机（已安装：{'、'.join(names)}）"
    return True, f"常见打印机：{'、'.join(names)}；默认：{default}"


def check_office() -> Tuple[bool, str]:
    """检测 Office / WPS / LibreOffice 可用性。"""
    import winreg

    found: List[str] = []
    for progid in OFFICE_PROGIDS:
        try:
            key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid)
            try:
                name, _ = winreg.QueryValueEx(key, "")
            except OSError:
                name = progid
            key.Close()
            found.append(f"{progid}={name}")
        except OSError:
            continue

    soffice = shutil.which("soffice") or shutil.which("soffice.exe")
    if soffice:
        found.append(f"LibreOffice={soffice}")

    if not found:
        return False, "未检测到 Office / WPS / LibreOffice：doc、xls、ppt、wps 等 9 种文档格式将不可用"
    return True, "；".join(found)


def check_vc_runtime() -> Tuple[bool, str]:
    """检测 VC++ 运行库是否存在。"""
    system32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
    missing = [name for name in VC_RUNTIME_DLLS if not (system32 / name).exists()]
    if missing:
        return False, f"缺少 VC++ 运行库文件：{'、'.join(missing)}（请安装 Microsoft Visual C++ Redistributable）"
    return True, "VC++ 运行库存在"


def check_port(port: int = DEFAULT_PORT) -> Tuple[bool, str]:
    """检测服务端口是否被占用。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(1.0)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            return True, f"端口 {port} 已被监听（若为本程序运行中则属正常）"
        return True, f"端口 {port} 空闲"
    finally:
        sock.close()


def check_dependencies() -> Tuple[bool, str]:
    """检测源码运行所需的 Python 依赖。"""
    if getattr(sys, "frozen", False):
        return True, "以打包程序运行，跳过依赖检查"
    missing = []
    for name in REQUIRED_MODULES:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if missing:
        return False, f"缺少依赖：{'、'.join(missing)}（执行 pip install -r requirements.txt）"
    return True, "依赖齐全"


def check_firewall(port: int = DEFAULT_PORT) -> Tuple[bool, str]:
    """检测是否已为服务端口放行防火墙规则。"""
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=jyprinter"],
            capture_output=True,
            timeout=20,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"无法查询防火墙规则：{exc}"
    if result.returncode == 0 and "jyprinter" in _decode(result.stdout):
        return True, f"已存在 jyprinter 防火墙规则（端口 {port}）"
    return False, f"未找到 jyprinter 防火墙规则（局域网无法访问时可手动放行 TCP {port}）"


def fix_firewall(port: int = DEFAULT_PORT) -> Tuple[bool, str]:
    """为服务端口添加防火墙放行规则。"""
    try:
        result = subprocess.run(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                "name=jyprinter",
                "dir=in",
                "action=allow",
                "protocol=TCP",
                f"localport={port}",
            ],
            capture_output=True,
            timeout=30,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"添加失败：{exc}"
    if result.returncode == 0:
        return True, f"已添加防火墙放行规则（TCP {port}）"
    return False, f"添加失败：{_decode(result.stderr or result.stdout).strip()[:120]}"


def _decode(raw: bytes) -> str:
    """按中文 Windows 常见编码解码命令输出。"""
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    """执行环境检测（可选自动修复）。"""
    auto_fix = "--fix" in sys.argv
    admin, admin_text = check_admin()

    checks = [
        ("管理员权限", check_admin),
        ("安装路径", check_path),
        ("打印后台服务", check_spooler),
        ("打印机", check_printers),
        ("Office/WPS", check_office),
        ("VC++ 运行库", check_vc_runtime),
        ("服务端口", check_port),
        ("防火墙规则", check_firewall),
        ("Python 依赖", check_dependencies),
    ]

    print("=" * 68)
    print("  jyprinter 运行环境检测" + ("（含自动修复）" if auto_fix else ""))
    print("=" * 68)

    failed: List[str] = []
    for name, func in checks:
        try:
            ok, message = func()
        except Exception as exc:  # 检测项自身异常不影响其他项
            ok, message = False, f"检测异常：{exc}"
        print(f"[{'正常' if ok else '异常'}] {name}：{message}")
        if not ok:
            failed.append(name)

    if auto_fix:
        print("-" * 68)
        print("尝试自动修复：")
        if "打印后台服务" in failed:
            print(f"  启动打印服务 -> {fix_spooler()[1]}")
        if "防火墙规则" in failed:
            if admin:
                print(f"  添加防火墙规则 -> {fix_firewall()[1]}")
            else:
                print("  添加防火墙规则 -> 跳过：需要管理员权限")
        if "Python 依赖" in failed:
            print("  安装依赖 -> 请手动执行：pip install -r requirements.txt")
        if "Office/WPS" in failed:
            print("  安装办公软件 -> 需手动安装 Microsoft Office 或 WPS Office")
        if "打印机" in failed:
            print("  添加打印机 -> 请在「设置 → 蓝牙和其他设备 → 打印机和扫描仪」中添加")

    print("-" * 68)
    if failed:
        print(f"检测完成：{len(failed)} 项异常 -> {'、'.join(failed)}")
    else:
        print("检测完成：全部正常")
    if not admin:
        print("提示：以管理员身份重新运行可获得更完整的检测与修复能力。")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
