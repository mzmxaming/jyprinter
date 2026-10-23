"""用户模块：账号存储、口令哈希校验与角色管理。

设计要点：
    · 账号数据保存在 data/users.json，口令仅存 PBKDF2-SHA256 哈希与随机盐，不落明文；
    · 首次运行自动创建管理员账号 admin / admin123，并在网页端提示尽快改密；
    · 角色仅两级：admin（用户管理与查看全部记录）、user（上传打印并查看自己的记录）。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import settings

ROLES = ("admin", "user")
ROLE_TEXT = {"admin": "管理员", "user": "普通用户"}

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"
MIN_PASSWORD_LENGTH = 6
_ITERATIONS = 200_000
_SALT_BYTES = 16

USERS_PATH: Path = settings.DATA_DIR / "users.json"
_lock = threading.RLock()


def role_text(role: str) -> str:
    """把角色标识转换为中文文案。

    参数：
        role - admin / user
    返回：
        str，中文角色名
    """
    return ROLE_TEXT.get(role, role)


def _hash_password(password: str, salt: str) -> str:
    """使用 PBKDF2-SHA256 计算口令哈希。

    参数：
        password - 明文口令
        salt     - 十六进制盐值
    返回：
        str，十六进制哈希
    """
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS
    )
    return digest.hex()


def _load() -> Dict[str, Any]:
    """读取账号文件（不存在或损坏时返回空结构）。

    参数：无
    返回：
        dict，{"users": [...]}
    """
    if not USERS_PATH.exists():
        return {"users": []}
    try:
        data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"users": []}
    if not isinstance(data, dict) or not isinstance(data.get("users"), list):
        return {"users": []}
    return data


def _save(data: Dict[str, Any]) -> None:
    """写入账号文件（限制权限位，避免被其他用户读取）。

    参数：
        data - 账号数据结构
    返回：
        None
    """
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(USERS_PATH, 0o600)
    except OSError:
        pass


def ensure_default_admin() -> bool:
    """确保至少存在一个管理员账号，不存在时创建默认管理员。

    参数：无
    返回：
        bool，本次是否新建了默认管理员
    """
    with _lock:
        data = _load()
        if any(user.get("role") == "admin" for user in data["users"]):
            return False
        salt = secrets.token_hex(_SALT_BYTES)
        data["users"].append(
            {
                "username": DEFAULT_USERNAME,
                "role": "admin",
                "salt": salt,
                "hash": _hash_password(DEFAULT_PASSWORD, salt),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_login": "",
            }
        )
        _save(data)
        return True


def list_users() -> List[Dict[str, Any]]:
    """列出全部账号（不含口令哈希与盐）。

    参数：无
    返回：
        list[dict]，元素含 username / role / role_text / created_at / last_login
    """
    with _lock:
        data = _load()
    return [
        {
            "username": user.get("username", ""),
            "role": user.get("role", "user"),
            "role_text": role_text(user.get("role", "user")),
            "created_at": user.get("created_at", ""),
            "last_login": user.get("last_login", ""),
        }
        for user in data["users"]
    ]


def get(username: str) -> Optional[Dict[str, Any]]:
    """按用户名查询账号内部结构。

    参数：
        username - 用户名
    返回：
        dict 或 None，包含哈希与盐的原始记录
    """
    with _lock:
        data = _load()
    for user in data["users"]:
        if user.get("username") == username:
            return dict(user)
    return None


def verify(username: str, password: str) -> Optional[Dict[str, Any]]:
    """校验用户名与口令。

    参数：
        username - 用户名
        password - 明文口令
    返回：
        dict 或 None，校验通过时返回账号公开信息（username / role）
    """
    record = get(username)
    if not record:
        # 仍执行一次哈希运算，避免通过响应时间区分「用户不存在」与「口令错误」
        _hash_password(password, "00" * _SALT_BYTES)
        return None
    expected = str(record.get("hash", ""))
    actual = _hash_password(password, str(record.get("salt", "")))
    if not secrets.compare_digest(expected, actual):
        return None
    return {"username": record.get("username", ""), "role": record.get("role", "user")}


def mark_login(username: str) -> None:
    """记录账号最近登录时间。

    参数：
        username - 用户名
    返回：
        None
    """
    with _lock:
        data = _load()
        for user in data["users"]:
            if user.get("username") == username:
                user["last_login"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _save(data)
                return


def create(username: str, password: str, role: str = "user") -> Tuple[bool, str]:
    """新建账号。

    参数：
        username - 用户名（3-32 位字母/数字/下划线/短横线）
        password - 明文口令（至少 6 位）
        role     - admin / user
    返回：
        (是否成功, 说明文本)
    """
    name = (username or "").strip()
    if not _valid_username(name):
        return False, "用户名需为 3-32 位字母、数字、下划线或短横线"
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return False, f"密码至少 {MIN_PASSWORD_LENGTH} 位"
    if role not in ROLES:
        return False, "角色只能是管理员或普通用户"

    with _lock:
        data = _load()
        if any(user.get("username") == name for user in data["users"]):
            return False, "用户名已存在"
        salt = secrets.token_hex(_SALT_BYTES)
        data["users"].append(
            {
                "username": name,
                "role": role,
                "salt": salt,
                "hash": _hash_password(password, salt),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_login": "",
            }
        )
        _save(data)
    return True, "账号已创建"


def set_password(username: str, password: str) -> Tuple[bool, str]:
    """重置指定账号的口令。

    参数：
        username - 用户名
        password - 新明文口令
    返回：
        (是否成功, 说明文本)
    """
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return False, f"密码至少 {MIN_PASSWORD_LENGTH} 位"
    with _lock:
        data = _load()
        for user in data["users"]:
            if user.get("username") == username:
                salt = secrets.token_hex(_SALT_BYTES)
                user["salt"] = salt
                user["hash"] = _hash_password(password, salt)
                _save(data)
                return True, "密码已更新"
    return False, "账号不存在"


def remove(username: str) -> Tuple[bool, str]:
    """删除账号（不允许删除最后一个管理员）。

    参数：
        username - 用户名
    返回：
        (是否成功, 说明文本)
    """
    with _lock:
        data = _load()
        target = next((u for u in data["users"] if u.get("username") == username), None)
        if target is None:
            return False, "账号不存在"
        if target.get("role") == "admin":
            admins = [u for u in data["users"] if u.get("role") == "admin"]
            if len(admins) <= 1:
                return False, "至少需要保留一个管理员账号"
        data["users"] = [u for u in data["users"] if u.get("username") != username]
        _save(data)
    return True, "账号已删除"


def is_default_password(username: str) -> bool:
    """判断指定账号是否仍在使用初始默认口令。

    参数：
        username - 用户名
    返回：
        bool，仍为默认口令时返回 True
    """
    record = get(username)
    if not record:
        return False
    actual = _hash_password(DEFAULT_PASSWORD, str(record.get("salt", "")))
    return secrets.compare_digest(str(record.get("hash", "")), actual)


def _valid_username(name: str) -> bool:
    """校验用户名格式。

    参数：
        name - 用户名
    返回：
        bool，合法返回 True
    """
    if not 3 <= len(name) <= 32:
        return False
    return all(char.isalnum() or char in "_-" for char in name)
