"""办公文档转换分发模块：按平台加载 Windows（Office/WPS COM）或 Linux（LibreOffice）实现。

对外统一暴露：OFFICE_EXTS / is_office_ext / convert_to_pdf / find_soffice / probe_availability。
"""

from __future__ import annotations

from core.formats import OFFICE_EXTS, is_office_ext  # noqa: F401 - 统一由 formats 定义
from core.osinfo import IS_WINDOWS

if IS_WINDOWS:
    from core.converter_win import convert_to_pdf, find_soffice, probe_availability  # noqa: F401
else:
    from core.converter_linux import convert_to_pdf, find_soffice, probe_availability  # noqa: F401
