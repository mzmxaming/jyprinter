"""Linux / macOS 打印机管理：基于 CUPS 命令行工具。

macOS 自带 CUPS（lp/lpstat/lpoptions 均位于 /usr/bin），因此与 Linux 共用本实现。

依赖系统命令：
    lpstat   —— 查询打印机、默认打印机、作业队列（cups-client / cups 包）
    lpoptions—— 查询打印机支持的纸张、单双面等选项
    lp       —— 提交打印作业
    cancel   —— 取消作业（保留接口，暂未在界面开放）

所有「命令输出 → 结构化数据」的解析都写成纯函数，便于离线单元测试
（CUPS 输出格式在不同版本/语言环境下有差异，是这一层最容易出错的地方）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from core.formats import SUPPORTED_EXTS  # noqa: F401 - 供上层统一引用
from core.osinfo import IS_MACOS

# 常用纸张名称（CUPS 的 media 取值）与尺寸（点，1 点 = 1/72 英寸）
PAPER_SIZES: Dict[str, Tuple[float, float]] = {
    "A3": (841.89, 1190.55),
    "A4": (595.28, 841.89),
    "A5": (419.53, 595.28),
    "A6": (297.64, 419.53),
    "B4": (708.66, 1000.63),
    "B5": (498.90, 708.66),
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
    "Executive": (522.0, 756.0),
    "Tabloid": (792.0, 1224.0),
    "16K": (567.0, 794.0),
}

# 默认纸张
DEFAULT_PAPER = "A4"

# 标准纸张列表（驱动未上报可用纸张名时的兜底展示顺序，A4 优先）
STANDARD_PAPERS = ("A4", "A5", "B5", "Letter", "Legal", "A3", "16K")

# 无信息量的选择项名：部分 PPD 只上报占位名（如 CustomPage0…CustomPage21、CustomSlot0），
# 名字里不含任何尺寸信息，界面无法据此让用户选择纸张（实测联想 LJ2200 即如此）
_MEANINGLESS_CHOICE = re.compile(
    r"^(custompage\d+|customslot\d+|custom\.\S*)$", re.IGNORECASE
)

# 虚拟打印机关键字（队列名或型号命中即视为虚拟设备）
VIRTUAL_KEYWORDS = (
    "pdf",
    "xps",
    "fax",
    "virtual",
    "print-to-file",
    "print_to_file",
    "cups-pdf",
    "file",
)

# 命令超时（秒）
_CMD_TIMEOUT = 20


class CupsError(RuntimeError):
    """CUPS 命令不可用或执行失败时抛出的可读异常。"""


def _which(command: str) -> Optional[str]:
    """查找系统命令路径。

    参数：
        command - 命令名
    返回：
        str 或 None，命令绝对路径
    """
    return shutil.which(command)


def cups_available() -> bool:
    """判断 CUPS 客户端命令是否可用。

    参数：无
    返回：
        bool，lpstat 与 lp 均存在时返回 True
    """
    return bool(_which("lpstat") and _which("lp"))


def require_cups() -> None:
    """校验 CUPS 客户端是否可用，不可用时抛出可读异常。

    参数：无
    返回：
        None
    异常：
        CupsError - 缺少 CUPS 客户端命令
    """
    if cups_available():
        return
    if IS_MACOS:
        raise CupsError(
            "未检测到 CUPS 打印组件（lp/lpstat）。macOS 自带 CUPS，"
            "请检查 /usr/bin/lpstat 是否存在，并在「系统设置 → 打印机与扫描仪」中添加打印机"
        )
    raise CupsError(
        "未检测到 CUPS 打印组件（lp/lpstat），请先安装："
        "sudo apt install cups cups-client（Debian/Ubuntu/银河麒麟）"
        "或 sudo yum install cups（RPM 系），并确保 cups 服务已启动"
    )


def command_env() -> Dict[str, str]:
    """构造 CUPS 子进程的环境变量：强制使用 C 语言环境。

    CUPS 命令的输出会随系统语言本地化，例如中文系统下 `lp` 输出的是
    「请求 ID 为 队列-42（1 个文件）」而不是
    "request id is queue-42 (1 file(s))"，`lpstat -p` 的 "printer X is idle."
    同样会被翻译 —— 本地化文本无法稳定解析（实测麒麟上正是如此）。
    这里统一以 C 环境执行，保证所有发行版上输出格式一致。

    注意：GNU gettext 的 LANGUAGE 优先级高于 LC_ALL，必须一并设置。

    参数：无
    返回：
        dict，子进程环境变量
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["LANGUAGE"] = "C"
    return env


def run_command(args: List[str]) -> str:
    """执行系统命令并返回标准输出（失败时抛出 CupsError）。

    参数：
        args - 命令与参数列表
    返回：
        str，标准输出文本
    异常：
        CupsError - 命令不存在或返回码非 0
    """
    executable = _which(args[0])
    if not executable:
        raise CupsError(f"未找到系统命令：{args[0]}")
    try:
        result = subprocess.run(
            [executable] + args[1:],
            capture_output=True,
            timeout=_CMD_TIMEOUT,
            env=command_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise CupsError(f"命令执行超时：{' '.join(args)}") from exc
    except OSError as exc:
        raise CupsError(f"命令执行失败：{exc}") from exc

    text = _decode(result.stdout)
    if result.returncode != 0:
        detail = " ".join(_decode(result.stderr).split()) or f"返回码 {result.returncode}"
        raise CupsError(f"{' '.join(args)} 执行失败：{detail}")
    return text


def _decode(raw: bytes) -> str:
    """按 UTF-8 / GBK 依次解码命令输出。

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


# --------------------------------------------------------------------------- 解析（纯函数，可离线单测）


def parse_lpstat_d(text: str) -> str:
    """解析 `lpstat -d` 输出，取默认打印机名。

    参数：
        text - 命令输出，如 "system default destination: HP_LaserJet"
               或中文环境下的 "默认目标：HP_LaserJet"（全角冒号）
    返回：
        str，默认打印机名；未设置时返回空字符串
    """
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if "default destination" not in lowered and "默认目标" not in stripped:
            continue
        # 兼容半角冒号与中文全角冒号
        for separator in (":", "："):
            if separator in stripped:
                return stripped.split(separator, 1)[1].strip()
    return ""


def parse_lpstat_p(text: str) -> List[Dict[str, Any]]:
    """解析 `lpstat -p` 输出，得到打印机列表与状态。

    参数：
        text - 命令输出，每台打印机形如：
               printer HP_LaserJet is idle.  enabled since ...
               printer HP_LaserJet disabled since ... -
               中文环境（未强制 C 语言环境时）形如：
               打印机 Lenovo-LJ2200 目前空闲。从 2026年09月23日 星期三 15时43分05秒 开始启用
    返回：
        list[dict]，元素含 name / enabled / status / detail
    """
    printers: List[Dict[str, Any]] = []
    pattern = re.compile(r"^\s*printer\s+(\S+)\s+(.*)$", re.IGNORECASE)
    pattern_zh = re.compile(r"^\s*打印机\s+(\S+)\s+(.*)$")
    for line in text.splitlines():
        match = pattern.match(line) or pattern_zh.match(line)
        if not match:
            continue
        name = match.group(1)
        rest = match.group(2).strip()
        lowered = rest.lower()
        enabled = "disabled" not in lowered and "已停用" not in rest
        if "is idle" in lowered or "空闲" in rest:
            status = "就绪"
        elif "now printing" in lowered or "printing" in lowered or "正在打印" in rest:
            status = "打印中"
        elif not enabled:
            status = "已停用"
        elif "stopped" in lowered or "已停止" in rest:
            status = "已停止"
        else:
            status = rest.split(".")[0][:40] or "未知"
        printers.append(
            {"name": name, "enabled": enabled, "status": status, "detail": rest}
        )
    return printers


def parse_lpstat_v(text: str) -> Dict[str, str]:
    """解析 `lpstat -v` 输出，得到「队列 → 设备」映射。

    参数：
        text - 命令输出，形如 device for HP_LaserJet: ipp://192.168.1.10/ipp/print
               或中文环境的 用于 Lenovo-LJ2200 的设备：usb://Lenovo/LJ2200?serial=...
    返回：
        dict，{队列名: 设备 URI}
    """
    devices: Dict[str, str] = {}
    pattern = re.compile(r"^\s*device for\s+(\S+)\s*:\s*(.+)$", re.IGNORECASE)
    pattern_zh = re.compile(r"^\s*用于\s+(\S+)\s+的设备\s*[：:]\s*(.+)$")
    for line in text.splitlines():
        match = pattern.match(line) or pattern_zh.match(line)
        if match:
            devices[match.group(1)] = match.group(2).strip()
    return devices


def parse_lpstat_o(text: str, printer: Optional[str] = None) -> List[Dict[str, str]]:
    """解析 `lpstat -o` 输出，得到排队中的作业列表。

    参数：
        text    - 命令输出，形如 HP_LaserJet-42 user 1024 Wed 23 Sep ...
        printer - 仅统计指定打印机（None 表示全部）
    返回：
        list[dict]，元素含 printer / job_id / user / size
    """
    jobs: List[Dict[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts or "-" not in parts[0]:
            continue
        queue, _, job_number = parts[0].rpartition("-")
        if not queue or not job_number.isdigit():
            continue
        if printer and queue != printer:
            continue
        jobs.append(
            {
                "printer": queue,
                "job_id": parts[0],
                "user": parts[1] if len(parts) > 1 else "",
                "size": parts[2] if len(parts) > 2 else "",
            }
        )
    return jobs


def parse_lpoptions_l(text: str) -> Dict[str, Dict[str, Any]]:
    """解析 `lpoptions -p QUEUE -l` 输出，得到该打印机支持的选项。

    参数：
        text - 命令输出，每行形如：
               PageSize/Page Size: A4 *Letter Legal
               sides/2-Sided Printing: *one-sided two-sided-long-edge
    返回：
        dict，{选项名: {"description": 描述, "choices": [...], "default": 默认项}}
    """
    options: Dict[str, Dict[str, Any]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        head, _, tail = stripped.partition(":")
        if "/" in head:
            name, _, description = head.partition("/")
        else:
            name, description = head, head
        choices: List[str] = []
        default = ""
        for token in tail.split():
            token = token.strip()
            if not token:
                continue
            if token.startswith("*"):
                default = token[1:]
                choices.append(default)
            else:
                choices.append(token)
        options[name.strip()] = {
            "description": description.strip(),
            "choices": choices,
            "default": default,
        }
    return options


def paper_names_from_options(options: Dict[str, Dict[str, Any]]) -> List[str]:
    """从 CUPS 选项中提取纸张名称列表。

    参数：
        options - parse_lpoptions_l 的结果
    返回：
        list[str]，纸张名称（取不到时返回空列表）
    """
    for key in ("media", "PageSize", "PageRegion", "InputSlot"):
        item = options.get(key)
        if item and item.get("choices"):
            if key == "InputSlot":
                continue
            return [str(name) for name in item["choices"]]
    return []


def paper_names_usable(names: List[str]) -> bool:
    """判断驱动上报的纸张名是否含有可用信息。

    部分 PPD 只上报占位名（CustomPage0…CustomPage21 之类），名字里不含任何尺寸信息，
    界面无法据此让用户选择纸张；此时应改用程序内置的标准纸张表。

    参数：
        names - 纸张名称列表
    返回：
        bool，至少有一个非占位名时返回 True
    """
    for name in names:
        text = str(name or "").strip()
        if text and not _MEANINGLESS_CHOICE.match(text):
            return True
    return False


# 双面「开启」的可选值：IPP 关键字（driverless 队列）与 PPD 关键字（PPD 队列）
_DUPLEX_ON_CHOICES = (
    "two-sided-long-edge",
    "two-sided-short-edge",
    "duplexnotumble",
    "duplextumble",
)


def duplex_supported(options: Dict[str, Dict[str, Any]]) -> bool:
    """判断打印机是否支持双面打印。

    兼容两类队列：driverless（IPP）队列上报 `sides` 选项，取值形如
    `two-sided-long-edge`；PPD 队列上报 `Duplex` 选项，取值形如
    `DuplexNoTumble`（实测联想 LJ2200 属后者，只认 IPP 关键字会误判为不支持）。

    参数：
        options - parse_lpoptions_l 的结果
    返回：
        bool，支持返回 True
    """
    for key in ("sides", "Duplex"):
        item = options.get(key)
        if not item:
            continue
        choices = [str(choice).lower() for choice in item.get("choices", [])]
        if any(choice in _DUPLEX_ON_CHOICES for choice in choices):
            return True
    return False


def duplex_args_from_options(
    options: Dict[str, Dict[str, Any]], duplex: str
) -> List[str]:
    """把单双面选择解析为 CUPS 的 -o 参数（纯函数，便于离线单测）。

    PPD 队列用 `Duplex=None/DuplexNoTumble/DuplexTumble` 关键字（CUPS 官方文档中
    `Option "Duplex/Two-Sided Printing"` 的规范写法即这三项）；
    driverless（IPP）队列用 `sides=one-sided/two-sided-long-edge`（CUPS 标准选项）。
    只要驱动声明了 `Duplex`，两个方向都直接用它的原生关键字，不依赖 CUPS 的选项名映射。

    参数：
        options - parse_lpoptions_l 的结果
        duplex  - simplex 单面 / vertical 双面长边翻转
    返回：
        list[str]，形如 ["-o", "Duplex=DuplexNoTumble"]
    """
    item = options.get("Duplex")
    choices = [str(choice).lower() for choice in item.get("choices", [])] if item else []

    if duplex == "vertical":
        if "duplexnotumble" in choices:
            return ["-o", "Duplex=DuplexNoTumble"]
        if "duplextumble" in choices:
            return ["-o", "Duplex=DuplexTumble"]
        return ["-o", "sides=two-sided-long-edge"]

    if "none" in choices:
        return ["-o", "Duplex=None"]
    return ["-o", "sides=one-sided"]


def parse_lp_job_id(text: str) -> str:
    """从 `lp` 输出中解析作业号。

    参数：
        text - 命令输出，形如 "request id is HP_LaserJet-42 (1 file(s))"；
               若未强制 C 语言环境，中文系统下会形如
               "请求 ID 为 Lenovo-LJ2200-2（1 个文件）"
    返回：
        str，作业号（如 HP_LaserJet-42）；解析失败返回空字符串
    """
    match = re.search(r"request id is\s+(\S+)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    # 兜底：作业号恒为「队列名-数字」，与语言无关，可按此形态直接提取
    fallback = re.search(r"(\S+-\d+)", text)
    return fallback.group(1) if fallback else ""


# --------------------------------------------------------------------------- 对外接口


def is_virtual(printer_name: str, driver_name: str = "") -> bool:
    """判断指定打印机是否为虚拟打印机。

    参数：
        printer_name - 队列名称
        driver_name  - 型号信息（Linux 下通常为空）
    返回：
        bool，命中虚拟设备关键字返回 True
    """
    text = f"{printer_name} {driver_name}".lower()
    return any(keyword in text for keyword in VIRTUAL_KEYWORDS)


def get_default_printer() -> str:
    """获取默认打印机名称。

    参数：无
    返回：
        str，默认打印机名；未设置或 CUPS 不可用时返回空字符串
    """
    if not cups_available():
        return ""
    try:
        return parse_lpstat_d(run_command(["lpstat", "-d"]))
    except CupsError:
        return ""


def list_printers(include_virtual: bool = False) -> List[Dict[str, Any]]:
    """枚举 CUPS 中的打印机列表。

    参数：
        include_virtual - 为 True 时保留虚拟打印机
    返回：
        list[dict]，元素含 name / driver / port / is_default / is_virtual / status
    """
    if not cups_available():
        return []

    try:
        status_text = run_command(["lpstat", "-p"])
    except CupsError:
        return []
    try:
        device_map = parse_lpstat_v(run_command(["lpstat", "-v"]))
    except CupsError:
        device_map = {}

    default_name = get_default_printer()
    printers: List[Dict[str, Any]] = []
    for item in parse_lpstat_p(status_text):
        name = item["name"]
        device = device_map.get(name, "")
        virtual = is_virtual(name)
        if virtual and not include_virtual:
            continue
        printers.append(
            {
                "name": name,
                "driver": device or "CUPS 队列",
                "port": device,
                "is_default": name == default_name,
                "is_virtual": virtual,
                "status": item["status"],
            }
        )
    return printers


def _printer_options(printer_name: str) -> Dict[str, Dict[str, Any]]:
    """读取指定打印机的驱动选项（失败时返回空字典）。

    参数：
        printer_name - 打印机名称
    返回：
        dict，parse_lpoptions_l 的结果
    """
    if not printer_name or not cups_available() or not _which("lpoptions"):
        return {}
    try:
        return parse_lpoptions_l(run_command(["lpoptions", "-p", printer_name, "-l"]))
    except CupsError:
        return {}


def _default_capabilities() -> Dict[str, Any]:
    """返回无法探测驱动能力时的默认能力表。

    参数：无
    返回：
        dict，{papers, duplex, probed}，纸张为标准纸张表、假定支持双面
    """
    return {
        "papers": _papers_payload(list(STANDARD_PAPERS)),
        "duplex": True,
        "probed": False,
    }


def get_capabilities(printer_name: str) -> Dict[str, Any]:
    """读取打印机支持的纸张与双面能力。

    参数：
        printer_name - 打印机名称
    返回：
        dict，{papers: [{id, name}], duplex: bool, probed: bool, paper_note?: str}
        `paper_note` 仅在「驱动只上报占位纸张名」时出现，供界面提示用户
    """
    if not printer_name or not cups_available() or not _which("lpoptions"):
        return _default_capabilities()

    options = _printer_options(printer_name)
    if not options:
        return _default_capabilities()

    duplex = duplex_supported(options)
    names = paper_names_from_options(options)
    if paper_names_usable(names):
        return {"papers": _papers_payload(names), "duplex": duplex, "probed": True}

    # 驱动只上报 CustomPage0… 这类占位名（无尺寸信息），改用标准纸张表，
    # 并且不向驱动传递 media 参数（见 resolve_media_name），
    # 实际出纸尺寸由打印机自身的默认设置决定
    result = _default_capabilities()
    result["duplex"] = duplex
    if names:
        result["paper_note"] = (
            "该打印机驱动未上报具体纸张尺寸（仅占位名），纸张列表已改为标准纸张；"
            "实际出纸尺寸由打印机自身的默认设置决定"
        )
    return result


def _papers_payload(names: List[str]) -> List[Dict[str, Any]]:
    """把纸张名称序列转换为接口数据结构。

    参数：
        names - 纸张名称序列
    返回：
        list[dict]，元素为 {"id": int, "name": str}
    """
    return [{"id": index + 1, "name": str(name)} for index, name in enumerate(names)]


def resolve_media_name(printer_name: str, paper: str) -> str:
    """把纸张名称解析为要传给 `lp -o media=` 的取值。

    参数：
        printer_name - 打印机名称
        paper        - 纸张名称（如 A4）
    返回：
        str，media 取值；返回**空字符串表示不传 media 参数**
        （驱动只上报占位纸张名时无法可靠指定纸张，交由打印机自身默认设置决定）
    """
    text = str(paper or "").strip()
    if not text:
        return ""

    available = paper_names_from_options(_printer_options(printer_name))
    if not paper_names_usable(available):
        # 驱动只上报 CustomPage0… 这类占位名，传过去没有意义，反而可能与
        # 页面实际尺寸（由 paper_size_points 决定）不一致
        return ""

    for name in available:
        if str(name).lower() == text.lower():
            return str(name)
    if text in PAPER_SIZES:
        return text
    return DEFAULT_PAPER


def duplex_args(printer_name: str, duplex: str) -> List[str]:
    """把单双面选择解析为 CUPS 的 -o 参数（含驱动探测）。

    参数：
        printer_name - 打印机名称（空表示系统默认打印机）
        duplex       - simplex 单面 / vertical 双面长边翻转
    返回：
        list[str]，形如 ["-o", "Duplex=DuplexNoTumble"]
    """
    target = printer_name or get_default_printer()
    return duplex_args_from_options(_printer_options(target), duplex)


def paper_size_points(paper: str) -> Tuple[float, float]:
    """返回纸张的宽高（点）。

    参数：
        paper - 纸张名称
    返回：
        (宽, 高)，单位点；未知纸张按 A4 处理
    """
    text = str(paper or "").strip()
    if text in PAPER_SIZES:
        return PAPER_SIZES[text]
    for name, size in PAPER_SIZES.items():
        if name.lower() == text.lower():
            return size
    return PAPER_SIZES[DEFAULT_PAPER]


def status_of(printer_name: str) -> str:
    """读取打印机当前状态的中文描述。

    参数：
        printer_name - 打印机名称
    返回：
        str，状态描述；查询失败返回可读原因
    """
    if not printer_name:
        return "未选择打印机"
    if not cups_available():
        return "未安装 CUPS 打印组件"
    try:
        text = run_command(["lpstat", "-p", printer_name])
    except CupsError as exc:
        return f"打印机不可用（{exc}）"
    items = parse_lpstat_p(text)
    if items:
        return items[0]["status"]
    return "状态未知"


def count_jobs(printer_name: str = "") -> int:
    """统计排队中的作业数量。

    参数：
        printer_name - 打印机名称（空表示全部打印机）
    返回：
        int，作业数；查询失败返回 -1
    """
    if not cups_available():
        return -1
    try:
        text = run_command(["lpstat", "-o"])
    except CupsError:
        return -1
    return len(parse_lpstat_o(text, printer_name or None))


def list_job_ids(printer_name: str = "") -> List[str]:
    """列出排队中的作业号（用于打印完成后判断是否送达）。

    参数：
        printer_name - 打印机名称（空表示全部）
    返回：
        list[str]，作业号列表
    """
    if not cups_available():
        return []
    try:
        text = run_command(["lpstat", "-o"])
    except CupsError:
        return []
    return [item["job_id"] for item in parse_lpstat_o(text, printer_name or None)]


def submit_job(
    file_path: str,
    printer: str,
    *,
    copies: int = 1,
    media: str = DEFAULT_PAPER,
    duplex: str = "simplex",
    title: str = "",
) -> str:
    """通过 lp 提交打印作业。

    参数：
        file_path - 待打印文件（PDF 最佳）
        printer   - 目标打印机名称（空表示系统默认）
        copies    - 份数
        media     - 纸张（CUPS media 取值）
        duplex    - simplex 单面 / vertical 双面长边翻转
        title     - 作业名称
    返回：
        str，作业号（如 HP_LaserJet-42）；命令成功但无法解析作业号时返回空字符串
    异常：
        CupsError - CUPS 不可用或提交失败
    """
    require_cups()
    args = ["lp"]
    if printer:
        args += ["-d", printer]
    if title:
        args += ["-t", title]
    args += ["-n", str(max(1, int(copies or 1)))]
    if media:
        args += ["-o", f"media={media}"]
    # 双面参数按驱动声明的关键字选择（PPD 用 Duplex=…，IPP 用 sides=…）
    args += duplex_args(printer, duplex)
    args.append(file_path)

    # run_command 在返回码非 0 时已抛异常，能执行到这里说明作业已被 CUPS 接受。
    # 若仅因输出格式差异取不到作业号，绝不能判为失败（否则会出现「纸已打出却显示失败」），
    # 此时返回空字符串，由上层跳过队列跟踪。
    output = run_command(args)
    job_id = parse_lp_job_id(output)
    if not job_id:
        print(
            "[提示] 打印作业已提交，但未能从输出中解析作业号，将跳过队列跟踪："
            + " ".join(output.split())[:120],
            flush=True,
        )
    return job_id


def cancel_job(job_id: str) -> Tuple[bool, str]:
    """取消指定作业。

    参数：
        job_id - 作业号
    返回：
        (是否成功, 说明文本)
    """
    if not job_id:
        return False, "作业号为空"
    try:
        run_command(["cancel", job_id])
        return True, "作业已取消"
    except CupsError as exc:
        return False, str(exc)
