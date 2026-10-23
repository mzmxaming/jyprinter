"""系统集成分发模块：按运行平台加载 Windows / macOS / Linux 实现。

对外统一暴露：权限判断、剪贴板、开机启动、控制台、打开文件/网络面板、
网络配置读写与只读摘要、以及简单的输入/选择/提示对话框。
"""

from __future__ import annotations

from core.osinfo import IS_MACOS, IS_WINDOWS

if IS_WINDOWS:
    from core.system_win import (  # noqa: F401
        ask_choice,
        ask_form,
        autostart_enabled,
        clipboard_copy,
        console_visible,
        enable_dhcp,
        hide_console,
        info,
        is_admin,
        list_adapters,
        network_summary,
        open_network_panel,
        open_path,
        set_autostart,
        set_static_ip,
        toggle_console,
    )
elif IS_MACOS:
    # macOS 与 Linux 同属 POSIX，但自启、网络配置、剪贴板命令完全不同，
    # 因此单独提供 system_macos 实现（LaunchAgent / networksetup / pbcopy）。
    from core.system_macos import (  # noqa: F401
        ask_choice,
        ask_form,
        autostart_enabled,
        clipboard_copy,
        console_visible,
        enable_dhcp,
        hide_console,
        info,
        is_admin,
        list_adapters,
        network_summary,
        open_network_panel,
        open_path,
        set_autostart,
        set_static_ip,
        toggle_console,
    )
else:
    from core.system_linux import (  # noqa: F401
        DESKTOP_PATH,
        SERVICE_NAME,
        SERVICE_PATH,
        ask_choice,
        ask_form,
        autostart_enabled,
        clipboard_copy,
        console_visible,
        desktop_autostart_enabled,
        distro_text,
        enable_dhcp,
        hide_console,
        info,
        install_desktop_autostart,
        install_service,
        is_admin,
        list_adapters,
        network_summary,
        open_network_panel,
        open_path,
        service_active,
        service_command,
        service_enabled,
        service_installed,
        set_autostart,
        set_static_ip,
        systemd_available,
        toggle_console,
        uninstall_desktop_autostart,
        uninstall_service,
    )
