"""打印任务模块：内存队列 + 单 Worker 串行消费 + 任务状态机。

状态机：
    review（待确认） --confirm()--> queued（排队中）
    queued（排队中） --cancel()--> canceled（已取消）
    queued          --Worker 取出--> printing（打印中） --成功--> done（已完成，同时删除源文件）
                                                        --异常/超时--> failed（失败）
                                                        --cancel()--> canceled（打印中取消）
    failed --retry()--> queued（失败保留文件，可重试）
    canceled（已删除文件，故取消后无法重试）
    done --retry()--> queued（仅当 keep_file_after_print=true 保留文件时，界面显示「重打」）

设计说明：
    · 采用单 Worker 串行执行，与打印机物理串行一致，避免多任务争抢设备；
    · **打印成功后默认立即删除上传的源文件与预览缓存**（取消同样删除）：服务端只做
      「接收 → 打印」，不在主机上留存用户打印过的文件；如需保留文件以便「重打」，
      把配置 `keep_file_after_print` 置为 true。失败的任务一律保留文件以便「重试」，
      超期后由定时清理回收。任务与队列本身保存在内存，程序重启后不保留；
    · 打印完成后对比系统打印队列条目数判断是否真正送出，超时则判定失败；
    · 「打印中取消」为协作式中止：引擎在每一页开始前检查取消标记（通常 1~2 秒内生效），
      若作业已提交到系统队列则再向队列发取消指令；**已经出纸的页面无法收回**。
"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import audit, engines, preview, printers, settings, stats
from core.utils import human_size, now_str, remove_file

STATUS_QUEUED = "queued"
STATUS_REVIEW = "review"
STATUS_PRINTING = "printing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"

# 维护线程的巡检间隔（秒）
_MAINTENANCE_INTERVAL = 300


@dataclass
class PrintTask:
    """单个打印任务的数据载体。"""

    id: str
    filename: str
    stored_path: str
    ext: str
    size: int
    copies: int
    duplex: str
    paper: str
    printer: str
    page_range: str = ""
    scale: str = "fit"
    scale_percent: int = 100
    color: str = "color"
    quality: str = "normal"
    orientation: str = "auto"
    fit_table: bool = True
    client_ip: str = ""
    client_host: str = ""
    user: str = ""
    status: str = STATUS_QUEUED
    message: str = ""
    pages: int = 0                    # 打印完成后写入的「每份页数」（与 copies 相乘才是总页数）
    job_id: Any = None                # 系统打印队列中的作业号（用于「打印中取消」）
    cancel_requested: bool = False    # 用户是否已请求取消（打印中为协作式标记）
    created_at: str = field(default_factory=now_str)
    started_at: str = ""
    finished_at: str = ""

    def options(self) -> Any:
        """构造该任务的渲染选项。

        参数：无
        返回：
            render.RenderOptions，含分辨率、页码范围、缩放、颜色、方向与表格适配设置
        """
        return engines.RenderOptions(
            dpi=engines.QUALITY_DPI.get(self.quality, engines.RENDER_DPI),
            pages=self.page_range,
            scale=self.scale,
            scale_percent=self.scale_percent,
            color=self.color,
            orientation=self.orientation,
            fit_table=self.fit_table,
        )

    def table_fit_active(self) -> bool:
        """本次是否对表格启用「适应一页宽」。

        参数：无
        返回：
            bool，仅当开关打开且文件属于表格类时为 True
        """
        return bool(self.fit_table) and engines.is_spreadsheet_ext(self.ext)

    def to_dict(self) -> Dict[str, Any]:
        """转换为可 JSON 序列化的字典。

        参数：无
        返回：
            dict，包含任务全部字段及派生展示字段
        """
        return {
            "id": self.id,
            "filename": self.filename,
            "ext": self.ext,
            "size": self.size,
            "size_text": human_size(self.size),
            "copies": self.copies,
            "duplex": self.duplex,
            "duplex_text": "双面" if self.duplex == "vertical" else "单面",
            "paper": self.paper,
            "printer": self.printer or "默认打印机",
            "page_range": self.page_range,
            "page_range_text": self.page_range or "全部页",
            "scale": self.scale,
            "scale_percent": self.scale_percent,
            "scale_text": audit.scale_text(self.scale, self.scale_percent),
            "color": self.color,
            "color_text": audit.color_text(self.color),
            "quality": self.quality,
            "quality_text": audit.quality_text(self.quality),
            "orientation": self.orientation,
            "orientation_text": audit.orientation_text(self.orientation),
            "fit_table": self.fit_table,
            "fit_table_text": audit.FIT_TABLE_TEXT if self.table_fit_active() else "",
            "params_text": audit.param_text(
                self.copies,
                self.duplex,
                self.paper,
                self.page_range,
                self.scale,
                self.scale_percent,
                self.color,
                self.quality,
                self.orientation,
                self.table_fit_active(),
            ),
            "client_ip": self.client_ip,
            "client_host": self.client_host,
            "user": self.user,
            "job_id": self.job_id,
            "cancel_requested": self.cancel_requested,
            "status": self.status,
            "status_text": audit.status_text(self.status),
            "message": self.message,
            "pages": self.pages,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _sweep_directory(directory: Path, deadline: float, keep_names: set) -> int:
    """删除目录中超过保留时间的文件与子目录。

    参数：
        directory  - 待清理目录
        deadline   - 修改时间早于该时间戳（秒）的条目会被删除
        keep_names - 需要保留的条目名称集合
    返回：
        int，删除的条目数量
    """
    if not directory.exists():
        return 0
    removed = 0
    for item in directory.iterdir():
        if item.name in keep_names:
            continue
        try:
            if item.stat().st_mtime >= deadline:
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
            removed += 1
        except OSError:
            continue
    return removed


class TaskQueue:
    """线程安全的打印任务队列。"""

    def __init__(
        self,
        timeout_seconds: Optional[int] = None,
        file_ttl_minutes: Optional[int] = None,
    ) -> None:
        """初始化队列。

        参数：
            timeout_seconds  - 等待打印完成的超时时间（秒），None 表示读取配置
            file_ttl_minutes - 上传文件保留时间（分钟），None 表示读取配置
        返回：
            None
        """
        self._tasks: Dict[str, PrintTask] = {}
        self._order: List[str] = []
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._worker: Optional[threading.Thread] = None
        self._maintainer: Optional[threading.Thread] = None
        self._running = False
        self._timeout_seconds = timeout_seconds
        self._file_ttl_minutes = file_ttl_minutes

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """启动 Worker 线程与维护线程（幂等）。

        参数：无
        返回：
            None
        """
        with self._lock:
            if self._running:
                return
            self._running = True
            self._worker = threading.Thread(target=self._worker_loop, name="print-worker", daemon=True)
            self._maintainer = threading.Thread(
                target=self._maintenance_loop, name="print-maintainer", daemon=True
            )
            self._worker.start()
            self._maintainer.start()

    def stop(self) -> None:
        """停止 Worker 线程（用于程序退出）。

        参数：无
        返回：
            None
        """
        with self._condition:
            self._running = False
            self._condition.notify_all()

    # ------------------------------------------------------------------ 对外操作

    def submit(
        self,
        *,
        filename: str,
        stored_path: str,
        ext: str,
        size: int,
        copies: int,
        duplex: str,
        paper: str,
        printer: str,
        page_range: str = "",
        scale: str = "fit",
        scale_percent: int = 100,
        color: str = "color",
        quality: str = "normal",
        orientation: str = "auto",
        fit_table: bool = True,
        client_ip: str = "",
        client_host: str = "",
        user: str = "",
        require_review: bool = False,
    ) -> PrintTask:
        """提交一个新的打印任务并入队。

        参数：
            filename       - 原始文件名
            stored_path    - 落盘后的文件绝对路径
            ext            - 文件扩展名
            size           - 文件字节数
            copies         - 份数
            duplex         - 单双面标识
            paper          - 纸张名称
            printer        - 目标打印机名称（空表示默认打印机）
            page_range     - 页码范围文本（空表示全部）
            scale          - 缩放方式（fit / actual / custom）
            scale_percent  - 自定义缩放百分比
            color          - 颜色模式（color / mono）
            quality        - 打印质量（draft / normal / high）
            orientation    - 出纸方向（auto / portrait / landscape）
            fit_table      - 表格类文件是否设为「适应一页宽」
            client_ip      - 客户端 IP
            client_host    - 客户端计算机名
            user           - 操作账号
            require_review - 为 True 时任务进入「待确认」状态，需调用 confirm() 后才打印
        返回：
            PrintTask，新建的任务对象
        """
        task = PrintTask(
            id=uuid.uuid4().hex[:16],
            filename=filename,
            stored_path=stored_path,
            ext=engines.normalize_ext(ext),
            size=int(size or 0),
            copies=max(1, int(copies or 1)),
            duplex="vertical" if duplex == "vertical" else "simplex",
            paper=paper or settings.get("paper", "A4"),
            printer=printer or "",
            page_range=str(page_range or "").strip(),
            scale=scale if scale in ("fit", "actual", "custom") else "fit",
            scale_percent=max(10, min(400, int(scale_percent or 100))),
            color="mono" if str(color).lower() == "mono" else "color",
            quality=quality if quality in ("draft", "normal", "high") else "normal",
            orientation=orientation if orientation in ("portrait", "landscape") else "auto",
            fit_table=bool(fit_table),
            client_ip=client_ip,
            client_host=client_host,
            user=user,
            status=STATUS_REVIEW if require_review else STATUS_QUEUED,
        )
        with self._condition:
            self._tasks[task.id] = task
            self._order.append(task.id)
            self._condition.notify_all()

        audit.record(
            client_ip=task.client_ip,
            client_host=task.client_host,
            filename=task.filename,
            copies=task.copies,
            duplex=task.duplex,
            paper=task.paper,
            printer=task.printer or "默认打印机",
            status=task.status,
            message="已上传，待预览确认" if require_review else "已加入打印队列",
            user=task.user,
            page_range=task.page_range,
            scale=task.scale,
            scale_percent=task.scale_percent,
            color=task.color,
            quality=task.quality,
            orientation=task.orientation,
            fit_table=task.table_fit_active(),
        )
        return task

    def snapshot(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取最近的队列快照（最新任务在前）。

        参数：
            limit - 返回条数上限
        返回：
            list[dict]，任务字典列表
        """
        with self._lock:
            ids = list(reversed(self._order))[:limit]
            return [self._tasks[task_id].to_dict() for task_id in ids if task_id in self._tasks]

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取单个任务详情。

        参数：
            task_id - 任务 ID
        返回：
            dict 或 None，任务字典
        """
        with self._lock:
            task = self._tasks.get(task_id)
            return task.to_dict() if task else None

    def source_of(self, task_id: str) -> Optional[Tuple[str, str]]:
        """获取任务的源文件路径与扩展名（供预览等内部功能使用，不对外暴露）。

        参数：
            task_id - 任务 ID
        返回：
            (stored_path, ext) 或 None
        """
        with self._lock:
            task = self._tasks.get(task_id)
            return (task.stored_path, task.ext) if task else None

    def cancel(self, task_id: str) -> Tuple[bool, str]:
        """取消任务（待确认 / 排队中立即取消；打印中为协作式中止）。

        说明：
            · 待确认、排队中：立即置为「已取消」，并**删除**源文件与预览缓存（取消即删除）；
            · 打印中：置取消标记，Worker 会在下一页检查点中止；若作业已提交到系统打印队列，
              再向队列发取消指令撤回尚未出纸的部分。**已经出纸的页面无法收回**。

        参数：
            task_id - 任务 ID
        返回：
            (是否已受理, 说明文本)
        """
        discard = False
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False, "任务不存在"
            if task.status == STATUS_PRINTING:
                if task.cancel_requested:
                    return True, "已在取消中，请稍候"
                task.cancel_requested = True
                task.message = "正在停止打印…（已出纸的页面无法收回）"
                # 打印中的收尾（队列撤回、状态与审计）由 Worker 负责
                return True, "已请求取消，正在停止打印（已出纸的页面无法收回）"
            if task.status not in (STATUS_QUEUED, STATUS_REVIEW):
                return False, "该任务当前状态不支持取消"
            task.status = STATUS_CANCELED
            task.message = "用户已取消（文件已删除）"
            task.finished_at = now_str()
            discard = True

        if discard:
            self._discard_task_files(task)

        audit.record(
            client_ip=task.client_ip,
            client_host=task.client_host,
            filename=task.filename,
            copies=task.copies,
            duplex=task.duplex,
            paper=task.paper,
            printer=task.printer or "默认打印机",
            status=STATUS_CANCELED,
            message=task.message,
            user=task.user,
            page_range=task.page_range,
            scale=task.scale,
            scale_percent=task.scale_percent,
            color=task.color,
            quality=task.quality,
            orientation=task.orientation,
            fit_table=task.table_fit_active(),
        )
        return True, "已取消"

    def confirm(self, task_id: str) -> Tuple[bool, str]:
        """确认「待确认」状态的任务进入打印队列。

        参数：
            task_id - 任务 ID
        返回：
            (是否成功, 说明文本)
        """
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                return False, "任务不存在"
            if task.status != STATUS_REVIEW:
                return False, "该任务当前无需确认"
            task.status = STATUS_QUEUED
            task.message = "已确认，等待打印"
            self._condition.notify_all()

        audit.record(
            client_ip=task.client_ip,
            client_host=task.client_host,
            filename=task.filename,
            copies=task.copies,
            duplex=task.duplex,
            paper=task.paper,
            printer=task.printer or "默认打印机",
            status=STATUS_QUEUED,
            message="用户预览确认后提交打印",
            user=task.user,
            page_range=task.page_range,
            scale=task.scale,
            scale_percent=task.scale_percent,
            color=task.color,
            quality=task.quality,
            orientation=task.orientation,
            fit_table=task.table_fit_active(),
        )
        return True, "已提交打印"

    def retry(self, task_id: str) -> Tuple[bool, str]:
        """重新排队一个失败、已取消或已完成的任务。

        说明：已完成的任务只有在**源文件仍存在**时才能重打 —— 默认配置下打印成功会删除文件，
        此时会返回「原始文件已被清理」；把配置 `keep_file_after_print` 置为 true 保留文件后，
        界面会对已完成任务显示「重打」。

        参数：
            task_id - 任务 ID
        返回：
            (是否成功, 说明文本)
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False, "任务不存在"
            if task.status not in (STATUS_FAILED, STATUS_CANCELED, STATUS_DONE):
                return False, "仅失败、已取消或已完成的任务可以重试"
            if not Path(task.stored_path).exists():
                return False, "原始文件已被清理，无法重试"
            task.status = STATUS_QUEUED
            task.message = "重新排队"
            task.started_at = ""
            task.finished_at = ""
            task.pages = 0
            # 清理上一轮的运行痕迹，避免重打被上一轮的取消标记或作业号影响
            task.job_id = None
            task.cancel_requested = False
            self._condition.notify_all()
        return True, "已重新排队"

    def healthy(self) -> bool:
        """判断 Worker 线程是否存活（供健康检测使用）。

        参数：无
        返回：
            bool，Worker 正常存活返回 True
        """
        return bool(self._running and self._worker and self._worker.is_alive())

    def restart_worker(self) -> bool:
        """重建已退出的 Worker 线程（健康检测自动恢复）。

        参数：无
        返回：
            bool，本次确实执行了重启则返回 True
        """
        with self._lock:
            if not self._running:
                return False
            if self._worker is not None and self._worker.is_alive():
                return False
            self._worker = threading.Thread(
                target=self._worker_loop, name="print-worker", daemon=True
            )
            self._worker.start()
            return True

    def cleanup_files(self, ttl_minutes: Optional[int] = None) -> int:
        """清理超过保留时间的上传文件与转换产物（正在排队/打印的文件会被跳过）。

        另外：配置 `keep_file_after_print` 为真时，**已完成任务**的源文件与预览缓存也跳过
        清理——否则「重打」会在保留时间到期后失效；把该开关改回 false 后，这些文件会
        在后续巡检中被正常回收。

        参数：
            ttl_minutes - 保留时间（分钟），None 表示读取配置
        返回：
            int，删除的文件数量
        """
        minutes = ttl_minutes if ttl_minutes is not None else self._file_ttl_minutes
        if minutes is None:
            minutes = int(settings.get("file_ttl_minutes", 30))

        keep_printed = bool(settings.get("keep_file_after_print", False))
        active = (STATUS_QUEUED, STATUS_PRINTING, STATUS_REVIEW)
        with self._lock:
            protected = [
                task
                for task in self._tasks.values()
                if task.status in active or (keep_printed and task.status == STATUS_DONE)
            ]
            protected_files = {Path(task.stored_path).name for task in protected}
            protected_ids = {task.id for task in protected}

        deadline = time.time() - minutes * 60
        removed = _sweep_directory(settings.UPLOAD_DIR, deadline, protected_files)
        removed += _sweep_directory(settings.CONVERTED_DIR, deadline, set())
        removed += _sweep_directory(settings.PREVIEW_DIR, deadline, protected_ids)
        return removed

    # ------------------------------------------------------------------ 内部实现

    def _next_queued(self) -> Optional[PrintTask]:
        """取出下一个待打印任务（FIFO）。

        参数：无
        返回：
            PrintTask 或 None
        """
        for task_id in self._order:
            task = self._tasks.get(task_id)
            if task is not None and task.status == STATUS_QUEUED:
                return task
        return None

    def _worker_loop(self) -> None:
        """Worker 主循环：串行取出并执行打印任务。

        参数：无
        返回：
            None
        """
        while True:
            with self._condition:
                if not self._running:
                    return
                task = self._next_queued()
                if task is None:
                    self._condition.wait(_MAINTENANCE_INTERVAL)
                    continue
                task.status = STATUS_PRINTING
                task.started_at = now_str()
                task.message = "正在发送到打印机"
            self._execute(task)

    def _execute(self, task: PrintTask) -> None:
        """执行单个打印任务并更新状态与审计日志。

        支持协作式中止：把「是否已请求取消」传给引擎，引擎在每一页开始前检查一次；
        若作业已提交到系统打印队列后才取消，则再向队列发取消指令撤回未出纸的部分。

        参数：
            task - 待执行任务
        返回：
            None
        """
        target = task.printer or printers.get_default_printer()
        baseline = printers.count_jobs(target) if target else -1
        pending = baseline

        def canceled() -> bool:
            """用户是否已请求取消本任务（供引擎逐页检查）。

            参数：无
            返回：
                bool，已请求取消返回 True
            """
            return task.cancel_requested

        try:
            result = engines.print_file(
                path=task.stored_path,
                ext=task.ext,
                printer=task.printer,
                paper=task.paper,
                duplex=task.duplex,
                copies=task.copies,
                title=task.filename,
                options=task.options(),
                should_abort=canceled,
            )
            task.pages = int(result.get("pages", 0))
            task.job_id = result.get("job_id")
            target = result.get("printer") or target

            if task.cancel_requested:
                task.status = STATUS_CANCELED
                task.message = self._abort_spool_job(task, target)
            else:
                drained, pending = self._wait_spool_drain(target, baseline, canceled)
                if task.cancel_requested:
                    task.status = STATUS_CANCELED
                    task.message = self._abort_spool_job(task, target)
                elif drained:
                    task.status = STATUS_DONE
                    # pages 为「每份页数」，份数由 copies 单独表达；多份时一并写出，避免误读
                    if task.copies > 1:
                        task.message = (
                            f"已送出 {task.pages} 页 × {task.copies} 份，等待打印机输出完成"
                        )
                    else:
                        task.message = f"已送出 {task.pages} 页，等待打印机输出完成"
                else:
                    task.status = STATUS_FAILED
                    state = printers.status_of(target)
                    task.message = (
                        f"打印超时：系统打印队列仍有 {pending} 个未完成作业"
                        f"（提交前 {baseline} 个，打印机状态：{state}），"
                        "请检查打印机电源、网络与纸张后重试"
                    )
        except engines.PrintCanceled:
            # 用户在渲染/绘制阶段取消：引擎已回滚（未提交的作业不会出纸）
            task.status = STATUS_CANCELED
            task.message = "用户已取消打印（已出纸的页面无法收回）"
        except Exception as exc:  # 打印链路上的任何异常都转为失败状态，避免 Worker 退出
            task.status = STATUS_FAILED
            task.message = str(exc) or exc.__class__.__name__
        finally:
            task.finished_at = now_str()
            if task.status == STATUS_DONE:
                # 打印量统计：只累计成功的任务（失败与取消没有消耗纸张）
                stats.record(
                    copies=task.copies,
                    pages=task.pages,
                    paper=task.paper,
                    duplex=task.duplex,
                )
                if settings.get("keep_file_after_print", False):
                    # 配置要求保留源文件：不删除，界面上可对已完成任务「重打」
                    task.message = task.message + "（源文件已保留，可重打）"
                else:
                    # 打印成功即删除：服务端不再保留已打印完的源文件与预览缓存（需要时重新上传）
                    self._discard_task_files(task)
                    task.message = task.message + "（源文件已删除）"
            elif task.status == STATUS_CANCELED:
                # 取消即删除：不再保留源文件与预览缓存
                self._discard_task_files(task)

        audit.record(
            client_ip=task.client_ip,
            client_host=task.client_host,
            filename=task.filename,
            copies=task.copies,
            duplex=task.duplex,
            paper=task.paper,
            printer=target or "默认打印机",
            status=task.status,
            message=task.message,
            user=task.user,
            page_range=task.page_range,
            scale=task.scale,
            scale_percent=task.scale_percent,
            color=task.color,
            quality=task.quality,
            orientation=task.orientation,
            fit_table=task.table_fit_active(),
            pages=task.pages,
        )

    def _abort_spool_job(self, task: PrintTask, printer: str) -> str:
        """向系统打印队列发出取消指令，返回面向用户的任务说明。

        参数：
            task    - 打印任务（需已写入 job_id）
            printer - 打印机名称
        返回：
            str，任务说明文本（含「已出纸无法收回」的提示）
        """
        if not task.job_id:
            return "用户已取消：作业已提交但未能取得作业号，无法撤回队列作业（已出纸的页面无法收回）"
        ok, message = printers.cancel_job(printer, task.job_id)
        if ok:
            return "用户已取消：已撤回打印队列中尚未出纸的部分（已出纸的页面无法收回）"
        return f"用户已取消：打印队列撤回失败（{message}，已出纸的页面无法收回）"

    def _discard_task_files(self, task: PrintTask) -> None:
        """删除任务的源文件与预览缓存（打印成功或取消后不再保留）。

        取消（canceled）一定会走到这里；打印成功（done）则在 `keep_file_after_print`
        为假（默认）时走到这里——服务端只做「接收 → 打印」，不长期保存用户文件。
        失败的任务保留文件以便「重试」，超期后由定时清理回收。

        参数：
            task - 已结束的任务
        返回：
            None
        """
        try:
            remove_file(Path(task.stored_path))
        except OSError:
            pass
        try:
            preview.remove(task.id)
        except OSError:
            pass

    def _wait_spool_drain(
        self,
        printer: str,
        baseline: int,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> Tuple[bool, int]:
        """等待系统打印队列中的未完成作业回落到提交前的水平。

        参数：
            printer      - 打印机名称
            baseline     - 提交前的未完成作业数（-1 表示无法查询）
            should_abort - 可选回调，返回 True 表示用户已请求取消，立即结束等待
        返回：
            (是否已回落, 最后一次观测到的未完成作业数)
            无法查询队列时直接返回 (True, baseline)，不影响任务判定为成功
        """
        if not printer or baseline < 0:
            return True, baseline

        timeout = self._timeout_seconds
        if timeout is None:
            timeout = int(settings.get("task_timeout_seconds", 180))

        current = baseline
        deadline = time.time() + max(10, timeout)
        while time.time() < deadline:
            if should_abort is not None and should_abort():
                # 用户已请求取消：立刻结束等待，由调用方处理队列撤回与状态收尾
                return False, current
            current = printers.count_jobs(printer)
            if current < 0 or current <= baseline:
                return True, current if current >= 0 else baseline
            time.sleep(1.0)
        return False, current

    def _maintenance_loop(self) -> None:
        """维护线程：定时清理过期上传文件与超期日志。

        参数：无
        返回：
            None
        """
        while True:
            with self._lock:
                if not self._running:
                    return
            try:
                self.cleanup_files()
                audit.cleanup(int(settings.get("log_keep_days", 7)))
            except Exception:
                # 维护任务异常不应影响打印主链路
                pass
            with self._condition:
                if not self._running:
                    return
                self._condition.wait(_MAINTENANCE_INTERVAL)
