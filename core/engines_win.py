"""Windows 静默打印引擎：位图页面通过 GDI 直接送入打印机（全程不弹窗）。

实现要点（已在本机实测）：
    · win32gui.CreateDC("WINSPOOL", 打印机名, DEVMODE) → 纸张与双面设置真正生效
    · win32ui.CreateDCFromHandle(hdc) → 得到可绘制的 PyCDC
    · 位图经 BMP 临时文件加载（PyCBitmap.LoadBitmapFile 需要文件对象）
    · 全部绘制在 StartDoc/StartPage 事务内完成，异常时 AbortDoc 回滚，避免留下半截任务

页面渲染（图片/文本/PDF/Office 转 PDF）由跨平台的 core.render 提供。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator

import pywintypes
import win32con
import win32gui
import win32ui
from PIL import Image

from core import converter_win as converter
from core import printers_win as printers
from core import render
from core.utils import remove_file

# GetDeviceCaps 索引（与 Windows GDI 定义一致）
HORZRES = 8
VERTRES = 10
LOGPIXELSX = 88

# 渲染分辨率（与跨平台渲染核心保持一致）
RENDER_DPI = render.RENDER_DPI


def build_page_factory(path: str, ext: str, options: Any = None) -> Any:
    """构建页面生成器工厂（Windows 侧，办公文档走本机 COM 转换）。

    参数：
        path    - 文件绝对路径
        ext     - 文件扩展名
        options - render.RenderOptions 渲染选项，None 表示全部默认
    返回：
        (page_factory, converted_pdf)
    """
    return render.build_page_factory(path, ext, converter.convert_to_pdf, options)


def count_pages(path: str, ext: str) -> int:
    """统计文件总页数（预览用）。

    参数：
        path - 文件绝对路径
        ext  - 文件扩展名
    返回：
        int，总页数（至少为 1）
    """
    return render.count_pages(path, ext, converter.convert_to_pdf)


def print_file(
    *,
    path: str,
    ext: str,
    printer: str,
    paper: str,
    duplex: str,
    copies: int,
    title: str = "",
    options: Any = None,
) -> Dict[str, Any]:
    """静默打印一个文件。

    参数：
        path    - 文件绝对路径
        ext     - 文件扩展名
        printer - 目标打印机名称（空字符串表示系统默认打印机）
        paper   - 纸张名称（如 A4）
        duplex  - simplex 单面 / vertical 双面长边翻转
        copies  - 份数
        title   - 打印任务名称，用于 Windows 打印队列显示
        options - render.RenderOptions 渲染选项（分辨率、页码范围、缩放、颜色）
    返回：
        dict，包含 printer / format / pages / copies 等执行结果
    异常：
        RuntimeError - 文件不存在、格式不支持、打印机不可用等可读错误
    """
    normalized = render.formats.normalize_ext(ext)
    opts = options or render.RenderOptions()
    page_factory, converted = build_page_factory(path, normalized, opts)

    resolved_printer = printer or printers.get_default_printer()
    if not resolved_printer:
        if converted is not None:
            remove_file(converted)
        raise RuntimeError("未找到可用打印机，请先在系统中安装并设置默认打印机")

    copies = max(1, int(copies or 1))
    try:
        drawn = _print_pages(
            page_factory=page_factory,
            printer=resolved_printer,
            paper=paper,
            duplex=duplex,
            copies=copies,
            title=title or Path(path).name,
            options=opts,
        )
    finally:
        # 转换产物仅用于本次打印，完成后立即清理
        if converted is not None:
            remove_file(converted)

    return {
        "printer": resolved_printer,
        "format": normalized,
        "pages": drawn,
        "copies": copies,
    }


def _print_pages(
    *,
    page_factory: Callable[[], Iterator[Image.Image]],
    printer: str,
    paper: str,
    duplex: str,
    copies: int,
    title: str,
    options: render.RenderOptions,
) -> int:
    """把页面序列绘制到打印机并提交打印作业。

    参数：
        page_factory - 页面生成器工厂（每份重新调用，逐页产出位图）
        printer      - 目标打印机名称
        paper        - 纸张名称
        duplex       - 单双面标识
        copies       - 份数
        title        - 打印作业名称
        options      - 渲染选项（缩放比例等）
    返回：
        int，实际绘制的总页数（含多份）
    异常：
        RuntimeError - 打印机不可用或驱动拒绝任务
    """
    paper_id = printers.resolve_paper_id(printer, paper)
    devmode = printers.build_devmode(printer, paper_id, duplex)

    try:
        hdc = win32gui.CreateDC("WINSPOOL", printer, devmode)
    except pywintypes.error as exc:
        raise RuntimeError(f"无法连接打印机「{printer}」：{_gdi_reason(exc)}") from exc
    if not hdc:
        raise RuntimeError(f"无法创建打印机上下文：{printer}")

    dc = win32ui.CreateDCFromHandle(hdc)
    tmpdir = tempfile.mkdtemp(prefix="jyprint_")
    started = False
    drawn = 0
    try:
        printable_w = dc.GetDeviceCaps(HORZRES)
        printable_h = dc.GetDeviceCaps(VERTRES)
        if printable_w <= 0 or printable_h <= 0:
            raise RuntimeError("打印机未返回有效纸张尺寸，请检查驱动是否正常")

        # 设备分辨率：把位图自然尺寸换算为设备像素时使用（实际大小 / 自定义缩放）
        device_dpi = int(dc.GetDeviceCaps(LOGPIXELSX) or 0) or options.dpi
        ratio = options.scale_ratio()

        dc.StartDoc(title)
        started = True
        for _ in range(copies):
            for image in page_factory():
                dc.StartPage()
                try:
                    _blit(
                        dc,
                        image,
                        printable_w,
                        printable_h,
                        tmpdir,
                        ratio=ratio,
                        render_dpi=options.dpi,
                        device_dpi=device_dpi,
                    )
                finally:
                    image.close()
                dc.EndPage()
                drawn += 1
        dc.EndDoc()
        started = False
    except RuntimeError:
        if started:
            _abort(dc)
        raise
    except pywintypes.error as exc:
        if started:
            _abort(dc)
        raise RuntimeError(f"打印失败（打印机异常）：{_gdi_reason(exc)}") from exc
    except (OSError, ValueError) as exc:
        if started:
            _abort(dc)
        raise RuntimeError(f"文件解析或渲染失败：{exc}") from exc
    finally:
        try:
            dc.DeleteDC()
        except pywintypes.error:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    if drawn == 0:
        raise RuntimeError("文件没有可打印的页面")
    return drawn


def _abort(dc: Any) -> None:
    """中止当前打印作业（用于异常回滚，避免留下半截任务）。

    参数：
        dc - PyCDC 打印机上下文
    返回：
        None
    """
    try:
        dc.AbortDoc()
    except pywintypes.error:
        pass


def _gdi_reason(exc: Exception) -> str:
    """把 GDI/打印子系统异常转换为可读的中文说明。

    参数：
        exc - pywintypes.error 异常对象
    返回：
        str，可读的错误原因
    """
    if isinstance(exc, pywintypes.error):
        code = exc.args[0] if exc.args else 0
        detail = exc.args[2] if len(exc.args) > 2 else str(exc)
        mapping = {
            5: "访问被拒绝，请以管理员权限运行本程序",
            1801: "打印机名称无效，请重新选择打印机",
            1722: "打印服务（Spooler）未响应，请检查 Print Spooler 服务",
            1231: "网络不可达，无法连接到该打印机",
        }
        hint = mapping.get(int(code) if isinstance(code, int) else 0)
        return f"{detail}（错误码 {code}{'，' + hint if hint else ''}）"
    return str(exc)


def _blit(
    dc: Any,
    image: Image.Image,
    printable_w: int,
    printable_h: int,
    tmpdir: str,
    ratio: Any = None,
    render_dpi: int = RENDER_DPI,
    device_dpi: int = RENDER_DPI,
) -> None:
    """把一张位图绘制到打印机页面上（居中）。

    ratio 为 None 时按「适应页面」等比缩放（可放大）；否则按「位图自然尺寸」乘 ratio
    （1.0 即实际大小）绘制，超出可打印区域的部分由打印机驱动自动裁切。

    参数：
        dc          - PyCDC 打印机上下文
        image       - PIL 图像对象（RGB 或 L 灰度）
        printable_w - 可打印区域宽度（设备像素）
        printable_h - 可打印区域高度（设备像素）
        tmpdir      - 位图临时文件目录
        ratio       - 相对自然尺寸的缩放比例，None 表示适应页面
        render_dpi  - 位图的渲染分辨率
        device_dpi  - 打印机设备分辨率
    返回：
        None
    """
    width, height = image.size
    if width <= 0 or height <= 0:
        return

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    if ratio is None:
        scale = min(printable_w / width, printable_h / height)
    else:
        scale = ratio * (device_dpi / float(render_dpi or device_dpi))
    target_w = max(1, int(width * scale))
    target_h = max(1, int(height * scale))
    offset_x = (printable_w - target_w) // 2
    offset_y = (printable_h - target_h) // 2

    bmp_path = os.path.join(tmpdir, f"{uuid.uuid4().hex}.bmp")
    image.save(bmp_path, "BMP")

    bitmap = win32ui.CreateBitmap()
    with open(bmp_path, "rb") as fp:
        bitmap.LoadBitmapFile(fp)

    mem_dc = dc.CreateCompatibleDC()
    try:
        mem_dc.SelectObject(bitmap)
        dc.StretchBlt(
            (offset_x, offset_y),
            (target_w, target_h),
            mem_dc,
            (0, 0),
            (width, height),
            win32con.SRCCOPY,
        )
    finally:
        try:
            mem_dc.DeleteDC()
        except pywintypes.error:
            pass
        try:
            win32gui.DeleteObject(bitmap.GetHandle())
        except pywintypes.error:
            pass
