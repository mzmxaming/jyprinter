"""Linux / macOS 办公文档转换：调用 LibreOffice headless 把 Office/WPS 文档转为 PDF。

要点（Linux 上的实际坑）：
    · 必须显式指定独立的 UserInstallation 配置目录，否则并发/服务账号下会因
      profile 被占用而静默失败；
    · 转换后的产物必须校验（存在 + 页数≥1），部分文件会「退出码为 0 但没有输出」；
    · 转换耗时与文件大小正相关，超时时间放宽到 300 秒。

macOS 与 Linux 同样使用 LibreOffice headless，仅可执行文件路径与安装提示不同。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import render, settings
from core.formats import OFFICE_EXTS, is_office_ext  # noqa: F401 - 统一由 formats 定义，此处转发
from core.osinfo import IS_MACOS, linux_distro_name
from core.utils import remove_file

# LibreOffice 可执行文件候选路径（PATH 中找不到时按此顺序查找）
SOFFICE_CANDIDATES = (
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
    "/snap/bin/libreoffice",
    "/var/lib/flatpak/exports/bin/org.libreoffice.LibreOffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)

# 转换超时（秒）
SOFFICE_TIMEOUT = 300

# 家族名称（用于网页端展示）
FAMILY_NAMES = {
    "word": "LibreOffice 文字（doc/docx/wps）",
    "excel": "LibreOffice 表格（xls/xlsx/et）",
    "powerpoint": "LibreOffice 演示（ppt/pptx/dps）",
}

_probe_cache: Optional[Dict[str, Any]] = None


def find_soffice() -> Optional[str]:
    """查找 LibreOffice 可执行文件。

    参数：无
    返回：
        str 或 None，soffice 的完整路径
    """
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for candidate in SOFFICE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _profile_dir() -> Path:
    """返回转换专用的 LibreOffice 配置目录。

    参数：无
    返回：
        Path，配置目录路径
    """
    target = settings.DATA_DIR / "lo_profile"
    target.mkdir(parents=True, exist_ok=True)
    return target


def convert_to_pdf(source: Path, ext: str) -> Path:
    """把 Office/WPS 文档转换为 PDF。

    参数：
        source - 源文件绝对路径（建议放在无空格、无中文的路径下）
        ext    - 源文件扩展名
    返回：
        Path，生成的 PDF 路径（位于 data/converted）
    异常：
        RuntimeError - LibreOffice 不可用、转换失败或产物无效
    """
    normalized = str(ext or "").strip().lower().lstrip(".")
    if normalized not in OFFICE_EXTS:
        raise RuntimeError(f"不支持的 Office 格式：.{normalized}")
    if not source.exists():
        raise RuntimeError("文件不存在或已被清理")

    soffice = find_soffice()
    if not soffice:
        if IS_MACOS:
            hint = (
                "未检测到 LibreOffice，无法转换该文档格式，请先安装："
                "brew install --cask libreoffice，"
                "或从 https://www.libreoffice.org/download/ 下载 macOS 版安装"
            )
        else:
            hint = (
                "未检测到 LibreOffice，无法转换该文档格式，请先安装："
                "sudo apt install libreoffice（Debian/Ubuntu/银河麒麟）"
                "或 sudo yum install libreoffice（RPM 系）"
            )
        raise RuntimeError(hint)

    settings.CONVERTED_DIR.mkdir(parents=True, exist_ok=True)
    target = settings.CONVERTED_DIR / f"{source.stem}.pdf"
    if _is_reusable(target, source):
        # 预览阶段已转换过，直接复用，避免打印时重复转换
        return target
    remove_file(target)

    command = [
        soffice,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        f"-env:UserInstallation=file://{_profile_dir()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(settings.CONVERTED_DIR),
        str(source),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=SOFFICE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"文档转换超时（超过 {SOFFICE_TIMEOUT} 秒），请稍后重试") from exc
    except OSError as exc:
        raise RuntimeError(f"无法启动 LibreOffice：{exc}") from exc

    if not target.exists() or target.stat().st_size == 0:
        detail = _decode(result.stderr or result.stdout).strip()[:160]
        raise RuntimeError(f"文档转换失败（LibreOffice 未生成 PDF）{detail}")

    pages = render.pdf_page_count(target)
    print(f"[转换] .{normalized} → PDF 成功，引擎 LibreOffice，共 {pages} 页", flush=True)
    return target


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


def _decode(raw: bytes) -> str:
    """解码命令输出。

    参数：
        raw - 原始字节
    返回：
        str，解码结果
    """
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def probe_availability(refresh: bool = False) -> Dict[str, Any]:
    """探测本机是否具备办公文档转换能力。

    参数：
        refresh - 为 True 时忽略缓存重新探测
    返回：
        dict，{families, libreoffice, available_exts}（结构与 Windows 版保持一致）
    """
    global _probe_cache
    if _probe_cache is not None and not refresh:
        return dict(_probe_cache)

    soffice = find_soffice()
    families: Dict[str, List[Dict[str, str]]] = {"word": [], "excel": [], "powerpoint": []}
    available: List[str] = []
    if soffice:
        for family in families:
            families[family] = [{"progid": "soffice", "name": FAMILY_NAMES[family]}]
        available = sorted(OFFICE_EXTS)

    result = {
        "families": families,
        "libreoffice": soffice or "",
        "available_exts": available,
        "distro": linux_distro_name(),
    }
    _probe_cache = result
    return dict(result)
