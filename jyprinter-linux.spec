# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Linux / 银河麒麟）：生成单文件可执行程序 jyprinter。

打包命令（在项目根目录执行）：
    bash tools/build_linux.sh
或手动：
    .venv-linux/bin/python -m PyInstaller --clean --noconfirm jyprinter-linux.spec

与 Windows 版 jyprinter.spec 的差异：
    · 不打包 pywin32 与 win32com（Linux 不存在），改为声明 GTK/Xorg 托盘后端；
    · 不使用 .ico 与 version_info.txt（两者仅 Windows 有效），图标由 .desktop 指定；
    · 额外内置 assets/jyprinter.png，供托盘与桌面图标使用。

说明：模板与静态资源通过 datas 打入包内，运行时由 Flask 从 _MEIPASS 读取。
"""

from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821 - PyInstaller 注入

datas = [
    (str(ROOT / "web" / "templates"), "web/templates"),
    (str(ROOT / "web" / "static"), "web/static"),
    # 图标随包内置（运行时可被程序目录下的同名文件覆盖）
    (str(ROOT / "assets" / "jyprinter.png"), "assets"),
    (str(ROOT / "assets" / "jyprinter.ico"), "assets"),
]

hiddenimports = [
    # 托盘：两种后端全部声明，运行时按桌面环境自动选择
    "pystray._gtk",
    "pystray._xorg",
    "pystray._dummy",
    # 图像与二维码
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
    excludes=["pytest", "unittest", "test", "setuptools", "pip", "tkinter.test"],
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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
