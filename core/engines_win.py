"""Windows 静默打印引擎：位图页面通过 GDI 直接送入打印机（全程不弹窗）。

实现要点（已在本机实测）：
    · win32gui.CreateDC("WINSPOOL", 打印机名, DEVMODE) → 纸张与双面设置真正生效
    · win32ui.CreateDCFromHandle(hdc) → 得到可绘制的 PyCDC
    · 位图经 BMP 临时文件加载（PyCBitmap.LoadBitmapFile 需要文件对象）
    · 全部绘制在 StartDoc/StartPage 事务内完成，异常时 AbortDoc 回滚，避免留下半截任务

页面渲染（图片/文本/PDF/Office 转 PDF）由跨平台的 core.render 提供。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

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
RASTERCAPS = 38
LOGPIXELSX = 88

# RASTERCAPS 光栅能力位：判断驱动支持哪种位图绘制方式
RC_BITBLT = 0x0001      # 支持 BitBlt（1:1 拷贝）
RC_STRETCHBLT = 0x0800  # 支持 StretchBlt（带缩放的拷贝）

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
    should_abort: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """静默打印一个文件。

    参数：
        path         - 文件绝对路径
        ext          - 文件扩展名
        printer      - 目标打印机名称（空字符串表示系统默认打印机）
        paper        - 纸张名称（如 A4）
        duplex       - simplex 单面 / vertical 双面长边翻转
        copies       - 份数
        title        - 打印任务名称，用于 Windows 打印队列显示
        options      - render.RenderOptions 渲染选项（分辨率、页码范围、缩放、颜色）
        should_abort - 可选回调，返回 True 表示用户已请求取消；每页开始前检查一次，
                       命中则中止打印并抛 render.PrintCanceled（已出纸的页无法收回）
    返回：
        dict，包含 printer / format / pages / copies / job_id 等执行结果
        （pages 为**每份页数**，份数由 copies 单独表达，与 Linux 侧口径一致；
        job_id 为提交后从打印队列认领到的作业号，供「打印中取消」使用，取不到为 None）
    异常：
        RuntimeError - 文件不存在、格式不支持、打印机不可用等可读错误
        render.PrintCanceled - 用户在打印过程中取消了任务
    """
    normalized = render.formats.normalize_ext(ext)
    opts = options or render.RenderOptions()
    page_factory, converted = build_page_factory(path, normalized, opts)

    # 出纸方向：显式指定时采用用户选择，auto 时取源文档首页方向
    # （Office 文件这里已经是转换后的 PDF，因此统一按 pdf 判定）
    source_for_orientation = converted if converted is not None else Path(path)
    orientation = render.resolve_orientation(
        source_for_orientation, "pdf" if converted is not None else normalized, opts
    )

    resolved_printer = printer or printers.get_default_printer()
    if not resolved_printer:
        if converted is not None:
            remove_file(converted)
        raise RuntimeError("未找到可用打印机，请先在系统中安装并设置默认打印机")

    copies = max(1, int(copies or 1))
    try:
        drawn, job_id = _print_pages(
            page_factory=page_factory,
            printer=resolved_printer,
            paper=paper,
            duplex=duplex,
            copies=copies,
            title=title or Path(path).name,
            options=opts,
            orientation=orientation,
            should_abort=should_abort,
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
        "job_id": job_id,
        "orientation": orientation,
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
    orientation: str = render.ORIENTATION_PORTRAIT,
    should_abort: Optional[Callable[[], bool]] = None,
) -> Tuple[int, Optional[int]]:
    """把页面序列绘制到打印机并提交打印作业。

    参数：
        page_factory - 页面生成器工厂（每份重新调用，逐页产出位图）
        printer      - 目标打印机名称
        paper        - 纸张名称
        duplex       - 单双面标识
        copies       - 份数
        title        - 打印作业名称
        options      - 渲染选项（缩放比例等）
        orientation  - 纸张方向（portrait / landscape）
        should_abort - 可选回调，返回 True 表示用户已请求取消（每页开始前检查）
    返回：
        (每份的页数, 打印队列中的作业号或 None)
    异常：
        RuntimeError - 打印机不可用或驱动拒绝任务
        render.PrintCanceled - 用户取消（已通过 AbortDoc 回滚，不会留下半截作业）
    """
    paper_id = printers.resolve_paper_id(printer, paper)
    devmode = printers.build_devmode(printer, paper_id, duplex, orientation)

    def aborted() -> bool:
        """是否已收到取消请求。

        参数：无
        返回：
            bool，需要中止打印返回 True
        """
        return bool(should_abort and should_abort())

    # 渲染/转换期间就被取消的话，连作业都不必提交
    if aborted():
        raise render.PrintCanceled()

    try:
        hdc = win32gui.CreateDC("WINSPOOL", printer, devmode)
    except pywintypes.error as exc:
        raise RuntimeError(f"无法连接打印机「{printer}」：{_gdi_reason(exc)}") from exc
    if not hdc:
        raise RuntimeError(f"无法创建打印机上下文：{printer}")

    dc = win32ui.CreateDCFromHandle(hdc)
    started = False
    drawn = 0
    job_id: Optional[int] = None
    try:
        printable_w = dc.GetDeviceCaps(HORZRES)
        printable_h = dc.GetDeviceCaps(VERTRES)
        if printable_w <= 0 or printable_h <= 0:
            raise RuntimeError("打印机未返回有效纸张尺寸，请检查驱动是否正常")

        # 设备分辨率：把位图自然尺寸换算为设备像素时使用（实际大小 / 自定义缩放）
        device_dpi = int(dc.GetDeviceCaps(LOGPIXELSX) or 0) or options.dpi
        # 驱动光栅能力：决定用 BitBlt（1:1 拷贝）还是 StretchBlt（带缩放）绘制
        raster_caps = int(dc.GetDeviceCaps(RASTERCAPS) or 0)
        ratio = options.scale_ratio()
        # 纸张方向以 DC 返回的可打印区宽高为准（DEVMODE 已写入方向）
        sheet_landscape = printable_w > printable_h

        # GDI 的 StartDoc 不返回作业号（实测为 None），于是提交前后各取一次队列快照，
        # 用「新出现的作业号」认领本次作业，供「打印中取消」向队列发取消指令
        jobs_before = printers.job_ids(printer)
        dc.StartDoc(title)
        started = True
        job_id = printers.new_job_id(printer, jobs_before)

        for _ in range(copies):
            page_no = 0
            for image in page_factory():
                page_no += 1
                if aborted():
                    # 逐页检查点：用户取消后不再继续绘制/提交后续页面
                    raise render.PrintCanceled()
                dc.StartPage()
                try:
                    _blit(
                        dc,
                        image,
                        printable_w,
                        printable_h,
                        ratio=ratio,
                        render_dpi=options.dpi,
                        device_dpi=device_dpi,
                        printer=printer,
                        page_no=page_no,
                        sheet_landscape=sheet_landscape,
                        raster_caps=raster_caps,
                    )
                finally:
                    image.close()
                dc.EndPage()
            # 页数按「每份的页数」计（份数由 copies 单独表达），与 Linux 侧 `-n 份数` 的口径一致；
            # 好处：失败提示里的「第 N 页」是文档里的真实页码，不会因为多份而累加
            drawn = page_no
        dc.EndDoc()
        started = False
    except RuntimeError:
        if started:
            _abort(dc)
        raise
    except (pywintypes.error, win32ui.error) as exc:
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

    if drawn == 0:
        raise RuntimeError("文件没有可打印的页面")
    return drawn, job_id


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
        exc - pywintypes.error 或 win32ui.error 异常对象
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


def _raster_text(raster_caps: int) -> str:
    """把驱动光栅能力位转换为可读文本（用于失败诊断）。

    参数：
        raster_caps - GetDeviceCaps(RASTERCAPS) 的返回值
    返回：
        str，如「StretchBlt/BitBlt」或「无StretchBlt/BitBlt」
    """
    items = [
        ("StretchBlt", RC_STRETCHBLT),
        ("BitBlt", RC_BITBLT),
    ]
    return "/".join(name if raster_caps & bit else "无" + name for name, bit in items)


def _blit(
    dc: Any,
    image: Image.Image,
    printable_w: int,
    printable_h: int,
    ratio: Any = None,
    render_dpi: int = RENDER_DPI,
    device_dpi: int = RENDER_DPI,
    printer: str = "",
    page_no: int = 0,
    sheet_landscape: bool = False,
    raster_caps: int = 0,
) -> None:
    """把一张位图绘制到打印机页面上（居中）。

    ratio 为 None 时按「适应页面」等比缩放（可放大）；否则按「位图自然尺寸」乘 ratio
    （1.0 即实际大小）绘制。位图方向与纸张方向不一致时先整页旋转 90°，避免内容被缩成
    一条；内容超出可打印区域时先按可见范围裁剪源位图，再按裁剪结果计算目标矩形，
    因此提交给驱动的目标矩形始终落在可打印区域内。

    缩放由 Pillow 在提交前完成，交给 GDI 的是「目标尺寸的位图」，因此用 1:1 的 BitBlt
    拷贝即可出图 —— 这样可以绕开部分驱动（如惠普主机型/GDI 打印机）不支持跨设备
    StretchBlt 的问题；只有驱动连 BitBlt 都拒绝时才退回 StretchBlt。

    参数：
        dc             - PyCDC 打印机上下文
        image          - PIL 图像对象（内部统一转为 24 位 RGB）
        printable_w    - 可打印区域宽度（设备像素）
        printable_h    - 可打印区域高度（设备像素）
        ratio          - 相对自然尺寸的缩放比例，None 表示适应页面
        render_dpi     - 位图的渲染分辨率
        device_dpi     - 打印机设备分辨率
        printer        - 打印机名称（仅用于失败时的错误说明）
        page_no        - 页序号（仅用于失败时的错误说明）
        sheet_landscape- 纸张是否横向（用于判断是否需要旋转位图）
        raster_caps    - 驱动光栅能力位（仅用于失败时的错误说明）
    返回：
        None
    异常：
        RuntimeError - GDI 绘制失败，错误信息含位图与目标矩形尺寸，便于定位
    """
    if image.size[0] <= 0 or image.size[1] <= 0:
        return

    # GDI 侧统一使用 24 位 RGB：灰度内容在 RGB 中同样呈灰色，
    # 同时避免向部分单色驱动提交带调色板的 8 位位图
    if image.mode != "RGB":
        image = image.convert("RGB")

    # 页面方向与纸张方向不一致时整页旋转 90°，让内容尽量铺满纸张
    if render.should_rotate(image.size[0], image.size[1], sheet_landscape):
        rotated = image.transpose(render.ROTATE_TO_FIT)
        image.close()
        image = rotated

    width, height = image.size

    if ratio is None:
        scale = min(printable_w / float(width), printable_h / float(height))
    else:
        scale = ratio * (device_dpi / float(render_dpi or device_dpi))

    draw_w = width * scale
    draw_h = height * scale
    left = (printable_w - draw_w) / 2.0
    top = (printable_h - draw_h) / 2.0

    box = render.visible_blit_box(
        width, height, left, top, draw_w, draw_h, printable_w, printable_h
    )
    if box is None:
        return
    crop, offset_x, offset_y, target_w, target_h = box
    if crop != (0, 0, width, height):
        cropped = image.crop(crop)
        image.close()
        image = cropped

    # 先由 Pillow 缩放到最终目标尺寸（缩放质量优于 GDI，且让后续拷贝变成 1:1）。
    # 目标尺寸异常巨大（超出单页像素上限）时不做预缩放，改由 GDI 拉伸，避免内存爆掉。
    if image.size != (target_w, target_h) and target_w * target_h <= render.MAX_PAGE_PIXELS:
        scaled = image.resize((target_w, target_h), Image.LANCZOS)
        image.close()
        image = scaled

    bitmap = None
    mem_dc = None
    try:
        # 位图直接以内存流交给 GDI，不再落临时文件
        # （600DPI 的 A4 单页 BMP 可达上百 MB，落盘既慢又占用磁盘空间）
        buffer = io.BytesIO()
        image.save(buffer, "BMP")
        buffer.seek(0)
        bitmap = win32ui.CreateBitmap()
        bitmap.LoadBitmapFile(buffer)
        # 位图已进入 GDI，及时释放内存副本（600DPI 的 A4 单页可达上百 MB）
        buffer = None
        mem_dc = dc.CreateCompatibleDC()
        mem_dc.SelectObject(bitmap)
        errors = []
        if image.size == (target_w, target_h):
            try:
                dc.BitBlt(
                    (offset_x, offset_y),
                    (target_w, target_h),
                    mem_dc,
                    (0, 0),
                    win32con.SRCCOPY,
                )
                return
            except (pywintypes.error, win32ui.error) as exc:
                errors.append(f"BitBlt：{_gdi_reason(exc)}")
        try:
            dc.StretchBlt(
                (offset_x, offset_y),
                (target_w, target_h),
                mem_dc,
                (0, 0),
                image.size,
                win32con.SRCCOPY,
            )
        except (pywintypes.error, win32ui.error) as exc:
            errors.append(f"StretchBlt：{_gdi_reason(exc)}")
            raise RuntimeError(
                "第 %d 页绘制失败（%s）：位图 %dx%d、目标 %dx%d@(%d,%d)、"
                "可打印区 %dx%d、设备 %dDPI、渲染 %dDPI、驱动光栅能力 0x%04X（%s）：%s"
                % (
                    page_no or 1,
                    printer or "默认打印机",
                    width,
                    height,
                    target_w,
                    target_h,
                    offset_x,
                    offset_y,
                    printable_w,
                    printable_h,
                    device_dpi,
                    render_dpi,
                    raster_caps,
                    _raster_text(raster_caps),
                    "；".join(errors),
                )
            ) from exc
    finally:
        if mem_dc is not None:
            try:
                mem_dc.DeleteDC()
            except pywintypes.error:
                pass
        if bitmap is not None:
            try:
                win32gui.DeleteObject(bitmap.GetHandle())
            except pywintypes.error:
                pass

