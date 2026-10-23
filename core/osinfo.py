"""运行平台识别模块。

所有跨平台分支判断集中在此，避免各处散落 `sys.platform` 判断。
"""

from __future__ import annotations

import sys

IS_WINDOWS: bool = sys.platform.startswith("win")
IS_LINUX: bool = sys.platform.startswith("linux")
IS_MACOS: bool = sys.platform == "darwin"


def platform_name() -> str:
    """返回当前平台的展示名称。

    参数：无
    返回：
        str，如 Windows / 银河麒麟等 Linux 发行版 / macOS
    """
    if IS_WINDOWS:
        return "Windows"
    if IS_LINUX:
        return linux_distro_name() or "Linux"
    if IS_MACOS:
        return "macOS"
    return sys.platform


def linux_distro_name() -> str:
    """读取 Linux 发行版名称（优先 /etc/os-release 的 PRETTY_NAME）。

    参数：无
    返回：
        str，发行版名称；读取失败时返回空字符串
    """
    if not IS_LINUX:
        return ""
    try:
        with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def platform_tag() -> str:
    """返回用于文件名/标识的简短平台标签。

    参数：无
    返回：
        str，windows / linux / macos
    """
    if IS_WINDOWS:
        return "windows"
    if IS_LINUX:
        return "linux"
    if IS_MACOS:
        return "macos"
    return "unknown"
