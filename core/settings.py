"""配置模块：负责 config.json 的加载、默认值合并与持久化。

同时统一声明运行时目录（data/uploads、data/logs），避免路径散落在各模块中。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from core import osinfo

# 项目根目录：
#   源码运行时为 core 的上一级；
#   PyInstaller 打包后模块位于临时解包目录，必须改用可执行文件所在目录，否则配置与数据无法持久化。
if getattr(sys, "frozen", False):
    BASE_DIR: Path = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve_data_dir() -> Path:
    """确定可写数据目录。

    默认使用程序目录下的 data（源码运行与免安装的 tar.gz 发行包）；
    deb/rpm 系统包由 systemd 设置 JYPRINTER_DATA_DIR=/var/lib/jyprinter，
    让可写数据落在系统数据目录，而程序目录（/opt/jyprinter）保持只读。

    参数：无
    返回：
        Path，数据目录
    """
    override = os.environ.get("JYPRINTER_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return BASE_DIR / "data"


DATA_DIR: Path = _resolve_data_dir()


def _resolve_config_path() -> Path:
    """确定配置文件 config.json 的路径。

    默认与程序目录同级；deb/rpm 包与 macOS 的 .app 包内目录只读，
    因此允许通过 JYPRINTER_CONFIG_PATH 指向可写位置（如用户应用支持目录）。

    参数：无
    返回：
        Path，配置文件路径
    """
    override = os.environ.get("JYPRINTER_CONFIG_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return BASE_DIR / "config.json"


UPLOAD_DIR: Path = DATA_DIR / "uploads"
CONVERTED_DIR: Path = DATA_DIR / "converted"
PREVIEW_DIR: Path = DATA_DIR / "previews"
LOG_DIR: Path = DATA_DIR / "logs"
CONFIG_PATH: Path = _resolve_config_path()

# 产品与开发者信息（用于页面、托盘「关于」与控制台横幅展示）
VERSION = "1.7.1"
PRODUCT_NAME = "网络打印服务"
DEVELOPER_NAME = "陈光明"
DEVELOPER_WECHAT = "cgm10050"
DEVELOPER_PHONE = "13923010050"
DEVELOPER_LINE = (
    f"程序开发：{DEVELOPER_NAME}　微信号：{DEVELOPER_WECHAT}　联系电话：{DEVELOPER_PHONE}"
)

# 默认配置：所有可调项集中在此，缺失项由 load() 自动补齐
DEFAULT_CONFIG: Dict[str, Any] = {
    "host": "0.0.0.0",                # 监听地址，0.0.0.0 表示允许局域网访问
    "port": 5000,                     # 网页服务端口
    "printer": "",                    # 目标打印机名，空字符串表示跟随系统默认打印机
    "copies": 1,                      # 默认份数
    "duplex": "simplex",              # 默认单双面：simplex 单面 / vertical 双面长边翻转
    "paper": "A4",                    # 默认纸张
    "page_range": "",                 # 默认页码范围（空表示全部），如 "1-3,5"
    "scale": "fit",                   # 默认缩放：fit 适应页面 / actual 实际大小 / custom 自定义
    "scale_percent": 100,             # scale=custom 时的默认百分比
    "color": "color",                 # 默认颜色模式：color 彩色 / mono 黑白
    "quality": "normal",              # 默认打印质量：draft 草稿 / normal 标准 / high 高清
    "orientation": "auto",            # 默认出纸方向：auto 跟随文档 / portrait 纵向 / landscape 横向
    "fit_table": True,                # 表格类文件转换前是否设为「适应一页宽」（仅 Windows 生效）
    "filter_virtual_printers": True,  # 是否在网页端过滤虚拟打印机
    "require_confirmation": False,    # 上传后是否必须先预览确认再打印
    "hide_console_on_start": True,    # 托盘模式下启动后自动隐藏控制台窗口
    "task_timeout_seconds": 180,      # 单个任务的打印完成等待上限（秒）
    "file_ttl_minutes": 30,           # 上传文件的存活时间（分钟），超时自动清理
    "keep_file_after_print": False,   # 打印成功后是否保留源文件：False 立即删除（默认）；True 保留以便界面「重打」
    "log_keep_days": 7,               # 打印日志保留天数
    "max_upload_mb": 100,             # 单文件上传大小上限（MB）
}

_lock = threading.RLock()
_config: Optional[Dict[str, Any]] = None


def brand() -> Dict[str, str]:
    """返回品牌与开发者信息（供页面模板渲染）。

    参数：无
    返回：
        dict，{product, version, developer, wechat, phone}
    """
    return {
        "product": PRODUCT_NAME,
        "version": VERSION,
        "developer": DEVELOPER_LINE,
        "wechat": DEVELOPER_WECHAT,
        "phone": DEVELOPER_PHONE,
    }


def icon_path() -> Optional[Path]:
    """返回程序图标文件路径。

    优先使用程序所在目录下的 assets/ 图标（便于用户替换），
    其次使用打包时内置到程序包内的同名图标。
    Windows 优先 .ico（可作窗口/任务栏图标），Linux 与 macOS 优先 .png。

    参数：无
    返回：
        Path 或 None，图标不存在时返回 None
    """
    names = (
        ("jyprinter.png", "jyprinter.ico")
        if (osinfo.IS_LINUX or osinfo.IS_MACOS)
        else ("jyprinter.ico", "jyprinter.png")
    )
    bundled = Path(__file__).resolve().parent.parent / "assets"
    # Flet 打包后的 .app 会把 assets 放在包内独立目录，并给出该环境变量
    flet_assets = os.environ.get("FLET_ASSETS_DIR", "").strip()
    roots = [BASE_DIR / "assets", bundled]
    if flet_assets:
        roots.append(Path(flet_assets))
    for name in names:
        for candidate in (root / name for root in roots):
            if candidate.exists():
                return candidate
    return None


def ensure_dirs() -> None:
    """创建运行时所需目录。

    参数：无
    返回：None
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CONVERTED_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load(force: bool = False) -> Dict[str, Any]:
    """加载配置，缺失项以默认值补齐。

    参数：
        force - 为 True 时忽略内存缓存，强制从磁盘重新读取
    返回：
        dict，合并后的完整配置对象
    """
    global _config
    with _lock:
        if _config is not None and not force:
            return dict(_config)

        data: Dict[str, Any] = {}
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) or {}
                if not isinstance(data, dict):
                    data = {}
            except (OSError, ValueError):
                # 配置文件损坏时回退到默认配置，保证服务仍可启动
                data = {}

        merged = dict(DEFAULT_CONFIG)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
        _config = merged
        return dict(merged)


def save() -> None:
    """将当前配置写入 config.json。

    参数：无
    返回：None
    """
    with _lock:
        payload = dict(_config or DEFAULT_CONFIG)
        CONFIG_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def get(key: str, default: Any = None) -> Any:
    """读取单个配置项。

    参数：
        key     - 配置键名
        default - 键不存在时返回的默认值
    返回：
        配置值
    """
    return load().get(key, default)


def update(patch: Dict[str, Any]) -> Dict[str, Any]:
    """更新并持久化配置项（仅接受已声明的键）。

    参数：
        patch - 待更新的键值对
    返回：
        dict，更新后的完整配置对象
    """
    global _config
    with _lock:
        current = load()
        for key, value in patch.items():
            if key in DEFAULT_CONFIG:
                current[key] = value
        _config = current
        save()
        return dict(current)
