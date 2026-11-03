"""A3 → A4 页面拆分（服务端矢量裁剪，基于 PyMuPDF）。

与浏览器端工具的分工：
    · 浏览器端（pdf.js + jsPDF）：任何格式都能处理，但导出的是「画布 → JPEG → PDF」的位图；
    · 本模块（服务端）：只处理 PDF，用 PyMuPDF 的 `show_pdf_page(clip=…)` 把源页面的某一段
      **原样搬运**到 A4 新页上 —— 文字仍是文字、矢量仍是矢量，也不经过位图重采样。

设计要点：
    · 全程在内存里完成（`pymupdf.open(stream=…)` → `tobytes()`），不落临时文件，
      契合「服务端不留用户文件」的既定原则；
    · 裁切位置用「页面宽度的百分比」表示，与前端红线的位置一致；
      同一页给出多个比例就从左到右切成多段；
    · 每段等比缩放居中放入一页 A4：**对半拆（0.5）时半张 A3 正好等于 A4，实际就是 1:1**；
    · 某页没有裁切比例 → 整页缩放到一张 A4（与浏览器端行为保持一致）；
    · 页面的 /Rotate 会被正确换算（先把「视觉比例」还原成源页坐标再取裁剪框）。

对外只暴露两个函数：
    `normalize_cuts()`  —— 校验并规整前端传来的裁切比例（纯函数，可离线单测）
    `split_pdf_bytes()` —— 真正做拆分，返回新的 A4 PDF 字节
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from core import render

# A4 尺寸（点，1 点 = 1/72 英寸）：210mm × 297mm
A4_WIDTH_PT = 595.276
A4_HEIGHT_PT = 841.89

# 相邻裁切线的最近间距（比例）：太近的区间会产生宽度为 0 的碎片，直接忽略
_MIN_SEGMENT_RATIO = 1e-4


def segment_ranges(ratios: Sequence[Any]) -> List[Tuple[float, float]]:
    """把一个页面的裁切比例换算成若干「左闭右开」区间（纯函数）。

    参数：
        ratios - 裁切比例序列（0~1，可乱序、可重复）
    返回：
        list[(起点比例, 终点比例)]，例如 [0.33, 0.66] → [(0.0, 0.33), (0.33, 0.66), (0.66, 1.0)]
    """
    marks: List[float] = []
    for item in ratios or []:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        if 0.0 < value < 1.0:
            marks.append(value)

    positions = [0.0] + sorted(set(marks)) + [1.0]
    segments: List[Tuple[float, float]] = []
    for start, end in zip(positions, positions[1:]):
        if end - start > _MIN_SEGMENT_RATIO:
            segments.append((start, end))
    return segments


def normalize_cuts(payload: Any, page_count: int) -> List[List[float]]:
    """校验并规整前端传来的裁切数据（纯函数）。

    参数：
        payload    - 形如 [[0.5], [], [0.33, 0.66]]：每个元素是**对应页**的裁切比例列表
        page_count - 源 PDF 的页数
    返回：
        list[list[float]]，长度等于 page_count；缺的页按「整页」处理（空列表）
    异常：
        ValueError - 结构不是「列表的列表」，或比例不是 0~1 之间的数字
    """
    count = max(0, int(page_count or 0))
    if payload is None or payload == "":
        return [[] for _ in range(count)]
    if not isinstance(payload, list):
        raise ValueError("裁切参数格式不正确：应为「每页比例列表」组成的数组")

    result: List[List[float]] = []
    for index in range(count):
        raw = payload[index] if index < len(payload) else []
        if raw in (None, ""):
            result.append([])
            continue
        if not isinstance(raw, (list, tuple)):
            raise ValueError("第 %d 页的裁切参数格式不正确：应为比例数组" % (index + 1))
        marks: List[float] = []
        for item in raw:
            try:
                value = float(item)
            except (TypeError, ValueError):
                raise ValueError("第 %d 页的裁切位置不是数字：%r" % (index + 1, item))
            if not 0.0 < value < 1.0:
                raise ValueError(
                    "第 %d 页的裁切位置需在 0 与 1 之间（表示距左边的比例）：%s" % (index + 1, value)
                )
            marks.append(value)
        result.append(marks)
    return result


def count_output_pages(payload: Any, page_count: int) -> int:
    """按裁切参数预估输出页数（纯函数，用于给用户提示）。

    参数：
        payload    - 同 normalize_cuts
        page_count - 源 PDF 页数
    返回：
        int，拆分后 A4 页数（某页无裁切线时按 1 页计）
    """
    total = 0
    for ratios in normalize_cuts(payload, page_count):
        total += max(1, len(segment_ranges(ratios)))
    return total


def _clip_rect(page: Any, pymupdf: Any, start: float, end: float) -> Any:
    """按「视觉宽度的比例区间」在源页上切出一条竖直裁剪带。

    调用前已通过 `remove_rotation()` 把页面的 /Rotate 烘焙进内容，因此这里可以直接用
    视觉矩形 `page.rect` 计算，不必再推导旋转方向。

    参数：
        page     - 源页（PyMuPDF Page）
        pymupdf  - PyMuPDF 模块
        start    - 起始比例（0~1，自左向右）
        end      - 结束比例
    返回：
        pymupdf.Rect，源页坐标下的裁剪矩形
    """
    visual = page.rect
    return pymupdf.Rect(
        visual.x0 + start * visual.width,
        visual.y0,
        visual.x0 + end * visual.width,
        visual.y1,
    )


def split_pdf_bytes(data: bytes, cuts: Any = None) -> bytes:
    """把 PDF 的每一页沿垂直裁切线拆成若干 A4 页，返回新的 PDF 字节（矢量）。

    参数：
        data - 源 PDF 的字节内容
        cuts - 裁切参数，同 normalize_cuts；为空表示每页整页缩放到一张 A4
    返回：
        bytes，新的 A4 PDF；每段等比缩放居中放入一页 A4
    异常：
        ValueError    - 不是有效 PDF / 没有页面 / 裁切参数不合法
    """
    pymupdf = render.import_pymupdf()

    try:
        source = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - PyMuPDF 的异常类型不稳定，统一转为可读错误
        raise ValueError("无法读取该 PDF：%s" % exc) from exc

    try:
        if source.page_count <= 0:
            raise ValueError("该 PDF 没有可拆分的页面")
        per_page = normalize_cuts(cuts, source.page_count)

        target = pymupdf.open()
        try:
            for index in range(source.page_count):
                page = source.load_page(index)
                if page.rotation:
                    # 把 /Rotate 烘焙进页面内容：这样裁切只需按视觉宽度切分，
                    # 而且被裁掉的部分不会残留在文本层里（否则复制/搜索会多出未见文字）
                    page.remove_rotation()
                for start, end in segment_ranges(per_page[index]):
                    target_page = target.new_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
                    _place_segment(target_page, source, page, pymupdf, start, end)
            if target.page_count <= 0:
                raise ValueError("没有生成任何页面，请检查裁切线设置")
            return target.tobytes(garbage=3, deflate=True)
        finally:
            target.close()
    finally:
        source.close()


def _place_segment(target_page: Any, source: Any, page: Any, pymupdf: Any, start: float, end: float) -> None:
    """把源页的一段等比缩放居中画到 A4 目标页上（矢量搬运）。

    参数：
        target_page - 新的 A4 页
        source      - 源文档
        page        - 源页
        pymupdf     - PyMuPDF 模块
        start, end  - 沿视觉宽度的比例区间
    返回：
        None
    """
    clip = _clip_rect(page, pymupdf, start, end)
    if clip.width <= 0 or clip.height <= 0:
        return

    scale = min(A4_WIDTH_PT / clip.width, A4_HEIGHT_PT / clip.height)
    width = clip.width * scale
    height = clip.height * scale
    left = (A4_WIDTH_PT - width) / 2
    top = (A4_HEIGHT_PT - height) / 2
    target = pymupdf.Rect(left, top, left + width, top + height)

    # show_pdf_page 是「把另一页的一段矢量内容贴过来」，不会把内容栅格化
    target_page.show_pdf_page(target, source, page.number, clip=clip)


def summarize(cuts: Any, page_count: int) -> Dict[str, Any]:
    """生成一份拆分说明（供接口返回给前端做提示）。

    参数：
        cuts       - 裁切参数
        page_count - 源 PDF 页数
    返回：
        dict，{source_pages, output_pages, per_page}
    """
    per_page = normalize_cuts(cuts, page_count)
    return {
        "source_pages": int(page_count or 0),
        "output_pages": count_output_pages(per_page, page_count),
        "per_page": [len(segment_ranges(item)) for item in per_page],
    }
