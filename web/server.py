"""Flask 网页管理端：提供页面渲染与上传、参数、队列、日志等 HTTP 接口。"""

from __future__ import annotations

import io
import json
import socket
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.middleware.proxy_fix import ProxyFix

from core import audit, converter, engines, preview, printers, render, settings, split, stats
from core.tasks import TaskQueue
from core.utils import get_all_local_ips, resolve_client_host, sanitize_filename
from web import auth

# 上传表单允许的字段名
_FILE_FIELDS = ("files", "file")


def create_app(task_queue: TaskQueue) -> Flask:
    """创建 Flask 应用实例。

    参数：
        task_queue - 打印任务队列（由入口注入，全局共享）
    返回：
        Flask，已注册全部路由的应用对象
    """
    web_root = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        static_folder=str(web_root / "static"),
        template_folder=str(web_root / "templates"),
    )
    app.config["MAX_CONTENT_LENGTH"] = int(settings.get("max_upload_mb", 100)) * 1024 * 1024
    # 兼容内网穿透 / 反向代理：信任一层 X-Forwarded-* 头，使客户端 IP、协议与主机名正确
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    auth.register(app)

    @app.get("/")
    def index() -> str:
        """渲染网页管理端首页。

        参数：无
        返回：
            str，渲染后的 HTML
        """
        account = auth.current_user() or {"username": "", "role": "user"}
        return render_template(
            "index.html",
            config=settings.load(),
            brand=settings.brand(),
            user=account,
            is_admin=auth.is_admin(),
            supported=sorted(engines.SUPPORTED_EXTS),
            pending=sorted(engines.PENDING_EXTS),
            office_available=_office_available(),
            unavailable=sorted(converter.OFFICE_EXTS - set(_office_available())),
            engines=_engine_summary(),
            access=_access_info(),
            qr_available=_qr_available(),
        )

    @app.get("/a3-to-a4")
    def a3_split_page() -> str:
        """渲染「A3 页面拆分为 A4」工具页。

        纯前端工具：文件在浏览器内用 pdf.js 读取、jsPDF 导出，**不上传、不经过服务端**；
        需要登录后访问（入口在首页顶栏），与主界面共用同一套品牌顶栏。

        参数：无
        返回：
            str，渲染后的 HTML
        """
        account = auth.current_user() or {"username": "", "role": "user"}
        return render_template(
            "a3_split.html",
            brand=settings.brand(),
            user=account,
            is_admin=auth.is_admin(),
        )

    @app.post("/api/split-a4")
    def api_split_a4() -> Any:
        """把上传的 A3 PDF 沿裁切线拆成 A4（服务端矢量裁剪，输出真矢量 PDF）。

        与浏览器内拆分的区别：这里用 PyMuPDF 把源页的一段**原样搬运**到 A4 新页上，
        文字仍是文字、矢量仍是矢量（浏览器内那条链路导出的是位图）。
        文件只在内存里处理、处理完即释放，不落临时文件、不进打印队列、不写审计日志。

        参数（multipart 表单）：
            files - 一个 PDF 文件
            cuts  - JSON 数组，每个元素是**对应页**的裁切比例列表，
                    例如 [[0.5], [], [0.33,0.66]]；元素为空表示该页整页缩放到一张 A4
        返回：
            PDF 文件（附件下载）；参数不合法时返回 JSON {ok:false, error}
        """
        files = _collect_files()
        if not files:
            return jsonify({"ok": False, "error": "未收到文件"}), 400
        if len(files) > 1:
            return jsonify({"ok": False, "error": "矢量拆分一次只处理一个 PDF 文件"}), 400

        storage = files[0]
        name = sanitize_filename(storage.filename or "")
        if engines.normalize_ext(Path(name).suffix) != "pdf":
            return jsonify({"ok": False, "error": "矢量拆分仅支持 PDF（图片请用浏览器内拆分）"}), 400

        raw_cuts = (request.form.get("cuts") or "").strip()
        try:
            cuts = json.loads(raw_cuts) if raw_cuts else None
        except ValueError:
            return jsonify({"ok": False, "error": "裁切参数不是合法的 JSON"}), 400

        try:
            data = split.split_pdf_bytes(storage.read(), cuts)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - 兜底：不让底层异常变成 500 页面
            return jsonify({"ok": False, "error": "矢量拆分失败：%s" % exc}), 500

        return send_file(
            io.BytesIO(data),
            mimetype="application/pdf",
            as_attachment=True,
            download_name="拆分后页面_A4_矢量.pdf",
        )

    @app.get("/api/printers")
    def api_printers() -> Any:
        """查询可用打印机列表。

        参数（查询串）：
            include_virtual - 传 1 时返回虚拟打印机
        返回：
            JSON，{printers: [...], default: 默认打印机名}
        """
        include_virtual = request.args.get("include_virtual") == "1"
        if not include_virtual:
            include_virtual = not bool(settings.get("filter_virtual_printers", True))
        return jsonify(
            {
                "printers": printers.list_printers(include_virtual=include_virtual),
                "default": printers.get_default_printer(),
            }
        )

    @app.get("/api/printer/capabilities")
    def api_capabilities() -> Any:
        """查询指定打印机的纸张与双面能力。

        参数（查询串）：
            name - 打印机名称
        返回：
            JSON，{papers: [...], duplex: bool}
        """
        name = request.args.get("name", "")
        if not name:
            name = printers.get_default_printer()
        return jsonify(printers.get_capabilities(name))

    @app.get("/api/formats")
    def api_formats() -> Any:
        """查询支持格式清单与本机可用的文档转换引擎。

        参数：无
        返回：
            JSON，{supported, pending, office_available, engines, libreoffice}
        """
        probe = converter.probe_availability()
        return jsonify(
            {
                "supported": sorted(engines.SUPPORTED_EXTS),
                "pending": sorted(engines.PENDING_EXTS),
                "office_available": probe["available_exts"],
                "engines": probe["families"],
                "libreoffice": probe["libreoffice"],
            }
        )

    @app.post("/api/upload")
    def api_upload() -> Any:
        """接收上传文件并按当前参数提交打印任务。

        参数（multipart 表单）：
            files          - 一个或多个文件
            printer        - 目标打印机名称（空表示默认打印机）
            copies         - 份数
            duplex         - simplex / vertical
            paper          - 纸张名称
            page_range     - 页码范围，如 1-3,5（空表示全部）
            scale          - fit 适应页面 / actual 实际大小 / custom 自定义
            scale_percent  - 自定义缩放百分比（10~400）
            color          - color 彩色 / mono 黑白
            quality        - draft 草稿 / normal 标准 / high 高清
            orientation    - auto 跟随文档 / portrait 纵向 / landscape 横向
            fit_table      - 表格类文件是否设为「适应一页宽」
        返回：
            JSON，{ok, tasks: [...], rejected: [{filename, reason}]}
        """
        files = _collect_files()
        if not files:
            return jsonify({"ok": False, "error": "未收到任何文件"}), 400

        form = request.form
        printer = (form.get("printer") or "").strip()
        paper = (form.get("paper") or settings.get("paper", "A4")).strip()
        duplex = "vertical" if form.get("duplex") == "vertical" else "simplex"
        copies = _safe_int(form.get("copies"), int(settings.get("copies", 1)))
        require_review = _flag(form.get("require_confirmation"), bool(settings.get("require_confirmation", False)))

        # 页面范围在提交前先校验，避免任务进入队列后才失败
        page_range = (form.get("page_range") or "").strip()
        try:
            render.parse_page_range(page_range)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        scale = (form.get("scale") or settings.get("scale", "fit")).strip()
        if scale not in ("fit", "actual", "custom"):
            scale = "fit"
        scale_percent = _safe_int(
            form.get("scale_percent"), int(settings.get("scale_percent", 100))
        )
        scale_percent = max(10, min(400, scale_percent))
        color = (
            "mono"
            if (form.get("color") or settings.get("color", "color")).lower() == "mono"
            else "color"
        )
        quality = (form.get("quality") or settings.get("quality", "normal")).strip()
        if quality not in ("draft", "normal", "high"):
            quality = "normal"
        orientation = (form.get("orientation") or settings.get("orientation", "auto")).strip()
        if orientation not in ("auto", "portrait", "landscape"):
            orientation = "auto"
        fit_table = _flag(
            form.get("fit_table"), bool(settings.get("fit_table", True))
        )

        client_ip, client_host = _client_info()
        account = auth.current_user() or {}
        created: List[Dict[str, Any]] = []
        rejected: List[Dict[str, str]] = []

        settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for storage in files:
            original = sanitize_filename(storage.filename or "")
            ext = engines.normalize_ext(Path(original).suffix)
            if not original or not ext:
                rejected.append({"filename": original or "未命名文件", "reason": "无法识别文件扩展名"})
                continue
            if ext not in engines.SUPPORTED_EXTS:
                if ext in engines.PENDING_EXTS:
                    rejected.append({"filename": original, "reason": f".{ext} 属于后续版本支持的格式"})
                else:
                    rejected.append({"filename": original, "reason": f"不支持的格式 .{ext}"})
                continue
            if converter.is_office_ext(ext) and ext not in _office_available():
                rejected.append(
                    {
                        "filename": original,
                        "reason": "本机未检测到可用的 Office / WPS / LibreOffice，无法转换该文档",
                    }
                )
                continue

            target = settings.UPLOAD_DIR / f"{uuid.uuid4().hex}.{ext}"
            try:
                storage.save(str(target))
            except OSError as exc:
                rejected.append({"filename": original, "reason": f"文件保存失败：{exc}"})
                continue

            task = task_queue.submit(
                filename=original,
                stored_path=str(target),
                ext=ext,
                size=target.stat().st_size,
                copies=copies,
                duplex=duplex,
                paper=paper,
                printer=printer,
                page_range=page_range,
                scale=scale,
                scale_percent=scale_percent,
                color=color,
                quality=quality,
                orientation=orientation,
                fit_table=fit_table,
                client_ip=client_ip,
                client_host=client_host,
                user=account.get("username", ""),
                require_review=require_review,
            )
            created.append(task.to_dict())

        return jsonify({"ok": bool(created), "tasks": created, "rejected": rejected})

    @app.get("/api/tasks")
    def api_tasks() -> Any:
        """查询打印队列快照。

        参数（查询串）：
            limit - 返回条数上限，默认 100
        返回：
            JSON，{tasks: [...]}
        """
        limit = max(1, min(500, _safe_int(request.args.get("limit"), 100)))
        tasks = task_queue.snapshot(500 if not auth.is_admin() else limit)
        if not auth.is_admin():
            account = auth.current_user() or {}
            tasks = [
                item for item in tasks if item.get("user") == account.get("username")
            ][:limit]
        return jsonify({"tasks": tasks})

    @app.post("/api/tasks/<task_id>/cancel")
    def api_cancel(task_id: str) -> Any:
        """取消任务（待确认 / 排队中立即取消；打印中为协作式中止）。

        打印中的取消只撤回尚未出纸的部分，已经出纸的页面无法收回；两种情况下
        都会删除该任务的源文件与预览缓存（取消即删除）。

        参数：
            task_id - 任务 ID（路径参数）
        返回：
            JSON，{ok, message}
        """
        task = task_queue.get(task_id)
        if task is None:
            return jsonify({"ok": False, "message": "任务不存在或已过期"}), 404
        if not _owned(task):
            return jsonify({"ok": False, "message": "无权操作其他账号的任务"}), 403
        ok, message = task_queue.cancel(task_id)
        return jsonify({"ok": ok, "message": message}), (200 if ok else 409)

    @app.post("/api/tasks/<task_id>/retry")
    def api_retry(task_id: str) -> Any:
        """重试失败或已取消的任务，以及保留了源文件的已完成任务（「重打」）。

        参数：
            task_id - 任务 ID（路径参数）
        返回：
            JSON，{ok, message}
        """
        task = task_queue.get(task_id)
        if task is None:
            return jsonify({"ok": False, "message": "任务不存在或已过期"}), 404
        if not _owned(task):
            return jsonify({"ok": False, "message": "无权操作其他账号的任务"}), 403
        ok, message = task_queue.retry(task_id)
        return jsonify({"ok": ok, "message": message}), (200 if ok else 409)

    @app.post("/api/tasks/<task_id>/print")
    def api_confirm(task_id: str) -> Any:
        """确认「待确认」状态的任务进入打印队列。

        参数：
            task_id - 任务 ID（路径参数）
        返回：
            JSON，{ok, message}
        """
        task = task_queue.get(task_id)
        if task is None:
            return jsonify({"ok": False, "message": "任务不存在或已过期"}), 404
        if not _owned(task):
            return jsonify({"ok": False, "message": "无权操作其他账号的任务"}), 403
        ok, message = task_queue.confirm(task_id)
        return jsonify({"ok": ok, "message": message}), (200 if ok else 409)

    @app.get("/api/preview/<task_id>")
    def api_preview(task_id: str) -> Any:
        """生成或读取指定任务的预览缩略图。

        预览与打印使用**同一页码范围**：只显示会被打印的那些页，缩略图标注其在原文档中的
        真实页码（例如页码范围填 `5`，就只出现「第 5 页」）。

        参数：
            task_id - 任务 ID（路径参数）
        返回：
            JSON，{ok, page_count, document_pages, pages_range, rendered, pages: [{index, url}]}
        """
        task = task_queue.get(task_id)
        if task is None:
            return jsonify({"ok": False, "error": "任务不存在或已过期"}), 404
        if not _owned(task):
            return jsonify({"ok": False, "error": "无权查看其他账号的任务"}), 403

        source = task_queue.source_of(task_id)
        if source is None:
            return jsonify({"ok": False, "error": "任务不存在或已过期"}), 404

        stored_path, ext = source
        try:
            meta = preview.build(
                task_id, stored_path, ext, str(task.get("page_range") or "")
            )
        except (RuntimeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        pages = [
            {"index": item["index"], "url": f"/api/preview/{task_id}/{item['index']}"}
            for item in meta.get("pages", [])
        ]
        return jsonify(
            {
                "ok": True,
                "page_count": meta.get("page_count", len(pages)),
                "document_pages": meta.get("document_pages", meta.get("page_count", len(pages))),
                "pages_range": meta.get("pages_range", ""),
                "rendered": meta.get("rendered", len(pages)),
                "pages": pages,
            }
        )

    @app.get("/api/preview/<task_id>/<int:page>")
    def api_preview_page(task_id: str, page: int) -> Any:
        """返回预览缩略图图片。

        参数：
            task_id - 任务 ID（路径参数）
            page    - 页码（路径参数，从 1 开始）
        返回：
            PNG 图片响应
        """
        path = preview.thumbnail_path(task_id, page)
        if path is None:
            return jsonify({"ok": False, "error": "缩略图不存在"}), 404
        return send_file(str(path), mimetype="image/png")

    @app.get("/api/logs")
    def api_logs() -> Any:
        """查询最近的打印审计日志（仅管理员）。

        参数（查询串）：
            limit - 返回条数上限，默认 50
        返回：
            JSON，{logs: [...]}
        """
        if not auth.is_admin():
            return jsonify({"ok": False, "error": "打印日志仅管理员可见"}), 403
        limit = max(1, min(200, _safe_int(request.args.get("limit"), 50)))
        return jsonify({"logs": audit.recent(limit)})

    @app.get("/api/stats")
    def api_stats() -> Any:
        """查询打印量统计（仅管理员）。

        统计口径：只计入**已完成**的任务，失败与取消不计（它们没有消耗纸张）；
        用纸张数按「单面 1 页/张、双面 2 页/张」估算。
        数据来自 data/logs/stats.json，独立于审计日志清理，长期累计。

        参数：无
        返回：
            JSON，{windows: {today/week/month/all}, first_date, last_date, note, ...}
        """
        if not auth.is_admin():
            return jsonify({"ok": False, "error": "打印统计仅管理员可见"}), 403
        return jsonify(stats.snapshot())

    @app.get("/api/sysinfo")
    def api_sysinfo() -> Any:
        """查询本机服务信息（访问地址、主机名、支持格式）。

        参数：无
        返回：
            JSON，{hostname, port, urls, supported, pending, default_printer}
        """
        info = _access_info()
        return jsonify(
            {
                "hostname": socket.gethostname(),
                "port": int(settings.get("port", 5000)),
                "mode": info["mode"],
                "current_url": info["current_url"],
                "urls": info["all_urls"],
                "supported": sorted(engines.SUPPORTED_EXTS),
                "pending": sorted(engines.PENDING_EXTS),
                "default_printer": printers.get_default_printer(),
            }
        )

    @app.get("/api/qrcode")
    def api_qrcode() -> Any:
        """生成当前访问地址的二维码图片（便于手机扫码进入）。

        参数（查询串）：
            url - 可选，指定要编码的地址（必须是本机已识别的地址）
        返回：
            PNG 图片响应
        """
        try:
            import qrcode
        except ImportError:
            return jsonify({"ok": False, "error": "未安装 qrcode 库，请执行 pip install qrcode"}), 501

        target = _qr_target()
        if not target:
            return jsonify({"ok": False, "error": "无法确定可用的访问地址"}), 400

        image = qrcode.make(target)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return send_file(buffer, mimetype="image/png")

    @app.get("/api/config")
    def api_config() -> Any:
        """查询默认打印参数。

        参数：无
        返回：
            JSON，默认打印机/份数/单双面/纸张等
        """
        config = settings.load()
        return jsonify(
            {
                "printer": config.get("printer", ""),
                "copies": config.get("copies", 1),
                "duplex": config.get("duplex", "simplex"),
                "paper": config.get("paper", "A4"),
                "require_confirmation": config.get("require_confirmation", False),
                "filter_virtual_printers": config.get("filter_virtual_printers", True),
            }
        )

    @app.errorhandler(413)
    def handle_too_large(_error: Any) -> Any:
        """处理上传体积超限。

        参数：
            _error - Flask 传入的异常对象
        返回：
            JSON 错误响应
        """
        limit = settings.get("max_upload_mb", 100)
        return jsonify({"ok": False, "error": f"文件超过上传上限（{limit} MB）"}), 413

    return app


def _collect_files() -> List[Any]:
    """从请求中收集上传文件（兼容 files / file 两种字段名）。

    参数：无
    返回：
        list，非空的 FileStorage 列表
    """
    collected: List[Any] = []
    for field in _FILE_FIELDS:
        for storage in request.files.getlist(field):
            if storage and storage.filename:
                collected.append(storage)
    return collected


def _safe_int(value: Any, default: int) -> int:
    """把表单/查询串中的值安全转换为整数。

    参数：
        value   - 原始值
        default - 转换失败时的回退值
    返回：
        int，转换结果
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _owned(task: Any) -> bool:
    """判断当前账号是否有权操作指定任务（管理员或任务属主）。

    参数：
        task - 任务字典
    返回：
        bool，有权操作返回 True
    """
    if auth.is_admin():
        return True
    if not isinstance(task, dict):
        return False
    account = auth.current_user() or {}
    return task.get("user") == account.get("username")


def _flag(value: Any, default: bool) -> bool:
    """解析布尔型表单项。

    参数：
        value   - 原始值（"1" / "true" / "on" / "yes" 视为真）
        default - 值为空时的缺省值
    返回：
        bool，解析结果
    """
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def _client_info() -> Tuple[str, str]:
    """解析客户端 IP 与计算机名。

    参数：无
    返回：
        (ip, hostname)
    """
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    ip = forwarded or request.remote_addr or ""
    return ip, resolve_client_host(ip)


def _office_available() -> List[str]:
    """获取本机当前可转换的 Office/WPS 扩展名清单。

    参数：无
    返回：
        list[str]，可用扩展名（如 ["doc","docx",...]）
    """
    return list(converter.probe_availability()["available_exts"])


def _engine_summary() -> Dict[str, str]:
    """获取各家族的转换引擎展示名称（用于网页端提示）。

    参数：无
    返回：
        dict，{word: "名称", excel: "...", powerpoint: "..."}，未检测到则不含该键
    """
    probe = converter.probe_availability()
    summary: Dict[str, str] = {}
    for family, items in probe["families"].items():
        if items:
            summary[family] = f"{items[0]['name']}（{items[0]['progid']}）"
    if probe["libreoffice"]:
        summary["libreoffice"] = "LibreOffice"
    return summary


def _access_info() -> Dict[str, Any]:
    """汇总当前访问方式信息（内网 / 穿透公网）。

    参数：无
    返回：
        dict，{mode, current_url, lan_urls, all_urls}
    """
    port = int(settings.get("port", 5000))
    lan_ips = get_all_local_ips()
    lan_urls = [f"http://{ip}:{port}" for ip in lan_ips]

    host = request.host or f"127.0.0.1:{port}"
    hostname = host.split(":")[0]
    current_url = f"{request.scheme}://{host}"
    if hostname in lan_ips or hostname in ("localhost", "127.0.0.1"):
        mode = "内网"
    else:
        mode = "穿透 / 公网"

    all_urls = [current_url] + [item for item in lan_urls if item != current_url]
    return {"mode": mode, "current_url": current_url, "lan_urls": lan_urls, "all_urls": all_urls}


def _qr_available() -> bool:
    """判断二维码功能是否可用（依赖 qrcode 库）。

    参数：无
    返回：
        bool，可用返回 True
    """
    try:
        import qrcode  # noqa: F401
    except ImportError:
        return False
    return True


def _qr_target() -> str:
    """确定二维码要编码的访问地址。

    参数：无
    返回：
        str，合法地址；若调用方传入非法地址则返回空字符串
    """
    info = _access_info()
    wanted = (request.args.get("url") or "").strip()
    if wanted:
        # 仅允许本机已识别的地址，避免被当作任意内容的二维码生成器
        return wanted if wanted in info["all_urls"] else ""
    return info["current_url"] or (info["lan_urls"][0] if info["lan_urls"] else "")
