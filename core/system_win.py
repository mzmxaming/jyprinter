"""Windows 系统集成模块：开机自启、控制台显隐、剪贴板、网络配置与只读状态查询。

供电托盘菜单使用。所有涉及系统改动的操作都会返回 (是否成功, 说明文本)，并区分「需要管理员权限」。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import win32clipboard
import win32con

# 开机自启动注册表位置（当前用户，无需管理员）
_AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_NAME = "jyprinter"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_admin() -> bool:
    """判断当前进程是否具有管理员权限。

    参数：无
    返回：
        bool，具备管理员权限返回 True
    """
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def clipboard_copy(text: str) -> bool:
    """把文本写入系统剪贴板。

    参数：
        text - 待复制的文本
    返回：
        bool，成功返回 True
    """
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


def autostart_enabled() -> bool:
    """查询是否已设置开机自启。

    参数：无
    返回：
        bool，已启用返回 True
    """
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY)
        try:
            winreg.QueryValueEx(key, _AUTOSTART_NAME)
            return True
        except OSError:
            return False
        finally:
            key.Close()
    except OSError:
        return False


def set_autostart(enable: bool) -> Tuple[bool, str]:
    """启用或关闭开机自启（写入当前用户注册表 Run 项）。

    参数：
        enable - True 启用，False 关闭
    返回：
        (是否成功, 说明文本)
    """
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
        )
    except OSError as exc:
        return False, f"无法写入注册表：{exc}"

    try:
        if enable:
            winreg.SetValueEx(key, _AUTOSTART_NAME, 0, winreg.REG_SZ, _launch_command())
            return True, "已开启开机自启"
        try:
            winreg.DeleteValue(key, _AUTOSTART_NAME)
        except OSError:
            pass
        return True, "已关闭开机自启"
    except OSError as exc:
        return False, f"注册表操作失败：{exc}"
    finally:
        key.Close()


def _launch_command() -> str:
    """生成开机自启命令（区分 exe 运行与源码运行）。

    参数：无
    返回：
        str，带引号的启动命令行
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    script = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{sys.executable}" "{script}"'


def console_visible() -> bool:
    """判断当前控制台窗口是否可见。

    参数：无
    返回：
        bool，可见返回 True（无控制台时返回 False）
    """
    handle = _console_handle()
    return bool(handle) and bool(ctypes.windll.user32.IsWindowVisible(handle))


def toggle_console() -> Tuple[bool, str]:
    """显示/隐藏当前控制台窗口。

    参数：无
    返回：
        (是否切换成功, 说明文本)
    """
    handle = _console_handle()
    if not handle:
        return False, "当前没有控制台窗口（可能以 pythonw 或打包模式运行）"
    if ctypes.windll.user32.IsWindowVisible(handle):
        ctypes.windll.user32.ShowWindow(handle, 0)   # SW_HIDE
        return True, "控制台已隐藏"
    ctypes.windll.user32.ShowWindow(handle, 5)       # SW_SHOW
    return True, "控制台已显示"


def hide_console() -> Tuple[bool, str]:
    """隐藏当前控制台窗口（用于启动后自动转入后台）。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    handle = _console_handle()
    if not handle:
        return False, "当前没有控制台窗口"
    if not ctypes.windll.user32.IsWindowVisible(handle):
        return True, "控制台已是隐藏状态"
    ctypes.windll.user32.ShowWindow(handle, 0)       # SW_HIDE
    return True, "控制台已隐藏"


def _console_handle() -> int:
    """获取当前进程的控制台窗口句柄。

    参数：无
    返回：
        int，窗口句柄；无控制台时为 0
    """
    try:
        return int(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return 0


def open_path(path: str) -> Tuple[bool, str]:
    """用系统默认程序打开文件或目录。

    参数：
        path - 文件或目录路径
    返回：
        (是否成功, 说明文本)
    """
    target = Path(path)
    if not target.exists():
        return False, f"路径不存在：{target}"
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]
        return True, f"已打开 {target}"
    except OSError as exc:
        return False, f"打开失败：{exc}"


def open_network_panel() -> Tuple[bool, str]:
    """打开 Windows「网络连接」面板。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    try:
        os.startfile("ncpa.cpl")  # type: ignore[attr-defined]
        return True, "已打开网络连接面板"
    except OSError as exc:
        return False, f"打开失败：{exc}"


def list_adapters() -> List[Dict[str, str]]:
    """列出本机网络适配器及其 IPv4 配置（只读，解析 netsh 输出）。

    参数：无
    返回：
        list[dict]，元素含 name / dhcp / ip / gateway / dns
    """
    output = _run_netsh(["interface", "ipv4", "show", "config"])
    if not output:
        return []

    adapters: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith('Configuration for interface "') or line.startswith("接口 "):
            if current:
                adapters.append(current)
            name = line.split('"')[1] if '"' in line else line.split(" ", 3)[-1]
            current = {"name": name, "dhcp": "", "ip": "", "gateway": "", "dns": ""}
            continue
        if current is None:
            continue

        lowered = line.lower()
        if "dhcp enabled" in lowered or "dhcp 已启用" in line:
            current["dhcp"] = "是" if (": yes" in lowered or "是" in line) else "否"
        elif lowered.startswith("ip address") or line.startswith("IP 地址"):
            current["ip"] = line.split(":", 1)[-1].strip()
        elif lowered.startswith("default gateway") or line.startswith("默认网关"):
            current["gateway"] = line.split(":", 1)[-1].strip()
        elif lowered.startswith("dns servers") or line.startswith("DNS 服务器"):
            current["dns"] = line.split(":", 1)[-1].strip()

    if current:
        adapters.append(current)
    return [item for item in adapters if item["name"]]


def set_static_ip(
    adapter: str, ip: str, mask: str, gateway: str = ""
) -> Tuple[bool, str]:
    """把指定适配器设置为静态 IPv4 地址（需要管理员权限）。

    参数：
        adapter - 适配器名称
        ip      - 静态 IP
        mask    - 子网掩码
        gateway - 默认网关（可留空）
    返回：
        (是否成功, 说明文本)
    """
    if not is_admin():
        return False, "需要管理员权限才能修改网络配置"
    args = ["interface", "ipv4", "set", "address", f'name={adapter}', "static", ip, mask]
    if gateway:
        args.append(gateway)
    ok, message = _run_netsh_checked(args)
    return (ok, f"已设置静态地址 {ip}" if ok else message)


def enable_dhcp(adapter: str) -> Tuple[bool, str]:
    """把指定适配器恢复为自动获取 IP（需要管理员权限）。

    参数：
        adapter - 适配器名称
    返回：
        (是否成功, 说明文本)
    """
    if not is_admin():
        return False, "需要管理员权限才能修改网络配置"
    ok, message = _run_netsh_checked(
        ["interface", "ipv4", "set", "address", f'name={adapter}', "dhcp"]
    )
    if ok:
        _run_netsh_checked(["interface", "ipv4", "set", "dnsservers", f'name={adapter}', "dhcp"])
        return True, "已恢复为自动获取 IP（DHCP）"
    return False, message


def network_summary() -> str:
    """生成当前网络配置的简要文本（用于托盘菜单展示）。

    参数：无
    返回：
        str，形如「以太网：192.168.1.10 / 网关 192.168.1.1」
    """
    items = []
    for adapter in list_adapters():
        ip = adapter.get("ip") or "无地址"
        items.append(f"{adapter['name']}：{ip}")
    return "；".join(items) if items else "未读取到网络配置"


def _run_netsh(args: List[str]) -> str:
    """执行 netsh 命令并返回输出文本。

    参数：
        args - netsh 参数列表
    返回：
        str，标准输出（失败返回空字符串）
    """
    try:
        result = subprocess.run(
            ["netsh"] + args,
            capture_output=True,
            timeout=20,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _decode(result.stdout)


def _run_netsh_checked(args: List[str]) -> Tuple[bool, str]:
    """执行 netsh 命令并判断是否成功。

    参数：
        args - netsh 参数列表
    返回：
        (是否成功, 说明文本)
    """
    try:
        result = subprocess.run(
            ["netsh"] + args,
            capture_output=True,
            timeout=30,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"命令执行失败：{exc}"
    if result.returncode == 0:
        return True, "执行成功"
    detail = " ".join(_decode(result.stderr or result.stdout).split())[:120]
    return False, f"netsh 返回错误：{detail or result.returncode}"


def _decode(raw: bytes) -> str:
    """按中文 Windows 常见编码解码命令输出。

    参数：
        raw - 原始字节
    返回：
        str，解码结果
    """
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def ask_form(title: str, fields: List[Tuple[str, str]], defaults: Dict[str, str] = None) -> Optional[Dict[str, str]]:
    """弹出简单的输入对话框（依赖 tkinter，不可用时返回 None）。

    参数：
        title    - 对话框标题
        fields   - [(字段键, 显示名)] 列表
        defaults - 各字段的默认值
    返回：
        dict 或 None，用户填写的结果；取消或不可用时返回 None
    """
    try:
        import tkinter as tk
    except ImportError:
        return None

    defaults = defaults or {}
    result: Dict[str, str] = {}
    window = tk.Tk()
    window.title(title)
    window.resizable(False, False)
    window.attributes("-topmost", True)

    entries: Dict[str, Any] = {}
    for index, (key, label) in enumerate(fields):
        tk.Label(window, text=label, anchor="w").grid(
            row=index, column=0, sticky="w", padx=10, pady=6
        )
        entry = tk.Entry(window, width=28)
        entry.insert(0, str(defaults.get(key, "")))
        entry.grid(row=index, column=1, padx=10, pady=6)
        entries[key] = entry

    def on_ok() -> None:
        for key, entry in entries.items():
            result[key] = entry.get().strip()
        window.destroy()

    def on_cancel() -> None:
        result.clear()
        window.destroy()

    button_row = tk.Frame(window)
    button_row.grid(row=len(fields), column=0, columnspan=2, pady=10)
    tk.Button(button_row, text="确定", width=10, command=on_ok).pack(side="left", padx=6)
    tk.Button(button_row, text="取消", width=10, command=on_cancel).pack(side="left", padx=6)

    window.protocol("WM_DELETE_WINDOW", on_cancel)

    try:
        window.update_idletasks()
        window.geometry(
            f"+{window.winfo_screenwidth() // 2 - window.winfo_width() // 2}"
            f"+{window.winfo_screenheight() // 3}"
        )
        window.lift()
        window.focus_force()
        window.mainloop()
    finally:
        # 兜底销毁，避免异常路径下留下无法关闭的窗口
        try:
            window.destroy()
        except Exception:
            pass
    return result or None


def ask_choice(title: str, prompt: str, options: List[Tuple[str, str]]) -> Optional[str]:
    """弹出单项选择对话框（依赖 tkinter，不可用时返回 None）。

    参数：
        title   - 对话框标题
        prompt  - 提示文本
        options - [(值, 显示名)] 列表
    返回：
        str 或 None，用户选择的值
    """
    try:
        import tkinter as tk
    except ImportError:
        return None

    selected: Dict[str, Optional[str]] = {"value": None}
    window = tk.Tk()
    window.title(title)
    window.resizable(False, False)
    window.attributes("-topmost", True)

    def close_window() -> None:
        selected["value"] = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", close_window)
    tk.Label(window, text=prompt, justify="left", anchor="w").pack(padx=14, pady=(12, 6))

    def choose(value: str) -> None:
        selected["value"] = value
        window.destroy()

    for value, label in options:
        tk.Button(window, text=label, width=34, command=lambda v=value: choose(v)).pack(
            padx=14, pady=3
        )
    tk.Button(window, text="取消", width=34, command=close_window).pack(padx=14, pady=(8, 12))

    try:
        window.lift()
        window.focus_force()
        window.mainloop()
    finally:
        try:
            window.destroy()
        except Exception:
            pass
    return selected["value"]


def info(title: str, message: str) -> None:
    """弹出信息提示框（基于 tkinter，失败时回退到控制台输出）。

    注意：实测 pywin32 的 win32api.MessageBox 在工作线程中调用时不会生成可枚举窗口
    且会永久阻塞，因此这里统一使用 tkinter 实现，并显式绑定关闭处理与兜底销毁，
    保证窗口一定能被关闭。

    参数：
        title   - 标题
        message - 内容
    返回：
        None
    """
    try:
        import tkinter as tk
        from tkinter import messagebox

        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        try:
            messagebox.showinfo(title, message, parent=window)
        finally:
            window.destroy()
    except Exception:
        print(f"[{title}] {message}", flush=True)
