"""Office/WPS 文档转换模块：把 doc/docx/xls/xlsx/ppt/pptx/wps/et/dps 统一转为 PDF。

转换引擎优先级（每一步都会校验产物有效性，失败自动降级到下一个）：
    1) 本机 Office / WPS 的 COM 自动化（窗口隐藏，无需用户操作）
    2) LibreOffice headless（检测到 soffice.exe 时可用）

重要实测结论（本机 Office 2013 + WPS 2016）：
    · WPS 会抢注 `Excel.Application` 与 `PowerPoint.Application`，因此家族内需按扩展名排序尝试，
      并以「产物是否有效（存在、非空、页数≥1）」作为唯一判定标准，而不是以 ProgID 名称判断；
    · .dps 经 PowerPoint 系 COM 可能产出 0 页空 PDF，必须能被识别为失败并继续降级；
    · 转换过程中的 Close/Quit 全部容错，避免清理动作掩盖真实错误；
    · 大文件（如 300 页表格）转换可能耗时数十秒，期间任务保持「打印中」。
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pythoncom
import win32api
import win32com.client
import win32con

from core import settings
from core.formats import OFFICE_EXTS, is_office_ext  # noqa: F401 - 统一由 formats 定义，此处转发
from core.utils import import_pymupdf, remove_file

WORD_EXTS = {"doc", "docx", "wps"}
EXCEL_EXTS = {"xls", "xlsx", "et"}
POWERPOINT_EXTS = {"ppt", "pptx", "dps"}

# 各扩展名的 COM 引擎尝试顺序（同名引擎在不同机器上可能由 Office 或 WPS 提供）
COM_CHAINS: Dict[str, List[str]] = {
    "doc": ["Word.Application", "KWPS.Application"],
    "docx": ["Word.Application", "KWPS.Application"],
    "wps": ["KWPS.Application", "Word.Application"],
    "xls": ["Excel.Application", "KET.Application"],
    "xlsx": ["Excel.Application", "KET.Application"],
    "et": ["KET.Application", "Excel.Application"],
    "ppt": ["PowerPoint.Application", "KWPP.Application"],
    "pptx": ["PowerPoint.Application", "KWPP.Application"],
    "dps": ["KWPP.Application", "PowerPoint.Application"],
}

# Office 自动化常量（避免依赖 makepy 生成的类型库）
WD_EXPORT_FORMAT_PDF = 17        # wdExportFormatPDF
WD_DO_NOT_SAVE_CHANGES = 0       # wdDoNotSaveChanges
XL_EXPORT_TYPE_PDF = 0           # xlTypePDF
PP_SAVE_AS_PDF = 32              # ppSaveAsPDF

# LibreOffice 常见安装路径
SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)

# LibreOffice 转换超时（秒）
SOFFICE_TIMEOUT = 300

# 各家族对应的可执行文件名（用于清理本次转换产生的残留进程）
# 实测：WPS 表格（et.exe）在 Quit() 后仍可能驻留并占用源文件，需要按 PID 差分清理
FAMILY_PROCESSES: Dict[str, Set[str]] = {
    "word": {"winword.exe", "wps.exe"},
    "excel": {"excel.exe", "et.exe"},
    "powerpoint": {"powerpnt.exe", "wpp.exe"},
}

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_probe_lock = threading.Lock()
_probe_cache: Optional[Dict[str, Any]] = None


def family_of(ext: str) -> str:
    """返回扩展名所属的引擎家族。

    参数：
        ext - 扩展名（可带点）
    返回：
        str，word / excel / powerpoint；不在支持范围内时返回空字符串
    """
    normalized = str(ext or "").strip().lower().lstrip(".")
    if normalized in WORD_EXTS:
        return "word"
    if normalized in EXCEL_EXTS:
        return "excel"
    if normalized in POWERPOINT_EXTS:
        return "powerpoint"
    return ""


def find_soffice() -> Optional[str]:
    """查找本机 LibreOffice 可执行文件。

    参数：无
    返回：
        str 或 None，soffice 的完整路径
    """
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    for candidate in SOFFICE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def convert_to_pdf(source: Path, ext: str) -> Path:
    """把 Office/WPS 文档转换为 PDF。

    参数：
        source - 源文件绝对路径（建议为纯 ASCII 路径）
        ext    - 源文件扩展名
    返回：
        Path，生成的 PDF 路径（位于 data/converted）
    异常：
        RuntimeError - 所有引擎均失败，消息中包含每一次尝试的原因
    """
    normalized = str(ext or "").strip().lower().lstrip(".")
    if normalized not in OFFICE_EXTS:
        raise RuntimeError(f"不支持的 Office 格式：.{normalized}")
    if not source.exists():
        raise RuntimeError("文件不存在或已被清理")

    settings.CONVERTED_DIR.mkdir(parents=True, exist_ok=True)
    target = settings.CONVERTED_DIR / f"{source.stem}.pdf"
    if _is_reusable(target, source):
        # 预览阶段已转换过，直接复用，避免打印时重复转换
        return target
    remove_file(target)

    attempts: List[str] = []

    for progid in COM_CHAINS.get(normalized, []):
        if not _progid_available(progid):
            attempts.append(f"{progid}：未安装或未注册")
            continue
        try:
            _convert_with_com(source, target, normalized, progid)
            pages = _validate_pdf(target)
            print(f"[转换] .{normalized} → PDF 成功，引擎 {progid}，共 {pages} 页", flush=True)
            return target
        except Exception as exc:
            attempts.append(f"{progid}：{_brief(exc)}")
            remove_file(target)

    soffice = find_soffice()
    if soffice:
        try:
            _convert_with_soffice(soffice, source, settings.CONVERTED_DIR)
            pages = _validate_pdf(target)
            print(f"[转换] .{normalized} → PDF 成功，引擎 LibreOffice，共 {pages} 页", flush=True)
            return target
        except Exception as exc:
            attempts.append(f"LibreOffice：{_brief(exc)}")
            remove_file(target)
    else:
        attempts.append("LibreOffice：未安装")

    raise RuntimeError(
        "无法转换该文档，请在本机安装 Microsoft Office / WPS Office / LibreOffice 后重试（"
        + "；".join(attempts)
        + "）"
    )


def _convert_with_com(source: Path, target: Path, ext: str, progid: str) -> None:
    """使用指定 COM 引擎执行转换（窗口隐藏）。

    参数：
        source - 源文件路径
        target - 目标 PDF 路径
        ext    - 源文件扩展名
        progid - COM ProgID
    返回：
        None
    异常：
        RuntimeError - 引擎未返回可用对象或转换调用失败
    """
    pythoncom.CoInitialize()
    app = None
    family = family_of(ext)
    existing = _family_pids(family)
    try:
        app = win32com.client.DispatchEx(progid)
        if family == "word":
            _word_to_pdf(app, source, target)
        elif family == "excel":
            _excel_to_pdf(app, source, target)
        else:
            _powerpoint_to_pdf(app, source, target)
    finally:
        _safe_quit(app)
        pythoncom.CoUninitialize()
        _reap_leftovers(family, existing)


def _family_pids(family: str) -> Set[int]:
    """枚举指定家族当前运行的 Office/WPS 进程 PID。

    使用 tasklist 而非 psapi，避免 32/64 位进程可见性问题（实测 psapi 漏报 et.exe）。

    参数：
        family - word / excel / powerpoint
    返回：
        set[int]，进程 PID 集合
    """
    names = FAMILY_PROCESSES.get(family)
    if not names:
        return set()
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    pids: Set[int] = set()
    for line in (result.stdout or "").splitlines():
        parts = [item.strip().strip('"') for item in line.split(",")]
        if len(parts) >= 2 and parts[0].lower() in names and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _reap_leftovers(family: str, existing: Set[int]) -> int:
    """清理本次转换新产生且未被 Quit 退出的残留进程。

    只处理「转换前不存在、转换后出现」的 PID，因此不会影响用户已打开的文档窗口。

    参数：
        family   - word / excel / powerpoint
        existing - 转换开始前已存在的同家族 PID 集合
    返回：
        int，被结束的进程数量
    """
    if not family:
        return 0

    new_pids = _family_pids(family) - existing
    if not new_pids:
        return 0

    # 给引擎一点收尾时间，再复查一次
    time.sleep(0.6)
    new_pids = (_family_pids(family) - existing) | new_pids

    for pid in sorted(new_pids):
        handle = None
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
            win32api.TerminateProcess(handle, 1)
        except Exception:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    timeout=10,
                    creationflags=_NO_WINDOW,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        finally:
            if handle is not None:
                try:
                    win32api.CloseHandle(handle)
                except Exception:
                    pass

    # 部分 WPS 预加载占位进程会拒绝退出，这里以「复查后确实消失」为准，避免误报
    remaining = _family_pids(family) - existing
    cleaned = len(new_pids - remaining)
    if cleaned:
        print(f"[转换] 已清理残留的 {family} 进程 {cleaned} 个", flush=True)
    return cleaned


def _word_to_pdf(app: Any, source: Path, target: Path) -> None:
    """Word 系：打开文档并导出为 PDF。

    参数：
        app    - Word 系 COM 应用对象
        source - 源文件路径
        target - 目标 PDF 路径
    返回：
        None
    异常：
        RuntimeError - 打开文档失败或导出失败
    """
    _set(app, "Visible", False)
    _set(app, "DisplayAlerts", 0)

    documents = _require(app, "Documents")
    document = documents.Open(str(source), ReadOnly=True, AddToRecentFiles=False)
    if document is None:
        raise RuntimeError("打开文档失败")
    try:
        export = getattr(document, "ExportAsFixedFormat", None)
        if export is not None:
            try:
                export(str(target), WD_EXPORT_FORMAT_PDF)
                return
            except Exception:
                pass
        save_as2 = getattr(document, "SaveAs2", None)
        if save_as2 is not None:
            save_as2(str(target), FileFormat=WD_EXPORT_FORMAT_PDF)
            return
        save_as = _require(document, "SaveAs")
        save_as(str(target), WD_EXPORT_FORMAT_PDF)
    finally:
        _safe_call(document, "Close", WD_DO_NOT_SAVE_CHANGES)


def _excel_to_pdf(app: Any, source: Path, target: Path) -> None:
    """Excel 系：打开工作簿并导出为 PDF。

    参数：
        app    - Excel 系 COM 应用对象
        source - 源文件路径
        target - 目标 PDF 路径
    返回：
        None
    异常：
        RuntimeError - 打开工作簿失败或导出失败
    """
    _set(app, "Visible", False)
    _set(app, "DisplayAlerts", False)

    workbooks = _require(app, "Workbooks")
    book = workbooks.Open(str(source), ReadOnly=True, UpdateLinks=0)
    if book is None:
        raise RuntimeError("打开工作簿失败")
    try:
        export = getattr(book, "ExportAsFixedFormat", None)
        if export is None:
            raise RuntimeError("该表格引擎不支持导出 PDF")
        export(XL_EXPORT_TYPE_PDF, str(target))
    finally:
        _safe_call(book, "Close", False)


def _powerpoint_to_pdf(app: Any, source: Path, target: Path) -> None:
    """PowerPoint 系：打开演示文稿并另存为 PDF。

    参数：
        app    - PowerPoint 系 COM 应用对象
        source - 源文件路径
        target - 目标 PDF 路径
    返回：
        None
    异常：
        RuntimeError - 打开演示文稿失败或导出失败
    """
    # PowerPoint 自动化通常要求应用可见；文档窗口本身不显示
    _set(app, "Visible", True)

    presentations = _require(app, "Presentations")
    presentation = presentations.Open(
        str(source), ReadOnly=True, Untitled=False, WithWindow=False
    )
    if presentation is None:
        raise RuntimeError("打开演示文稿失败")
    try:
        save_as = getattr(presentation, "SaveAs", None)
        if save_as is not None:
            try:
                save_as(str(target), PP_SAVE_AS_PDF)
                return
            except Exception:
                pass
        export = getattr(presentation, "ExportAsFixedFormat", None)
        if export is None:
            raise RuntimeError("该演示引擎不支持导出 PDF")
        export(str(target), 2)
    finally:
        _safe_call(presentation, "Close")


def _convert_with_soffice(soffice: str, source: Path, out_dir: Path) -> None:
    """使用 LibreOffice headless 执行转换。

    参数：
        soffice - soffice 可执行文件路径
        source  - 源文件路径
        out_dir - 输出目录
    返回：
        None
    异常：
        RuntimeError - 进程超时或执行失败
    """
    command = [
        soffice,
        "--headless",
        "--norestore",
        "--convert-to",
        "pdf:writer_pdf_Export",
        "--outdir",
        str(out_dir),
        str(source),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=SOFFICE_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"转换超时（超过 {SOFFICE_TIMEOUT} 秒）") from exc
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", errors="replace")[:160]
        raise RuntimeError(f"退出码 {result.returncode} {detail}".strip())


def _is_reusable(target: Path, source: Path) -> bool:
    """判断已有的转换产物是否可以复用。

    参数：
        target - 已存在的 PDF 路径
        source - 源文件路径
    返回：
        bool，产物存在、非空且不早于源文件时返回 True
    """
    try:
        stat = target.stat()
        return stat.st_size > 0 and stat.st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def _validate_pdf(path: Path) -> int:
    """校验转换产物是否为有效且非空的 PDF。

    参数：
        path - PDF 路径
    返回：
        int，页数
    异常：
        RuntimeError - 文件缺失、为空或页数为 0
    """
    pymupdf = import_pymupdf()

    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("未生成 PDF 文件")
    try:
        with pymupdf.open(str(path)) as document:
            pages = document.page_count
    except Exception as exc:
        raise RuntimeError(f"生成的 PDF 无法解析：{_brief(exc)}") from exc
    if pages < 1:
        raise RuntimeError("生成的 PDF 没有任何页面")
    return pages


def _set(obj: Any, attr: str, value: Any) -> None:
    """设置 COM 对象属性（失败时忽略，不同引擎属性支持度不一致）。

    参数：
        obj   - COM 对象
        attr  - 属性名
        value - 属性值
    返回：
        None
    """
    try:
        setattr(obj, attr, value)
    except Exception:
        pass


def _require(obj: Any, attr: str) -> Any:
    """获取 COM 对象的必需成员，缺失时抛出可读异常。

    参数：
        obj  - COM 对象
        attr - 成员名
    返回：
        Any，成员对象
    异常：
        RuntimeError - 成员不存在
    """
    member = getattr(obj, attr, None)
    if member is None:
        raise RuntimeError(f"引擎缺少必需接口：{attr}")
    return member


def _safe_call(obj: Any, method: str, *args: Any) -> None:
    """安全调用 COM 对象方法（忽略清理阶段的任何异常）。

    参数：
        obj    - COM 对象
        method - 方法名
        args   - 方法参数
    返回：
        None
    """
    if obj is None:
        return
    try:
        func = getattr(obj, method, None)
        if func is not None:
            func(*args)
    except Exception:
        pass


def _safe_quit(app: Any) -> None:
    """安全退出 COM 应用。

    参数：
        app - COM 应用对象（可为 None）
    返回：
        None
    """
    _safe_call(app, "Quit")


def _brief(exc: Exception) -> str:
    """把异常压缩为一行可读文本（优先取 COM 异常中的描述信息）。

    参数：
        exc - 异常对象
    返回：
        str，截断到 140 字符的说明
    """
    text = ""
    if isinstance(exc, pythoncom.com_error) and len(exc.args) > 2:
        info = exc.args[2]
        if isinstance(info, tuple) and len(info) > 2 and info[2]:
            text = str(info[2])
        elif len(exc.args) > 1 and exc.args[1]:
            text = str(exc.args[1])
    if not text:
        text = " ".join(str(exc).split())
    return (text or exc.__class__.__name__)[:140]


def _progid_available(progid: str) -> bool:
    """检测 COM ProgID 是否已注册（只读注册表，不启动进程）。

    参数：
        progid - COM ProgID
    返回：
        bool，已注册返回 True
    """
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\CLSID")
        key.Close()
        return True
    except OSError:
        return False


def probe_availability(refresh: bool = False) -> Dict[str, Any]:
    """探测各 Office 扩展名当前是否具备可用的转换引擎。

    参数：
        refresh - 为 True 时忽略缓存重新探测
    返回：
        dict，{exts_by_family, families, libreoffice, available_exts}
    """
    global _probe_cache
    with _probe_lock:
        if _probe_cache is not None and not refresh:
            return dict(_probe_cache)

        families: Dict[str, List[Dict[str, Any]]] = {}
        for name, progids in (
            ("word", COM_CHAINS["docx"]),
            ("excel", COM_CHAINS["xlsx"]),
            ("powerpoint", COM_CHAINS["pptx"]),
        ):
            engines: List[Dict[str, Any]] = []
            for progid in progids:
                if not _progid_available(progid):
                    continue
                engines.append({"progid": progid, "name": _describe_progid(progid)})
            families[name] = engines

        soffice = find_soffice()
        available = set()
        for ext in OFFICE_EXTS:
            if families.get(family_of(ext)):
                available.add(ext)
        if soffice:
            available |= OFFICE_EXTS

        result = {
            "families": families,
            "libreoffice": soffice or "",
            "available_exts": sorted(available),
        }
        _probe_cache = result
        return dict(result)


def _describe_progid(progid: str) -> str:
    """读取注册表中 ProgID 的展示名称。

    说明：Windows 注册表无法可靠定位 Office 自动化服务器可执行文件（Office 使用 AppID 注册，
    CLSID 下没有 LocalServer32），且 WPS 会把 Excel.Application / PowerPoint.Application
    指向自身，因此这里只展示注册名，实际生效引擎以转换日志为准。

    参数：
        progid - COM ProgID
    返回：
        str，注册名（读取失败时返回 ProgID 本身）
    """
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid)
    except OSError:
        return progid
    try:
        value, _ = winreg.QueryValueEx(key, "")
        return str(value) if value else progid
    except OSError:
        return progid
    finally:
        key.Close()
