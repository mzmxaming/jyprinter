# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：生成单文件 jyprinter.exe。

打包命令（在项目根目录执行）：
    .venv38\\Scripts\\python.exe -m PyInstaller --clean --noconfirm jyprinter.spec

说明：
    · 模板与静态资源通过 datas 打入包内，运行时由 Flask 从 _MEIPASS 读取；
    · 打包前会先做一次源码语法自检（见下方 _check_sources）：PyInstaller 遇到有语法
      错误的模块只写一条警告并把该模块跳过，**退出码仍是 0**，结果是 exe 能生成、
      启动却报 ImportError —— 这种包必须挡住。
"""

from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821 - PyInstaller 注入


def _check_sources(root: Path) -> None:
    """打包前校验源文件语法，存在语法错误时立即中止打包。

    参数：
        root - 项目根目录
    返回：
        None
    异常：
        SystemExit - 存在语法错误，异常信息中列出全部出错文件与原因
    """
    targets = list((root / "core").rglob("*.py"))
    targets += list((root / "web").rglob("*.py"))
    targets.append(root / "main.py")
    bad = []
    for path in targets:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            bad.append("%s 第 %s 行：%s" % (path.relative_to(root), exc.lineno, exc.msg))
    if bad:
        raise SystemExit("源文件存在语法错误，已中止打包：\n  " + "\n  ".join(bad))


_check_sources(ROOT)

datas = [
    (str(ROOT / "web" / "templates"), "web/templates"),
    (str(ROOT / "web" / "static"), "web/static"),
    # 图标随包内置（运行时可被程序目录下的同名文件覆盖）
    (str(ROOT / "assets" / "jyprinter.ico"), "assets"),
]

hiddenimports = [
    # pywin32 动态导入部分需要显式声明
    "win32com.client",
    "win32com.shell",
    "pythoncom",
    "pywintypes",
    "win32print",
    "win32ui",
    "win32gui",
    "win32api",
    "win32con",
    "win32clipboard",
    # win32timezone 由 pywintypes 在「把打印队列作业的时间字段转成带时区的 datetime」时
    # **动态导入**（C 层发起），PyInstaller 静态分析看不到它；缺了它会让打印成功后
    # 查询队列（EnumJobs / GetJob）抛 ModuleNotFoundError，任务被误判为失败
    "win32timezone",
    # 托盘与图像、二维码
    "pystray._win32",
    "PIL._imaging",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "PIL.Image",
    "qrcode",
    # Web 框架中被动态加载的中间件
    "werkzeug.middleware.proxy_fix",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest", "test", "setuptools", "pip"],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="jyprinter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # 2026-09-28：发布版打包为「无控制台程序」——双击运行不会出现 CMD 窗口，也不占任务栏。
    # 启动信息与告警由 main._redirect_output_to_file() 写入 data/logs/console.log；
    # 需要看实时输出时用源码方式运行（python main.py）即可。
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "jyprinter.ico"),
    version=str(ROOT / "assets" / "version_info.txt"),
)
