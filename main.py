"""jyprinter 程序入口：初始化配置与日志 → 启动网页服务 → 托盘常驻（含健康检测与自动重启）。

启动方式：
    python main.py              以托盘方式后台常驻（推荐，无窗口）
    python main.py --no-tray    前台运行，便于调试查看日志
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from typing import Any, Dict, Optional

from werkzeug.serving import make_server

from core import audit, converter, engines, osinfo, printers, settings, system
from core.tasks import TaskQueue
from core.utils import get_all_local_ips, get_local_ip
from web.server import create_app

# 健康检测间隔（秒）
WATCH_INTERVAL = 30


def _configure_stdout() -> None:
    """把标准输出切换为 UTF-8 行缓冲，避免中文乱码与日志不刷新。

    参数：无
    返回：
        None
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            continue


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。

    参数：无
    返回：
        argparse.Namespace，包含 host / port / no_tray
    """
    parser = argparse.ArgumentParser(description="网络打印服务（后台托盘常驻）")
    parser.add_argument("--host", default=None, help="监听地址，留空读取 config.json（会写入配置）")
    parser.add_argument("--port", type=int, default=None, help="服务端口，留空读取 config.json（会写入配置）")
    parser.add_argument("--no-tray", action="store_true", help="不启用托盘，前台运行（调试用）")
    parser.add_argument(
        "--hide-console",
        action="store_true",
        help="源码运行时也自动隐藏控制台（打包运行默认已隐藏）",
    )
    return parser.parse_args()


def _print_banner(url: str, restarts: int = 0) -> None:
    """打印启动信息。

    参数：
        url      - 首选访问地址
        restarts - 已发生的自动重启次数
    返回：
        None
    """
    default_printer = printers.get_default_printer() or "未设置"
    lines = [
        "=" * 62,
        f"  {settings.PRODUCT_NAME}已启动",
        "=" * 62,
        f"  访问地址   : {url}",
        "  备用地址   : " + "  ".join(get_all_local_ips()),
        f"  运行平台   : {osinfo.platform_name()}（{osinfo.platform_tag()}）",
        f"  默认打印机 : {default_printer}",
        f"  支持格式   : 共 {len(engines.SUPPORTED_EXTS)} 种 "
        + " ".join(sorted(f".{ext}" for ext in engines.SUPPORTED_EXTS)),
        f"  管理员账号 : admin（初始密码 admin123，登录后请立即修改）",
        f"  上传目录   : {settings.UPLOAD_DIR}",
        f"  日志文件   : {audit.LOG_FILE}",
        "  运行权限   : " + ("管理员/root" if system.is_admin() else "普通用户（部分功能如网络配置不可用）"),
        "-" * 62,
        f"  {settings.DEVELOPER_LINE}",
        "-" * 62,
        "  在其他设备浏览器中打开上面的访问地址即可上传并打印。",
        (
            "  服务在托盘图标中常驻，右键图标可打开页面、配置网络或退出程序。"
            if osinfo.IS_WINDOWS
            else "  服务在后台常驻；查看日志：journalctl -u jyprinter -f"
        ),
        "=" * 62,
    ]
    if engines.PENDING_EXTS:
        lines.insert(
            8,
            "  待支持格式 : " + " ".join(sorted(f".{ext}" for ext in engines.PENDING_EXTS)),
        )
    if restarts:
        lines.append(f"  （已自动重启 {restarts} 次）")
    print("\n".join(lines), flush=True)

    if osinfo.IS_LINUX:
        if not printers.cups_available():
            print(
                "[警告] 未检测到 CUPS 打印组件（lp/lpstat），网页可打开但无法提交打印任务。\n"
                "       安装：sudo apt install cups cups-client（银河麒麟/Debian 系）"
                "或 sudo yum install cups（RPM 系）",
                flush=True,
            )
        elif not converter.probe_availability()["available_exts"]:
            print(
                "[提示] 未检测到 LibreOffice，Office/WPS 文档（doc/docx/xls/xlsx/ppt/pptx/wps/et/dps）"
                "将无法打印，其余 11 种格式不受影响。\n"
                "       安装：sudo apt install libreoffice",
                flush=True,
            )


def _autohide_console(delay: float = 3.0) -> None:
    """启动后延时自动隐藏控制台窗口。

    在等待期间若检测到按键，则保留控制台显示（便于查看启动信息或排查问题）。
    隐藏后可通过托盘菜单的「显示控制台」重新打开。

    参数：
        delay - 自动隐藏前的等待秒数
    返回：
        None
    """
    try:
        import msvcrt
    except ImportError:
        system.hide_console()
        return

    deadline = time.time() + delay
    while time.time() < deadline:
        try:
            if msvcrt.kbhit():
                msvcrt.getch()
                print("[提示] 检测到按键，保留控制台；可在托盘菜单中随时隐藏", flush=True)
                return
        except (OSError, ValueError):
            break
        time.sleep(0.15)

    ok, message = system.hide_console()
    if ok:
        print("[提示] 控制台已自动隐藏，可在托盘菜单「显示控制台」中重新打开", flush=True)
        time.sleep(0.3)
    else:
        print(f"[提示] 未能自动隐藏控制台：{message}", flush=True)


class ServiceSupervisor:
    """管理 HTTP 服务与打印队列的启动、健康检测与自动重启。"""

    def __init__(self, host: str, port: int, app: Any, task_queue: TaskQueue) -> None:
        """初始化监督器。

        参数：
            host       - 监听地址
            port       - 监听端口
            app        - Flask 应用
            task_queue - 打印任务队列
        返回：
            None
        """
        self._host = host
        self._port = port
        self._app = app
        self._queue = task_queue
        self._server: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._watchdog: Optional[threading.Thread] = None
        self._running = False
        self.restarts = 0

    @property
    def port(self) -> int:
        """返回当前监听端口。

        参数：无
        返回：
            int，端口号
        """
        return self._port

    def start(self) -> None:
        """启动 HTTP 服务与健康检测线程。

        参数：无
        返回：
            None
        异常：
            OSError - 端口被占用等无法监听的情况
        """
        self._running = True
        self._start_server()
        self._watchdog = threading.Thread(
            target=self._watch_loop, name="health-watchdog", daemon=True
        )
        self._watchdog.start()

    def _start_server(self) -> None:
        """创建并启动 HTTP 服务线程。

        参数：无
        返回：
            None
        """
        self._server = make_server(self._host, self._port, self._app, threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="http-server", daemon=True
        )
        self._thread.start()

    def _watch_loop(self) -> None:
        """健康检测循环：定期检查 HTTP 线程与打印队列线程。

        参数：无
        返回：
            None
        """
        while self._running:
            time.sleep(WATCH_INTERVAL)
            if not self._running:
                return
            try:
                self._check_once()
            except Exception:
                # 健康检测本身出错不应导致进程退出
                continue

    def _check_once(self) -> None:
        """执行一次健康检查，必要时重启异常组件。

        参数：无
        返回：
            None
        """
        if self._thread is None or not self._thread.is_alive():
            try:
                if self._server is not None:
                    # 先请求优雅停止（旧线程仍存活时可干净退出），再释放监听套接字
                    self._server.shutdown()
                    self._server.server_close()
            except Exception:
                pass
            self._start_server()
            self.restarts += 1
            print("[健康检测] HTTP 服务异常，已自动重启", flush=True)
            return

        if not self._queue.healthy() and self._queue.restart_worker():
            self.restarts += 1
            print("[健康检测] 打印队列线程异常，已自动重启", flush=True)

    def stop(self) -> None:
        """停止服务与队列。

        参数：无
        返回：
            None
        """
        self._running = False
        self._queue.stop()
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass


def _install_console_handler(supervisor: "ServiceSupervisor") -> None:
    """注册控制台事件处理，保证窗口被关闭时进程立即干净退出。

    未处理时：关闭控制台窗口 / 注销 / 关机只会终止外层引导进程，
    可能留下仍在后台运行（托盘图标还在）的残留实例，表现为「关不掉」。
    这里主动响应这些事件并立即退出。

    参数：
        supervisor - 服务监督器（用于先停止服务与队列）
    返回：
        None
    """
    try:
        import win32api
        import win32con

        events = {
            getattr(win32con, "CTRL_CLOSE_EVENT", 2),
            getattr(win32con, "CTRL_LOGOFF_EVENT", 5),
            getattr(win32con, "CTRL_SHUTDOWN_EVENT", 6),
            getattr(win32con, "CTRL_C_EVENT", 0),
            getattr(win32con, "CTRL_BREAK_EVENT", 1),
        }

        def handler(event: int) -> bool:
            """控制台事件回调：先停服务再立即退出。

            参数：
                event - 控制台事件编号
            返回：
                bool，始终返回 False 表示继续交给系统默认处理
            """
            if event in events:
                try:
                    supervisor.stop()
                except Exception:
                    pass
                os._exit(0)
            return False

        win32api.SetConsoleCtrlHandler(handler, True)
    except Exception:
        # 注册失败不影响主流程，仅失去「关闭控制台即退出」的能力
        pass


def _tray_image() -> Any:
    """加载托盘图标（优先使用 assets/jyprinter.ico，缺失时回退为内置绘制）。

    参数：无
    返回：
        PIL.Image，托盘图标
    """
    path = settings.icon_path()
    if path is not None:
        try:
            from PIL import Image

            return Image.open(path)
        except Exception:
            pass
    return _drawn_tray_image()


def _drawn_tray_image() -> Any:
    """内置绘制托盘图标（无图标文件时的兜底方案）。

    参数：无
    返回：
        PIL.Image，64x64 的 RGBA 图标
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # 打印机机身
    draw.rounded_rectangle([6, 22, 58, 48], radius=6, fill=(47, 111, 237, 255))
    # 进纸（上方白纸）
    draw.rectangle([16, 8, 48, 23], fill=(255, 255, 255, 255), outline=(47, 111, 237, 255), width=2)
    # 出纸（下方深色纸张）
    draw.rectangle([14, 34, 50, 58], fill=(31, 36, 48, 255))
    draw.rectangle([22, 40, 42, 54], fill=(255, 255, 255, 255))
    return image


def _apply_window_icon() -> None:
    """把程序图标应用到控制台窗口（标题栏与任务栏）。

    参数：无
    返回：
        None
    """
    path = settings.icon_path()
    if path is None:
        return
    try:
        import ctypes

        import win32con
        import win32gui

        handle = ctypes.windll.kernel32.GetConsoleWindow()
        if not handle:
            return
        icon = win32gui.LoadImage(
            0, str(path), win32con.IMAGE_ICON, 0, 0, win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE
        )
        if not icon:
            return
        win32gui.SendMessage(handle, win32con.WM_SETICON, win32con.ICON_SMALL, icon)
        win32gui.SendMessage(handle, win32con.WM_SETICON, win32con.ICON_BIG, icon)
        try:
            ctypes.windll.kernel32.SetConsoleIcon(icon)
        except Exception:
            pass
    except Exception:
        # 图标设置失败不影响主流程（例如无控制台窗口时）
        pass


def _run_tray(url: str, supervisor: ServiceSupervisor) -> None:
    """创建并运行托盘图标及菜单（阻塞直到用户选择退出）。

    参数：
        url        - 打开页面时使用的访问地址
        supervisor - 服务监督器（用于退出与状态展示）
    返回：
        None
    异常：
        ImportError - 未安装 pystray
    """
    import pystray
    from pystray import Menu, MenuItem

    def notify(ok: bool, message: str) -> None:
        """以弹窗（或控制台）方式反馈操作结果。

        参数：
            ok      - 是否成功
            message - 说明文本
        返回：
            None
        """
        print(("[成功] " if ok else "[失败] ") + message, flush=True)
        system.info(settings.PRODUCT_NAME, message)

    def ui(action: Any) -> Any:
        """把带界面的菜单动作放到独立线程执行。

        托盘菜单回调运行在 pystray 的消息循环（TrackPopupMenu）内部，
        若在其中再嵌套 tkinter 主循环，部分系统下弹窗会出现无法关闭的情况；
        因此这里先让菜单正常收起，再在独立线程里执行动作。

        参数：
            action - 无参可调用对象
        返回：
            供菜单使用的回调函数
        """

        def runner() -> None:
            try:
                action()
            except Exception as exc:
                print(f"[提示] 操作失败：{exc}", flush=True)
                system.info(settings.PRODUCT_NAME, f"操作失败：{exc}")

        return lambda *_: threading.Thread(target=runner, daemon=True).start()

    def on_open_page() -> None:
        """在浏览器中打开打印页面。"""
        import webbrowser

        webbrowser.open(url)

    def on_copy_url() -> None:
        """复制访问地址到剪贴板。"""
        ok = system.clipboard_copy(url)
        notify(ok, "访问地址已复制到剪贴板" if ok else "复制失败，请手动记录地址")

    def on_show_network() -> None:
        """展示当前网络配置。"""
        notify(True, "当前网络配置：\n" + system.network_summary())

    def on_set_static() -> None:
        """引导用户把某块网卡设置为静态 IP。"""
        adapters = system.list_adapters()
        if not adapters:
            notify(False, "未读取到网络适配器信息")
            return
        options = [
            (item["name"], f"{item['name']}（当前 {item.get('ip') or '无地址'}）")
            for item in adapters
        ]
        adapter = system.ask_choice("设置静态 IP", "请选择要设置的网络适配器：", options)
        if not adapter:
            notify(False, "已取消（或无图形界面，请使用 config.json 与系统网络设置）")
            return
        current = next((item for item in adapters if item["name"] == adapter), {})
        form = system.ask_form(
            "设置静态 IP",
            [("ip", "IP 地址"), ("mask", "子网掩码"), ("gateway", "默认网关（可留空）")],
            {
                "ip": (current.get("ip") or "").split(" ")[0],
                "mask": "255.255.255.0",
                "gateway": current.get("gateway") or "",
            },
        )
        if not form:
            return
        confirm = system.ask_choice(
            "确认修改",
            f"将把「{adapter}」设置为静态地址 {form.get('ip')}，\n修改期间网络会短暂中断，确认继续？",
            [("yes", "确认修改"), ("no", "取消")],
        )
        if confirm != "yes":
            return
        ok, message = system.set_static_ip(
            adapter, form.get("ip", ""), form.get("mask", ""), form.get("gateway", "")
        )
        notify(ok, message)

    def on_dhcp() -> None:
        """把某块网卡恢复为自动获取 IP。"""
        adapters = system.list_adapters()
        if not adapters:
            notify(False, "未读取到网络适配器信息")
            return
        adapter = system.ask_choice(
            "恢复自动获取 IP",
            "请选择要改为自动获取 IP（DHCP）的网络适配器：",
            [(item["name"], f"{item['name']}（当前 {item.get('ip') or '无地址'}）") for item in adapters],
        )
        if not adapter:
            return
        ok, message = system.enable_dhcp(adapter)
        notify(ok, message)

    def on_change_port() -> None:
        """修改服务端口并写入配置（重启后生效）。"""
        form = system.ask_form(
            "修改端口",
            [("port", "新的服务端口（1024-65535）")],
            {"port": str(supervisor.port)},
        )
        if not form:
            return
        try:
            port = int(form.get("port", ""))
        except ValueError:
            notify(False, "端口必须是数字")
            return
        if not 1024 <= port <= 65535:
            notify(False, "端口范围应为 1024-65535")
            return
        settings.update({"port": port})
        notify(True, f"端口已保存为 {port}，请退出并重新启动程序后生效")

    def on_toggle_autostart() -> None:
        """切换开机自启。"""
        ok, message = system.set_autostart(not system.autostart_enabled())
        notify(ok, message)

    def on_toggle_console() -> None:
        """显示或隐藏控制台窗口。"""
        ok, message = system.toggle_console()
        notify(ok, message)

    def on_open_log() -> None:
        """打开打印日志文件。"""
        ok, message = system.open_path(str(audit.LOG_FILE))
        notify(ok, message)

    def on_about() -> None:
        """展示程序与开发者信息。"""
        lines = [
            settings.PRODUCT_NAME,
            "",
            settings.DEVELOPER_LINE,
            "",
            f"版本：{settings.VERSION}",
            f"运行平台：{osinfo.platform_name()}",
            f"访问地址：{url}",
            f"支持格式：共 {len(engines.SUPPORTED_EXTS)} 种",
            f"数据目录：{settings.DATA_DIR}",
            "",
            "界面图标：Material Design Icons（Apache-2.0）",
        ]
        if osinfo.IS_LINUX:
            lines.insert(-2, f"开机启动：{'已开启' if system.autostart_enabled() else '未开启'}")
        system.info(f"关于 {settings.PRODUCT_NAME}", "\n".join(lines))

    def on_quit() -> None:
        """退出程序。"""
        print("[提示] 正在退出，等待服务关闭……", flush=True)
        supervisor.stop()
        icon.stop()

    menu_items = [
        MenuItem("打开打印页面", ui(on_open_page), default=True),
        MenuItem(f"访问地址：{url}", None, enabled=False),
        MenuItem("复制访问地址", ui(on_copy_url)),
        Menu.SEPARATOR,
        MenuItem(
            "网络配置",
            Menu(
                MenuItem("查看当前网络配置", ui(on_show_network)),
                MenuItem("设置静态 IP…", ui(on_set_static)),
                MenuItem("恢复自动获取 IP (DHCP)", ui(on_dhcp)),
                MenuItem(
                    "打开系统网络设置",
                    ui(lambda: notify(*system.open_network_panel())),
                ),
            ),
        ),
        MenuItem("修改服务端口…", ui(on_change_port)),
        MenuItem(
            lambda item: "关闭开机自启" if system.autostart_enabled() else "开启开机自启",
            ui(on_toggle_autostart),
        ),
    ]
    if osinfo.IS_WINDOWS:
        menu_items.append(
            MenuItem(
                lambda item: "隐藏控制台" if system.console_visible() else "显示控制台",
                ui(on_toggle_console),
            )
        )
    menu_items += [
        MenuItem("打开打印日志", ui(on_open_log)),
        MenuItem("关于", ui(on_about)),
        Menu.SEPARATOR,
        MenuItem("退出", on_quit),
    ]

    icon = pystray.Icon(
        "jyprinter",
        _tray_image(),
        settings.PRODUCT_NAME,
        menu=Menu(*menu_items),
    )
    icon.run()


def main() -> int:
    """程序主入口。

    参数：无
    返回：
        int，进程退出码（0 正常，1 启动失败）
    """
    args = _parse_args()
    _configure_stdout()
    settings.ensure_dirs()

    patch: Dict[str, Any] = {}
    if args.host:
        patch["host"] = args.host
    if args.port:
        patch["port"] = int(args.port)
    if patch:
        settings.update(patch)

    config = settings.load()
    if not settings.CONFIG_PATH.exists():
        # 首次运行生成配置文件，便于用户直接编辑
        settings.save()
    host = str(config.get("host", "0.0.0.0"))
    port = int(config.get("port", 5000))

    audit.cleanup(int(config.get("log_keep_days", 7)))

    task_queue = TaskQueue()
    task_queue.start()

    app = create_app(task_queue)
    supervisor = ServiceSupervisor(host, port, app, task_queue)
    try:
        supervisor.start()
    except OSError as exc:
        print(f"[错误] 无法在 {host}:{port} 启动服务：{exc}")
        print("        若提示端口被占用，说明可能已有另一个实例在运行：")
        print("        请右键托盘图标选择「退出」，或修改 config.json 中的 port 后重试。")
        task_queue.stop()
        return 1

    if osinfo.IS_WINDOWS:
        _install_console_handler(supervisor)

    url = f"http://{get_local_ip()}:{port}"
    _print_banner(url, supervisor.restarts)
    if osinfo.IS_WINDOWS:
        _apply_window_icon()

    # 托盘模式下启动后自动隐藏控制台：打包运行默认隐藏；源码运行需加 --hide-console
    # （直接在终端里跑 python main.py 时不隐藏，避免连带隐藏用户的终端窗口；Linux 无独立控制台）
    should_hide_console = osinfo.IS_WINDOWS and not args.no_tray and (
        args.hide_console
        or (
            getattr(sys, "frozen", False)
            and bool(config.get("hide_console_on_start", True))
        )
    )
    if should_hide_console:
        _autohide_console()

    # Linux 无图形会话（如 systemd 服务）时自动降级为纯后台运行
    headless = args.no_tray or (
        osinfo.IS_LINUX
        and not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
    )
    if headless and not args.no_tray:
        print(
            "[提示] 当前无图形会话（未检测到 DISPLAY），已按后台服务方式运行；"
            "桌面登录环境下启动可显示托盘图标。",
            flush=True,
        )

    try:
        if headless:
            while True:
                time.sleep(1)
        else:
            try:
                _run_tray(url, supervisor)
            except ImportError:
                print("[提示] 未安装 pystray，改为后台运行（安装命令：pip install pystray）")
                while True:
                    time.sleep(1)
            except Exception as exc:
                # 托盘不可用（缺少 GTK/Xorg 后端等）不应影响打印服务本身
                print(f"[提示] 托盘不可用（{exc}），已改为后台运行。")
                while True:
                    time.sleep(1)
    except KeyboardInterrupt:
        print("\n[提示] 收到退出信号，正在关闭服务……")
    finally:
        supervisor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
