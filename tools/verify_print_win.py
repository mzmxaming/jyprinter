"""Windows 出图链路离线校验：把真实打印管线的输出重定向到「Microsoft Print to PDF」。

用途：
    在不能消耗纸张、也无法把待排查打印机搬到开发机时，验证「文件 → 渲染 → GDI 绘制 →
    驱动栅格化」整条链路的出图结果是否正确（页数、纸张方向、落位、缩放、颜色），
    并确认提交给驱动的方式是 1:1 的 BitBlt —— 即在打印机 DC 上不再做 StretchBlt。
    惠普主机型（GDI）打印机等驱动对跨设备 StretchBlt 支持不佳，本项检查用于防止回归。

用法：
    python tools/verify_print_win.py
退出码：
    0 全部通过；1 存在失败项；2 环境不支持（非 Windows 或缺少 PDF 虚拟打印机）
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from core import osinfo  # noqa: E402

PDF_PRINTER = "Microsoft Print to PDF"
SAMPLE_DPI = 50

_results: List[Tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    """记录一项校验结果并打印。

    参数：
        name   - 校验项名称
        ok     - 是否通过
        detail - 附加说明（失败原因或关键数据）
    返回：
        无
    """
    _results.append((name, bool(ok), detail))
    print("[%s] %s%s" % ("通过" if ok else "失败", name, ("：" + detail) if detail else ""))


def _build_test_image(path: Path, size: Tuple[int, int], colors: Tuple[Any, ...]) -> None:
    """生成一张带边框与色块的测试图片（用于回读像素比对）。

    参数：
        path   - 输出图片路径
        size   - 图片尺寸（宽, 高）
        colors - 自上而下的色块颜色序列
    返回：
        无
    """
    width, height = size
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(0, 0, 0), width=6)
    band = height // len(colors)
    for index, color in enumerate(colors):
        top = index * band + band // 10
        bottom = (index + 1) * band - band // 10
        draw.rectangle([width // 10, top, width - width // 10, bottom], fill=color)
    image.save(path)
    image.close()


def _build_test_pdf(path: Path, colors: Tuple[Any, ...]) -> None:
    """生成一份多页测试 PDF，每页一种底色（用于校验页码范围）。

    参数：
        path   - 输出 PDF 路径
        colors - 每页的底色序列
    返回：
        无
    """
    import pymupdf

    doc = pymupdf.open()
    for color in colors:
        page = doc.new_page(width=595, height=842)
        fill = tuple(channel / 255.0 for channel in color)
        page.draw_rect(pymupdf.Rect(100, 100, 495, 742), color=None, fill=fill, width=0)
    doc.save(str(path))
    doc.close()


@contextmanager
def _capture_print_job(output_pdf: Path, stats: Dict[str, int]):
    """把打印作业的输出重定向到指定 PDF，并统计驱动侧的绘制方式。

    参数：
        output_pdf - 捕获输出用的 PDF 路径
        stats      - 用于累计 bitblt / stretchblt 调用次数的字典
    返回：
        上下文管理器
    """
    import win32ui

    real_create = win32ui.CreateDCFromHandle

    class CaptureDC:
        """PyCDC 代理：改写 StartDoc 的输出文件，并统计位图绘制方式。"""

        def __init__(self, dc: Any) -> None:
            object.__setattr__(self, "_dc", dc)

        def StartDoc(self, title: str, *args: Any, **kwargs: Any) -> Any:
            return self._dc.StartDoc(title, str(output_pdf))

        def BitBlt(self, *args: Any, **kwargs: Any) -> Any:
            stats["bitblt"] = stats.get("bitblt", 0) + 1
            return self._dc.BitBlt(*args, **kwargs)

        def StretchBlt(self, *args: Any, **kwargs: Any) -> Any:
            stats["stretchblt"] = stats.get("stretchblt", 0) + 1
            return self._dc.StretchBlt(*args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._dc, name)

    win32ui.CreateDCFromHandle = lambda handle: CaptureDC(real_create(handle))
    try:
        yield
    finally:
        win32ui.CreateDCFromHandle = real_create


def _print_to_pdf(
    source: Path,
    ext: str,
    output_pdf: Path,
    stats: Dict[str, int],
    options: Any = None,
    copies: int = 1,
) -> Dict[str, Any]:
    """调用真实打印引擎，把出图结果写进 PDF。

    参数：
        source     - 待打印文件路径
        ext        - 文件扩展名
        output_pdf - 捕获输出用的 PDF 路径
        stats      - 绘制方式计数字典
        options    - render.RenderOptions 渲染与打印选项
        copies     - 份数
    返回：
        dict，打印引擎的执行结果
    """
    from core import engines, render

    with _capture_print_job(output_pdf, stats):
        return engines.print_file(
            path=str(source),
            ext=ext,
            printer=PDF_PRINTER,
            paper="A4",
            duplex="simplex",
            copies=copies,
            title=source.name,
            options=options or render.RenderOptions(),
        )


def _page_pixmap(pdf_path: Path, index: int = 0):
    """把 PDF 的指定页渲染为位图，用于像素采样。

    参数：
        pdf_path - PDF 路径
        index    - 页码（从 0 开始）
    返回：
        (PyMuPDF Pixmap, 页宽点, 页高点, 总页数)
    """
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    page = doc[index]
    pixmap = page.get_pixmap(dpi=SAMPLE_DPI)
    return pixmap, page.rect.width, page.rect.height, doc.page_count


def _sample(pixmap: Any, xf: float, yf: float) -> Tuple[int, int, int]:
    """按相对位置采样像素颜色。

    参数：
        pixmap - PyMuPDF Pixmap
        xf     - 横向相对位置（0~1）
        yf     - 纵向相对位置（0~1）
    返回：
        (r, g, b) 元组
    """
    x = min(pixmap.width - 1, max(0, int(pixmap.width * xf)))
    y = min(pixmap.height - 1, max(0, int(pixmap.height * yf)))
    return pixmap.pixel(x, y)


def _near(color: Tuple[int, int, int], expect: Tuple[int, int, int], tol: int = 30) -> bool:
    """判断颜色是否接近期望值（用于容忍栅格化与缩放带来的轻微偏差）。

    参数：
        color  - 实测颜色
        expect - 期望颜色
        tol    - 各通道最大允许偏差
    返回：
        bool
    """
    return all(abs(a - b) <= tol for a, b in zip(color, expect))


def check_fit_portrait(tmp: Path) -> None:
    """校验项一：纵向单页图片「适应页面」居中铺满，并以 BitBlt 方式出图。

    参数：
        tmp - 临时目录
    返回：
        无
    """
    source = tmp / "fit.png"
    output = tmp / "fit.pdf"
    _build_test_image(source, (1000, 1400), ((255, 0, 0), (0, 0, 255), (0, 0, 0)))
    stats: Dict[str, int] = {}
    _print_to_pdf(source, "png", output, stats)
    pixmap, width, height, pages = _page_pixmap(output)
    _check("适应页面｜页数与纸张方向", pages == 1 and height > width, "页数 %d，页面 %.0fx%.0f" % (pages, width, height))
    _check("适应页面｜内容居中铺满", _near(_sample(pixmap, 0.5, 0.18), (255, 0, 0))
           and _near(_sample(pixmap, 0.5, 0.46), (0, 0, 255))
           and _near(_sample(pixmap, 0.5, 0.75), (0, 0, 0)),
           "上/中/下色块 = %s / %s / %s" % (_sample(pixmap, 0.5, 0.18), _sample(pixmap, 0.5, 0.46), _sample(pixmap, 0.5, 0.75)))
    _check("适应页面｜四边留白不裁切", _near(_sample(pixmap, 0.5, 0.02), (255, 255, 255)),
           "页顶采样 = %s" % (_sample(pixmap, 0.5, 0.02),))
    _check("出图方式｜打印机 DC 上只用 1:1 BitBlt",
           stats.get("bitblt", 0) >= 1 and stats.get("stretchblt", 0) == 0,
           "BitBlt %d 次，StretchBlt %d 次" % (stats.get("bitblt", 0), stats.get("stretchblt", 0)))


def check_landscape_rotate(tmp: Path) -> None:
    """校验项二：纵向内容按横向纸张打印时旋转 90°，页面变横向且内容铺满。

    参数：
        tmp - 临时目录
    返回：
        无
    """
    from core import render

    source = tmp / "land.png"
    output = tmp / "land.pdf"
    _build_test_image(source, (1000, 1400), ((255, 0, 0), (0, 0, 255), (0, 0, 0)))
    stats: Dict[str, int] = {}
    result = _print_to_pdf(
        source, "png", output, stats, options=render.RenderOptions(orientation="landscape")
    )
    pixmap, width, height, pages = _page_pixmap(output)
    _check("横向打印｜页面变横向", height < width, "页面 %.0fx%.0f，引擎判定 %s" % (width, height, result.get("orientation")))
    # 逆时针旋转后：原页面顶部落在纸张左缘，色块沿横向排列
    _check("横向打印｜旋转方向（顶→左）",
           _near(_sample(pixmap, 0.18, 0.5), (255, 0, 0))
           and _near(_sample(pixmap, 0.46, 0.5), (0, 0, 255))
           and _near(_sample(pixmap, 0.75, 0.5), (0, 0, 0)),
           "左/中/右色块 = %s / %s / %s" % (_sample(pixmap, 0.18, 0.5), _sample(pixmap, 0.46, 0.5), _sample(pixmap, 0.75, 0.5)))
    _check("横向打印｜内容铺满纸张", _near(_sample(pixmap, 0.5, 0.02), (255, 255, 255))
           and not _near(_sample(pixmap, 0.5, 0.5), (255, 255, 255)),
           "页顶/页中采样 = %s / %s" % (_sample(pixmap, 0.5, 0.02), _sample(pixmap, 0.5, 0.5)))


def check_page_range(tmp: Path) -> None:
    """校验项三：多页文件的页码范围只输出选中页。

    参数：
        tmp - 临时目录
    返回：
        无
    """
    from core import render

    source = tmp / "range.pdf"
    output = tmp / "range_out.pdf"
    _build_test_pdf(source, ((255, 0, 0), (0, 128, 0), (0, 0, 255)))
    stats: Dict[str, int] = {}
    _print_to_pdf(source, "pdf", output, stats, options=render.RenderOptions(pages="2"))
    pixmap, _width, _height, pages = _page_pixmap(output)
    _check("页码范围｜只打印第 2 页", pages == 1 and _near(_sample(pixmap, 0.5, 0.5), (0, 128, 0)),
           "输出页数 %d，页面中部颜色 %s" % (pages, _sample(pixmap, 0.5, 0.5)))


def check_actual_scale(tmp: Path) -> None:
    """校验项四：「实际大小」按原始物理尺寸出图并居中，不铺满页面。

    参数：
        tmp - 临时目录
    返回：
        无
    """
    from core import render

    source = tmp / "actual.png"
    output = tmp / "actual.pdf"
    _build_test_image(source, (1000, 1400), ((255, 0, 0), (0, 0, 255), (0, 0, 0)))
    stats: Dict[str, int] = {}
    _print_to_pdf(source, "png", output, stats, options=render.RenderOptions(scale="actual"))
    pixmap, _width, _height, _pages = _page_pixmap(output)
    _check("实际大小｜左右留白且居中出图",
           _near(_sample(pixmap, 0.1, 0.4), (255, 255, 255))
           and not _near(_sample(pixmap, 0.5, 0.4), (255, 255, 255)),
           "左侧采样 = %s，中部采样 = %s" % (_sample(pixmap, 0.1, 0.4), _sample(pixmap, 0.5, 0.4)))


def check_mono(tmp: Path) -> None:
    """校验项五：黑白模式输出的彩块变为灰阶（R=G=B）。

    参数：
        tmp - 临时目录
    返回：
        无
    """
    from core import render

    source = tmp / "mono.png"
    output = tmp / "mono.pdf"
    _build_test_image(source, (1000, 1400), ((255, 0, 0), (0, 0, 255), (0, 0, 0)))
    stats: Dict[str, int] = {}
    _print_to_pdf(source, "png", output, stats, options=render.RenderOptions(color="mono"))
    pixmap, _width, _height, _pages = _page_pixmap(output)
    red = _sample(pixmap, 0.5, 0.18)
    blue = _sample(pixmap, 0.5, 0.46)
    _check("黑白模式｜彩块输出为灰阶",
           abs(red[0] - red[1]) <= 12 and abs(red[1] - red[2]) <= 12
           and abs(blue[0] - blue[1]) <= 12 and abs(blue[1] - blue[2]) <= 12,
           "红色块 %s，蓝色块 %s" % (red, blue))


def main() -> int:
    """执行全部校验项并汇总结果。

    参数：无
    返回：
        int，进程退出码
    """
    if not osinfo.IS_WINDOWS:
        print("非 Windows 平台，跳过（本项校验依赖 Windows 打印子系统）")
        return 2

    import win32print

    names = []
    try:
        names = [item[2] for item in win32print.EnumPrinters(2)]
    except Exception:  # noqa: BLE001
        names = []
    if PDF_PRINTER not in names:
        print("未找到虚拟打印机「%s」，请在「Windows 功能」中启用后重试" % PDF_PRINTER)
        return 2

    from core import engines  # noqa: F401
    try:
        import pymupdf  # noqa: F401
    except Exception:  # noqa: BLE001
        print("缺少 PyMuPDF，无法回读 PDF 校验结果")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="jyverify_"))
    try:
        check_fit_portrait(tmp)
        check_landscape_rotate(tmp)
        check_page_range(tmp)
        check_actual_scale(tmp)
        check_mono(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for name, ok, _ in _results if not ok]
    print("\n共 %d 项，通过 %d 项，失败 %d 项" % (len(_results), len(_results) - len(failed), len(failed)))
    if failed:
        print("失败项：%s" % "；".join(failed))
        return 1
    print("全部通过：出图链路（渲染 → GDI → 驱动）输出正确，且未在打印机 DC 上使用 StretchBlt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
