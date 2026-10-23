"""打印引擎分发模块：按运行平台加载 Windows（GDI）或 Linux（CUPS）实现。

对外统一暴露：
    · 格式常量与工具（来自 core.formats）：SUPPORTED_EXTS / PENDING_EXTS /
      IMAGE_EXTS / TEXT_EXTS / PDF_EXTS / EXT_ALIASES / normalize_ext / is_supported
    · 渲染选项（来自 core.render）：RenderOptions / QUALITY_DPI / RENDER_DPI
    · 引擎接口：build_page_factory / count_pages / print_file
"""

from __future__ import annotations

from core.formats import (  # noqa: F401
    EXT_ALIASES,
    IMAGE_EXTS,
    OFFICE_EXTS,
    PDF_EXTS,
    PENDING_EXTS,
    SUPPORTED_EXTS,
    TEXT_EXTS,
    is_office_ext,
    is_supported,
    normalize_ext,
)
from core.osinfo import IS_WINDOWS
from core.render import QUALITY_DPI, RENDER_DPI, RenderOptions  # noqa: F401

if IS_WINDOWS:
    from core.engines_win import build_page_factory, count_pages, print_file  # noqa: F401
else:
    from core.engines_linux import build_page_factory, count_pages, print_file  # noqa: F401
