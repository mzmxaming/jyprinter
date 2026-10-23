"""Linux 静默打印引擎：把页面位图合成 PDF，再交给 CUPS 打印（全程无弹窗）。

实现思路：
    · 与 Windows 侧共用 core.render 的页面渲染（图片 / 文本 / PDF / Office 转 PDF）；
    · 页面按所选纸张尺寸、等比缩放居中写入一个多页 PDF；
    · 通过 `lp` 一次性提交（份数用 -n、双面用 -o sides=、纸张用 -o media=），
      由 CUPS 驱动完成光栅化，避免自行拼接原始打印流带来的兼容性风险；
    · 提交后按作业号跟踪排队情况，队列回落即视为已送达。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator

from PIL import Image

from core import converter_linux as converter
from core import printers_linux as printers
from core import render, settings
from core.utils import remove_file

# 页面内边距（点）：CUPS 驱动普遍存在不可打印边缘，留 12 点（约 4.2mm）避免内容被裁切
PAGE_MARGIN_PT = 12.0

RENDER_DPI = render.RENDER_DPI


def build_page_factory(path: str, ext: str, options: Any = None) -> Any:
    """构建页面生成器工厂（Linux 侧，办公文档走 LibreOffice 转换）。

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
        title   - 打印作业名称，用于 CUPS 队列显示
        options - render.RenderOptions 渲染选项（分辨率、页码范围、缩放、颜色）
    返回：
        dict，包含 printer / format / pages / copies / job_id 等执行结果
    异常：
        RuntimeError - 文件不存在、格式不支持、打印机或 CUPS 不可用等可读错误
    """
    normalized = render.formats.normalize_ext(ext)
    opts = options or render.RenderOptions()
    page_factory, converted = build_page_factory(path, normalized, opts)

    resolved_printer = printer or printers.get_default_printer()
    if not resolved_printer:
        if converted is not None:
            remove_file(converted)
        raise RuntimeError("未找到可用打印机，请先在系统中添加打印机并设置默认打印机")

    # 页面尺寸按用户选择的纸张（程序内置纸张表）合成；media 参数则按驱动实际能力解析，
    # 二者可能不同（例如驱动只上报占位纸张名时 media 为空 → 不传参数，纸张由打印机默认决定）
    requested_paper = paper or printers.DEFAULT_PAPER
    media = printers.resolve_media_name(resolved_printer, requested_paper)
    copies = max(1, int(copies or 1))

    try:
        pdf_path, pages = _compose_pdf(page_factory, requested_paper, opts)
        job_id = printers.submit_job(
            str(pdf_path),
            resolved_printer,
            copies=copies,
            media=media,
            duplex=duplex,
            title=title or Path(path).name,
        )
        # 作业号缺失（CUPS 输出格式异常但作业已被接受）时跳过队列跟踪，不影响任务成功
        if job_id:
            _wait_for_delivery(resolved_printer, job_id)
    finally:
        if converted is not None:
            remove_file(converted)

    return {
        "printer": resolved_printer,
        "format": normalized,
        "pages": pages,
        "copies": copies,
        "job_id": job_id,
    }


def _compose_pdf(
    page_factory: Callable[[], Iterator[Image.Image]],
    paper: str,
    options: render.RenderOptions,
) -> tuple:
    """把页面位图合成为一个多页 PDF。

    参数：
        page_factory - 页面生成器工厂
        paper        - 纸张名称（决定页面尺寸）
        options      - 渲染选项（缩放比例、渲染分辨率）
    返回：
        (PDF 路径, 页数)
    异常：
        RuntimeError - 没有可打印页面或生成失败
    """
    pymupdf = render.import_pymupdf()
    width_pt, height_pt = printers.paper_size_points(paper)
    settings.CONVERTED_DIR.mkdir(parents=True, exist_ok=True)
    output = settings.CONVERTED_DIR / f"print_{int(time.time() * 1000)}_{id(page_factory)}.pdf"

    ratio = options.scale_ratio()
    document = pymupdf.open()
    pages = 0
    try:
        for image in page_factory():
            try:
                page = document.new_page(width=width_pt, height=height_pt)
                _draw_page(page, image, width_pt, height_pt, ratio, options.dpi)
                pages += 1
            finally:
                image.close()
        if pages == 0:
            raise RuntimeError("文件没有可打印的页面")
        document.save(str(output))
    except Exception:
        remove_file(output)
        raise
    finally:
        document.close()

    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("生成打印用 PDF 失败")
    return output, pages


def _draw_page(
    page: Any,
    image: Image.Image,
    width_pt: float,
    height_pt: float,
    ratio: Any = None,
    render_dpi: int = RENDER_DPI,
) -> None:
    """把一张位图绘制到 PDF 页面（居中）。

    ratio 为 None 时按「适应页面」等比缩放（可放大）；否则按位图自然尺寸（由渲染分辨率
    换算为点）乘 ratio 绘制（1.0 即实际大小）。矩形按位图宽高比计算，超出页面的部分
    由页面边界裁切，不会把内容压缩进页面。

    参数：
        page       - PyMuPDF 页面对象
        image      - 页面位图（RGB 或 L 灰度）
        width_pt   - 页面宽度（点）
        height_pt  - 页面高度（点）
        ratio      - 相对自然尺寸的缩放比例，None 表示适应页面
        render_dpi - 位图的渲染分辨率
    返回：
        None
    """
    import io

    img_w, img_h = image.size
    if img_w <= 0 or img_h <= 0:
        return

    if ratio is None:
        target_w = max(1.0, width_pt - PAGE_MARGIN_PT * 2)
        target_h = max(1.0, height_pt - PAGE_MARGIN_PT * 2)
        scale = min(target_w / img_w, target_h / img_h)
    else:
        # 自然尺寸（点）：按渲染分辨率换算，1 英寸 = 72 点
        scale = ratio * (72.0 / float(render_dpi or RENDER_DPI))

    draw_w = img_w * scale
    draw_h = img_h * scale
    left = (width_pt - draw_w) / 2
    top = (height_pt - draw_h) / 2

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    # 矩形按位图宽高比计算，因此不会被 PyMuPDF 二次等比适配；
    # 超出页面的部分由页面边界自然裁切（实测 insert_image 会原样保留给定矩形）
    page.insert_image(
        pymupdf_rect(page, left, top, draw_w, draw_h),
        stream=buffer.getvalue(),
    )


def pymupdf_rect(page: Any, left: float, top: float, width: float, height: float) -> Any:
    """构造 PyMuPDF 矩形对象（延迟导入以避免模块级依赖）。

    参数：
        page   - 页面对象（用于获取模块）
        left   - 左边界
        top    - 上边界
        width  - 宽度
        height - 高度
    返回：
        pymupdf.Rect
    """
    pymupdf = render.import_pymupdf()
    return pymupdf.Rect(left, top, left + width, top + height)


def _wait_for_delivery(printer: str, job_id: str, timeout: int = 180) -> bool:
    """等待作业离开队列（视为已送达打印机）。

    参数：
        printer - 打印机名称
        job_id  - 作业号
        timeout - 等待上限（秒）
    返回：
        bool，作业已离开队列返回 True；超时返回 False（不抛出异常，由上层决定提示）
    """
    deadline = time.time() + max(10, timeout)
    while time.time() < deadline:
        if job_id not in set(printers.list_job_ids(printer)):
            return True
        time.sleep(1.0)
    return False
