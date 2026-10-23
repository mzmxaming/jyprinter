"""macOS 系统集成：登录自启（LaunchAgent）、剪贴板、网络配置与只读查询。

与 Linux 版的差异：
    · 没有 systemd，登录自启写入 ~/Library/LaunchAgents/com.cgm.jyprinter.plist，
      由 launchctl 注册；打包成 .app 后通过 `open -a` 启动应用包；
    · 网络配置使用系统自带的 networksetup（按「网络服务」名操作），
      普通用户修改时通过 osascript 请求管理员授权；
    · 图形对话框优先使用 tkinter，Flet 打包环境缺少 tkinter 时降级为终端输出。

对外接口与 core/system_win.py、core/system_linux.py 保持一致。
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import xml.sax.saxutils as saxutils
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import settings
from core.utils import remove_file

# 系统命令统一使用绝对路径：从 Finder 启动的 .app 进程 PATH 很短，
# 仅靠 shutil.which 可能找不到 /usr/sbin 下的命令
NETWORKSETUP = "/usr/sbin/networksetup"
PBCOPY = "/usr/bin/pbcopy"
OSASCRIPT = "/usr/bin/osascript"
OPEN = "/usr/bin/open"
LAUNCHCTL = "/bin/launchctl"

# 登录自启配置
LAUNCH_AGENT_LABEL = "com.cgm.jyprinter"
LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"

# 命令默认超时（秒）
_CMD_TIMEOUT = 30


def _run(args: List[str], timeout: int = _CMD_TIMEOUT) -> Tuple[int, str, str]:
    """执行系统命令并返回 (返回码, 标准输出, 标准错误)。

    参数：
        args    - 命令与参数，首项可为绝对路径或命令名
        timeout - 超时秒数
    返回：
        (int, str, str)
    """
    if os.path.isabs(args[0]):
        executable = args[0] if Path(args[0]).exists() else None
    else:
        executable = shutil.which(args[0])
    if not executable:
        return 127, "", f"未找到命令 {args[0]}"
    try:
        result = subprocess.run(
            [executable] + args[1:], capture_output=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"命令超时：{' '.join(args)}"
    except OSError as exc:
        return 1, "", str(exc)
    return result.returncode, _decode(result.stdout), _decode(result.stderr)


def _decode(raw: bytes) -> str:
    """解码命令输出。

    参数：
        raw - 原始字节
    返回：
        str，解码结果
    """
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def is_admin() -> bool:
    """判断当前进程是否具有管理员（root）权限。

    参数：无
    返回：
        bool，root 返回 True
    """
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def clipboard_copy(text: str) -> bool:
    """把文本写入系统剪贴板（pbcopy）。

    参数：
        text - 待复制的文本
    返回：
        bool，成功返回 True
    """
    if not Path(PBCOPY).exists():
        return False
    try:
        process = subprocess.run(
            [PBCOPY], input=text.encode("utf-8"), timeout=10, capture_output=True
        )
        return process.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------------- 开机启动


def _bundle_path() -> Optional[Path]:
    """查找当前进程所属的 .app 应用包。

    打包为 Flet 桌面应用后，可执行文件位于
    「<应用名>.app/Contents/MacOS/」下，据此向上定位 .app 目录。

    参数：无
    返回：
        Path 或 None，应用包路径
    """
    try:
        executable = Path(sys.executable).resolve()
    except OSError:
        return None
    for parent in executable.parents:
        if parent.suffix == ".app":
            return parent
    return None


def launch_command() -> List[str]:
    """生成登录自启时执行的命令（打包运行与源码运行不同）。

    参数：无
    返回：
        list[str]，命令与参数
    """
    bundle = _bundle_path()
    if bundle is not None:
        return [OPEN, "-a", str(bundle)]
    root = Path(__file__).resolve().parent.parent
    entry = root / "flet_app.py"
    if entry.exists():
        return [sys.executable, str(entry)]
    return [sys.executable, str(root / "main.py"), "--no-tray"]


def launch_agent_text() -> str:
    """生成 LaunchAgent 的 plist 内容。

    参数：无
    返回：
        str，plist 文件内容
    """
    arguments = "".join(
        f"        <string>{saxutils.escape(str(item))}</string>\n"
        for item in launch_command()
    )
    log_path = saxutils.escape(str(settings.LOG_DIR / "launcher.log"))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{LAUNCH_AGENT_LABEL}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"{arguments}"
        "    </array>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "    <key>StandardOutPath</key>\n"
        f"    <string>{log_path}</string>\n"
        "    <key>StandardErrorPath</key>\n"
        f"    <string>{log_path}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def autostart_enabled() -> bool:
    """判断是否已设置登录后自动启动。

    参数：无
    返回：
        bool，LaunchAgent 配置文件存在时返回 True
    """
    return LAUNCH_AGENT_PATH.exists()


def install_autostart() -> Tuple[bool, str]:
    """写入并注册 LaunchAgent（普通用户即可）。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    try:
        LAUNCH_AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_AGENT_PATH.write_text(launch_agent_text(), encoding="utf-8")
    except OSError as exc:
        return False, f"写入自启配置失败：{exc}"

    domain = f"gui/{os.getuid()}"
    # 先注销旧配置（可能未注册，失败可忽略），再注册，避免 bootstrap 报已存在
    _run([LAUNCHCTL, "bootout", domain, str(LAUNCH_AGENT_PATH)])
    code, _, err = _run([LAUNCHCTL, "bootstrap", domain, str(LAUNCH_AGENT_PATH)])
    if code != 0:
        code, _, fallback_err = _run([LAUNCHCTL, "load", "-w", str(LAUNCH_AGENT_PATH)])
        if code != 0:
            detail = (err or fallback_err).strip()[:140]
            return False, f"注册开机启动失败：{detail}"
    return True, "已设置登录后自动启动"


def uninstall_autostart() -> Tuple[bool, str]:
    """停止并删除 LaunchAgent。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    domain = f"gui/{os.getuid()}"
    _run([LAUNCHCTL, "bootout", domain, str(LAUNCH_AGENT_PATH)])
    _run([LAUNCHCTL, "unload", "-w", str(LAUNCH_AGENT_PATH)])
    try:
        remove_file(LAUNCH_AGENT_PATH)
    except OSError as exc:
        return False, f"删除自启配置失败：{exc}"
    return True, "已取消开机启动"


def set_autostart(enable: bool) -> Tuple[bool, str]:
    """启用或关闭登录后自动启动。

    参数：
        enable - True 启用，False 关闭
    返回：
        (是否成功, 说明文本)
    """
    return install_autostart() if enable else uninstall_autostart()


# --------------------------------------------------------------------------- 控制台 / 打开文件


def console_visible() -> bool:
    """macOS 版没有独立控制台窗口。

    参数：无
    返回：
        bool，恒为 False
    """
    return False


def toggle_console() -> Tuple[bool, str]:
    """macOS 版不支持控制台显隐。

    参数：无
    返回：
        (False, 说明文本)
    """
    return False, "macOS 版没有独立控制台窗口；运行日志见「打开打印日志」"


def hide_console() -> Tuple[bool, str]:
    """macOS 版无需隐藏控制台。

    参数：无
    返回：
        (False, 说明文本)
    """
    return False, "macOS 版没有独立控制台窗口"


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
    code, _, err = _run([OPEN, str(target)])
    if code == 0:
        return True, f"已打开 {target}"
    return False, f"打开失败：{err.strip()[:140]}"


def open_network_panel() -> Tuple[bool, str]:
    """打开「系统设置 → 网络」面板。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    # macOS 13 起系统设置改为独立进程，旧 URL 仅作兜底
    for url in (
        "x-apple.systempreferences:com.apple.Network-Settings.extension",
        "x-apple.systempreferences:com.apple.preference.network",
    ):
        code, _, _ = _run([OPEN, url])
        if code == 0:
            return True, "已打开系统网络设置"
    return False, "无法打开系统网络设置，请手动打开「系统设置 → 网络」"


# --------------------------------------------------------------------------- 网络配置


def _prefix_from_mask(mask: str) -> str:
    """把子网掩码转换为前缀长度。

    参数：
        mask - 形如 255.255.255.0，或直接是 24
    返回：
        str，前缀长度字符串；无法解析时返回 24
    """
    text = str(mask or "").strip()
    if text.isdigit():
        return text
    parts = text.split(".")
    if len(parts) != 4:
        return "24"
    try:
        return str(sum(bin(int(part)).count("1") for part in parts))
    except ValueError:
        return "24"


def _network_services() -> List[str]:
    """列出所有已启用的网络服务名。

    参数：无
    返回：
        list[str]，网络服务名称（如「Wi-Fi」「USB 10/100/1000 LAN」）
    """
    code, stdout, _ = _run([NETWORKSETUP, "-listallnetworkservices"])
    if code != 0:
        return []
    services: List[str] = []
    for line in stdout.splitlines():
        name = line.strip()
        if not name or name.startswith("An asterisk") or name.startswith("*"):
            continue
        services.append(name)
    return services


def list_adapters() -> List[Dict[str, str]]:
    """列出本机网络服务与 IPv4 地址。

    参数：无
    返回：
        list[dict]，元素含 name（网络服务名）/ ip / cidr；查询失败返回空列表
    """
    adapters: List[Dict[str, str]] = []
    for service in _network_services():
        code, stdout, _ = _run([NETWORKSETUP, "-getinfo", service])
        if code != 0:
            continue
        ip = mask = ""
        for line in stdout.splitlines():
            key, sep, value = line.partition(":")
            if not sep:
                continue
            label = key.strip().lower()
            if label == "ip address":
                ip = value.strip()
            elif label == "subnet mask":
                mask = value.strip()
        if not ip or ip.lower() == "none":
            continue
        adapters.append(
            {"name": service, "ip": ip, "cidr": f"{ip}/{_prefix_from_mask(mask)}"}
        )
    return adapters


def _run_admin(args: List[str], timeout: int = 60) -> Tuple[int, str, str]:
    """以管理员权限执行命令（非 root 时弹出系统授权框）。

    参数：
        args    - 命令与参数
        timeout - 超时秒数
    返回：
        (返回码, 标准输出, 标准错误)
    """
    if is_admin():
        return _run(args, timeout=timeout)
    command = " ".join(shlex.quote(str(item)) for item in args)
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    script = f'do shell script "{escaped}" with administrator privileges'
    return _run([OSASCRIPT, "-e", script], timeout=timeout)


def set_static_ip(adapter: str, ip: str, mask: str, gateway: str = "") -> Tuple[bool, str]:
    """把指定网络服务设置为静态 IPv4。

    参数：
        adapter - 网络服务名（取自 list_adapters 的 name）
        ip      - 静态 IP
        mask    - 子网掩码（如 255.255.255.0）
        gateway - 默认网关（可留空）
    返回：
        (是否成功, 说明文本)
    """
    if not adapter:
        return False, "未指定网络服务"
    if not ip or not mask:
        return False, "IP 地址与子网掩码不能为空"

    args = [NETWORKSETUP, "-setmanual", adapter, ip, mask]
    if gateway:
        args.append(gateway)
    code, _, err = _run_admin(args)
    if code != 0:
        return False, f"设置失败：{err.strip()[:140]}"
    return True, f"{adapter} 已设置为静态地址 {ip}/{_prefix_from_mask(mask)}"


def enable_dhcp(adapter: str) -> Tuple[bool, str]:
    """把指定网络服务恢复为自动获取 IP（DHCP）。

    参数：
        adapter - 网络服务名
    返回：
        (是否成功, 说明文本)
    """
    if not adapter:
        return False, "未指定网络服务"
    code, _, err = _run_admin([NETWORKSETUP, "-setdhcp", adapter])
    if code != 0:
        return False, f"设置失败：{err.strip()[:140]}"
    return True, f"{adapter} 已恢复为自动获取 IP"


def network_summary() -> str:
    """生成当前网络配置的简要文本。

    参数：无
    返回：
        str，形如「Wi-Fi：192.168.1.10；USB LAN：无地址」
    """
    adapters = list_adapters()
    if not adapters:
        return "未读取到网络配置"
    return "；".join(f"{item['name']}：{item['ip']}" for item in adapters)


# --------------------------------------------------------------------------- 对话框


def ask_form(
    title: str,
    fields: List[Tuple[str, str]],
    defaults: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, str]]:
    """弹出简单的输入对话框（依赖 tkinter，不可用时返回 None）。

    参数：
        title    - 对话框标题
        fields   - [(字段键, 显示名)] 列表
        defaults - 各字段默认值
    返回：
        dict 或 None
    """
    try:
        import tkinter as tk
    except ImportError:
        print(f"[{title}] 当前环境不支持图形输入框", flush=True)
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
        entry = tk.Entry(window, width=30)
        entry.insert(0, str(defaults.get(key, "")))
        entry.grid(row=index, column=1, padx=10, pady=6)
        entries[key] = entry

    def on_ok() -> None:
        """收集输入内容并关闭窗口。

        参数：无
        返回：None
        """
        for key, entry in entries.items():
            result[key] = entry.get().strip()
        window.destroy()

    def on_cancel() -> None:
        """放弃输入并关闭窗口。

        参数：无
        返回：None
        """
        result.clear()
        window.destroy()

    button_row = tk.Frame(window)
    button_row.grid(row=len(fields), column=0, columnspan=2, pady=10)
    tk.Button(button_row, text="确定", width=10, command=on_ok).pack(side="left", padx=6)
    tk.Button(button_row, text="取消", width=10, command=on_cancel).pack(side="left", padx=6)
    window.protocol("WM_DELETE_WINDOW", on_cancel)

    try:
        window.lift()
        window.focus_force()
        window.mainloop()
    finally:
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
        str 或 None
    """
    try:
        import tkinter as tk
    except ImportError:
        print(f"[{title}] {prompt}（当前环境不支持图形选择框）", flush=True)
        return None

    selected: Dict[str, Optional[str]] = {"value": None}
    window = tk.Tk()
    window.title(title)
    window.resizable(False, False)
    window.attributes("-topmost", True)

    def close_window() -> None:
        """取消选择并关闭窗口。

        参数：无
        返回：None
        """
        selected["value"] = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", close_window)
    tk.Label(window, text=prompt, justify="left", anchor="w").pack(padx=14, pady=(12, 6))

    def choose(value: str) -> None:
        """记录选择结果并关闭窗口。

        参数：
            value - 用户选择的值
        返回：None
        """
        selected["value"] = value
        window.destroy()

    for value, label in options:
        tk.Button(
            window, text=label, width=40, command=lambda v=value: choose(v)
        ).pack(padx=14, pady=3)
    tk.Button(window, text="取消", width=40, command=close_window).pack(padx=14, pady=(8, 12))

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
    """弹出信息提示框（基于 tkinter；不可用时输出到终端）。

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
