"""认证与权限模块：登录会话、角色校验与账号管理接口。

权限模型：
    · 未登录：只能访问登录页与静态资源；
    · 普通用户（user）：上传打印、查看/操作自己的任务与日志；
    · 管理员（admin）：额外可查看全部任务与日志，并管理账号。
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from core import audit, settings, users
from core.utils import resolve_client_host

SECRET_PATH: Path = settings.DATA_DIR / "secret.key"
SESSION_HOURS = 12

# 无需登录即可访问的路径
_PUBLIC_PATHS = ("/login", "/favicon.ico")


def register(app: Flask) -> None:
    """为 Flask 应用注册会话配置、登录拦截与账号相关路由。

    参数：
        app - Flask 应用实例
    返回：
        None
    """
    app.secret_key = _secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
    )
    users.ensure_default_admin()

    @app.before_request
    def _guard() -> Optional[Any]:
        """登录拦截：未登录时 API 返回 401，页面跳转登录页。

        参数：无
        返回：
            响应对象或 None
        """
        path = request.path
        if path in _PUBLIC_PATHS or path.startswith("/static/"):
            return None
        if current_user() is not None:
            return None
        if path.startswith("/api/"):
            return jsonify({"ok": False, "error": "登录已过期，请重新登录", "need_login": True}), 401
        return redirect(url_for("login_page", next=path))

    @app.get("/login")
    def login_page() -> Any:
        """渲染登录页。

        参数：无
        返回：
            str，登录页 HTML
        """
        if current_user() is not None:
            return redirect(url_for("index"))
        return render_template(
            "login.html", next_path=request.args.get("next", "/"), brand=settings.brand()
        )

    @app.post("/login")
    def login_submit() -> Any:
        """处理登录表单提交。

        参数（表单）：
            username - 用户名
            password - 密码
            next     - 登录成功后的跳转路径
        返回：
            重定向或渲染带错误提示的登录页
        """
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_path = request.form.get("next") or "/"
        client_ip, client_host = _client_info()

        account = users.verify(username, password)
        if account is None:
            audit.record_login(
                user=username or "(空)",
                client_ip=client_ip,
                client_host=client_host,
                success=False,
                message="用户名或密码错误",
            )
            return (
                render_template(
                    "login.html",
                    next_path=next_path,
                    error="用户名或密码错误",
                    username=username,
                    brand=settings.brand(),
                ),
                401,
            )

        session.clear()
        session["user"] = account["username"]
        session["role"] = account["role"]
        session.permanent = True
        users.mark_login(account["username"])
        audit.record_login(
            user=account["username"],
            client_ip=client_ip,
            client_host=client_host,
            success=True,
            message="登录成功",
        )
        return redirect(next_path if next_path.startswith("/") else "/")

    @app.get("/logout")
    def logout() -> Any:
        """退出登录并记录审计日志。

        参数：无
        返回：
            重定向到登录页
        """
        account = current_user()
        if account:
            client_ip, client_host = _client_info()
            audit.record_login(
                user=account["username"],
                client_ip=client_ip,
                client_host=client_host,
                success=True,
                message="退出登录",
            )
        session.clear()
        return redirect(url_for("login_page"))

    @app.get("/api/me")
    def api_me() -> Any:
        """返回当前登录账号信息。

        参数：无
        返回：
            JSON，{username, role, role_text, default_password}
        """
        account = current_user()
        if account is None:
            return jsonify({"ok": False, "error": "未登录"}), 401
        return jsonify(
            {
                "ok": True,
                "username": account["username"],
                "role": account["role"],
                "role_text": users.role_text(account["role"]),
                "default_password": users.is_default_password(account["username"]),
            }
        )

    @app.post("/api/password")
    def api_change_password() -> Any:
        """修改当前账号密码。

        参数（表单）：
            old_password - 原密码
            new_password - 新密码
        返回：
            JSON，{ok, message}
        """
        account = current_user()
        if account is None:
            return jsonify({"ok": False, "error": "未登录"}), 401
        old_password = request.form.get("old_password") or ""
        new_password = request.form.get("new_password") or ""
        if users.verify(account["username"], old_password) is None:
            return jsonify({"ok": False, "message": "原密码不正确"}), 400
        ok, message = users.set_password(account["username"], new_password)
        if ok:
            audit.record_login(
                user=account["username"],
                client_ip=request.remote_addr or "",
                client_host=resolve_client_host(request.remote_addr or ""),
                success=True,
                message="修改密码成功",
            )
        return jsonify({"ok": ok, "message": message}), (200 if ok else 400)

    @app.get("/api/users")
    @admin_required
    def api_list_users() -> Any:
        """列出全部账号（仅管理员）。

        参数：无
        返回：
            JSON，{ok, users: [...]}
        """
        return jsonify({"ok": True, "users": users.list_users()})

    @app.post("/api/users")
    @admin_required
    def api_create_user() -> Any:
        """新建账号（仅管理员）。

        参数（表单）：
            username - 用户名
            password - 初始密码
            role     - admin / user
        返回：
            JSON，{ok, message}
        """
        ok, message = users.create(
            request.form.get("username", ""),
            request.form.get("password", ""),
            request.form.get("role", "user"),
        )
        return jsonify({"ok": ok, "message": message}), (200 if ok else 400)

    @app.post("/api/users/<username>/password")
    @admin_required
    def api_reset_password(username: str) -> Any:
        """重置指定账号密码（仅管理员）。

        参数：
            username - 目标账号（路径参数）
            password - 新密码（表单）
        返回：
            JSON，{ok, message}
        """
        ok, message = users.set_password(username, request.form.get("password", ""))
        return jsonify({"ok": ok, "message": message}), (200 if ok else 400)

    @app.delete("/api/users/<username>")
    @admin_required
    def api_delete_user(username: str) -> Any:
        """删除账号（仅管理员）。

        参数：
            username - 目标账号（路径参数）
        返回：
            JSON，{ok, message}
        """
        account = current_user()
        if account and account["username"] == username:
            return jsonify({"ok": False, "message": "不能删除当前登录账号"}), 400
        ok, message = users.remove(username)
        return jsonify({"ok": ok, "message": message}), (200 if ok else 400)


def current_user() -> Optional[Dict[str, Any]]:
    """获取当前登录账号。

    参数：无
    返回：
        dict 或 None，形如 {"username": "admin", "role": "admin"}
    """
    username = session.get("user")
    if not username:
        return None
    return {"username": str(username), "role": str(session.get("role", "user"))}


def is_admin() -> bool:
    """判断当前账号是否为管理员。

    参数：无
    返回：
        bool，管理员返回 True
    """
    account = current_user()
    return bool(account and account["role"] == "admin")


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """装饰器：要求已登录且为管理员。

    参数：
        view - 视图函数
    返回：
        包装后的视图函数
    """

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not is_admin():
            return jsonify({"ok": False, "error": "需要管理员权限"}), 403
        return view(*args, **kwargs)

    return wrapper


def _secret_key() -> str:
    """读取或生成会话签名密钥（保存在 data/secret.key）。

    参数：无
    返回：
        str，密钥文本
    """
    if SECRET_PATH.exists():
        try:
            value = SECRET_PATH.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
    value = secrets.token_hex(32)
    try:
        SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRET_PATH.write_text(value, encoding="utf-8")
    except OSError:
        pass
    return value


def _client_info() -> tuple:
    """解析客户端 IP 与计算机名。

    参数：无
    返回：
        (ip, hostname)
    """
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    ip = forwarded or request.remote_addr or ""
    return ip, resolve_client_host(ip)
