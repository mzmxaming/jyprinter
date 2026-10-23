"""预览模块：为上传的文件生成缩略图，供用户在打印前确认版式与页码。

设计要点：
    · 缩略图按需生成（首次请求时），生成结果缓存到 data/previews/<任务ID>/ 便于重复查看；
    · 渲染分辨率远低于打印（100 DPI vs 300 DPI），兼顾速度与可辨认度；
    · Office/WPS 文档在预览时会完成一次 PDF 转换，该产物会被打印阶段复用，避免重复转换。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from core import engines, settings, utils

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


def build(task_id: str, stored_path: str, ext: str) -> Dict[str, Any]:
    """生成（或复用）指定文件的预览缩略图。

    参数：
        task_id     - 任务 ID
        stored_path - 上传文件绝对路径
        ext         - 文件扩展名
    返回：
        dict，{page_count, rendered, pages: [{index, file}]}
    异常：
        RuntimeError - 文件不可读、格式不支持或渲染失败
    """
    cached = load(task_id)
    if cached:
        return cached

    directory = preview_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)

    total = engines.count_pages(stored_path, ext)
    # 第二个返回值为 Office 转换产物，保留在 data/converted 供打印阶段复用
    # 预览只关心页数与缩略图，因此固定用预览分辨率、不做页码范围与颜色处理
    factory, _converted = engines.build_page_factory(
        stored_path, ext, engines.RenderOptions(dpi=PREVIEW_DPI)
    )

    pages: List[Dict[str, Any]] = []
    generated: List[Path] = []
    try:
        for index, image in enumerate(factory(), start=1):
            if index > MAX_PAGES:
                image.close()
                break
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

    meta: Dict[str, Any] = {"page_count": total, "rendered": len(pages), "pages": pages}
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
