"""打印机管理分发模块：按运行平台加载 Windows（win32print）或 Linux（CUPS）实现。

对外统一暴露：list_printers / get_default_printer / get_capabilities /
is_virtual / count_jobs / status_of。
"""

from __future__ import annotations

from core.osinfo import IS_WINDOWS

if IS_WINDOWS:
    from core.printers_win import (  # noqa: F401
        COMMON_PAPERS,
        DEFAULT_PAPER_ID,
        VIRTUAL_KEYWORDS,
        build_devmode,
        count_jobs,
        get_capabilities,
        get_default_printer,
        is_virtual,
        list_printers,
        resolve_paper_id,
        status_of,
    )
else:
    from core.printers_linux import (  # noqa: F401
        DEFAULT_PAPER,
        PAPER_SIZES,
        VIRTUAL_KEYWORDS,
        CupsError,
        cancel_job,
        count_jobs,
        cups_available,
        get_capabilities,
        get_default_printer,
        is_virtual,
        list_job_ids,
        list_printers,
        paper_size_points,
        require_cups,
        resolve_media_name,
        status_of,
        submit_job,
    )
