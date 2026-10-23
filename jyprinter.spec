# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：生成单文件 jyprinter.exe。

打包命令（在项目根目录执行）：
    .venv\\Scripts\\python.exe -m PyInstaller --clean --noconfirm jyprinter.spec

说明：模板与静态资源通过 datas 打入包内，运行时由 Flask 从 _MEIPASS 读取。
"""

from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821 - PyInstaller 注入

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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "jyprinter.ico"),
    version=str(ROOT / "assets" / "version_info.txt"),
)
