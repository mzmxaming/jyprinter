"""审计日志模块：把每次打印行为写入文件，并保留最近记录供网页端展示。"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

from core import settings
from core.utils import now_str

LOG_FILE: Path = settings.LOG_DIR / "print_log.txt"

# 内存中保留的最近记录条数，供网页端轮询展示
_MAX_MEMORY_RECORDS = 500
_records: deque = deque(maxlen=_MAX_MEMORY_RECORDS)
_lock = threading.Lock()

# 文件日志的列顺序（管道分隔）；新增列一律追加在末尾，保证旧日志仍可解析
_COLUMNS = [
    "time",
    "client_ip",
    "client_host",
    "filename",
    "copies",
    "duplex",
    "paper",
    "printer",
    "status",
    "message",
    "user",
    "event",
    "pages_range",
    "scale",
    "color",
    "quality",
]

_DUPLEX_TEXT = {"simplex": "单面", "vertical": "双面"}
_STATUS_TEXT = {
    "queued": "排队中",
    "review": "待确认",
    "printing": "打印中",
    "done": "已完成",
    "failed": "失败",
    "canceled": "已取消",
}
_SCALE_TEXT = {"fit": "适应页面", "actual": "实际大小", "custom": "自定义"}
_COLOR_TEXT = {"color": "彩色", "mono": "黑白"}
_QUALITY_TEXT = {"draft": "草稿", "normal": "标准", "high": "高清"}
_QUALITY_DPI = {"draft": 150, "normal": 300, "high": 600}


def scale_text(scale: str, percent: Any = 100) -> str:
    """把缩放方式转换为展示文本。

    参数：
        scale   - fit / actual / custom
        percent - scale=custom 时的百分比
    返回：
        str，如「适应页面」「实际大小」「80%」
    """
    key = str(scale or "fit").lower()
    if key == "custom":
        try:
            value = int(percent)
        except (TypeError, ValueError):
            value = 100
        return f"{value}%"
    return _SCALE_TEXT.get(key, key)


def color_text(color: str) -> str:
    """把颜色模式转换为展示文本。

    参数：
        color - color 彩色 / mono 黑白
    返回：
        str，中文展示文本
    """
    key = str(color or "color").lower()
    return _COLOR_TEXT.get(key, key)


def quality_text(quality: str) -> str:
    """把打印质量转换为展示文本（附带分辨率）。

    参数：
        quality - draft / normal / high
    返回：
        str，如「标准（300DPI）」
    """
    key = str(quality or "normal").lower()
    name = _QUALITY_TEXT.get(key, key)
    dpi = _QUALITY_DPI.get(key)
    return f"{name}（{dpi}DPI）" if dpi else name


def param_text(
    copies: Any = 1,
    duplex: str = "simplex",
    paper: str = "",
    page_range: str = "",
    scale: str = "fit",
    scale_percent: Any = 100,
    color: str = "color",
    quality: str = "normal",
) -> str:
    """组合打印参数的展示文本（用于队列与日志的「参数」列）。

    参数：
        copies        - 份数
        duplex        - simplex / vertical
        paper         - 纸张名称
        page_range    - 页码范围文本，空表示全部
        scale         - fit / actual / custom
        scale_percent - scale=custom 时的百分比
        color         - color / mono
        quality       - draft / normal / high
    返回：
        str，形如「1 份 / 单面 / A4 / 全部页 / 适应页面 / 彩色 / 标准（300DPI）」
    """
    parts = [
        f"{copies} 份",
        _DUPLEX_TEXT.get(duplex, duplex),
        paper,
        str(page_range or "").strip() or "全部页",
        scale_text(scale, scale_percent),
        color_text(color),
        quality_text(quality),
    ]
    return " / ".join(str(item) for item in parts if str(item or "").strip())


def record(
    *,
    client_ip: str = "",
    client_host: str = "",
    filename: str = "",
    copies: Any = "",
    duplex: str = "",
    paper: str = "",
    printer: str = "",
    status: str = "",
    message: str = "",
    user: str = "",
    page_range: str = "",
    scale: str = "fit",
    scale_percent: Any = 100,
    color: str = "color",
    quality: str = "normal",
) -> Dict[str, Any]:
    """写入一条打印审计记录（内存 + 文件）。

    参数：
        client_ip     - 客户端 IP
        client_host   - 客户端计算机名
        filename      - 打印文件名
        copies        - 份数
        duplex        - 单双面标识（simplex / vertical）
        paper         - 纸张名称
        printer       - 目标打印机
        status        - 任务状态（review/queued/printing/done/failed/canceled）
        message       - 附加说明或失败原因
        user          - 操作账号
        page_range    - 页码范围文本，空表示全部
        scale         - 缩放方式（fit / actual / custom）
        scale_percent - 自定义缩放百分比
        color         - 颜色模式（color / mono）
        quality       - 打印质量（draft / normal / high）
    返回：
        dict，本次写入的记录
    """
    item: Dict[str, Any] = {
        "time": now_str(),
        "client_ip": client_ip,
        "client_host": client_host,
        "filename": filename,
        "copies": copies,
        "duplex": _DUPLEX_TEXT.get(duplex, duplex),
        "paper": paper,
        "printer": printer,
        "status": _STATUS_TEXT.get(status, status),
        "message": message,
        "user": user,
        "event": "打印",
        "pages_range": str(page_range or "").strip() or "全部页",
        "scale": scale_text(scale, scale_percent),
        "color": color_text(color),
        "quality": quality_text(quality),
    }
    return _write(item)


def record_login(
    *,
    user: str,
    client_ip: str = "",
    client_host: str = "",
    success: bool = True,
    message: str = "",
) -> Dict[str, Any]:
    """写入一条登录审计记录。

    参数：
        user        - 登录账号
        client_ip   - 客户端 IP
        client_host - 客户端计算机名
        success     - 是否登录成功
        message     - 附加说明
    返回：
        dict，本次写入的记录
    """
    item: Dict[str, Any] = {
        "time": now_str(),
        "client_ip": client_ip,
        "client_host": client_host,
        "filename": "",
        "copies": "",
        "duplex": "",
        "paper": "",
        "printer": "",
        "status": "成功" if success else "失败",
        "message": message or ("登录成功" if success else "登录失败"),
        "user": user,
        "event": "登录",
    }
    return _write(item)


def _write(item: Dict[str, Any]) -> Dict[str, Any]:
    """把记录写入内存缓存与日志文件。

    参数：
        item - 待写入的记录
    返回：
        dict，写入的记录
    """
    with _lock:
        _records.append(item)
        try:
            settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
            line = "|".join(str(item.get(col, "")).replace("|", "/") for col in _COLUMNS)
            with LOG_FILE.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")
        except OSError:
            # 日志写盘失败不应影响打印主流程
            pass

    return item


def recent(limit: int = 50) -> List[Dict[str, Any]]:
    """读取最近若干条打印记录（优先取内存缓存，为空时回读日志文件）。

    参数：
        limit - 返回条数上限
    返回：
        list[dict]，按时间倒序（最新在前）
    """
    with _lock:
        cached = list(_records)

    if cached:
        return list(reversed(cached[-limit:]))

    return [parse_line(line) for line in _tail_lines(limit)][::-1]


def _tail_lines(limit: int) -> List[str]:
    """从日志文件尾部读取若干行。

    参数：
        limit - 读取行数上限
    返回：
        list[str]，按文件顺序的原始行
    """
    if not LOG_FILE.exists():
        return []
    try:
        with LOG_FILE.open("r", encoding="utf-8", errors="replace") as fp:
            lines = [line.rstrip("\n") for line in fp if line.strip()]
    except OSError:
        return []
    return lines[-limit:]


def parse_line(line: str) -> Dict[str, Any]:
    """把一行日志文本解析为记录字典。

    参数：
        line - 管道分隔的日志行
    返回：
        dict，键与 _COLUMNS 一致；列数不足时以空串补齐
    """
    parts = line.split("|")
    parts += [""] * (len(_COLUMNS) - len(parts))
    return dict(zip(_COLUMNS, parts[: len(_COLUMNS)]))


def cleanup(keep_days: int) -> int:
    """清理超过保留天数的日志记录。

    参数：
        keep_days - 保留天数
    返回：
        int，被删除的行数
    """
    if not LOG_FILE.exists() or keep_days <= 0:
        return 0

    deadline = datetime.now() - timedelta(days=keep_days)
    kept: List[str] = []
    removed = 0
    try:
        with LOG_FILE.open("r", encoding="utf-8", errors="replace") as fp:
            for line in fp:
                text = line.rstrip("\n")
                if not text.strip():
                    continue
                stamp = text.split("|", 1)[0].strip()
                try:
                    if datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S") < deadline:
                        removed += 1
                        continue
                except ValueError:
                    pass
                kept.append(text)
        if removed:
            LOG_FILE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except OSError:
        return 0
    return removed


def status_text(status: str) -> str:
    """把内部状态标识转换为中文文案。

    参数：
        status - queued/printing/done/failed/canceled
    返回：
        str，中文状态文案（未知状态原样返回）
    """
    return _STATUS_TEXT.get(status, status)
