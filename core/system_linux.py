"""Linux 系统集成：开机启动（systemd 服务 / 桌面自启）、剪贴板、网络配置与只读查询。

开机启动两种方式：
    · systemd 系统服务：开机即运行、无需登录，适合当作打印服务器（推荐，需 root）；
    · 桌面自启：写入 ~/.config/autostart，登录后运行并带托盘图标（普通用户即可）。

网络配置依赖 NetworkManager（nmcli）；若目标机未使用 NetworkManager，
则只提供只读信息与打开系统网络设置入口，避免误改导致断网。
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import settings
from core.osinfo import linux_distro_name
from core.utils import remove_file

# systemd 服务名
SERVICE_NAME = "jyprinter.service"
SERVICE_PATH = Path("/etc/systemd/system") / SERVICE_NAME
# 桌面自启文件
DESKTOP_PATH = Path.home() / ".config" / "autostart" / "jyprinter.desktop"

_CMD_TIMEOUT = 30


def _run(args: List[str], timeout: int = _CMD_TIMEOUT) -> Tuple[int, str, str]:
    """执行系统命令并返回 (返回码, 标准输出, 标准错误)。

    参数：
        args    - 命令与参数
        timeout - 超时秒数
    返回：
        (int, str, str)
    """
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
    """判断当前进程是否为 root。

    参数：无
    返回：
        bool，root 返回 True
    """
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def clipboard_copy(text: str) -> bool:
    """把文本写入系统剪贴板（依赖 xclip / xsel / wl-copy）。

    参数：
        text - 待复制的文本
    返回：
        bool，成功返回 True
    """
    candidates = (
        ("wl-copy", []),
        ("xclip", ["-selection", "clipboard"]),
        ("xsel", ["--clipboard", "--input"]),
    )
    for command, extra in candidates:
        if not shutil.which(command):
            continue
        try:
            process = subprocess.run(
                [command] + extra,
                input=text.encode("utf-8"),
                timeout=10,
                capture_output=True,
            )
            if process.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


# --------------------------------------------------------------------------- 开机启动


def service_command() -> str:
    """生成启动本程序的命令（区分源码运行与打包运行）。

    参数：无
    返回：
        str，命令行字符串
    """
    if getattr(sys, "frozen", False):
        return f"{shlex.quote(sys.executable)} --no-tray"
    script = Path(__file__).resolve().parent.parent / "main.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} --no-tray"


def service_unit_text() -> str:
    """生成 systemd 服务单元内容。

    参数：无
    返回：
        str，unit 文件内容
    """
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
    return "\n".join(
        [
            "[Unit]",
            "Description=网络打印服务（jyprinter 局域网静默打印）",
            "After=network-online.target cups.service",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={user}",
            f"WorkingDirectory={settings.BASE_DIR}",
            f"ExecStart={service_command()}",
            "Restart=always",
            "RestartSec=5",
            "Environment=PYTHONUNBUFFERED=1",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def desktop_entry_text() -> str:
    """生成桌面自启（autostart）条目内容。

    参数：无
    返回：
        str，.desktop 文件内容
    """
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=网络打印服务",
            "Comment=局域网文件静默打印服务",
            f"Exec={service_command().replace(' --no-tray', '')}",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            "",
        ]
    )


def systemd_available() -> bool:
    """判断系统是否使用 systemd。

    参数：无
    返回：
        bool，可用返回 True
    """
    return bool(shutil.which("systemctl")) and Path("/run/systemd/system").exists()


def service_installed() -> bool:
    """判断 systemd 服务单元是否已安装。

    参数：无
    返回：
        bool，已安装返回 True
    """
    return SERVICE_PATH.exists()


def service_enabled() -> bool:
    """判断 systemd 服务是否已设为开机启动。

    参数：无
    返回：
        bool，已启用返回 True
    """
    if not systemd_available():
        return False
    code, stdout, _ = _run(["systemctl", "is-enabled", SERVICE_NAME])
    return code == 0 and stdout.strip() in ("enabled", "enabled-runtime", "static")


def service_active() -> bool:
    """判断 systemd 服务当前是否在运行。

    参数：无
    返回：
        bool，运行中返回 True
    """
    if not systemd_available():
        return False
    code, stdout, _ = _run(["systemctl", "is-active", SERVICE_NAME])
    return code == 0 and stdout.strip() == "active"


def desktop_autostart_enabled() -> bool:
    """判断桌面自启条目是否存在。

    参数：无
    返回：
        bool，存在返回 True
    """
    return DESKTOP_PATH.exists()


def autostart_enabled() -> bool:
    """判断是否已设置开机启动（systemd 服务或桌面自启任一存在即可）。

    参数：无
    返回：
        bool，已设置返回 True
    """
    return service_enabled() or desktop_autostart_enabled()


def install_service() -> Tuple[bool, str]:
    """安装并启用 systemd 服务（需要 root）。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    if not systemd_available():
        return False, "当前系统未使用 systemd，无法安装系统服务（可改用桌面自启）"
    if not is_admin():
        return False, "安装系统服务需要 root 权限，请用 sudo 运行本程序，或改用桌面自启"
    try:
        SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SERVICE_PATH.write_text(service_unit_text(), encoding="utf-8")
    except OSError as exc:
        return False, f"写入服务文件失败：{exc}"

    for args in (["systemctl", "daemon-reload"], ["systemctl", "enable", SERVICE_NAME]):
        code, _, err = _run(args)
        if code != 0:
            return False, f"{' '.join(args)} 失败：{err.strip()[:140]}"
    return True, f"已安装并启用开机启动服务（{SERVICE_NAME}）"


def enable_service_now() -> Tuple[bool, str]:
    """启动 systemd 服务。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    if not service_installed():
        return False, "服务尚未安装"
    code, _, err = _run(["systemctl", "start", SERVICE_NAME])
    if code != 0:
        return False, f"启动服务失败：{err.strip()[:140]}"
    return True, "服务已启动"


def uninstall_service() -> Tuple[bool, str]:
    """停止并卸载 systemd 服务。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    if not is_admin():
        return False, "卸载系统服务需要 root 权限"
    _run(["systemctl", "stop", SERVICE_NAME])
    _run(["systemctl", "disable", SERVICE_NAME])
    try:
        remove_file(SERVICE_PATH)
    except OSError as exc:
        return False, f"删除服务文件失败：{exc}"
    _run(["systemctl", "daemon-reload"])
    return True, "已停止并卸载开机启动服务"


def install_desktop_autostart() -> Tuple[bool, str]:
    """写入桌面自启条目（普通用户即可）。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    try:
        DESKTOP_PATH.parent.mkdir(parents=True, exist_ok=True)
        DESKTOP_PATH.write_text(desktop_entry_text(), encoding="utf-8")
    except OSError as exc:
        return False, f"写入自启条目失败：{exc}"
    return True, "已设置登录后自动启动（桌面自启）"


def uninstall_desktop_autostart() -> Tuple[bool, str]:
    """删除桌面自启条目。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    try:
        remove_file(DESKTOP_PATH)
    except OSError as exc:
        return False, f"删除自启条目失败：{exc}"
    return True, "已取消桌面自启"


def set_autostart(enable: bool) -> Tuple[bool, str]:
    """启用/关闭开机启动（自动选择 systemd 或桌面自启）。

    参数：
        enable - True 启用，False 关闭
    返回：
        (是否成功, 说明文本)
    """
    if enable:
        if systemd_available() and is_admin():
            return install_service()
        if systemd_available():
            install_desktop_autostart()
            return True, "当前非 root：已改用桌面自启（如需开机即运行，请用 sudo 执行安装脚本）"
        return install_desktop_autostart()

    messages: List[str] = []
    if service_installed():
        ok, message = uninstall_service()
        messages.append(message)
        if not ok:
            return False, message
    if desktop_autostart_enabled():
        ok, message = uninstall_desktop_autostart()
        messages.append(message)
        if not ok:
            return False, message
    return True, "；".join(messages) or "开机启动已关闭"


# --------------------------------------------------------------------------- 控制台 / 打开文件


def console_visible() -> bool:
    """Linux 版没有独立控制台窗口。

    参数：无
    返回：
        bool，恒为 False
    """
    return False


def toggle_console() -> Tuple[bool, str]:
    """Linux 版不支持控制台显隐。

    参数：无
    返回：
        (False, 说明文本)
    """
    return False, "Linux 版没有独立控制台窗口；服务日志可用 journalctl -u jyprinter 查看"


def hide_console() -> Tuple[bool, str]:
    """Linux 版无需隐藏控制台。

    参数：无
    返回：
        (False, 说明文本)
    """
    return False, "Linux 版没有独立控制台窗口"


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
    if shutil.which("xdg-open"):
        code, _, err = _run(["xdg-open", str(target)])
        if code == 0:
            return True, f"已打开 {target}"
        return False, f"打开失败：{err.strip()[:140]}"
    return False, "未找到 xdg-open，无法打开文件"


def open_network_panel() -> Tuple[bool, str]:
    """打开系统网络设置面板。

    参数：无
    返回：
        (是否成功, 说明文本)
    """
    for command, args in (
        ("nm-connection-editor", []),
        ("gnome-control-center", ["network"]),
        ("systemsettings5", ["kcm_networkmanagement"]),
        ("ukui-control-center", []),
    ):
        if shutil.which(command):
            try:
                subprocess.Popen([command] + args)
                return True, f"已打开网络设置（{command}）"
            except OSError:
                continue
    return False, "未找到可用的网络设置程序，请手动打开系统设置中的网络配置"


# --------------------------------------------------------------------------- 网络配置


def parse_ip_addr(text: str) -> List[Dict[str, str]]:
    """解析 `ip -4 -o addr show` 输出，得到网卡与 IPv4 地址。

    参数：
        text - 命令输出，形如：
               2: ens33    inet 192.168.1.10/24 brd 192.168.1.255 scope global ens33
    返回：
        list[dict]，元素含 name / ip / cidr
    """
    adapters: List[Dict[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        name = parts[1].rstrip(":")
        cidr = parts[3]
        ip = cidr.split("/")[0]
        if name == "lo":
            continue
        adapters.append({"name": name, "ip": ip, "cidr": cidr})
    return adapters


def list_adapters() -> List[Dict[str, str]]:
    """列出本机网卡与 IPv4 地址。

    参数：无
    返回：
        list[dict]，元素含 name / ip / cidr；查询失败返回空列表
    """
    if not shutil.which("ip"):
        return []
    code, stdout, _ = _run(["ip", "-4", "-o", "addr", "show"])
    if code != 0:
        return []
    return parse_ip_addr(stdout)


def parse_nmcli_devices(text: str) -> Dict[str, str]:
    """解析 `nmcli -t -f NAME,DEVICE con show` 输出，得到「网卡 → 连接名」映射。

    参数：
        text - 命令输出，形如 "有线连接 1:ens33"
    返回：
        dict，{网卡名: 连接名}
    """
    mapping: Dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            name, _, device = line.partition(": ")
        elif ":" in line:
            name, _, device = line.partition(":")
        else:
            continue
        name, device = name.strip(), device.strip()
        if device:
            mapping[device] = name
    return mapping


def nmcli_available() -> bool:
    """判断 NetworkManager 的 nmcli 是否可用。

    参数：无
    返回：
        bool，可用返回 True
    """
    return bool(shutil.which("nmcli"))


def _connection_of(adapter: str) -> Optional[str]:
    """查找网卡对应的 NetworkManager 连接名。

    参数：
        adapter - 网卡名（如 ens33）
    返回：
        str 或 None
    """
    code, stdout, _ = _run(["nmcli", "-t", "-f", "NAME,DEVICE", "con", "show"])
    if code != 0:
        return None
    return parse_nmcli_devices(stdout).get(adapter)


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
        bits = 0
        for part in parts:
            value = int(part)
            bits += bin(value).count("1")
        return str(bits)
    except ValueError:
        return "24"


def set_static_ip(adapter: str, ip: str, mask: str, gateway: str = "") -> Tuple[bool, str]:
    """把指定网卡设置为静态 IPv4（需要 NetworkManager 与 root 权限）。

    参数：
        adapter - 网卡名
        ip      - 静态 IP
        mask    - 子网掩码
        gateway - 默认网关（可留空）
    返回：
        (是否成功, 说明文本)
    """
    if not nmcli_available():
        return False, "当前系统未使用 NetworkManager，请在系统网络设置中手动配置静态 IP"
    if not is_admin():
        return False, "修改网络配置需要 root 权限"
    connection = _connection_of(adapter)
    if not connection:
        return False, f"未找到网卡「{adapter}」对应的网络连接"

    prefix = _prefix_from_mask(mask)
    args = [
        "nmcli",
        "con",
        "mod",
        connection,
        "ipv4.addresses",
        f"{ip}/{prefix}",
        "ipv4.method",
        "manual",
    ]
    if gateway:
        args += ["ipv4.gateway", gateway]
    code, _, err = _run(args)
    if code != 0:
        return False, f"设置失败：{err.strip()[:140]}"
    code, _, err = _run(["nmcli", "con", "up", connection], timeout=60)
    if code != 0:
        return False, f"应用配置失败：{err.strip()[:140]}"
    return True, f"网卡 {adapter} 已设置为静态地址 {ip}/{prefix}"


def enable_dhcp(adapter: str) -> Tuple[bool, str]:
    """把指定网卡恢复为自动获取 IP（DHCP）。

    参数：
        adapter - 网卡名
    返回：
        (是否成功, 说明文本)
    """
    if not nmcli_available():
        return False, "当前系统未使用 NetworkManager，请在系统网络设置中改回自动获取"
    if not is_admin():
        return False, "修改网络配置需要 root 权限"
    connection = _connection_of(adapter)
    if not connection:
        return False, f"未找到网卡「{adapter}」对应的网络连接"

    code, _, err = _run(["nmcli", "con", "mod", connection, "ipv4.method", "auto"])
    if code != 0:
        return False, f"设置失败：{err.strip()[:140]}"
    _run(["nmcli", "con", "mod", connection, "ipv4.gateway", ""])
    code, _, err = _run(["nmcli", "con", "up", connection], timeout=60)
    if code != 0:
        return False, f"应用配置失败：{err.strip()[:140]}"
    return True, f"网卡 {adapter} 已恢复为自动获取 IP"


def network_summary() -> str:
    """生成当前网络配置的简要文本（用于托盘菜单展示）。

    参数：无
    返回：
        str，形如「ens33：192.168.1.10；wlan0：无地址」
    """
    adapters = list_adapters()
    if not adapters:
        return "未读取到网络配置"
    return "；".join(f"{item['name']}：{item['ip']}" for item in adapters)


def add_adapters_without_ip() -> List[str]:
    """列出存在但没有 IPv4 地址的网卡（用于提示）。

    参数：无
    返回：
        list[str]，网卡名列表
    """
    if not shutil.which("ip"):
        return []
    code, stdout, _ = _run(["ip", "-o", "link", "show"])
    if code != 0:
        return []
    names = []
    for line in stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 2:
            names.append(parts[1].strip())
    with_ip = {item["name"] for item in list_adapters()}
    return [name for name in names if name and name not in with_ip]


def distro_text() -> str:
    """返回发行版名称（用于界面展示）。

    参数：无
    返回：
        str，发行版名称
    """
    return linux_distro_name() or "Linux"


# --------------------------------------------------------------------------- 对话框


def ask_form(title: str, fields: List[Tuple[str, str]], defaults: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
    """弹出简单的输入对话框（依赖 python3-tk，不可用时返回 None）。

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
        return None

    defaults = defaults or {}
    result: Dict[str, str] = {}
    window = tk.Tk()
    window.title(title)
    window.resizable(False, False)
    window.attributes("-topmost", True)

    entries: Dict[str, Any] = {}
    for index, (key, label) in enumerate(fields):
        tk.Label(window, text=label, anchor="w").grid(row=index, column=0, sticky="w", padx=10, pady=6)
        entry = tk.Entry(window, width=30)
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
    """弹出单项选择对话框（依赖 python3-tk，不可用时返回 None）。

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
        tk.Button(window, text=label, width=40, command=lambda v=value: choose(v)).pack(padx=14, pady=3)
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
    """弹出信息提示框（基于 tkinter；无图形环境时输出到终端）。

    参数：
        title   - 标题
        message - 内容
    返回：
        None
    """
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print(f"[{title}] {message}", flush=True)
        return
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
