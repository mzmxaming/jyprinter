"""跨平台页面渲染核心：把各类文件渲染成「页面位图序列」，供打印与预览共用。

设计说明：
    · 本模块只负责「文件 → 页面位图」，不涉及任何打印/绘图 API，因此 Windows 与 Linux 共用；
    · Windows 侧由 engines_win.py 用 GDI 把位图送入打印机，Linux 侧由 engines_linux.py
      把位图合成 PDF 后交给 CUPS；
    · 字体按平台在系统字体目录中查找，含中文字体缺失时的明确报错（Linux 常见问题）。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from core import formats
from core.osinfo import IS_WINDOWS
from core.utils import decode_text, import_pymupdf

# 位图渲染分辨率（DPI）：越高越清晰，同时占用更多内存
RENDER_DPI = 300

# 打印质量预设 → 渲染分辨率
QUALITY_DPI = {"draft": 150, "normal": 300, "high": 600}

# 单页位图像素上限：超大页面 + 高分辨率时自动收敛渲染倍率，避免内存耗尽
MAX_PAGE_PIXELS = 80_000_000

# 出纸方向
ORIENTATION_AUTO = "auto"          # 跟随文档首页方向
ORIENTATION_PORTRAIT = "portrait"  # 纵向
ORIENTATION_LANDSCAPE = "landscape"  # 横向

# 源页面方向与纸张方向不一致时的旋转方式：逆时针 90°（页面顶部朝纸张左缘），
# 阅读时把纸顺时针转一下即可
ROTATE_TO_FIT = Image.ROTATE_90


class PrintCanceled(RuntimeError):
    """打印任务被用户取消（协作式中止）。

    打印引擎在逐页绘制过程中检测到取消标记时抛出；调用方据此把任务记为「已取消」
    而不是「失败」。属于 RuntimeError 子类，因此撰写过程中已有的回滚逻辑
    （AbortDoc 等）依然会执行。
    """

    def __init__(self, message: str = "用户已取消打印") -> None:
        """构造取消异常。

        参数：
            message - 面向用户的说明文本
        返回：
            None
        """
        super().__init__(message)
        self.message = message

# A4 纸张物理尺寸（英寸）与文本排版参数
A4_WIDTH_IN = 8.27
A4_HEIGHT_IN = 11.69
PAGE_MARGIN_IN = 0.59          # 页边距 15mm
BODY_FONT_PT = 10.5            # 五号字
LINE_SPACING = 1.6

# 字体候选：Windows 用微软雅黑等，Linux 用 Noto / 文泉驿 / 思源等常见中文字体
FONT_CANDIDATES = (
    "msyh.ttc",
    "msyhl.ttc",
    "simhei.ttf",
    "simsun.ttc",
    "NotoSansCJK-Regular.ttc",
    "NotoSansCJKsc-Regular.otf",
    "NotoSansSC-Regular.otf",
    "SourceHanSansCN-Regular.otf",
    "wqy-zenhei.ttc",
    "wqy-microhei.ttc",
    "DroidSansFallbackFull.ttf",
    "arial.ttf",
    "DejaVuSans.ttf",
)

# Windows 字体目录
_WINDOWS_FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
# Linux 字体目录（按优先级）
_LINUX_FONT_DIRS = (
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path.home() / ".local" / "share" / "fonts",
)

_font_cache: dict = {}


def font_search_dirs() -> List[Path]:
    """返回当前平台的字体搜索目录列表。

    参数：无
    返回：
        list[Path]，存在且可枚举的字体目录
    """
    if IS_WINDOWS:
        return [_WINDOWS_FONT_DIR]
    return [item for item in _LINUX_FONT_DIRS if item.exists()]


def find_font_file() -> Optional[Path]:
    """在系统字体目录中查找可用的中文字体文件（结果缓存）。

    参数：无
    返回：
        Path 或 None，未找到时返回 None
    """
    if "path" in _font_cache:
        return _font_cache["path"]

    found: Optional[Path] = None
    lowered = {name.lower(): name for name in FONT_CANDIDATES}
    for directory in font_search_dirs():
        try:
            for item in directory.rglob("*"):
                if not item.is_file():
                    continue
                if item.name.lower() in lowered:
                    found = item
                    break
        except (OSError, PermissionError):
            continue
        if found:
            break

    _font_cache["path"] = found
    return found


def load_font(size: int) -> Any:
    """按优先级加载系统中文字体。

    参数：
        size - 字号（像素）
    返回：
        ImageFont 对象；找不到字体文件时回退 Pillow 内置字体
    """
    path = find_font_file()
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        # Pillow < 10.1 不支持 size 参数
        return ImageFont.load_default()


def ensure_cjk_font(text: str) -> None:
    """校验文本渲染所需的中文字体是否可用。

    参数：
        text - 待渲染文本
    返回：
        None
    异常：
        RuntimeError - 文本含非 ASCII 字符但系统未安装任何中文字体
    """
    if text.isascii():
        return
    if find_font_file() is not None:
        return
    raise RuntimeError(
        "系统未安装中文字体，无法渲染中文内容，请先安装字体后重试："
        "sudo apt install fonts-noto-cjk（Debian/Ubuntu/银河麒麟）"
        "或 sudo yum install wqy-zenhei-fonts（RPM 系）"
    )


def pdf_page_count(path: Path) -> int:
    """读取 PDF 页数。

    参数：
        path - PDF 文件路径
    返回：
        int，页数
    异常：
        RuntimeError - PDF 无法解析
    """
    pymupdf = import_pymupdf()
    try:
        with pymupdf.open(str(path)) as document:
            return max(1, document.page_count)
    except Exception as exc:
        raise RuntimeError(f"PDF 无法解析：{exc}") from exc


def iter_pdf_pages(path: Path, dpi: int = RENDER_DPI) -> Iterator[Image.Image]:
    """使用 PyMuPDF 逐页渲染 PDF 为位图页。

    参数：
        path - PDF 文件路径
        dpi  - 渲染分辨率
    返回：
        Iterator[Image.Image]，逐页位图
    异常：
        RuntimeError - 文件损坏、加密或渲染失败
    """
    pymupdf = import_pymupdf()
    try:
        document = pymupdf.open(str(path))
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise RuntimeError("PDF 文件损坏或无法打开") from exc

    try:
        if document.needs_pass:
            raise RuntimeError("PDF 已加密，请先解除密码后再打印")
        for page in document:
            # 高分辨率叠加超大页面会占用过多内存，这里按像素上限自动收敛渲染倍率
            scale = dpi / 72.0
            rect = page.rect
            pixels = (rect.width * scale) * (rect.height * scale)
            if pixels > MAX_PAGE_PIXELS:
                scale *= math.sqrt(MAX_PAGE_PIXELS / pixels)
            matrix = pymupdf.Matrix(scale, scale)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            yield Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    finally:
        document.close()


def iter_image_pages(path: Path) -> Iterator[Image.Image]:
    """读取图片文件并逐帧产出位图页（GIF / TIFF 多页自动展开）。

    超大图片（超过 MAX_PAGE_PIXELS）会按上限等比缩小后再输出：图片本身没有固定物理尺寸、
    打印时也会按纸张缩放，缩小不会影响观感，却可以避免位图过大导致 GDI / 内存失败
    （Windows 打印链路上表现为 StretchBlt failed）。

    参数：
        path - 图片文件路径
    返回：
        Iterator[Image.Image]，逐页位图
    异常：
        RuntimeError - 图片损坏或格式不受 Pillow 支持
    """
    try:
        with Image.open(path) as handle:
            frames = int(getattr(handle, "n_frames", 1))
            for index in range(max(1, frames)):
                handle.seek(index)
                image = handle.convert("RGB")
                pixels = image.size[0] * image.size[1]
                if pixels > MAX_PAGE_PIXELS:
                    ratio = math.sqrt(MAX_PAGE_PIXELS / float(pixels))
                    target = (
                        max(1, int(image.size[0] * ratio)),
                        max(1, int(image.size[1] * ratio)),
                    )
                    shrunk = image.resize(target, Image.LANCZOS)
                    image.close()
                    image = shrunk
                yield image
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"图片无法解析：{exc}") from exc


def iter_text_pages(path: Path, dpi: int = RENDER_DPI) -> Iterator[Image.Image]:
    """把纯文本按 A4 版式分页渲染为位图页。

    参数：
        path - 文本文件路径
        dpi  - 渲染分辨率
    返回：
        Iterator[Image.Image]，逐页位图
    异常：
        RuntimeError - 文件读取失败或缺少中文字体
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"文本文件读取失败：{exc}") from exc

    text = decode_text(raw)
    ensure_cjk_font(text)

    page_w = int(A4_WIDTH_IN * dpi)
    page_h = int(A4_HEIGHT_IN * dpi)
    margin = int(PAGE_MARGIN_IN * dpi)
    font = load_font(int(BODY_FONT_PT / 72.0 * dpi))
    line_height = int(getattr(font, "size", 40) * LINE_SPACING)
    max_width = page_w - margin * 2
    lines_per_page = max(1, (page_h - margin * 2) // line_height)

    # 先做一次排版测量（只用于换行，不产出页面）
    probe = Image.new("RGB", (page_w, page_h), "white")
    probe_draw = ImageDraw.Draw(probe)
    wrapped: List[str] = []
    for raw_line in text.splitlines():
        expanded = raw_line.expandtabs(4)
        if not expanded.strip():
            wrapped.append("")
            continue
        wrapped.extend(wrap_line(probe_draw, expanded, font, max_width))
    del probe

    if not wrapped:
        wrapped = [""]

    for start in range(0, len(wrapped), lines_per_page):
        chunk = wrapped[start : start + lines_per_page]
        image = Image.new("RGB", (page_w, page_h), "white")
        draw = ImageDraw.Draw(image)
        y = margin
        for line in chunk:
            if line:
                draw.text((margin, y), line, font=font, fill="black")
            y += line_height
        yield image


def wrap_line(draw: Any, text: str, font: Any, max_width: int) -> List[str]:
    """按像素宽度对单行文本做自动换行（兼容中英文混排）。

    参数：
        draw      - ImageDraw 对象，用于测量文本宽度
        text      - 待换行的文本行
        font      - 字体对象
        max_width - 单行最大像素宽度
    返回：
        list[str]，换行后的文本行集合
    """
    lines: List[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if not current or draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        # 优先在空格处断行，避免英文单词被硬切
        cut = current.rfind(" ")
        if cut > len(current) * 0.6:
            lines.append(current[:cut])
            current = current[cut + 1 :] + char
        else:
            lines.append(current)
            current = char
    lines.append(current)
    return lines


@dataclass
class RenderOptions:
    """一次打印（或预览）的渲染选项。

    字段：
        dpi           - 渲染分辨率，取值见 QUALITY_DPI
        pages         - 页码范围文本，空表示全部；支持 "1-3,5"、"2-"（第 2 页起）、"-3"（前 3 页）
        scale         - 缩放方式：fit 适应页面 / actual 实际大小 / custom 自定义百分比
        scale_percent - scale=custom 时的百分比（10~400）
        color         - 颜色模式：color 彩色 / mono 黑白（灰度）
        orientation   - 出纸方向：auto 跟随文档首页 / portrait 纵向 / landscape 横向
        fit_table     - 表格类文件转换前是否设置「适应一页宽」（仅 Windows 生效）
    """

    dpi: int = RENDER_DPI
    pages: str = ""
    scale: str = "fit"
    scale_percent: int = 100
    color: str = "color"
    orientation: str = ORIENTATION_AUTO
    fit_table: bool = True

    def wants_landscape(self) -> Optional[bool]:
        """返回显式要求的横向与否。

        参数：无
        返回：
            True 横向 / False 纵向 / None 表示自动（跟随文档）
        """
        name = str(self.orientation or ORIENTATION_AUTO).lower()
        if name == ORIENTATION_LANDSCAPE:
            return True
        if name == ORIENTATION_PORTRAIT:
            return False
        return None

    def scale_ratio(self) -> Optional[float]:
        """返回相对「自然尺寸」的缩放比例。

        参数：无
        返回：
            float 或 None，None 表示「适应页面」，由绘制方按可打印区域自行计算比例
        """
        if self.scale == "actual":
            return 1.0
        if self.scale == "custom":
            percent = int(self.scale_percent or 100)
            percent = max(10, min(400, percent))
            return percent / 100.0
        return None

    def is_mono(self) -> bool:
        """是否按黑白（灰度）渲染。

        参数：无
        返回：
            bool，黑白返回 True
        """
        return str(self.color or "").lower() == "mono"


def resolve_orientation(source: Any, ext: str, options: RenderOptions) -> str:
    """确定本次出纸方向（纸张方向）。

    显式指定时直接采用；auto 时取源文档首页方向：PDF 读首页页面框（已含 /Rotate 旋转），
    图片按宽高比判定，其余（文本等）按纵向。

    参数：
        source  - 源文件路径；Office 文件请传已转换的 PDF 并配 "pdf" 扩展名
        ext     - 文件扩展名
        options - 渲染选项
    返回：
        str，"portrait" 或 "landscape"
    """
    forced = options.wants_landscape()
    if forced is not None:
        return ORIENTATION_LANDSCAPE if forced else ORIENTATION_PORTRAIT

    normalized = formats.normalize_ext(ext)
    try:
        if normalized in formats.PDF_EXTS:
            pymupdf = import_pymupdf()
            with pymupdf.open(str(source)) as document:
                if document.page_count:
                    rect = document[0].rect
                    return (
                        ORIENTATION_LANDSCAPE
                        if rect.width > rect.height
                        else ORIENTATION_PORTRAIT
                    )
        elif normalized in formats.IMAGE_EXTS:
            with Image.open(str(source)) as handle:
                width, height = handle.size
            return ORIENTATION_LANDSCAPE if width > height else ORIENTATION_PORTRAIT
    except Exception:
        # 判定失败时按纵向处理，不影响后续打印
        pass
    return ORIENTATION_PORTRAIT


def oriented_size(width: float, height: float, orientation: str) -> Tuple[float, float]:
    """按出纸方向换算页面尺寸（横向时交换宽高）。

    参数：
        width/height - 纵向纸张尺寸（点或像素）
        orientation  - portrait 纵向 / landscape 横向
    返回：
        (宽, 高)，横向返回 (长边, 短边)
    """
    long_side = max(float(width), float(height))
    short_side = min(float(width), float(height))
    if str(orientation or "").lower() == ORIENTATION_LANDSCAPE:
        return long_side, short_side
    return short_side, long_side


def should_rotate(width: int, height: int, sheet_landscape: bool) -> bool:
    """判断位图是否需要旋转 90° 才能铺满当前纸张。

    参数：
        width/height   - 位图尺寸（像素）
        sheet_landscape- 纸张是否横向
    返回：
        bool，位图方向与纸张方向不一致时返回 True
    """
    if width <= 0 or height <= 0 or width == height:
        return False
    return (width > height) != bool(sheet_landscape)


def parse_page_range(text: str) -> Optional[List[Tuple[int, Optional[int]]]]:
    """解析页码范围文本。

    支持逗号分隔的页码与区间（中英文逗号、全角连接号均可）：
        "1-3,5"  第 1~3 页与第 5 页
        "2-"     第 2 页起至末尾
        "-3"     第 1~3 页
    参数：
        text - 页码范围文本，空白表示全部页面
    返回：
        None（表示全部页面）或 [(起页, 止页或 None)] 列表
    异常：
        ValueError - 文本格式不合法
    """
    raw = str(text or "").strip()
    if not raw:
        return None

    ranges: List[Tuple[int, Optional[int]]] = []
    for chunk in raw.replace("，", ",").replace("－", "-").replace("—", "-").split(","):
        item = chunk.strip()
        if not item:
            continue
        if "-" in item:
            left, _, right = item.partition("-")
            start_text, end_text = left.strip(), right.strip()
            if (start_text and not start_text.isdigit()) or (end_text and not end_text.isdigit()):
                raise ValueError(f"页码范围无法识别：{item}")
            if not start_text and not end_text:
                raise ValueError(f"页码范围无法识别：{item}")
            start = int(start_text) if start_text else 1
            end = int(end_text) if end_text else None
            if start < 1 or (end is not None and end < 1):
                raise ValueError(f"页码必须大于 0：{item}")
            if end is not None and end < start:
                raise ValueError(f"页码范围起止颠倒：{item}")
            ranges.append((start, end))
        else:
            if not item.isdigit() or int(item) < 1:
                raise ValueError(f"页码范围无法识别：{item}")
            ranges.append((int(item), int(item)))

    return ranges or None


def page_selected(ranges: Optional[List[Tuple[int, Optional[int]]]], index: int) -> bool:
    """判断第 index 页（从 1 开始）是否落在所选范围内。

    参数：
        ranges - parse_page_range 的结果，None 表示全部页面
        index  - 页序号（从 1 开始）
    返回：
        bool，命中返回 True
    """
    if not ranges:
        return True
    for start, end in ranges:
        if index >= start and (end is None or index <= end):
            return True
    return False


def apply_options(pages: Iterator[Image.Image], options: RenderOptions) -> Iterator[Image.Image]:
    """按渲染选项筛选页面并做颜色处理（页面范围 + 黑白灰度）。

    参数：
        pages   - 原始页面位图迭代器
        options - 渲染选项
    返回：
        Iterator[Image.Image]，处理后的页面（黑白模式下位图模式为 "L"）
    异常：
        RuntimeError - 所选页码范围在文档中没有任何对应页面
        ValueError   - 页码范围格式不合法
    """
    ranges = parse_page_range(options.pages)
    mono = options.is_mono()
    index = 0
    emitted = 0

    for image in pages:
        index += 1
        if not page_selected(ranges, index):
            image.close()
            continue
        if mono:
            gray = image.convert("L")
            image.close()
            image = gray
        emitted += 1
        yield image

    if emitted == 0:
        raise RuntimeError(f"所选页码范围没有对应页面（该文档共 {index} 页），请重新设置页面范围")


def visible_blit_box(
    width: int,
    height: int,
    left: float,
    top: float,
    draw_w: float,
    draw_h: float,
    area_w: int,
    area_h: int,
) -> Optional[Tuple[Tuple[int, int, int, int], int, int, int, int]]:
    """计算位图在目标区域内的可见范围，并把源位图裁剪到该范围。

    位图的目标矩形可能超出目标区域（「实际大小」或自定义放大时）。此时若直接把超大矩形
    交给 GDI，会因逻辑坐标只有 16 位、或驱动无法处理而报 `StretchBlt failed`。这里先把
    源位图按可见部分裁剪，再按裁剪结果给出新的目标矩形 —— 裁剪后目标矩形必定落在目标
    区域内，等同于「按区域裁切」的视觉效果。

    参数：
        width/height  - 源位图尺寸（像素）
        left/top      - 位图目标矩形左上角（目标坐标，可能为负）
        draw_w/draw_h - 位图目标矩形尺寸
        area_w/area_h - 目标区域尺寸（如打印机可打印区，像素）
    返回：
        None 表示完全不可见；否则返回 (裁剪框, 目标 x, 目标 y, 目标宽, 目标高)，
        其中裁剪框为源位图的整数像素范围 (x0, y0, x1, y1)
    """
    if width <= 0 or height <= 0 or draw_w <= 0 or draw_h <= 0 or area_w <= 0 or area_h <= 0:
        return None

    x0 = max(0.0, left)
    y0 = max(0.0, top)
    x1 = min(float(area_w), left + draw_w)
    y1 = min(float(area_h), top + draw_h)
    if x1 - x0 < 1.0 or y1 - y0 < 1.0:
        return None

    scale_x = draw_w / float(width)
    scale_y = draw_h / float(height)

    crop = (
        max(0, int((x0 - left) / scale_x)),
        max(0, int((y0 - top) / scale_y)),
        min(width, int(round((x1 - left) / scale_x))),
        min(height, int(round((y1 - top) / scale_y))),
    )
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        return None

    offset_x = max(0, min(int(round(x0)), area_w - 1))
    offset_y = max(0, min(int(round(y0)), area_h - 1))
    target_w = max(1, min(int(round((crop[2] - crop[0]) * scale_x)), area_w - offset_x))
    target_h = max(1, min(int(round((crop[3] - crop[1]) * scale_y)), area_h - offset_y))
    return crop, offset_x, offset_y, target_w, target_h


def build_page_factory(
    path: str,
    ext: str,
    office_converter: Callable[[Path, str], Path],
    options: Optional[RenderOptions] = None,
) -> Tuple[Callable[[], Iterator[Image.Image]], Optional[Path]]:
    """构建页面生成器工厂（打印与预览共用）。

    参数：
        path             - 文件绝对路径
        ext              - 文件扩展名
        office_converter - 办公文档转 PDF 的函数，签名 (源文件, 扩展名) -> PDF 路径
        options          - 渲染选项（分辨率、页码范围、缩放、颜色），None 表示全部默认
    返回：
        (page_factory, converted_pdf)：工厂每次调用都会重新产出全部页面；
        converted_pdf 非 None 表示该文件是临时转换产物，由调用方决定何时清理
    异常：
        RuntimeError - 文件不存在或格式不受支持
    """
    source = Path(path)
    if not source.exists():
        raise RuntimeError("文件不存在或已被清理")

    normalized = formats.normalize_ext(ext)
    if normalized not in formats.SUPPORTED_EXTS:
        raise RuntimeError(f"暂不支持的文件格式：.{normalized}")

    opts = options or RenderOptions()
    converted: Optional[Path] = None

    if normalized in formats.OFFICE_EXTS:
        converted = office_converter(source, normalized, opts)
        base: Callable[[], Iterator[Image.Image]] = lambda: iter_pdf_pages(converted, opts.dpi)
    elif normalized in formats.PDF_EXTS:
        base = lambda: iter_pdf_pages(source, opts.dpi)
    elif normalized in formats.TEXT_EXTS:
        base = lambda: iter_text_pages(source, opts.dpi)
    else:
        # 图片按原始像素输出，分辨率选项对其无效（缩放选项仍按渲染分辨率换算尺寸）
        base = lambda: iter_image_pages(source)

    return (lambda: apply_options(base(), opts), converted)


def count_pages(
    path: str, ext: str, office_converter: Callable[[Path, str], Path]
) -> int:
    """统计文件总页数（预览用，尽量走轻量路径）。

    参数：
        path             - 文件绝对路径
        ext              - 文件扩展名
        office_converter - 办公文档转 PDF 的函数
    返回：
        int，总页数（至少为 1）
    异常：
        RuntimeError - 文件不存在或格式不受支持
    """
    source = Path(path)
    if not source.exists():
        raise RuntimeError("文件不存在或已被清理")

    normalized = formats.normalize_ext(ext)
    if normalized not in formats.SUPPORTED_EXTS:
        raise RuntimeError(f"暂不支持的文件格式：.{normalized}")

    if normalized in formats.OFFICE_EXTS:
        return pdf_page_count(office_converter(source, normalized))
    if normalized in formats.PDF_EXTS:
        return pdf_page_count(source)
    if normalized in formats.TEXT_EXTS:
        # 文本行数按 DPI 等比缩放，页数与 DPI 无关
        return sum(1 for _ in iter_text_pages(source, RENDER_DPI))
    with Image.open(source) as handle:
        return max(1, int(getattr(handle, "n_frames", 1)))
