"""打印引擎分发模块：按运行平台加载 Windows（GDI）或 Linux（CUPS）实现。

对外统一暴露：
    · 格式常量与工具（来自 core.formats）：SUPPORTED_EXTS / PENDING_EXTS /
      IMAGE_EXTS / TEXT_EXTS / PDF_EXTS / EXT_ALIASES / normalize_ext / is_supported
    · 渲染选项与取消异常（来自 core.render）：RenderOptions / QUALITY_DPI /
      RENDER_DPI / PrintCanceled
    · 引擎接口：build_page_factory / count_pages / print_file
    （取消打印队列作业属于设备操作，见 core.printers.cancel_job）
"""

from __future__ import annotations

from core.formats import (  # noqa: F401
    EXT_ALIASES,
    IMAGE_EXTS,
    OFFICE_EXTS,
    PDF_EXTS,
    PENDING_EXTS,
    SPREADSHEET_EXTS,
    SUPPORTED_EXTS,
    TEXT_EXTS,
    is_office_ext,
    is_spreadsheet_ext,
    is_supported,
    normalize_ext,
)
from core.osinfo import IS_WINDOWS
from core.render import (  # noqa: F401
    QUALITY_DPI,
    RENDER_DPI,
    PrintCanceled,
    RenderOptions,
)

if IS_WINDOWS:
    from core.engines_win import (  # noqa: F401
        build_page_factory,
        count_pages,
        print_file,
    )
else:
    from core.engines_linux import (  # noqa: F401
        build_page_factory,
        count_pages,
        print_file,
    )
