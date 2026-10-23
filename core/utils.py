"""通用工具模块：本机地址、客户端主机名解析、文件名与文本解码等公共函数。"""

from __future__ import annotations

import re
import socket
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

_host_cache: Dict[str, str] = {}
_host_lock = threading.Lock()

# 上传文件名中需要剔除的字符（防目录穿越与非法路径）
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def now_str() -> str:
    """返回当前本地时间字符串。

    参数：无
    返回：
        str，格式为 YYYY-MM-DD HH:MM:SS
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_local_ip() -> str:
    """获取本机用于局域网访问的首选 IPv4 地址。

    参数：无
    返回：
        str，形如 192.168.1.10；获取失败时返回 127.0.0.1
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 不会真正发包，仅用于让系统选出默认出口网卡
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def get_all_local_ips() -> List[str]:
    """获取本机全部 IPv4 地址列表。

    参数：无
    返回：
        list[str]，包含所有网卡地址，首个为默认出口地址
    """
    ips: List[str] = []
    primary = get_local_ip()
    ips.append(primary)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in ips:
                ips.append(addr)
    except OSError:
        pass
    return ips


def resolve_client_host(ip: str) -> str:
    """将客户端 IP 反查为计算机名（带缓存，反查失败返回原 IP）。

    参数：
        ip - 客户端 IPv4 地址
    返回：
        str，计算机名或原 IP
    """
    if not ip:
        return ""
    with _host_lock:
        if ip in _host_cache:
            return _host_cache[ip]
    try:
        host = socket.gethostbyaddr(ip)[0]
    except (OSError, socket.error):
        host = ip
    with _host_lock:
        _host_cache[ip] = host
    return host


def sanitize_filename(name: str) -> str:
    """清洗上传文件名，仅保留安全字符用于展示。

    参数：
        name - 原始文件名（可能包含路径）
    返回：
        str，清洗后的文件名；为空时返回 untitled
    """
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = _UNSAFE_NAME.sub("_", base).strip().strip(".")
    return base or "untitled"


def human_size(size: int) -> str:
    """将字节数格式化为可读字符串。

    参数：
        size - 字节数
    返回：
        str，如 12.3 KB
    """
    value = float(max(int(size or 0), 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def remove_file(path: Any) -> None:
    """删除文件（忽略文件不存在与删除失败，兼容 Python 3.7）。

    使用本函数而不用 Path.unlink(missing_ok=True)，是为了兼容
    银河麒麟等发行版自带的 Python 3.7（missing_ok 参数自 3.8 起才支持）。

    参数：
        path - 文件路径（str 或 Path）
    返回：
        None
    """
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # 文件被占用或权限不足时不影响主流程（多为临时产物清理）
        pass


def import_pymupdf() -> Any:
    """导入 PyMuPDF 模块（兼容新旧模块名 pymupdf / fitz）。

    参数：无
    返回：
        module，PyMuPDF 模块对象
    异常：
        ImportError - 未安装 PyMuPDF
    """
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        import fitz  # 旧版本 PyMuPDF 的模块名

        return fitz


def decode_text(raw: bytes) -> str:
    """按常见中文编码依次尝试解码文本内容。

    参数：
        raw - 文件原始字节
    返回：
        str，解码结果；全部失败时以 UTF-8 忽略错误解码
    """
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")
