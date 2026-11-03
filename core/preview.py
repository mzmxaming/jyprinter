"""预览模块：为上传的文件生成缩略图，供用户在打印前确认版式与页码。

设计要点：
    · 缩略图按需生成（首次请求时），生成结果缓存到 data/previews/<任务ID>/ 便于重复查看；
    · 渲染分辨率远低于打印（100 DPI vs 300 DPI），兼顾速度与可辨认度；
    · **预览与打印使用同一页码范围**：只生成「将要打印的那些页」的缩略图，缩略图标注的
      是它在原文档中的真实页码（例如范围填 5，就只显示「第 5 页」），避免「选了 1 页却
      看到全部页」的误判；
    · Office/WPS 文档在预览时会完成一次 PDF 转换，该产物会被打印阶段复用，避免重复转换。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from core import engines, render, settings, utils

PREVIEW_DPI = 100        # 缩略图渲染分辨率（打印使用 300 DPI）
MAX_PAGES = 20           # 最多生成的缩略图页数，超出部分只显示总页数
MAX_WIDTH = 900          # 缩略图最大宽度（像素）
META_NAME = "meta.json"


def preview_dir(task_id: str) -> Path:
    """返回指定任务的预览目录。

    参数：
        task_id - 任务 ID
    返回：
        Path，预览目录路径
    """
    return settings.PREVIEW_DIR / task_id


def load(task_id: str) -> Optional[Dict[str, Any]]:
    """读取已生成的预览信息。

    参数：
        task_id - 任务 ID
    返回：
        dict 或 None，预览信息（page_count / rendered / pages）
    """
    meta_path = preview_dir(task_id) / META_NAME
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build(task_id: str, stored_path: str, ext: str, page_range: str = "") -> Dict[str, Any]:
    """生成（或复用）指定文件的预览缩略图。

    参数：
        task_id     - 任务 ID
        stored_path - 上传文件绝对路径
        ext         - 文件扩展名
        page_range  - 页码范围文本（与打印保持一致），空表示全部页
    返回：
        dict，{page_count, document_pages, pages_range, rendered, pages: [{index, file}]}
        其中 page_count 为「将要打印的页数」，document_pages 为文档总页数，
        pages[].index 为该页在原文档中的真实页码
    异常：
        RuntimeError - 文件不可读、格式不支持、渲染失败或所选页码范围无对应页面
        ValueError   - 页码范围格式不合法
    """
    cached = load(task_id)
    if cached:
        return cached

    directory = preview_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)

    total = engines.count_pages(stored_path, ext)
    ranges = render.parse_page_range(page_range)
    selected = [index for index in range(1, total + 1) if render.page_selected(ranges, index)]
    # 与打印一致：用同一页码范围渲染，只生成会被打印的页
    # （页码范围在文档中无对应页面时，render.apply_options 会抛出与打印相同的错误）
    factory, _converted = engines.build_page_factory(
        stored_path, ext, engines.RenderOptions(dpi=PREVIEW_DPI, pages=page_range)
    )

    pages: List[Dict[str, Any]] = []
    generated: List[Path] = []
    try:
        for order, image in enumerate(factory(), start=1):
            if order > MAX_PAGES:
                image.close()
                break
            # order 是「第几个被选中的页」，这里换算回它在原文档中的真实页码
            index = selected[order - 1] if order <= len(selected) else order
            name = f"p{index:03d}.png"
            target = directory / name
            _save_thumbnail(image, target)
            image.close()
            pages.append({"index": index, "file": name})
            generated.append(target)
    except Exception:
        # 生成失败时不留下半成品缓存，便于下次重试
        for item in generated:
            utils.remove_file(item)
        raise

    meta: Dict[str, Any] = {
        "page_count": len(selected) or total,
        "document_pages": total,
        "pages_range": str(page_range or "").strip(),
        "rendered": len(pages),
        "pages": pages,
    }
    (directory / META_NAME).write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def thumbnail_path(task_id: str, page: int) -> Optional[Path]:
    """获取指定页缩略图的文件路径。

    参数：
        task_id - 任务 ID
        page    - 页码（从 1 开始）
    返回：
        Path 或 None，缩略图不存在时返回 None
    """
    meta = load(task_id)
    if not meta:
        return None
    for item in meta.get("pages", []):
        if int(item.get("index", 0)) == int(page):
            candidate = preview_dir(task_id) / str(item.get("file", ""))
            return candidate if candidate.exists() else None
    return None


def remove(task_id: str) -> None:
    """删除指定任务的预览缓存。

    参数：
        task_id - 任务 ID
    返回：
        None
    """
    shutil.rmtree(preview_dir(task_id), ignore_errors=True)


def _save_thumbnail(image: Image.Image, target: Path) -> None:
    """把页面位图等比缩放后保存为 PNG 缩略图。

    参数：
        image  - 页面位图
        target - 输出文件路径
    返回：
        None
    """
    thumb = image.convert("RGB")
    if thumb.width > MAX_WIDTH:
        ratio = MAX_WIDTH / thumb.width
        thumb = thumb.resize((MAX_WIDTH, max(1, int(thumb.height * ratio))), Image.LANCZOS)
    thumb.save(target, "PNG", optimize=True)
