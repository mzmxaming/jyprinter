"""网络打印 —— 移动端客户端（Flet，供 iOS / 桌面调试使用）。

设计说明：
    · 本文件是**客户端**，只通过 HTTP 接口与「网络打印服务」通信，不启动 Flask 服务；
    · 与 Android 客户端功能对齐：服务地址与账号密码保存、自动登录、选文件、
      全部打印参数（份数/单双面/纸张/页码范围/缩放/颜色/质量/出纸方向/表格适应）、
      打印任务查看与取消/重试；
    · iOS 无「用其他应用打开」的等价能力，文件通过系统文档选择器（FilePicker）导入；
    · 配置落在应用存储目录（FLET_APP_STORAGE_DATA），不写系统临时目录。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import flet as ft
import requests

APP_TITLE = "网络打印"
TIMEOUT_CONNECT = 12
TIMEOUT_READ = 120

_BRAND = "#16537E"
_BRAND_DARK = "#0B3A5C"
_BG = "#F5F7FA"


def _storage_dir() -> Path:
    """返回可写配置目录（iOS/Android 由 Flet 注入应用存储目录）。"""
    override = os.environ.get("FLET_APP_STORAGE_DATA", "").strip()
    base = Path(override) if override else Path.home() / ".jyprinter-client"
    base.mkdir(parents=True, exist_ok=True)
    return base


class Store:
    """极简 JSON 配置存储（地址、账号、密码、自动登录）。"""

    def __init__(self) -> None:
        self.path = _storage_dir() / "config.json"
        self.data: Dict[str, Any] = {}
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str, default: Any = None) -> Any:
        """读取配置项。"""
        return self.data.get(key, default)

    def save(self, patch: Dict[str, Any]) -> None:
        """合并并持久化配置。"""
        self.data.update(patch)
        try:
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass


class ApiError(Exception):
    """接口异常，携带可直接展示的中文提示。"""


class Api:
    """网络打印服务的 HTTP 客户端（会话 Cookie 由 requests.Session 维护）。"""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.base = ""

    @staticmethod
    def normalize(raw: str) -> str:
        """补全协议并去掉末尾斜杠。"""
        text = (raw or "").strip()
        if not text:
            return ""
        if not text.lower().startswith(("http://", "https://")):
            text = "http://" + text
        return text.rstrip("/")

    def set_base(self, raw: str) -> str:
        """设置服务地址并返回规范化结果。"""
        self.base = self.normalize(raw)
        return self.base

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _raise_for_net(self, exc: Exception) -> None:
        detail = str(exc)
        if "timed out" in detail.lower():
            raise ApiError("连接超时，请确认手机与打印服务在同一网络")
        raise ApiError(f"网络错误：{detail}")

    def login(self, username: str, password: str) -> Dict[str, Any]:
        """表单登录并返回账号信息。"""
        if not self.base:
            raise ApiError("请先填写服务地址")
        try:
            resp = self.session.post(
                self._url("/login"),
                data={"username": username, "password": password, "next": "/"},
                timeout=(TIMEOUT_CONNECT, 30),
            )
        except requests.RequestException as exc:
            self._raise_for_net(exc)
        if resp.status_code == 401:
            raise ApiError("用户名或密码错误")
        if resp.status_code >= 400:
            raise ApiError(f"登录失败（HTTP {resp.status_code}）")
        me = self.session.get(self._url("/api/me"), timeout=(TIMEOUT_CONNECT, 20))
        if me.status_code != 200:
            raise ApiError("登录失败，请检查服务地址")
        data = me.json()
        if not data.get("ok"):
            raise ApiError("登录失败，请检查服务地址")
        return data

    def printers(self) -> Dict[str, Any]:
        """查询打印机列表。"""
        resp = self._get("/api/printers")
        return resp

    def capabilities(self, name: str) -> Dict[str, Any]:
        """查询指定打印机的纸张与双面能力。"""
        return self._get("/api/printer/capabilities", params={"name": name})

    def tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        """查询最近任务。"""
        data = self._get("/api/tasks", params={"limit": limit})
        return data.get("tasks") or []

    def stats(self) -> Optional[Dict[str, Any]]:
        """查询打印量统计；无权限时返回 None。"""
        try:
            resp = self.session.get(self._url("/api/stats"), timeout=(TIMEOUT_CONNECT, 20))
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        return resp.json()

    def upload(self, path: str, name: str, fields: Dict[str, str]) -> Dict[str, Any]:
        """上传文件并提交打印。"""
        try:
            with open(path, "rb") as handle:
                resp = self.session.post(
                    self._url("/api/upload"),
                    data=fields,
                    files={"files": (name, handle, "application/octet-stream")},
                    timeout=(TIMEOUT_CONNECT, TIMEOUT_READ),
                )
        except OSError as exc:
            raise ApiError(f"读取文件失败：{exc}")
        except requests.RequestException as exc:
            self._raise_for_net(exc)
        return self._decode(resp, "上传失败")

    def action(self, path: str) -> str:
        """执行取消/重试等写操作。"""
        try:
            resp = self.session.post(self._url(path), timeout=(TIMEOUT_CONNECT, 30))
        except requests.RequestException as exc:
            self._raise_for_net(exc)
        data = self._decode(resp, "操作失败")
        return str(data.get("message") or "操作成功")

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            resp = self.session.get(
                self._url(path), params=params, timeout=(TIMEOUT_CONNECT, TIMEOUT_READ)
            )
        except requests.RequestException as exc:
            self._raise_for_net(exc)
        return self._decode(resp, "请求失败")

    @staticmethod
    def _decode(resp: requests.Response, fallback: str) -> Dict[str, Any]:
        """解析响应，统一抛出中文异常。"""
        if resp.status_code == 401:
            raise ApiError("登录已过期，请重新登录")
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            raise ApiError(str(data.get("error") or data.get("message") or f"{fallback}（HTTP {resp.status_code}）"))
        return data


class ClientApp:
    """界面与状态管理。"""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.store = Store()
        self.api = Api()
        self.picker = ft.FilePicker()
        page.services.append(self.picker)

        self.messages: List[ft.SnackBar] = []
        self.file_path: Optional[str] = None
        self.file_name = ""
        self.file_size = 0
        self.printers: List[Dict[str, Any]] = []
        self.papers: List[Tuple[str, str]] = [("A4", "A4")]
        self.duplex_supported = True
        self.printer = ""
        self.copies = 1
        self.duplex = "simplex"
        self.paper = "A4"
        self.page_range = ""
        self.scale = "fit"
        self.scale_percent = 100
        self.color = "color"
        self.quality = "normal"
        self.orientation = "auto"
        self.fit_table = True
        self.tasks: List[Dict[str, Any]] = []
        self.stats: Optional[Dict[str, Any]] = None
        self.busy = False
        self.logged_in = False
        self.account = ""
        self._poll_started = False

        page.title = APP_TITLE
        page.theme_mode = ft.ThemeMode.LIGHT
        page.bgcolor = _BG
        page.padding = 0
        page.theme = ft.Theme(color_scheme_seed=_BRAND)

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        """启动：有保存的凭据则自动登录，否则显示登录页。"""
        server = str(self.store.get("server", "") or "")
        user = str(self.store.get("username", "") or "")
        password = str(self.store.get("password", "") or "")
        auto = bool(self.store.get("auto_login", True))
        if server and user and password and auto:
            self.api.set_base(server)
            self.page.add(self._loading_view("正在自动登录…"))
            self.page.update()
            self.page.run_task(self._auto_login, user, password)
        else:
            self.api.set_base(server)
            self.show_login()

    async def _auto_login(self, user: str, password: str) -> None:
        """后台执行自动登录。"""
        try:
            me = await asyncio.to_thread(self.api.login, user, password)
            await self._after_login(me)
        except ApiError:
            self.show_login(error="自动登录失败，请重新输入密码")

    # ------------------------------------------------------------ 通用组件
    def _toast(self, text: str) -> None:
        """显示一次性提示。"""
        snack = ft.SnackBar(ft.Text(text, color=ft.Colors.WHITE), bgcolor="#333333")
        self.page.show_dialog(snack)

    def _loading_view(self, text: str) -> ft.Control:
        """加载中视图。"""
        return ft.Container(
            content=ft.Column(
                [
                    ft.ProgressRing(width=28, height=28, stroke_width=2, color=_BRAND),
                    ft.Text(text, size=14, color=ft.Colors.GREY_700),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=14,
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
            padding=40,
        )

    def _card(self, content: ft.Control, padding: int = 18) -> ft.Container:
        """统一的白色圆角卡片。"""
        return ft.Container(
            content=content,
            bgcolor=ft.Colors.WHITE,
            border_radius=18,
            padding=padding,
            shadow=ft.BoxShadow(blur_radius=8, color="#11000000", offset=ft.Offset(0, 2)),
        )

    def _section_title(self, title: str, subtitle: str = "") -> ft.Control:
        """卡片标题。"""
        items: List[ft.Control] = [ft.Text(title, size=16, weight=ft.FontWeight.W_600)]
        if subtitle:
            items.append(ft.Text(subtitle, size=12, color=ft.Colors.GREY_600))
        return ft.Column(items, spacing=2)

    # ------------------------------------------------------------ 登录页
    def show_login(self, error: str = "") -> None:
        """渲染登录页。"""
        self.logged_in = False
        server_field = ft.TextField(
            label="服务地址",
            value=str(self.store.get("server", "") or ""),
            hint_text="http://192.168.1.10:5000",
            keyboard_type=ft.KeyboardType.URL,
            border_radius=12,
            prefix_icon=ft.Icons.DNS,
        )
        user_field = ft.TextField(
            label="账号",
            value=str(self.store.get("username", "") or ""),
            border_radius=12,
            prefix_icon=ft.Icons.PERSON,
        )
        password_field = ft.TextField(
            label="密码",
            password=True,
            can_reveal_password=True,
            border_radius=12,
            prefix_icon=ft.Icons.LOCK,
        )
        auto_switch = ft.Switch(label="自动登录（保存账号密码）", value=bool(self.store.get("auto_login", True)))
        error_text = ft.Text(error, size=13, color=ft.Colors.RED_600, visible=bool(error))
        login_button = ft.FilledButton(
            "登录",
            icon=ft.Icons.LOGIN,
            bgcolor=_BRAND,
            color=ft.Colors.WHITE,
            height=48,
            width=None,
        )

        async def on_login(_event: Any) -> None:
            server = self.api.normalize(server_field.value or "")
            if not server:
                error_text.value = "请填写服务地址"
                error_text.visible = True
                self.page.update()
                return
            if not (user_field.value or "").strip() or not (password_field.value or ""):
                error_text.value = "请填写账号与密码"
                error_text.visible = True
                self.page.update()
                return
            self.api.set_base(server)
            login_button.disabled = True
            login_button.content = ft.Row(
                [
                    ft.ProgressRing(width=16, height=16, stroke_width=2, color=ft.Colors.WHITE),
                    ft.Text("登录中…", color=ft.Colors.WHITE),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=8,
            )
            error_text.visible = False
            self.page.update()
            try:
                me = await asyncio.to_thread(self.api.login, user_field.value.strip(), password_field.value)
            except ApiError as exc:
                login_button.disabled = False
                login_button.content = "登录"
                error_text.value = str(exc)
                error_text.visible = True
                self.page.update()
                return
            self.store.save(
                {
                    "server": server,
                    "username": user_field.value.strip(),
                    "password": password_field.value if auto_switch.value else "",
                    "auto_login": bool(auto_switch.value),
                }
            )
            await self._after_login(me)

        login_button.on_click = on_login

        view = ft.Column(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Container(
                                content=ft.Icon(ft.Icons.PRINT, color=ft.Colors.WHITE, size=34),
                                bgcolor=_BRAND,
                                width=72,
                                height=72,
                                border_radius=36,
                                alignment=ft.Alignment.CENTER,
                            ),
                            ft.Text("网络打印服务", size=22, weight=ft.FontWeight.W_600),
                            ft.Text("手机上的文件，一键送到局域网打印机", size=13, color=ft.Colors.GREY_600),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    ),
                    padding=ft.Padding.only(top=48, bottom=24),
                ),
                self._card(
                    ft.Column(
                        [
                            server_field,
                            user_field,
                            password_field,
                            auto_switch,
                            error_text,
                            login_button,
                        ],
                        spacing=12,
                    )
                ),
                ft.Text(
                    "服务地址可在电脑端程序窗口或网页顶部查看",
                    size=12,
                    color=ft.Colors.GREY_600,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        self.page.controls.clear()
        self.page.add(ft.Container(content=view, padding=ft.Padding.symmetric(horizontal=20, vertical=10), expand=True))
        self.page.update()

    # ------------------------------------------------------------ 登录后
    async def _after_login(self, me: Dict[str, Any]) -> None:
        """登录成功：加载打印机、统计、任务并进入主页。"""
        self.logged_in = True
        self.account = str(me.get("username") or "")
        await asyncio.to_thread(self._load_printers)
        await asyncio.to_thread(self._load_stats)
        await asyncio.to_thread(self._load_tasks)
        self.show_home()
        if not self._poll_started:
            self._poll_started = True
            self.page.run_task(self._poll_loop)

    def _load_printers(self) -> None:
        """拉取打印机列表与默认打印机。"""
        data = self.api.printers()
        self.printers = data.get("printers") or []
        default = str(data.get("default") or "")
        names = [str(item.get("name") or "") for item in self.printers]
        if self.printer not in names:
            self.printer = default if default in names else (names[0] if names else "")
        self._load_caps()

    def _load_caps(self) -> None:
        """拉取当前打印机的纸张与双面能力。"""
        try:
            caps = self.api.capabilities(self.printer)
        except ApiError:
            return
        papers = caps.get("papers") or []
        self.papers = [(str(p.get("name") or ""), str(p.get("name") or "")) for p in papers if p.get("name")]
        if not self.papers:
            self.papers = [("A4", "A4")]
        names = [name for name, _ in self.papers]
        if self.paper not in names:
            self.paper = "A4" if "A4" in names else names[0]
        self.duplex_supported = bool(caps.get("duplex", True))
        if not self.duplex_supported:
            self.duplex = "simplex"

    def _load_stats(self) -> None:
        """拉取打印统计（非管理员为 None）。"""
        self.stats = self.api.stats()

    def _load_tasks(self) -> None:
        """拉取最近任务。"""
        try:
            self.tasks = self.api.tasks(20)
        except ApiError:
            self.tasks = []

    async def _poll_loop(self) -> None:
        """每 5 秒刷新任务状态。"""
        while True:
            await asyncio.sleep(5)
            if not self.logged_in:
                continue
            try:
                tasks = await asyncio.to_thread(self.api.tasks, 20)
            except ApiError:
                continue
            if [t.get("id") for t in tasks] != [t.get("id") for t in self.tasks]:
                self.tasks = tasks
                self.show_home(keep_scroll=True)
            else:
                statuses = [(t.get("id"), t.get("status")) for t in tasks]
                if statuses != [(t.get("id"), t.get("status")) for t in self.tasks]:
                    self.tasks = tasks
                    self.show_home(keep_scroll=True)

    # ------------------------------------------------------------ 主页
    def show_home(self, keep_scroll: bool = False) -> None:
        """渲染主界面。"""
        scroll_offset = 0
        if keep_scroll and self.page.controls:
            try:
                scroll_offset = int(getattr(self.page.controls[0], "offset", 0) or 0)
            except (TypeError, ValueError):
                scroll_offset = 0

        appbar = ft.AppBar(
            title=ft.Column(
                [
                    ft.Text(APP_TITLE, size=18, weight=ft.FontWeight.W_600),
                    ft.Text(self.api.base.replace("http://", "").replace("https://", ""), size=11, color=ft.Colors.GREY_600),
                ],
                spacing=0,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            bgcolor=_BG,
            actions=[
                ft.IconButton(ft.Icons.REFRESH, tooltip="刷新", on_click=lambda _e: self.page.run_task(self._refresh_all)),
                ft.IconButton(ft.Icons.LOGOUT, tooltip="退出登录", on_click=lambda _e: self.logout()),
            ],
        )

        header = ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text("准备就绪", size=18, color=ft.Colors.WHITE, weight=ft.FontWeight.W_600),
                            ft.Text(
                                f"{self.account} · {self.printer or '默认打印机'}",
                                size=12,
                                color=ft.Colors.WHITE_70,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    ft.Container(
                        content=ft.Text(f"队列 {self._active_count()}", size=12, color=ft.Colors.WHITE),
                        bgcolor="#33FFFFFF",
                        border_radius=20,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            padding=18,
            border_radius=18,
            gradient=ft.LinearGradient(colors=[_BRAND_DARK, _BRAND, "#0F8B8D"]),
        )

        body = ft.Column(
            [
                header,
                self._file_card(),
                self._options_card(),
                ft.FilledButton(
                    "立即打印",
                    icon=ft.Icons.PRINT,
                    height=52,
                    bgcolor=_BRAND,
                    color=ft.Colors.WHITE,
                    disabled=self.busy or self.file_path is None,
                    on_click=lambda _e: self.page.run_task(self._print),
                ),
                self._stats_card(),
                self._tasks_card(),
            ],
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        content = ft.Column(
            [appbar, ft.Container(content=body, padding=ft.Padding.symmetric(horizontal=16, vertical=8), expand=True)],
            spacing=0,
            expand=True,
        )
        self.page.controls.clear()
        self.page.add(content)
        self.page.update()

    def _active_count(self) -> int:
        """统计队列中的任务数。"""
        return sum(1 for t in self.tasks if t.get("status") in ("queued", "printing", "review"))

    def _file_card(self) -> ft.Control:
        """待打印文件卡片。"""
        if self.file_path:
            file_row: ft.Control = ft.Row(
                [
                    ft.Container(
                        content=ft.Icon(ft.Icons.INSERT_DRIVE_FILE, color=_BRAND, size=26),
                        bgcolor="#1A16537E",
                        width=48,
                        height=48,
                        border_radius=14,
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Column(
                        [
                            ft.Text(self.file_name, size=14, weight=ft.FontWeight.W_600, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(f"{self._size_text(self.file_size)}", size=12, color=ft.Colors.GREY_600),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.IconButton(ft.Icons.CLOSE, tooltip="移除", on_click=lambda _e: self._clear_file()),
                ],
                spacing=12,
            )
        else:
            file_row = ft.Container(
                content=ft.Column(
                    [
                        ft.Text("尚未选择文件", size=15, weight=ft.FontWeight.W_500),
                        ft.Text("点击右上角「选择文件」从「文件」应用导入", size=12, color=ft.Colors.GREY_600),
                    ],
                    spacing=4,
                ),
                bgcolor="#F2F5F8",
                border_radius=14,
                padding=16,
            )

        return self._card(
            ft.Column(
                [
                    ft.Row(
                        [
                            self._section_title("待打印文件", "支持 PDF、Office、WPS、图片与文本"),
                            ft.TextButton("选择文件", icon=ft.Icons.FOLDER_OPEN, on_click=lambda _e: self.page.run_task(self._pick_file)),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    file_row,
                ],
                spacing=12,
            )
        )

    def _options_card(self) -> ft.Control:
        """打印参数卡片。"""
        printer_options = [
            ft.dropdown.Option(key=item.get("name") or "", text=(item.get("name") or "") + ("（默认）" if item.get("is_default") else ""))
            for item in self.printers
        ]
        paper_options = [ft.dropdown.Option(key=name, text=text) for name, text in self.papers]

        controls: List[ft.Control] = [
            self._section_title("打印设置", "参数与服务端网页端完全一致"),
            ft.Dropdown(
                label="打印机",
                value=self.printer,
                options=printer_options,
                border_radius=12,
                on_select=self._on_printer_change,
            ),
            ft.Row(
                [
                    ft.Text("份数", size=13, color=ft.Colors.GREY_800),
                    ft.Row(
                        [
                            ft.IconButton(ft.Icons.REMOVE, on_click=lambda _e: self._change_copies(-1), disabled=self.copies <= 1),
                            ft.Text(f"{self.copies} 份", size=15),
                            ft.IconButton(ft.Icons.ADD, on_click=lambda _e: self._change_copies(1), disabled=self.copies >= 99),
                        ],
                        spacing=0,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Text("单双面" if self.duplex_supported else "单双面（打印机不支持双面）", size=13, color=ft.Colors.GREY_800),
            ft.SegmentedButton(
                segments=[
                    ft.Segment(value="simplex", label=ft.Text("单面")),
                    ft.Segment(value="vertical", label=ft.Text("双面")),
                ],
                selected={self.duplex},
                on_change=self._on_duplex_change,
            ),
            ft.Dropdown(label="纸张", value=self.paper, options=paper_options, border_radius=12, on_select=self._on_paper_change),
            ft.TextField(
                label="页码范围",
                hint_text="留空表示全部，如 1-3,5",
                value=self.page_range,
                border_radius=12,
                on_change=lambda e: setattr(self, "page_range", e.control.value or ""),
            ),
            ft.Text("缩放", size=13, color=ft.Colors.GREY_800),
            ft.SegmentedButton(
                segments=[
                    ft.Segment(value="fit", label=ft.Text("适应页面")),
                    ft.Segment(value="actual", label=ft.Text("实际大小")),
                    ft.Segment(value="custom", label=ft.Text("自定义")),
                ],
                selected={self.scale},
                on_change=self._on_scale_change,
            ),
        ]

        if self.scale == "custom":
            controls.append(
                ft.Row(
                    [
                        ft.Text(f"{self.scale_percent}%", size=13),
                        ft.Slider(
                            min=10,
                            max=400,
                            divisions=39,
                            value=self.scale_percent,
                            label="{value}%",
                            on_change=self._on_percent_change,
                            expand=True,
                        ),
                    ],
                    spacing=10,
                )
            )

        controls.extend(
            [
                ft.Text("颜色模式", size=13, color=ft.Colors.GREY_800),
                ft.SegmentedButton(
                    segments=[
                        ft.Segment(value="color", label=ft.Text("彩色")),
                        ft.Segment(value="mono", label=ft.Text("黑白")),
                    ],
                    selected={self.color},
                    on_change=self._on_color_change,
                ),
                ft.Text("打印质量", size=13, color=ft.Colors.GREY_800),
                ft.SegmentedButton(
                    segments=[
                        ft.Segment(value="draft", label=ft.Text("草稿")),
                        ft.Segment(value="normal", label=ft.Text("标准")),
                        ft.Segment(value="high", label=ft.Text("高清")),
                    ],
                    selected={self.quality},
                    on_change=self._on_quality_change,
                ),
                ft.Text("出纸方向", size=13, color=ft.Colors.GREY_800),
                ft.SegmentedButton(
                    segments=[
                        ft.Segment(value="auto", label=ft.Text("自动")),
                        ft.Segment(value="portrait", label=ft.Text("纵向")),
                        ft.Segment(value="landscape", label=ft.Text("横向")),
                    ],
                    selected={self.orientation},
                    on_change=self._on_orientation_change,
                ),
                ft.Switch(
                    label="表格适应一页宽（仅 Windows 办公文档）",
                    value=self.fit_table,
                    on_change=lambda e: setattr(self, "fit_table", bool(e.control.value)),
                ),
            ]
        )

        return self._card(ft.Column(controls, spacing=10))

    def _stats_card(self) -> ft.Control:
        """打印统计卡片（非管理员或接口不可用时返回空容器）。"""
        if not self.stats:
            return ft.Container(visible=False)
        windows = self.stats.get("windows") or {}
        cells: List[ft.Control] = []
        for key in ("today", "week", "month"):
            item = windows.get(key) or {}
            cells.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(str(item.get("label") or key), size=11, color=ft.Colors.GREY_600),
                            ft.Text(str(item.get("pages") or 0), size=20, weight=ft.FontWeight.W_600),
                            ft.Text(f"{item.get('sheets') or 0} 张纸 · {item.get('copies') or 0} 份", size=11, color=ft.Colors.GREY_600),
                        ],
                        spacing=2,
                    ),
                    bgcolor="#F2F5F8",
                    border_radius=14,
                    padding=12,
                    expand=True,
                )
            )
        return self._card(
            ft.Column(
                [
                    self._section_title("打印统计", "只统计已完成任务，纸张数按单双面估算"),
                    ft.Row(cells, spacing=10),
                ],
                spacing=12,
            )
        )

    def _tasks_card(self) -> ft.Control:
        """最近任务卡片。"""
        if not self.tasks:
            body: ft.Control = ft.Text("暂无打印任务", size=13, color=ft.Colors.GREY_600)
        else:
            rows: List[ft.Control] = []
            for task in self.tasks[:8]:
                rows.append(self._task_row(task))
            body = ft.Column(rows, spacing=10)
        return self._card(
            ft.Column(
                [
                    ft.Row(
                        [
                            self._section_title("最近任务", "状态每 5 秒自动刷新"),
                            ft.TextButton("刷新", on_click=lambda _e: self.page.run_task(self._refresh_all)),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    body,
                ],
                spacing=12,
            )
        )

    def _task_row(self, task: Dict[str, Any]) -> ft.Control:
        """单条任务。"""
        status = str(task.get("status") or "")
        color = {
            "done": ft.Colors.GREEN_700,
            "printing": _BRAND,
            "failed": ft.Colors.RED_600,
            "review": ft.Colors.ORANGE_800,
        }.get(status, ft.Colors.GREY_700)

        actions: List[ft.Control] = []
        if status in ("queued", "review"):
            actions.append(ft.TextButton("取消", on_click=lambda _e, tid=task.get("id"): self.page.run_task(self._task_action, tid, "cancel")))
        elif status in ("failed", "canceled"):
            actions.append(ft.TextButton("重试", on_click=lambda _e, tid=task.get("id"): self.page.run_task(self._task_action, tid, "retry")))

        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(str(task.get("filename") or ""), size=13, weight=ft.FontWeight.W_500, expand=True, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Container(
                            content=ft.Text(str(task.get("status_text") or status), size=11, color=color),
                            bgcolor="#0F000000",
                            border_radius=20,
                            padding=ft.Padding.symmetric(horizontal=10, vertical=3),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Text(
                    " · ".join(
                        part for part in [str(task.get("size_text") or ""), str(task.get("params_text") or "")] if part
                    ),
                    size=11,
                    color=ft.Colors.GREY_600,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    max_lines=2,
                ),
                ft.Row(actions, spacing=0) if actions else ft.Container(visible=False),
            ],
            spacing=3,
        )

    # ------------------------------------------------------------ 交互
    async def _pick_file(self) -> None:
        """调用系统文件选择器。"""
        files = await self.picker.pick_files(
            dialog_title="选择要打印的文件",
            allow_multiple=False,
        )
        if not files:
            return
        picked = files[0]
        path = getattr(picked, "path", None)
        if not path:
            self._toast("无法读取所选文件")
            return
        self.file_path = str(path)
        self.file_name = Path(path).name
        try:
            self.file_size = Path(path).stat().st_size
        except OSError:
            self.file_size = 0
        self.show_home()

    def _clear_file(self) -> None:
        """清除待打印文件。"""
        self.file_path = None
        self.file_name = ""
        self.file_size = 0
        self.show_home()

    def _on_printer_change(self, event: Any) -> None:
        """切换打印机并刷新能力。"""
        self.printer = event.control.value or ""

        async def reload() -> None:
            await asyncio.to_thread(self._load_caps)
            self.show_home()

        self.page.run_task(reload)

    def _on_duplex_change(self, event: Any) -> None:
        """切换单双面。"""
        selected = event.control.selected
        self.duplex = list(selected)[0] if selected else "simplex"

    def _on_paper_change(self, event: Any) -> None:
        """切换纸张。"""
        self.paper = event.control.value or "A4"

    def _on_scale_change(self, event: Any) -> None:
        """切换缩放方式（自定义时显示滑杆）。"""
        selected = event.control.selected
        self.scale = list(selected)[0] if selected else "fit"
        self.show_home()

    def _on_percent_change(self, event: Any) -> None:
        """调整自定义缩放百分比。"""
        self.scale_percent = int(event.control.value or 100)

    def _on_color_change(self, event: Any) -> None:
        """切换颜色模式。"""
        selected = event.control.selected
        self.color = list(selected)[0] if selected else "color"

    def _on_quality_change(self, event: Any) -> None:
        """切换打印质量。"""
        selected = event.control.selected
        self.quality = list(selected)[0] if selected else "normal"

    def _on_orientation_change(self, event: Any) -> None:
        """切换出纸方向。"""
        selected = event.control.selected
        self.orientation = list(selected)[0] if selected else "auto"

    def _change_copies(self, delta: int) -> None:
        """调整份数。"""
        self.copies = max(1, min(99, self.copies + delta))
        self.show_home()

    async def _refresh_all(self) -> None:
        """刷新打印机、能力、统计与任务。"""
        try:
            await asyncio.to_thread(self._load_printers)
            await asyncio.to_thread(self._load_stats)
            await asyncio.to_thread(self._load_tasks)
        except ApiError as exc:
            self._toast(str(exc))
        self.show_home()

    async def _print(self) -> None:
        """提交打印。"""
        if not self.file_path:
            return
        page_range = (self.page_range or "").strip()
        if page_range and not self._valid_range(page_range):
            self._toast("页码范围格式不正确，示例：1-3,5")
            return
        self.busy = True
        self.show_home()
        fields = {
            "printer": self.printer,
            "copies": str(self.copies),
            "duplex": self.duplex if self.duplex_supported else "simplex",
            "paper": self.paper,
            "page_range": page_range,
            "scale": self.scale,
            "scale_percent": str(self.scale_percent if self.scale == "custom" else 100),
            "color": self.color,
            "quality": self.quality,
            "orientation": self.orientation,
            "fit_table": "1" if self.fit_table else "0",
        }
        try:
            result = await asyncio.to_thread(self.api.upload, self.file_path, self.file_name, fields)
        except ApiError as exc:
            self.busy = False
            self.show_home()
            self._toast(str(exc))
            return
        self.busy = False
        created = result.get("tasks") or []
        rejected = result.get("rejected") or []
        if created:
            self.file_path = None
            self.file_name = ""
            self.file_size = 0
            self._toast(f"已提交 {len(created)} 个打印任务")
        if rejected:
            reason = (rejected[0] or {}).get("reason") or "文件未被接受"
            self._toast(f"{len(rejected)} 个文件未提交：{reason}")
        await asyncio.to_thread(self._load_tasks)
        self.show_home()

    async def _task_action(self, task_id: Any, action: str) -> None:
        """取消或重试任务。"""
        try:
            message = await asyncio.to_thread(self.api.action, f"/api/tasks/{task_id}/{action}")
            self._toast(message)
        except ApiError as exc:
            self._toast(str(exc))
        await asyncio.to_thread(self._load_tasks)
        self.show_home()

    def logout(self) -> None:
        """退出登录并清除保存的密码。"""
        self.store.save({"password": "", "auto_login": False})
        self.logged_in = False
        self.file_path = None
        self.file_name = ""
        self.tasks = []
        self.stats = None
        self.show_login()

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _size_text(size: int) -> str:
        """人类可读的文件大小。"""
        if size >= 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    @staticmethod
    def _valid_range(text: str) -> bool:
        """校验页码范围，与服务端规则一致。"""
        normalized = text.replace("，", ",").replace("、", ",").replace(" ", "")
        import re

        if not re.fullmatch(r"[0-9,\-]+", normalized):
            return False
        parts = [p for p in normalized.split(",") if p]
        if not parts:
            return False
        for part in parts:
            pieces = part.split("-")
            if len(pieces) > 2:
                return False
            if any(piece and not piece.isdigit() for piece in pieces):
                return False
            if len(pieces) == 2 and all(pieces):
                start, end = int(pieces[0]), int(pieces[1])
                if start <= 0 or end <= 0 or start > end:
                    return False
        return True


def main(page: ft.Page) -> None:
    """Flet 入口。"""
    app = ClientApp(page)
    app.start()


if __name__ == "__main__":
    ft.run(main)
