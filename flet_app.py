"""Flet 桌面外壳：把「网络打印服务」包装成 macOS 可安装的桌面应用。

启动流程：
    1. 先把数据目录与配置文件指向用户可写目录（.app 包内目录只读）；
    2. 初始化配置、日志、打印任务队列与 Flask 应用；
    3. 由 ServiceSupervisor 在后台启动局域网 HTTP 服务并做健康检测；
    4. 展示运行状态、打印机、任务队列与服务设置，关闭窗口时停止服务。

本模块既是 `flet build macos` 的入口模块（[tool.flet.app] module = "flet_app"），
也可以在源码目录直接运行：python flet_app.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 与 pyproject.toml 的 bundle_id 保持一致，用于推导 macOS 应用支持目录
APP_BUNDLE_ID = "com.cgm.jyprinter"


def _storage_root() -> Path:
    """确定桌面版运行时使用的可写根目录。

    优先级：
        1. Flet 打包运行时提供的 FLET_APP_STORAGE_DATA；
        2. macOS 的 ~/Library/Application Support/<bundle-id>；
        3. 其他平台（仅源码调试）使用项目目录下的 data/flet。

    参数：无
    返回：
        Path，可写根目录（此时不创建）
    """
    override = os.environ.get("FLET_APP_STORAGE_DATA", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_BUNDLE_ID
    return Path(__file__).resolve().parent / "data" / "flet"


def _prepare_runtime_env() -> Path:
    """在导入 core 之前设置数据目录与配置文件路径。

    core.settings 在导入时就会读取 JYPRINTER_DATA_DIR / JYPRINTER_CONFIG_PATH，
    因此必须在任何 core 模块导入之前调用本函数。

    参数：无
    返回：
        Path，可写根目录
    """
    root = _storage_root()
    os.environ.setdefault("JYPRINTER_DATA_DIR", str(root / "data"))
    os.environ.setdefault("JYPRINTER_CONFIG_PATH", str(root / "config.json"))
    return root


_RUNTIME_ROOT = _prepare_runtime_env()

import flet as ft  # noqa: E402

from core import audit, printers, settings, system  # noqa: E402
from core.tasks import TaskQueue  # noqa: E402
from core.utils import get_local_ip  # noqa: E402
from main import ServiceSupervisor  # noqa: E402
from web.server import create_app  # noqa: E402

# 后台状态：UI 线程与 asyncio 工作线程都会访问，统一用锁保护
_state_lock = threading.RLock()
_QUEUE: Optional[TaskQueue] = None
_APP: Optional[Any] = None
_SUPERVISOR: Optional[ServiceSupervisor] = None
_LOCAL_IP: str = ""
_LAST_ERROR: str = ""

# 状态颜色映射：用于任务列表的状态文字
_STATUS_COLORS = {
    "queued": ft.Colors.GREY_600,
    "review": ft.Colors.AMBER_700,
    "printing": ft.Colors.BLUE_600,
    "done": ft.Colors.GREEN_600,
    "failed": ft.Colors.RED_400,
    "canceled": ft.Colors.GREY_400,
}


def _init_backend() -> None:
    """初始化配置、日志、打印队列与 Flask 应用（不启动 HTTP 服务）。

    参数：无
    返回：
        None
    """
    global _QUEUE, _APP, _LOCAL_IP

    settings.ensure_dirs()
    settings.load()
    settings.save()
    try:
        audit.cleanup(int(settings.get("log_keep_days", 7)))
    except Exception:
        # 日志清理失败不应阻止应用启动
        pass

    with _state_lock:
        _QUEUE = TaskQueue()
        _APP = create_app(_QUEUE)
        _LOCAL_IP = get_local_ip()


def _service_running() -> bool:
    """判断 HTTP 服务是否已启动。

    参数：无
    返回：
        bool，已启动返回 True
    """
    with _state_lock:
        return _SUPERVISOR is not None


def _service_url() -> str:
    """拼接局域网访问地址。

    参数：无
    返回：
        str，如 http://192.168.1.10:5000
    """
    port = int(settings.get("port", 5000))
    return f"http://{_LOCAL_IP or '127.0.0.1'}:{port}"


def _start_service() -> Tuple[bool, str]:
    """启动 HTTP 服务与打印队列。

    参数：无
    返回：
        (bool, str)：是否成功，以及失败原因（成功时为空字符串）
    """
    global _QUEUE, _APP, _SUPERVISOR, _LAST_ERROR

    with _state_lock:
        if _SUPERVISOR is not None:
            return True, ""
        try:
            if _QUEUE is None:
                _QUEUE = TaskQueue()
            if _APP is None:
                _APP = create_app(_QUEUE)

            host = str(settings.get("host", "0.0.0.0"))
            port = int(settings.get("port", 5000))
            queue, app = _QUEUE, _APP

            queue.start()
            supervisor = ServiceSupervisor(host, port, app, queue)
            supervisor.start()
            _SUPERVISOR = supervisor
            _LAST_ERROR = ""
            return True, ""
        except Exception as exc:
            # 启动失败时回滚，保证可以修改端口后再次启动
            _LAST_ERROR = str(exc) or exc.__class__.__name__
            if _QUEUE is not None:
                try:
                    _QUEUE.stop()
                except Exception:
                    pass
            _QUEUE = None
            _APP = None
            _SUPERVISOR = None
            return False, _LAST_ERROR


def _stop_service() -> Tuple[bool, str]:
    """停止 HTTP 服务与打印队列。

    参数：无
    返回：
        (bool, str)：固定返回 (True, "")
    """
    global _QUEUE, _APP, _SUPERVISOR

    with _state_lock:
        supervisor = _SUPERVISOR
        _SUPERVISOR = None
        _QUEUE = None
        _APP = None
    if supervisor is not None:
        try:
            supervisor.stop()
        except Exception:
            pass
    return True, ""


def _restart_service() -> Tuple[bool, str]:
    """重启服务（用于端口等需要重新监听的配置变更）。

    参数：无
    返回：
        (bool, str)：是否成功，以及失败原因
    """
    _stop_service()
    return _start_service()


def _collect_printers() -> Tuple[str, List[Dict[str, Any]], str]:
    """读取系统默认打印机与打印机列表（在后台线程调用，避免阻塞 UI）。

    参数：无
    返回：
        (默认打印机名, 打印机列表, 错误信息)
    """
    try:
        default_name = printers.get_default_printer()
        rows = printers.list_printers(include_virtual=False)
        return default_name, rows, ""
    except Exception as exc:
        return "", [], str(exc) or exc.__class__.__name__


def _open_url(url: str) -> Tuple[bool, str]:
    """使用系统默认浏览器打开网址。

    参数：
        url - 待打开的完整网址
    返回：
        (bool, str)：是否成功，以及失败原因
    """
    try:
        if sys.platform == "darwin" and Path("/usr/bin/open").exists():
            subprocess.Popen(["/usr/bin/open", url])
            return True, ""
        if webbrowser.open(url):
            return True, ""
        return False, "无法调用系统浏览器"
    except Exception as exc:
        return False, str(exc) or exc.__class__.__name__


class DesktopApp:
    """主界面：状态、打印机、任务队列与服务设置。"""

    def __init__(self, page: ft.Page) -> None:
        """保存页面引用并创建所有需要在刷新时更新的控件。

        参数：
            page - Flet 页面对象
        返回：
            None
        """
        self.page = page
        self._closing = False
        self._recent_signature: Tuple[Any, ...] = ()
        self._printer_rows_ready = False

        self._status_dot = ft.Container(
            width=10, height=10, border_radius=5, bgcolor=ft.Colors.GREY_400
        )
        self._status_text = ft.Text(
            "正在初始化…", size=15, weight=ft.FontWeight.BOLD
        )
        self._url_text = ft.Text(
            "服务未启动",
            size=13,
            color=ft.Colors.ON_SURFACE_VARIANT,
            selectable=True,
            expand=True,
        )
        self._toggle_btn = ft.Button(
            content="启动服务",
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_toggle,
        )
        self._copy_btn = ft.OutlinedButton(
            content="复制地址",
            icon=ft.Icons.CONTENT_COPY,
            on_click=self._on_copy,
            disabled=True,
        )
        self._open_btn = ft.OutlinedButton(
            content="打开网页",
            icon=ft.Icons.OPEN_IN_BROWSER,
            on_click=self._on_open_web,
            disabled=True,
        )

        self._printer_summary = ft.Text(
            "正在读取打印机…", size=12, color=ft.Colors.ON_SURFACE_VARIANT
        )
        self._printer_list = ft.Column(controls=[], spacing=6)

        self._stat_values: Dict[str, ft.Text] = {
            "queued": self._stat_number("0"),
            "printing": self._stat_number("0"),
            "done": self._stat_number("0"),
            "failed": self._stat_number("0"),
        }
        self._recent = ft.Column(controls=[], spacing=6)

        self._port_field = ft.TextField(
            value=str(settings.get("port", 5000)),
            width=110,
            dense=True,
            text_size=14,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        self._autostart_switch = ft.Switch(
            label="登录时自动启动",
            value=system.autostart_enabled(),
            on_change=self._on_autostart,
        )

    @staticmethod
    def _stat_number(value: str) -> ft.Text:
        """创建统一样式的统计数字文本。

        参数：
            value - 初始显示的数字
        返回：
            ft.Text，统计数字控件
        """
        return ft.Text(value, size=22, weight=ft.FontWeight.BOLD)

    def _stat_tile(self, key: str, label: str) -> ft.Control:
        """创建单个统计卡片（数字 + 标题）。

        参数：
            key   - 统计键（queued/printing/done/failed）
            label - 展示标题
        返回：
            ft.Container，统计卡片
        """
        return ft.Container(
            content=ft.Column(
                controls=[self._stat_values[key], ft.Text(label, size=11, color=ft.Colors.ON_SURFACE_VARIANT)],
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=12,
            border_radius=10,
            bgcolor=ft.Colors.GREY_100,
            expand=1,
        )

    def _card(
        self,
        title: str,
        icon: ft.IconData,
        controls: List[ft.Control],
        actions: Optional[List[ft.Control]] = None,
    ) -> ft.Card:
        """构建统一样式的卡片容器。

        参数：
            title    - 卡片标题
            icon     - 标题前的图标
            controls - 卡片正文控件列表
            actions  - 标题右侧的操作控件（可选）
        返回：
            ft.Card，卡片控件
        """
        header = ft.Row(
            controls=[
                ft.Icon(icon=icon, size=18, color=ft.Colors.BLUE_600),
                ft.Text(title, size=14, weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                *(actions or []),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        header,
                        ft.Divider(height=1, color=ft.Colors.GREY_200),
                        *controls,
                    ],
                    spacing=10,
                ),
                padding=16,
            ),
            margin=0,
            elevation=1,
        )

    def build(self) -> None:
        """搭建窗口与全部界面控件。

        参数：无
        返回：
            None
        """
        self.page.title = f"{settings.PRODUCT_NAME} {settings.VERSION}"
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.bgcolor = ft.Colors.GREY_100
        self.page.padding = 0

        window = self.page.window
        window.width = 820
        window.height = 840
        window.min_width = 680
        window.min_height = 640
        # 关闭窗口时先停止后台服务，再真正销毁窗口
        window.prevent_close = True
        window.on_event = self._on_window_event

        status_card = self._card(
            "服务状态",
            ft.Icons.WIFI,
            controls=[
                ft.Row(
                    controls=[
                        self._status_dot,
                        self._status_text,
                        ft.Container(expand=True),
                        self._toggle_btn,
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    controls=[
                        ft.Icon(
                            icon=ft.Icons.LINK, size=16, color=ft.Colors.GREY_600
                        ),
                        self._url_text,
                        self._copy_btn,
                        self._open_btn,
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
        )

        printer_card = self._card(
            "打印机与队列",
            ft.Icons.PRINT,
            controls=[
                self._printer_summary,
                self._printer_list,
                ft.Divider(height=1, color=ft.Colors.GREY_200),
                ft.Row(
                    controls=[
                        self._stat_tile("queued", "排队中"),
                        self._stat_tile("printing", "打印中"),
                        self._stat_tile("done", "已完成"),
                        self._stat_tile("failed", "失败"),
                    ],
                    spacing=10,
                ),
                ft.Text("最近任务", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                self._recent,
            ],
            actions=[
                ft.OutlinedButton(
                    content="刷新打印机",
                    icon=ft.Icons.REFRESH,
                    on_click=self._on_refresh_printers,
                )
            ],
        )

        settings_card = self._card(
            "服务设置",
            ft.Icons.SETTINGS,
            controls=[
                ft.Row(
                    controls=[
                        ft.Text("服务端口", size=13),
                        self._port_field,
                        ft.OutlinedButton(
                            content="保存并重启",
                            icon=ft.Icons.SAVE,
                            on_click=self._on_save_port,
                        ),
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._autostart_switch,
                ft.Row(
                    controls=[
                        ft.OutlinedButton(
                            content="打开日志目录",
                            icon=ft.Icons.FOLDER_OPEN,
                            on_click=self._on_open_log_dir,
                        ),
                        ft.OutlinedButton(
                            content="打开数据目录",
                            icon=ft.Icons.FOLDER,
                            on_click=self._on_open_data_dir,
                        ),
                    ],
                    spacing=10,
                    wrap=True,
                ),
            ],
        )

        about_card = self._card(
            "关于",
            ft.Icons.INFO_OUTLINE,
            controls=[
                ft.Text(
                    f"{settings.PRODUCT_NAME}  v{settings.VERSION}",
                    size=13,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(settings.DEVELOPER_LINE, size=12),
                ft.Text(
                    "关闭本窗口会同时停止局域网打印服务；如需长期后台运行，"
                    "请开启上方「登录时自动启动」。",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Text(
                    f"数据目录：{settings.DATA_DIR}",
                    size=11,
                    color=ft.Colors.GREY_600,
                    selectable=True,
                ),
            ],
        )

        self.page.add(
            ft.Container(
                content=ft.Column(
                    controls=[
                        self._header(),
                        status_card,
                        printer_card,
                        settings_card,
                        about_card,
                    ],
                    spacing=14,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ),
                padding=20,
                expand=True,
            )
        )
        self.page.update()

    def _header(self) -> ft.Control:
        """构建顶部品牌栏。

        参数：无
        返回：
            ft.Control，品牌栏控件
        """
        return ft.Row(
            controls=[
                ft.Container(
                    content=ft.Image(
                        src="jyprinter.png",
                        width=44,
                        height=44,
                        fit=ft.BoxFit.CONTAIN,
                        error_content=ft.Icon(
                            icon=ft.Icons.PRINT, size=32, color=ft.Colors.BLUE_600
                        ),
                    ),
                    width=52,
                    height=52,
                    border_radius=12,
                    bgcolor=ft.Colors.WHITE,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Column(
                    controls=[
                        ft.Text(
                            settings.PRODUCT_NAME,
                            size=20,
                            weight=ft.FontWeight.BOLD,
                        ),
                        ft.Text(
                            f"v{settings.VERSION} · 局域网打印共享",
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=0,
                ),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def autorun(self) -> None:
        """界面就绪后自动启动服务并开始定时刷新。

    参数：无
    返回：
        None
        """
        ok, message = _start_service()
        if not ok:
            self._notify(f"服务启动失败：{message}", error=True)
        self._apply_service_state()
        self.page.update()
        self.page.run_task(self._refresh_loop)

    async def _refresh_loop(self) -> None:
        """定时刷新界面（每 2 秒，打印机每 10 秒读取一次）。

        参数：无
        返回：
            None
        """
        tick = 0
        while not self._closing:
            try:
                self._apply_service_state()
                self._apply_queue()
                if tick % 5 == 0:
                    default_name, rows, error = await asyncio.to_thread(
                        _collect_printers
                    )
                    self._apply_printers(default_name, rows, error)
                self.page.update()
            except Exception:
                # 单次刷新失败不应终止刷新循环
                pass
            tick += 1
            await asyncio.sleep(2)

    def _apply_service_state(self) -> None:
        """把服务运行状态同步到状态卡片。

        参数：无
        返回：
            None
        """
        running = _service_running()
        if running:
            self._status_dot.bgcolor = ft.Colors.GREEN_500
            self._status_text.value = "服务运行中"
            self._status_text.color = ft.Colors.GREEN_600
            self._toggle_btn.content = "停止服务"
            self._toggle_btn.icon = ft.Icons.STOP
            self._url_text.value = _service_url()
            self._url_text.color = ft.Colors.BLUE_600
        else:
            self._status_dot.bgcolor = ft.Colors.GREY_400
            self._status_text.value = "服务已停止"
            self._status_text.color = ft.Colors.GREY_600
            self._toggle_btn.content = "启动服务"
            self._toggle_btn.icon = ft.Icons.PLAY_ARROW
            self._url_text.value = (
                f"启动失败：{_LAST_ERROR}" if _LAST_ERROR else "服务未启动"
            )
            self._url_text.color = ft.Colors.RED_400 if _LAST_ERROR else ft.Colors.ON_SURFACE_VARIANT
        self._copy_btn.disabled = not running
        self._open_btn.disabled = not running

    def _apply_queue(self) -> None:
        """把队列统计与最近任务同步到界面。

        参数：无
        返回：
            None
        """
        with _state_lock:
            queue = _QUEUE
        tasks = queue.snapshot(200) if queue is not None else []

        counts = {"queued": 0, "printing": 0, "done": 0, "failed": 0}
        for task in tasks:
            status = str(task.get("status", ""))
            if status == "review":
                counts["queued"] += 1
            elif status in counts:
                counts[status] += 1
        for key, widget in self._stat_values.items():
            widget.value = str(counts[key])

        recent = tasks[:5]
        signature = tuple(
            (task.get("id"), task.get("status"), task.get("filename"))
            for task in recent
        )
        if signature == self._recent_signature:
            return

        self._recent_signature = signature
        if not recent:
            self._recent.controls = [
                ft.Text("暂无打印任务", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
            ]
            return

        rows: List[ft.Control] = []
        for task in recent:
            status = str(task.get("status", ""))
            rows.append(
                ft.Row(
                    controls=[
                        ft.Icon(
                            icon=ft.Icons.DESCRIPTION,
                            size=16,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Text(
                            str(task.get("filename", "")),
                            size=13,
                            no_wrap=True,
                            max_lines=1,
                            expand=True,
                        ),
                        ft.Text(
                            str(task.get("status_text", status)),
                            size=12,
                            color=_STATUS_COLORS.get(status, ft.Colors.GREY_600),
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        self._recent.controls = rows

    def _apply_printers(
        self, default_name: str, rows: List[Dict[str, Any]], error: str
    ) -> None:
        """更新打印机摘要与列表。

        参数：
            default_name - 系统默认打印机名
            rows         - printers.list_printers 返回的打印机列表
            error        - 读取失败时的错误信息
        返回：
            None
        """
        if error:
            self._printer_summary.value = f"无法读取打印机：{error}"
            self._printer_summary.color = ft.Colors.RED_400
            self._printer_list.controls = []
            return

        if not rows:
            self._printer_summary.value = (
                "未发现打印机，请在「系统设置 → 打印机与扫描仪」中添加"
            )
            self._printer_summary.color = ft.Colors.AMBER_700
            self._printer_list.controls = []
            return

        self._printer_summary.value = (
            f"共 {len(rows)} 台打印机，默认：{default_name or '未设置'}"
        )
        self._printer_summary.color = ft.Colors.ON_SURFACE_VARIANT

        controls: List[ft.Control] = []
        for row in rows:
            name = str(row.get("name", ""))
            is_default = bool(row.get("is_default")) or name == default_name
            controls.append(
                ft.Row(
                    controls=[
                        ft.Icon(
                            icon=ft.Icons.PRINT,
                            size=16,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Text(
                            name,
                            size=13,
                            weight=ft.FontWeight.BOLD if is_default else None,
                            expand=True,
                            no_wrap=True,
                            max_lines=1,
                        ),
                        ft.Text(
                            "默认" if is_default else str(row.get("status", "")),
                            size=11,
                            color=ft.Colors.GREEN_600 if is_default else ft.Colors.GREY_600,
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        self._printer_list.controls = controls

    def _notify(self, message: str, error: bool = False) -> None:
        """在窗口底部弹出短暂提示。

        参数：
            message - 提示内容
            error   - 是否为错误提示（红色背景）
        返回：
            None
        """
        snack = ft.SnackBar(
            content=ft.Text(message),
            duration=2600,
            bgcolor=ft.Colors.RED_400 if error else None,
        )
        self.page.show_dialog(snack)

    async def _on_toggle(self, event: Optional[ft.Event] = None) -> None:
        """启动或停止服务的按钮处理。

        参数：
            event - Flet 点击事件（可省略）
        返回：
            None
        """
        self._toggle_btn.disabled = True
        self.page.update()
        if _service_running():
            _, message = await asyncio.to_thread(_stop_service)
            self._notify("服务已停止")
        else:
            ok, message = await asyncio.to_thread(_start_service)
            if ok:
                self._notify(f"服务已启动：{_service_url()}")
            else:
                self._notify(f"服务启动失败：{message}", error=True)
        self._toggle_btn.disabled = False
        self._apply_service_state()
        self.page.update()

    async def _on_copy(self, event: Optional[ft.Event] = None) -> None:
        """复制访问地址到剪贴板。

        参数：
            event - Flet 点击事件（可省略）
        返回：
            None
        """
        if not _service_running():
            self._notify("服务未启动", error=True)
            return
        url = _service_url()
        ok = await asyncio.to_thread(system.clipboard_copy, url)
        self._notify(f"已复制：{url}" if ok else "复制失败，请手动选择地址复制", error=not ok)

    async def _on_open_web(self, event: Optional[ft.Event] = None) -> None:
        """在系统浏览器中打开服务页面。

        参数：
            event - Flet 点击事件（可省略）
        返回：
            None
        """
        if not _service_running():
            self._notify("服务未启动", error=True)
            return
        ok, message = await asyncio.to_thread(_open_url, _service_url())
        if not ok:
            self._notify(f"打开失败：{message}", error=True)

    async def _on_refresh_printers(self, event: Optional[ft.Event] = None) -> None:
        """手动刷新打印机列表。

        参数：
            event - Flet 点击事件（可省略）
        返回：
            None
        """
        default_name, rows, error = await asyncio.to_thread(_collect_printers)
        self._apply_printers(default_name, rows, error)
        self.page.update()
        self._notify(f"已刷新，共 {len(rows)} 台打印机" if not error else f"读取失败：{error}", error=bool(error))

    async def _on_save_port(self, event: Optional[ft.Event] = None) -> None:
        """保存端口并在服务运行中时重启服务。

        参数：
            event - Flet 点击事件（可省略）
        返回：
            None
        """
        raw = str(self._port_field.value or "").strip()
        if not raw.isdigit() or not (1 <= int(raw) <= 65535):
            self._notify("端口必须是 1-65535 之间的数字", error=True)
            return

        port = int(raw)
        settings.update({"port": port})
        was_running = _service_running()
        if was_running:
            ok, message = await asyncio.to_thread(_restart_service)
        else:
            ok, message = await asyncio.to_thread(_start_service)
        if ok:
            self._notify(f"端口已保存为 {port}，服务已启动")
        else:
            self._notify(f"端口已保存，但服务启动失败：{message}", error=True)
        self._apply_service_state()
        self.page.update()

    async def _on_autostart(self, event: Optional[ft.Event] = None) -> None:
        """开启或关闭登录时自动启动。

        参数：
            event - Flet 开关事件（可省略）
        返回：
            None
        """
        want = bool(self._autostart_switch.value)
        ok, message = await asyncio.to_thread(system.set_autostart, want)
        if not ok:
            self._autostart_switch.value = not want
            self.page.update()
        self._notify(message or ("已开启登录自启" if want else "已关闭登录自启"), error=not ok)

    async def _on_open_log_dir(self, event: Optional[ft.Event] = None) -> None:
        """在 Finder 中打开日志目录。

        参数：
            event - Flet 点击事件（可省略）
        返回：
            None
        """
        ok, message = await asyncio.to_thread(system.open_path, str(settings.LOG_DIR))
        if not ok:
            self._notify(f"打开失败：{message}", error=True)

    async def _on_open_data_dir(self, event: Optional[ft.Event] = None) -> None:
        """在 Finder 中打开数据目录。

        参数：
            event - Flet 点击事件（可省略）
        返回：
            None
        """
        ok, message = await asyncio.to_thread(system.open_path, str(settings.DATA_DIR))
        if not ok:
            self._notify(f"打开失败：{message}", error=True)

    async def _on_window_event(self, event: Any) -> None:
        """处理窗口事件：用户点击关闭时先停止服务再销毁窗口。

        参数：
            event - Flet WindowEvent，含 type 字段
        返回：
            None
        """
        event_type = getattr(event, "type", None)
        is_close = event_type == ft.WindowEventType.CLOSE or getattr(
            event_type, "value", None
        ) == "close"
        if not is_close or self._closing:
            return

        self._closing = True
        await asyncio.to_thread(_stop_service)
        await self.page.window.destroy()


def main(page: ft.Page) -> None:
    """Flet 应用入口：初始化后台并展示桌面界面。

    参数：
        page - Flet 页面对象
    返回：
        None
    """
    _init_backend()
    app = DesktopApp(page)
    app.build()
    app.autorun()


if __name__ == "__main__":
    ft.run(main)
