"""打印机管理模块：枚举打印机、过滤虚拟打印机、读取纸张与双面能力、构造 DEVMODE。

关键实现均基于 Windows 打印子系统（win32print / win32gui），已在本机实测：
    · win32print.DeviceCapabilities(设备名, 端口名, DC_PAPERS / DC_PAPERNAMES / DC_DUPLEX)
    · win32gui.CreateDC("WINSPOOL", 设备名, devmode) 可让自定义纸张/双面参数生效
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pywintypes
import win32con
import win32print

# DeviceCapabilities 的能力索引（与 Windows GDI 定义一致）
DC_PAPERS = 2
DC_DUPLEX = 7
DC_PAPERNAMES = 16

# 虚拟打印机关键字（驱动名或设备名命中即视为虚拟设备）
VIRTUAL_KEYWORDS = (
    "pdf",
    "xps",
    "onenote",
    "fax",
    "virtual",
    "imagewriter",
    "cute",
    "dopdf",
    "nova",
    "snagit",
    "pdfcreator",
    "wps pdf",
    "虚拟",
)

# 驱动能力查询失败时使用的通用纸张表（ID 遵循 Windows 约定）
COMMON_PAPERS: List[Tuple[int, str]] = [
    (9, "A4"),
    (1, "Letter"),
    (5, "Legal"),
    (11, "A5"),
    (13, "B5"),
    (7, "A3"),
]

DEFAULT_PAPER_ID = 9  # A4

# 已结束的作业状态位：作业即使仍留在队列中（打印机勾选了「保留打印文档」）也不算待打印任务
_JOB_FINISHED_STATUS = (
    getattr(win32print, "JOB_STATUS_PRINTED", 0x00000080)
    | getattr(win32print, "JOB_STATUS_DELETED", 0x00000100)
    | getattr(win32print, "JOB_STATUS_COMPLETE", 0x00001000)
    | getattr(win32print, "JOB_STATUS_RETAINED", 0x00002000)
)


def is_virtual(printer_name: str, driver_name: str = "") -> bool:
    """判断指定打印机是否为虚拟打印机。

    参数：
        printer_name - 打印机名称
        driver_name  - 打印机驱动名称
    返回：
        bool，命中虚拟设备关键字返回 True
    """
    text = f"{printer_name} {driver_name}".lower()
    return any(keyword in text for keyword in VIRTUAL_KEYWORDS)


def get_default_printer() -> str:
    """获取系统默认打印机名称。

    参数：无
    返回：
        str，默认打印机名；无默认打印机时返回空字符串
    """
    try:
        return win32print.GetDefaultPrinter() or ""
    except pywintypes.error:
        return ""


def list_printers(include_virtual: bool = False) -> List[Dict[str, Any]]:
    """枚举本机可见的打印机列表。

    参数：
        include_virtual - 为 True 时保留虚拟打印机
    返回：
        list[dict]，元素包含 name/driver/port/is_default/is_virtual/status
    """
    default_name = get_default_printer()
    try:
        # 2 = PRINTER_ENUM_LOCAL，4 = PRINTER_ENUM_CONNECTIONS（含网络共享打印机）
        entries = win32print.EnumPrinters(2 | 4, None, 2)
    except pywintypes.error:
        return []

    printers: List[Dict[str, Any]] = []
    for entry in entries:
        name = entry.get("pPrinterName", "")
        driver = entry.get("pDriverName", "")
        virtual = is_virtual(name, driver)
        if virtual and not include_virtual:
            continue
        printers.append(
            {
                "name": name,
                "driver": driver,
                "port": entry.get("pPortName", ""),
                "is_default": name == default_name,
                "is_virtual": virtual,
                "status": _status_text(entry.get("Status", 0)),
            }
        )
    return printers


def _status_text(status: Any) -> str:
    """把打印机状态位转换为中文说明。

    参数：
        status - EnumPrinters 返回的 Status 位掩码
    返回：
        str，状态描述文本
    """
    value = int(status or 0)
    if value == 0:
        return "就绪"
    mapping = [
        (win32print.PRINTER_STATUS_PAUSED, "已暂停"),
        (win32print.PRINTER_STATUS_ERROR, "故障"),
        (win32print.PRINTER_STATUS_PENDING_DELETION, "删除中"),
        (win32print.PRINTER_STATUS_PAPER_JAM, "卡纸"),
        (win32print.PRINTER_STATUS_PAPER_OUT, "缺纸"),
        (win32print.PRINTER_STATUS_PAPER_PROBLEM, "纸张异常"),
        (win32print.PRINTER_STATUS_OFFLINE, "离线"),
        (win32print.PRINTER_STATUS_OUTPUT_BIN_FULL, "出纸口已满"),
        (win32print.PRINTER_STATUS_NOT_AVAILABLE, "不可用"),
        (win32print.PRINTER_STATUS_BUSY, "忙"),
        (win32print.PRINTER_STATUS_OUT_OF_MEMORY, "内存不足"),
        (win32print.PRINTER_STATUS_DOOR_OPEN, "盖板未关"),
        (win32print.PRINTER_STATUS_USER_INTERVENTION, "需人工干预"),
    ]
    labels = [text for bit, text in mapping if value & bit]
    return "/".join(labels) if labels else f"状态码 {value}"


def _printer_info(printer_name: str, level: int = 2) -> Dict[str, Any]:
    """打开打印机并读取指定层级的信息。

    参数：
        printer_name - 打印机名称
        level        - GetPrinter 的信息层级
    返回：
        dict，打印机信息
    """
    handle = win32print.OpenPrinter(printer_name)
    try:
        return win32print.GetPrinter(handle, level)
    finally:
        win32print.ClosePrinter(handle)


def get_capabilities(printer_name: str) -> Dict[str, Any]:
    """读取打印机的纸张与双面打印能力。

    参数：
        printer_name - 打印机名称
    返回：
        dict，包含 papers（[{id, name}]）、duplex（bool）、probed（能力是否来自驱动）
    """
    if not printer_name:
        return {"papers": _papers_payload(COMMON_PAPERS), "duplex": True, "probed": False}

    try:
        info = _printer_info(printer_name)
        port = info.get("pPortName", "")
        ids = win32print.DeviceCapabilities(printer_name, port, DC_PAPERS)
        names = win32print.DeviceCapabilities(printer_name, port, DC_PAPERNAMES)
        duplex = win32print.DeviceCapabilities(printer_name, port, DC_DUPLEX) == 1
    except (pywintypes.error, OSError):
        return {"papers": _papers_payload(COMMON_PAPERS), "duplex": True, "probed": False}

    pairs: List[Tuple[int, str]] = []
    if isinstance(ids, (tuple, list)) and isinstance(names, (tuple, list)):
        for index, paper_id in enumerate(ids):
            label = str(names[index]) if index < len(names) else f"纸张 {paper_id}"
            pairs.append((int(paper_id), label))

    if not pairs:
        pairs = COMMON_PAPERS

    return {"papers": _papers_payload(pairs), "duplex": bool(duplex), "probed": True}


def _papers_payload(pairs: List[Tuple[int, str]]) -> List[Dict[str, Any]]:
    """把 (ID, 名称) 纸张序列转换为接口数据结构。

    参数：
        pairs - [(纸张ID, 纸张名称)] 序列
    返回：
        list[dict]，元素为 {"id": int, "name": str}
    """
    return [{"id": int(paper_id), "name": name} for paper_id, name in pairs]


def resolve_paper_id(printer_name: str, paper_name: str) -> int:
    """把纸张名称解析为当前打印机支持的纸张 ID。

    参数：
        printer_name - 打印机名称
        paper_name   - 纸张名称（如 A4），也可直接传入数字字符串
    返回：
        int，纸张 ID；无法匹配时回退 A4（ID=9）
    """
    text = str(paper_name or "").strip()
    if text.isdigit():
        return int(text)

    caps = get_capabilities(printer_name)
    for paper in caps["papers"]:
        if paper["name"].lower() == text.lower():
            return int(paper["id"])
    return DEFAULT_PAPER_ID


def build_devmode(
    printer_name: str,
    paper_id: Optional[int] = None,
    duplex: Optional[str] = None,
    orientation: Optional[str] = None,
) -> Any:
    """构造带纸张/双面/方向设置的 DEVMODE，用于创建打印机 DC。

    参数：
        printer_name - 打印机名称
        paper_id     - 纸张 ID，None 表示保持驱动默认
        duplex       - "simplex" 单面 / "vertical" 双面长边翻转，None 表示保持驱动默认
        orientation  - "landscape" 横向 / "portrait" 纵向，None 表示保持驱动默认
    返回：
        PyDEVMODE，可直接传给 win32gui.CreateDC
    """
    try:
        info = _printer_info(printer_name)
    except pywintypes.error as exc:
        raise RuntimeError(f"无法打开打印机「{printer_name}」，请确认设备已安装并处于可用状态") from exc

    devmode = info.get("pDevMode")
    if devmode is None:
        raise RuntimeError(f"无法获取打印机配置：{printer_name}")

    if paper_id:
        devmode.PaperSize = int(paper_id)
        devmode.Fields |= win32con.DM_PAPERSIZE

    if duplex:
        devmode.Duplex = (
            win32con.DMDUP_VERTICAL if duplex == "vertical" else win32con.DMDUP_SIMPLEX
        )
        devmode.Fields |= win32con.DM_DUPLEX

    if orientation:
        # 纸张方向：设置后可打印区的宽高会随方向交换
        devmode.Orientation = (
            win32con.DMORIENT_LANDSCAPE
            if str(orientation).lower() == "landscape"
            else win32con.DMORIENT_PORTRAIT
        )
        devmode.Fields |= win32con.DM_ORIENTATION

    return devmode


def status_of(printer_name: str) -> str:
    """读取打印机当前状态的中文描述（用于打印超时时给出准确提示）。

    参数：
        printer_name - 打印机名称
    返回：
        str，状态描述；查询失败时返回可读原因
    """
    if not printer_name:
        return "未选择打印机"
    try:
        info = _printer_info(printer_name)
    except pywintypes.error:
        return "打印机已不可用（可能已离线或被移除）"
    return _status_text(info.get("Status", 0))


def _list_jobs(printer_name: str) -> Optional[List[Dict[str, Any]]]:
    """枚举打印队列中的全部作业（含已完成但被驱动保留的条目）。

    参数：
        printer_name - 打印机名称
    返回：
        list[dict]，作业信息列表；查询失败则返回 None
        （ImportError 也归为失败：EnumJobs 会把作业提交时间转成带时区的 datetime，
          该转换由 pywintypes 动态导入 win32timezone 完成，打包环境缺该模块会抛
          ModuleNotFoundError，此时只是「查不到」而不是「打印失败」）
    """
    if not printer_name:
        return None
    try:
        handle = win32print.OpenPrinter(printer_name)
        try:
            return list(win32print.EnumJobs(handle, 0, 999, 1))
        finally:
            win32print.ClosePrinter(handle)
    except (pywintypes.error, OSError, ImportError):
        return None


def count_jobs(printer_name: str) -> int:
    """统计打印机的当前**未完成**任务数（含其他程序提交的任务）。

    说明：不少打印机驱动或用户设置会启用「保留打印文档」，已完成的作业会一直留在
    打印队列中（状态为 JOB_STATUS_PRINTED）。若把这类条目也计入，则纸已经打出来了，
    队列数却永远回不到提交前的水平，任务会被误判为「打印超时失败」——
    因此这里只统计尚未完成的作业。

    参数：
        printer_name - 打印机名称
    返回：
        int，未完成的任务数；查询失败返回 -1（调用方据此不判定失败，见 tasks._wait_spool_drain）
    """
    jobs = _list_jobs(printer_name)
    if jobs is None:
        return -1
    return sum(
        1 for job in jobs if not int(job.get("Status", 0) or 0) & _JOB_FINISHED_STATUS
    )


def job_ids(printer_name: str) -> List[int]:
    """列出打印队列中所有作业的作业号。

    用途：GDI 的 `StartDoc` 不返回作业号（实测返回 None），因此在提交前后各取一次
    队列快照，用「新出现的作业号」认领本程序刚提交的作业，供「打印中取消」使用。

    参数：
        printer_name - 打印机名称
    返回：
        list[int]，作业号列表；查询失败返回空列表
    """
    jobs = _list_jobs(printer_name)
    if jobs is None:
        return []
    return [int(job.get("JobId", 0) or 0) for job in jobs]


def new_job_id(printer_name: str, known_ids: Any) -> Optional[int]:
    """在提交作业后找出「新出现」的作业号。

    参数：
        printer_name - 打印机名称
        known_ids    - 提交前调用 job_ids() 得到的作业号集合
    返回：
        int 或 None，新出现的作业号（多个时取最大）；取不到返回 None
    """
    known = {int(item) for item in (known_ids or [])}
    fresh = [job_id for job_id in job_ids(printer_name) if job_id and job_id not in known]
    return max(fresh) if fresh else None


def cancel_job(printer_name: str, job_id: Any) -> Tuple[bool, str]:
    """取消打印队列中的指定作业（用于「打印中取消」）。

    说明：只能撤回**尚未转成纸**的部分；已经出纸的页面无法收回。

    参数：
        printer_name - 打印机名称
        job_id       - 作业号（GDI 提交后通过 new_job_id 取得）
    返回：
        (是否成功, 说明文本)
    """
    if not printer_name or not job_id:
        return False, "缺少打印机或作业号，无法取消打印队列作业"
    try:
        handle = win32print.OpenPrinter(printer_name)
        try:
            win32print.SetJob(
                handle, int(job_id), 0, None, win32print.JOB_CONTROL_CANCEL
            )
            return True, "已从打印队列中取消该作业"
        finally:
            win32print.ClosePrinter(handle)
    except (pywintypes.error, OSError, ValueError) as exc:
        return False, f"取消打印队列作业失败：{exc}"
