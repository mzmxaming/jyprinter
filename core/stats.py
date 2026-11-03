"""打印量统计模块：按天累计「成功打印」的份数、页数与估算用纸张数。

设计要点：
    · 只统计**已完成**（done）的任务：失败与取消没有消耗纸张，不计入打印量；
    · 以「天」为最小聚合单位（本地时间），落盘为 `data/logs/stats.json`；
      它**独立于审计日志的清理**——日志只保留 `log_keep_days` 天，统计则长期累计；
    · 页数 = 每份页数 × 份数（即实际打印出来的页数）；用纸张数 = 份数 ×（单面：页数；双面：ceil(页数/2)）；
    · 首次运行（尚无 stats.json）时从审计日志**回填**既有记录：日志里有份数、纸张与单双面，
      但升级前的记录**没有页数**，这类记录只累计任务数与份数（页数与张数会偏小）；
    · 聚合逻辑全部写成纯函数（`sheets_for` / `add_print` / `aggregate_rows` / `summarize`），
      便于离线单元测试（与平台无关）。

与审计日志的分工：
    审计日志 = 「谁、什么时候、打了什么、结果如何」的逐条流水（可追溯，按天清理）；
    本模块   = 「打了多少」的累计口径（只保留聚合值，不含文件名、不缺页数就补 0）。
"""

from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import audit, settings
from core.utils import now_str

STATS_FILE: Path = settings.LOG_DIR / "stats.json"
STATS_VERSION = 1

# 统计窗口：(键, 展示名, 天数)；天数为 None 表示不限天数（累计）
WINDOWS: List[Tuple[str, str, Optional[int]]] = [
    ("today", "今日", 1),
    ("week", "近 7 天", 7),
    ("month", "近 30 天", 30),
    ("all", "累计", None),
]

# 审计日志中「已完成」的展示文本（日志里状态存的是中文）
_DONE_TEXT = "已完成"
_UNKNOWN_PAPER = "未指定"

_lock = threading.RLock()
_days: Optional[Dict[str, Dict[str, Any]]] = None


def is_duplex(value: Any) -> bool:
    """判断单双面标识是否为「双面」。

    参数：
        value - 可能是 simplex / vertical（内部值）或 单面 / 双面（日志展示值）
    返回：
        bool，双面返回 True
    """
    return str(value or "").strip() in ("vertical", "双面", "duplex")


def sheets_for(pages: Any, copies: Any, duplex: Any) -> int:
    """估算一次打印消耗的纸张数。

    单面：每页一张；双面：每张纸正反两面，页数不足两张时按 1 张计。

    参数：
        pages  - 每份的页数
        copies - 份数
        duplex - 单双面标识
    返回：
        int，估算用纸张数
    """
    page_count = max(0, int(pages or 0))
    copy_count = max(1, int(copies or 1))
    per_copy = math.ceil(page_count / 2) if is_duplex(duplex) else page_count
    return per_copy * copy_count


def new_bucket() -> Dict[str, Any]:
    """创建一个空的统计桶。

    参数：无
    返回：
        dict，形如 {tasks, copies, pages, sheets, papers: {}, duplex: {}}
    """
    return {
        "tasks": 0,
        "copies": 0,
        "pages": 0,
        "sheets": 0,
        "papers": {},
        "duplex": {},
    }


def _add_item(table: Dict[str, Dict[str, int]], name: str, copies: int, pages: int, sheets: int) -> None:
    """把一次打印累加到某个分布表（纸张 / 单双面）的指定项上。

    参数：
        table  - 分布表（会被就地修改）
        name   - 分组名（如 A4、单面）
        copies - 份数
        pages  - 页数
        sheets - 估算用纸张数
    返回：
        None
    """
    mark = str(name or "").strip() or _UNKNOWN_PAPER
    item = table.setdefault(mark, {"copies": 0, "pages": 0, "sheets": 0})
    item["copies"] += copies
    item["pages"] += pages
    item["sheets"] += sheets


def add_print(
    bucket: Dict[str, Any],
    *,
    copies: Any = 1,
    pages: Any = 0,
    paper: str = "",
    duplex: str = "simplex",
    tasks: int = 1,
) -> None:
    """把一次打印累加进统计桶（就地修改）。

    参数：
        bucket - 目标统计桶（new_bucket() 的结果）
        copies - 份数
        pages  - 每份页数
        paper  - 纸张名称
        duplex - 单双面标识
        tasks  - 计入的任务数（回填时为 1）
    返回：
        None

    口径说明：份数按份累计；**页数按「每份页数 × 份数」累计**（即实际打印的页数）；
    用纸张数按单面 1 页/张、双面 2 页/张估算。
    """
    copy_count = max(1, int(copies or 1))
    page_count = max(0, int(pages or 0))
    # 页数按「每份页数 × 份数」累计，也就是实际打印出来的页数（面数）
    total_pages = page_count * copy_count
    sheet_count = sheets_for(page_count, copy_count, duplex)
    bucket["tasks"] += int(tasks or 0)
    bucket["copies"] += copy_count
    bucket["pages"] += total_pages
    bucket["sheets"] += sheet_count
    _add_item(bucket["papers"], paper, copy_count, total_pages, sheet_count)
    _add_item(bucket["duplex"], "双面" if is_duplex(duplex) else "单面", copy_count, total_pages, sheet_count)


def _merge(target: Dict[str, Any], source: Any) -> None:
    """把统计桶合并进另一个统计桶（就地修改 target，容错处理缺失字段）。

    参数：
        target - 汇总目标
        source - 待合并的统计桶（可为不完整的字典）
    返回：
        None
    """
    if not isinstance(source, dict):
        return
    for key in ("tasks", "copies", "pages", "sheets"):
        try:
            target[key] += int(source.get(key) or 0)
        except (TypeError, ValueError):
            continue
    for table_key in ("papers", "duplex"):
        table = source.get(table_key)
        if not isinstance(table, dict):
            continue
        for name, item in table.items():
            if not isinstance(item, dict):
                continue
            try:
                target[table_key][str(name)] = target[table_key].get(
                    str(name), {"copies": 0, "pages": 0, "sheets": 0}
                )
                _add_item(
                    target[table_key],
                    str(name),
                    int(item.get("copies") or 0),
                    int(item.get("pages") or 0),
                    int(item.get("sheets") or 0),
                )
            except (TypeError, ValueError):
                continue


def aggregate_rows(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """把审计日志记录聚合为「按天统计桶」（纯函数）。

    只计入状态为「已完成」的打印记录；同一个任务的「排队中 / 打印中」等中间记录会被忽略。

    参数：
        rows - audit.parse_line 解析出的记录列表
    返回：
        dict，{日期: 统计桶}
    """
    days: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if str(row.get("status", "")).strip() != _DONE_TEXT:
            continue
        stamp = str(row.get("time", "")).strip()[:10]
        try:
            datetime.strptime(stamp, "%Y-%m-%d")
        except ValueError:
            continue
        bucket = days.setdefault(stamp, new_bucket())
        add_print(
            bucket,
            copies=row.get("copies"),
            pages=row.get("pages"),
            paper=row.get("paper"),
            duplex=row.get("duplex"),
        )
    return days


def summarize(days: Dict[str, Dict[str, Any]], window_days: Optional[int] = None) -> Dict[str, Any]:
    """汇总最近若干天的统计桶（纯函数）。

    参数：
        days        - aggregate_rows 的结果（按天的统计桶）
        window_days - 统计窗口天数；None 表示不限（累计）
    返回：
        dict，合并后的统计桶
    """
    today = datetime.now().date()
    start = None
    if window_days:
        start = today - timedelta(days=max(1, int(window_days)) - 1)

    total = new_bucket()
    for key, bucket in (days or {}).items():
        try:
            day = datetime.strptime(str(key), "%Y-%m-%d").date()
        except ValueError:
            continue
        if day > today:
            # 时钟回拨等异常数据不计入，避免「今日」出现未来数字
            continue
        if start is not None and day < start:
            continue
        _merge(total, bucket)
    return total


def record(
    *,
    copies: Any = 1,
    pages: Any = 0,
    paper: str = "",
    duplex: str = "simplex",
) -> None:
    """记录一次成功的打印（由任务完成后调用）。

    参数：
        copies - 份数
        pages  - 每份页数（累加时会乘以份数）
        paper  - 纸张名称
        duplex - 单双面标识
    返回：
        None
    """
    days = _ensure_loaded()
    key = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        bucket = days.setdefault(key, new_bucket())
        add_print(bucket, copies=copies, pages=pages, paper=paper, duplex=duplex)
        _save(days)


def snapshot() -> Dict[str, Any]:
    """生成供网页端展示的统计数据。

    参数：无
    返回：
        dict，{windows: {键: 统计桶 + label/days}, first_date, last_date, note, ...}
    """
    days = _ensure_loaded()
    windows: Dict[str, Dict[str, Any]] = {}
    for key, label, span in WINDOWS:
        bucket = summarize(days, span)
        bucket["key"] = key
        bucket["label"] = label
        bucket["days"] = span
        windows[key] = bucket

    dates = sorted(str(key) for key in days)
    return {
        "ok": True,
        "windows": windows,
        "first_date": dates[0] if dates else "",
        "last_date": dates[-1] if dates else "",
        "log_keep_days": int(settings.get("log_keep_days", 7)),
        "note": "只统计已完成的任务；用纸张数按单面 1 页/张、双面 2 页/张估算",
    }


# --------------------------------------------------------------------------- 持久化


def _ensure_loaded() -> Dict[str, Dict[str, Any]]:
    """读取统计文件（首次调用时加载，文件不存在则从审计日志回填）。

    参数：无
    返回：
        dict，{日期: 统计桶}
    """
    global _days
    with _lock:
        if _days is not None:
            return _days
        loaded = _load()
        if loaded is None:
            loaded = _backfill()
            _save(loaded)
        _days = loaded
        return _days


def _load() -> Optional[Dict[str, Dict[str, Any]]]:
    """从磁盘读取统计文件。

    参数：无
    返回：
        dict（{日期: 统计桶}）；文件不存在时返回 None（调用方据此触发回填），
        文件损坏或结构不符时返回空字典（视为暂无数据，不让主流程报错）
    """
    if not STATS_FILE.exists():
        return None
    try:
        data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for key, bucket in data["days"].items():
        try:
            datetime.strptime(str(key), "%Y-%m-%d")
        except ValueError:
            continue
        merged = new_bucket()
        _merge(merged, bucket)
        result[str(key)] = merged
    return result


def _save(days: Dict[str, Dict[str, Any]]) -> None:
    """把统计结果写入磁盘（先写临时文件再原子替换，避免读到半截内容）。

    参数：
        days - {日期: 统计桶}
    返回：
        None
    """
    try:
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"version": STATS_VERSION, "updated": now_str(), "days": days}
        temporary = STATS_FILE.with_name(STATS_FILE.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(STATS_FILE)
    except OSError:
        # 统计写盘失败不应影响打印主流程
        pass


def _backfill() -> Dict[str, Dict[str, Any]]:
    """从审计日志回填统计数据（仅首次运行、尚无 stats.json 时执行）。

    参数：无
    返回：
        dict，{日期: 统计桶}；读取失败时返回空字典
    """
    try:
        if not audit.LOG_FILE.exists():
            return {}
        text = audit.LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    rows = [audit.parse_line(line) for line in text.splitlines() if line.strip()]
    return aggregate_rows(rows)
