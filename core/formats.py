"""文件格式定义与扩展名工具（跨平台共享）。

放在单独模块是为了让 Windows / Linux 两套打印引擎共用同一份格式清单，
避免两边的支持列表出现漂移。
"""

from __future__ import annotations

# 图片（7 种，另将 .tif 视为 .tiff 的别名）
IMAGE_EXTS = {"jpg", "jpeg", "png", "bmp", "gif", "tiff", "webp"}
# 纯文本（3 种）
TEXT_EXTS = {"txt", "md", "log"}
# PDF
PDF_EXTS = {"pdf"}
# Office / WPS（9 种，需要本机办公软件转换）
OFFICE_EXTS = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "wps", "et", "dps"}

# 全部支持格式（共 20 种）
SUPPORTED_EXTS = IMAGE_EXTS | TEXT_EXTS | PDF_EXTS | OFFICE_EXTS

# 暂无待支持格式（后续计划外的格式才会出现在此集合）
PENDING_EXTS: set = set()

# 扩展名别名：统一到规范扩展名，保证格式计数与规格一致
EXT_ALIASES = {"tif": "tiff"}


def normalize_ext(ext: str) -> str:
    """规范化扩展名（去点、转小写并应用别名映射）。

    参数：
        ext - 原始扩展名（如 .TIF）
    返回：
        str，规范化后的扩展名（如 tiff）
    """
    value = str(ext or "").strip().lower().lstrip(".")
    return EXT_ALIASES.get(value, value)


def is_supported(ext: str) -> bool:
    """判断扩展名是否属于支持的格式。

    参数：
        ext - 扩展名（可带点）
    返回：
        bool，支持则返回 True
    """
    return normalize_ext(ext) in SUPPORTED_EXTS


def is_office_ext(ext: str) -> bool:
    """判断扩展名是否属于需要办公软件转换的格式。

    参数：
        ext - 扩展名（可带点）
    返回：
        bool，属于 Office/WPS 家族则返回 True
    """
    return normalize_ext(ext) in OFFICE_EXTS


def ext_list_text() -> str:
    """生成用于界面/日志展示的格式清单文本。

    参数：无
    返回：
        str，形如 ".bmp .doc .docx ..."
    """
    return " ".join(sorted(f".{ext}" for ext in SUPPORTED_EXTS))
