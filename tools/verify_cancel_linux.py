#!/usr/bin/env python3
"""麒麟 / Linux 侧「打印中取消」实机验证（不消耗纸张）。

思路与 Windows 侧的验证完全一致：先把 CUPS 队列**暂停**（`cupsdisable`），
暂停期间 CUPS 不会把作业交给打印机，因此可以安全地验证「提交 → 撤回」整条链路。

验证内容：
    ① 真实提交：用本程序真实的渲染管线（`engines.print_file`）合成并提交一份 1 页 PDF，
       确认作业出现在 CUPS 队列中，并取得作业号；
    ② 真实撤回：调用 `printers.cancel_job`，轮询确认作业已从队列消失；
    ③ 协作式中止：以 `should_abort` 恒为真的方式再跑一次，确认**不会提交作业**
       且抛出 `render.PrintCanceled`；
    ④ 命令级检查：`cancel_args` 生成的命令行符合 CUPS 约定。

安全设计：
    · 全程在「暂停队列」下进行，打印机不会收到数据；
    · 恢复打印前**必须确认队列为空**，否则清掉本次测试作业、仍不确定时保持暂停并提示人工确认；
    · 任何异常都会在 finally 中尝试恢复打印机。

用法（在麒麟机器上、与程序同一环境执行）：
    python3 tools/verify_cancel_linux.py                # 用默认打印机
    python3 tools/verify_cancel_linux.py --printer HP_LaserJet_M1005
退出码：0 全部通过；1 有失败项；2 环境不具备（缺少 CUPS 命令 / 没有打印机）
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import engines, printers, render  # noqa: E402
from core.utils import remove_file  # noqa: E402


def make_sample_pdf(path: Path) -> None:
    """生成一份 1 页测试 PDF。

    参数：
        path - 输出文件路径
    返回：
        None
    异常：
        RuntimeError - PyMuPDF 不可用或写文件失败
    """
    pymupdf = render.import_pymupdf()
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 120), "jyprinter 取消验证", fontsize=24)
    page.insert_text((72, 160), "本页仅用于验证取消功能，不会真的打印", fontsize=12)
    document.save(str(path))
    document.close()


def pause_queue(printer: str) -> bool:
    """暂停打印队列（cupsdisable）。

    参数：
        printer - 打印机名称
    返回：
        bool，暂停命令是否执行成功
    """
    try:
        printers.run_command(["cupsdisable", printer])
        return True
    except Exception as exc:  # noqa: BLE001 - 任何失败都按「未能暂停」处理
        print("  ！暂停队列失败：%s" % exc)
        print("    （需要 root 或 lpadmin 权限；若已暂停可忽略）")
        return False


def resume_queue(printer: str) -> bool:
    """恢复打印队列（cupsenable）。

    参数：
        printer - 打印机名称
    返回：
        bool，恢复命令是否执行成功
    """
    try:
        printers.run_command(["cupsenable", printer])
        return True
    except Exception as exc:  # noqa: BLE001
        print("  ！恢复队列失败，请手动执行：sudo cupsenable %s（%s）" % (printer, exc))
        return False


def wait_job_absent(printer: str, job_id: str, timeout: int = 15) -> bool:
    """轮询等待作业从队列中消失。

    参数：
        printer - 打印机名称
        job_id  - 作业号
        timeout - 最长等待秒数
    返回：
        bool，作业已消失返回 True
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job_id not in set(printers.list_job_ids(printer)):
            return True
        time.sleep(1.0)
    return False


def main() -> int:
    """脚本入口：按顺序执行四项验证并输出结论。

    参数：无（命令行参数见 --help）
    返回：
        int，退出码（0 通过 / 1 失败 / 2 环境不具备）
    """
    parser = argparse.ArgumentParser(description="麒麟侧「打印中取消」实机验证（不耗纸）")
    parser.add_argument("--printer", default="", help="目标打印机名称（默认使用系统默认打印机）")
    args = parser.parse_args()

    print("==" * 32)
    print("麒麟 / Linux 侧「打印中取消」验证（队列暂停，不会出纸）")
    print("==" * 32)

    if not hasattr(printers, "cups_available") or not printers.cups_available():
        print("本脚本用于麒麟 / Linux：未找到 CUPS 命令（lp / lpstat / cancel）")
        print("请先安装：sudo apt install cups-client（或 sudo yum install cups-client）")
        return 2

    printer = args.printer or printers.get_default_printer()
    if not printer:
        print("系统里没有可用打印机，请先在「设置 → 打印机」中添加并设为默认")
        return 2
    print("目标打印机：%s" % printer)

    failures = []

    # ④ 命令级检查（不依赖打印机的纯逻辑）
    expected = ["cancel", "Queue-7"]
    actual = printers.cancel_args("Queue-7")
    if actual == expected:
        print("[通过] cancel 命令参数：%s" % " ".join(actual))
    else:
        print("[失败] cancel 命令参数：期望 %s，实际 %s" % (expected, actual))
        failures.append("cancel_args")

    sample = Path(tempfile.gettempdir()) / "jyprinter_cancel_probe.pdf"
    paused = pause_queue(printer)
    job_id = ""
    try:
        make_sample_pdf(sample)
        if not paused:
            print("提示：队列未成功暂停，本脚本将只做「协作式中止」验证，避免真的出纸")
        else:
            # ① 真实提交 + ② 真实撤回
            print("\n-- ① 真实提交（队列已暂停）--")
            result = engines.print_file(
                path=str(sample),
                ext="pdf",
                printer=printer,
                paper="A4",
                duplex="simplex",
                copies=1,
                title="jyprinter 取消验证",
            )
            job_id = str(result.get("job_id") or "")
            print("  合成页数：%s ｜ 提交作业号：%s" % (result.get("pages"), job_id or "（未取到）"))
            in_queue = job_id and job_id in set(printers.list_job_ids(printer))
            print("  队列中可见：%s（当前队列：%s）" % (in_queue, printers.list_job_ids(printer)))
            if not in_queue:
                print("[失败] 提交后未在队列中看到该作业，无法完成撤回验证")
                failures.append("submit")
            else:
                print("[通过] 作业已进入 CUPS 队列")
                print("\n-- ② 真实撤回（printers.cancel_job）--")
                ok, message = printers.cancel_job(printer, job_id)
                gone = wait_job_absent(printer, job_id)
                print("  撤回返回：%s ｜ %s" % (ok, message))
                print("  作业已从队列消失：%s（当前队列：%s）" % (gone, printers.list_job_ids(printer)))
                if ok and gone:
                    print("[通过] 打印队列撤回生效")
                else:
                    print("[失败] 打印队列撤回未生效")
                    failures.append("cancel")

        # ③ 协作式中止：应完全不提交作业
        print("\n-- ③ 协作式中止（渲染期取消，不应提交作业）--")
        before = printers.list_job_ids(printer)
        try:
            engines.print_file(
                path=str(sample),
                ext="pdf",
                printer=printer,
                paper="A4",
                duplex="simplex",
                copies=1,
                title="jyprinter 协作式中止验证",
                should_abort=lambda: True,
            )
            print("[失败] 未抛出 PrintCanceled")
            failures.append("abort")
        except render.PrintCanceled as exc:
            after = printers.list_job_ids(printer)
            print("  捕获 PrintCanceled：%s" % exc)
            print("  提交前后队列：%s -> %s" % (before, after))
            if after == before:
                print("[通过] 未提交任何作业")
            else:
                print("[失败] 队列出现新增作业")
                failures.append("abort-queue")
    finally:
        remove_file(sample)
        # 恢复打印前确认队列为空，避免残留作业在恢复后被打出
        remaining = printers.list_job_ids(printer)
        if remaining:
            print("\n队列仍有作业（%s），先撤回本次测试作业" % remaining)
            if job_id:
                printers.cancel_job(printer, job_id)
                time.sleep(1.0)
            remaining = printers.list_job_ids(printer)
        if remaining:
            print("！队列仍非空，保持暂停、不恢复打印机，请人工确认：%s" % remaining)
        elif paused:
            resume_queue(printer)
            print("队列已空，打印机已恢复：%s ｜ 剩余作业：%s"
                  % (printers.status_of(printer), printers.list_job_ids(printer)))

    print("\n" + "==" * 32)
    if failures:
        print("验证结束：存在失败项 %s" % failures)
        return 1
    print("验证结束：全部通过（全程未出纸）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
