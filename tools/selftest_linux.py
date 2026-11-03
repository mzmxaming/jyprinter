"""命令输出解析与打印选项自检（离线单测 + 实机校验）。

用途：
    1. 离线：用固定样例数据校验三部分纯函数——
       · core/printers_linux.py 与 core/system_linux.py 中的「命令输出 → 结构化数据」函数，
         它们最容易因发行版差异而失效；
       · core/render.py 中的页码范围解析、缩放比例换算、页面过滤与黑白转换；
       · core/formats.py 的扩展名归一化。
       可在任意平台（含开发机 Windows）执行，属于发布前必跑项；
    2. 实机（--live）：用目标机真实的 lpstat / lpoptions / ip / nmcli 输出再跑一遍，
       确认解析逻辑与目标机的 CUPS 版本输出格式相符。

用法：
    python3 tools/selftest_linux.py            # 离线样例测试（任意平台）
    python3 tools/selftest_linux.py --live     # 追加实机校验（需在目标 Linux 上执行）

返回码：0 全部通过；1 存在失败项。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from core import printers_linux as pl  # noqa: E402
from core import render  # noqa: E402
from core import split  # noqa: E402
from core import stats  # noqa: E402
from core import system_linux as sl  # noqa: E402
from core.formats import normalize_ext, is_supported, is_office_ext, is_spreadsheet_ext  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, actual, expected) -> None:
    """断言实际值与期望值一致，并累计结果。

    参数：
        name     - 用例名称
        actual   - 实际值
        expected - 期望值
    返回：
        None
    """
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        print(f"  [通过] {name}")
    else:
        FAIL += 1
        print(f"  [失败] {name}\n         期望：{expected!r}\n         实际：{actual!r}")


def check_true(name: str, condition: bool) -> None:
    """断言条件为真。

    参数：
        name      - 用例名称
        condition - 判断结果
    返回：
        None
    """
    check(name, bool(condition), True)


# --------------------------------------------------------------------------- 样例数据
# 覆盖常见 CUPS 输出形态：就绪 / 打印中 / 停用、ipp 与 dnssd 设备、队列作业等
SAMPLE_LPSTAT_D = "system default destination: HP_LaserJet_M1005\n"
SAMPLE_LPSTAT_D_EMPTY = "no system default destination\n"
SAMPLE_LPSTAT_P = (
    "printer HP_LaserJet_M1005 is idle.  enabled since Wed 23 Sep 2026 10:12:03 AM CST\n"
    "printer Lenovo_M7675DXF now printing Lenovo_M7675DXF-42.  enabled since "
    "Wed 23 Sep 2026 10:00:00 AM CST\n"
    "printer Brother_MFC-7895DW disabled since Wed 23 Sep 2026 09:00:00 AM CST -\n"
    "\treason unknown\n"
)
SAMPLE_LPSTAT_V = (
    "device for Brother_MFC-7895DW: dnssd://Brother%20MFC-7895DW._ipp._tcp.local/?uuid=1a2b\n"
    "device for HP_LaserJet_M1005: ipp://192.168.1.10/ipp/print\n"
    "device for Lenovo_M7675DXF: usb://Lenovo/M7675DXF?serial=ABCDEF\n"
)
SAMPLE_LPSTAT_O = (
    "Lenovo_M7675DXF-42 cgm 204800 Wed 23 Sep 2026 10:20:00 AM CST\n"
    "HP_LaserJet_M1005-7 zhangsan 1024 Wed 23 Sep 2026 10:21:00 AM CST\n"
)
SAMPLE_LPOPTIONS = (
    "PageSize/Page Size: A4 *Letter Legal A3\n"
    "InputSlot/Input Slot: *Auto Tray1\n"
    "sides/2-Sided Printing: *one-sided two-sided-long-edge two-sided-short-edge\n"
    "Resolution/Output Resolution: 300dpi *600dpi\n"
)
SAMPLE_LPOPTIONS_SIMPLEX = "sides/2-Sided Printing: *one-sided\n"
# 实测自银河麒麟 + 联想 LJ2200：PPD 只上报占位纸张名，双面用的是 PPD 关键字而非 IPP 关键字
SAMPLE_LPOPTIONS_PLACEHOLDER = (
    "PageSize/Media Size: *CustomPage0 CustomPage1 CustomPage2 CustomPage3 CustomPage4"
    " CustomPage5 CustomPage6 CustomPage7 CustomPage8 CustomPage9 CustomPage10"
    " CustomPage11 CustomPage12 CustomPage13 CustomPage14 CustomPage15 CustomPage16"
    " CustomPage17 CustomPage18 CustomPage19 CustomPage20 CustomPage21\n"
    "Resolution/Resolution: *1200dpi 600dpi 300dpi\n"
    "InputSlot/Media Source: *CustomSlot0 CustomSlot1 CustomSlot2\n"
    "Duplex/2-Sided Printing: *None DuplexNoTumble DuplexTumble\n"
)
# 实测自银河麒麟（中文语言环境）的 lpstat 输出
SAMPLE_LPSTAT_D_ZH = "系统默认目标：Lenovo-LJ2200\n"
SAMPLE_LPSTAT_P_ZH = "打印机 Lenovo-LJ2200 目前空闲。从 2026年09月23日 星期三 15时43分05秒 开始启用\n"
SAMPLE_LPSTAT_V_ZH = "用于 Lenovo-LJ2200 的设备：usb://Lenovo/LJ2200?serial=P01915787\n"
SAMPLE_LP_JOB = "request id is HP_LaserJet_M1005-43 (1 file(s))\n"
# 实测自银河麒麟（中文语言环境）的真实输出：CUPS 的 lp 回执被本地化
SAMPLE_LP_JOB_ZH = "请求 ID 为 Lenovo-LJ2200-2（1 个文件）\n"
SAMPLE_IP_ADDR = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
    "2: ens33    inet 192.168.1.10/24 brd 192.168.1.255 scope global ens33\\       "
    "valid_lft forever preferred_lft forever\n"
    "3: wlan0    inet 10.0.0.6/16 brd 10.0.255.255 scope global wlan0\n"
)
SAMPLE_NMCLI = "有线连接 1:ens33\nWired connection 2:eth0\n"


def run_offline() -> None:
    """执行离线样例测试。

    参数：无
    返回：
        None
    """
    print("== 离线样例测试：lpstat -d（默认打印机） ==")
    check("英文输出", pl.parse_lpstat_d(SAMPLE_LPSTAT_D), "HP_LaserJet_M1005")
    check("未设置默认打印机", pl.parse_lpstat_d(SAMPLE_LPSTAT_D_EMPTY), "")
    check("中文输出", pl.parse_lpstat_d("默认目标：Lenovo_M7675DXF"), "Lenovo_M7675DXF")

    print("== 离线样例测试：lpstat -p（打印机与状态） ==")
    printers = pl.parse_lpstat_p(SAMPLE_LPSTAT_P)
    check("解析出 3 台打印机", len(printers), 3)
    check("就绪打印机状态", printers[0]["status"], "就绪")
    check_true("就绪打印机为启用", printers[0]["enabled"])
    check("打印中打印机状态", printers[1]["status"], "打印中")
    check_true("打印中打印机为启用", printers[1]["enabled"])
    check("停用打印机状态", printers[2]["status"], "已停用")
    check_true("停用打印机为禁用", not printers[2]["enabled"])
    check("无 printer 前缀的行被忽略", pl.parse_lpstat_p("\treason unknown\n"), [])

    print("== 离线样例测试：lpstat -v（设备 URI） ==")
    devices = pl.parse_lpstat_v(SAMPLE_LPSTAT_V)
    check("解析出 3 台设备", len(devices), 3)
    check(
        "ipp 设备",
        devices.get("HP_LaserJet_M1005"),
        "ipp://192.168.1.10/ipp/print",
    )
    check_true(
        "dnssd 设备带 uuid 参数",
        devices.get("Brother_MFC-7895DW", "").startswith("dnssd://"),
    )

    print("== 离线样例测试：lpstat -o（队列作业） ==")
    jobs = pl.parse_lpstat_o(SAMPLE_LPSTAT_O)
    check("解析出 2 个作业", len(jobs), 2)
    check("作业队列名", jobs[0]["printer"], "Lenovo_M7675DXF")
    check("作业号（含队列名连字符）", jobs[0]["job_id"], "Lenovo_M7675DXF-42")
    check("作业用户", jobs[0]["user"], "cgm")
    check("按打印机过滤", len(pl.parse_lpstat_o(SAMPLE_LPSTAT_O, "HP_LaserJet_M1005")), 1)
    check("过滤无匹配", pl.parse_lpstat_o(SAMPLE_LPSTAT_O, "NoSuchPrinter"), [])
    check("忽略非作业行", pl.parse_lpstat_o("scheduler is running\n"), [])

    print("== 离线样例测试：lpoptions -l（驱动能力） ==")
    options = pl.parse_lpoptions_l(SAMPLE_LPOPTIONS)
    check("选项数量", len(options), 4)
    check("PageSize 选项列表", options["PageSize"]["choices"], ["A4", "Letter", "Legal", "A3"])
    check("PageSize 默认值（* 标记）", options["PageSize"]["default"], "Letter")
    check("选项描述", options["sides"]["description"], "2-Sided Printing")
    check_true("识别双面能力", pl.duplex_supported(options))
    check_true("单面驱动判定为不支持双面", not pl.duplex_supported(pl.parse_lpoptions_l(SAMPLE_LPOPTIONS_SIMPLEX)))
    check("纸张名称取自 PageSize", pl.paper_names_from_options(options)[:2], ["A4", "Letter"])
    check("纸张名称优先取 media", pl.paper_names_from_options({"media": {"choices": ["A4"], "default": "A4"}}), ["A4"])
    check("无选项时纸张列表为空", pl.paper_names_from_options({}), [])

    print("== 离线样例测试：lp 提交回执与纸张尺寸 ==")
    check("作业号解析（英文）", pl.parse_lp_job_id(SAMPLE_LP_JOB), "HP_LaserJet_M1005-43")
    check("作业号解析（中文回执，麒麟实测样例）", pl.parse_lp_job_id(SAMPLE_LP_JOB_ZH), "Lenovo-LJ2200-2")
    check("作业号解析（队列名含连字符）", pl.parse_lp_job_id("request id is HP-4100-7 (1 file(s))"), "HP-4100-7")
    check("空回执解析为空", pl.parse_lp_job_id(""), "")
    check("无作业号回执解析为空", pl.parse_lp_job_id("lp: Error - unable to access file\n"), "")
    env = pl.command_env()
    check("CUPS 子进程强制 C 语言环境（LC_ALL）", env.get("LC_ALL"), "C")
    check("CUPS 子进程强制 C 语言环境（LANGUAGE，gettext 优先级更高）", env.get("LANGUAGE"), "C")
    check("A4 尺寸（点）", pl.paper_size_points("A4"), (595.28, 841.89))
    check("未知纸张回退 A4", pl.paper_size_points("X9"), pl.PAPER_SIZES["A4"])
    check_true("虚拟打印机识别（cups-pdf）", pl.is_virtual("cups-pdf"))
    check_true("物理打印机不误判", not pl.is_virtual("HP_LaserJet_M1005"))

    print("== 离线样例测试：网络信息解析 ==")
    adapters = sl.parse_ip_addr(SAMPLE_IP_ADDR)
    check("解析出 2 块网卡（lo 已过滤）", len(adapters), 2)
    check("网卡名", adapters[0]["name"], "ens33")
    check("IPv4 地址", adapters[0]["ip"], "192.168.1.10")
    check("CIDR", adapters[0]["cidr"], "192.168.1.10/24")
    check("nmcli 设备映射", sl.parse_nmcli_devices(SAMPLE_NMCLI), {"ens33": "有线连接 1", "eth0": "Wired connection 2"})
    check("掩码转前缀（点分）", sl._prefix_from_mask("255.255.255.0"), "24")
    check("掩码转前缀（已是数字）", sl._prefix_from_mask("16"), "16")
    check("掩码非法时回退 24", sl._prefix_from_mask("bad"), "24")
    check("空输入安全", sl.parse_ip_addr(""), [])

    print("== 离线样例测试：中文环境输出（麒麟实测样例） ==")
    check("lpstat -d 中文输出", pl.parse_lpstat_d(SAMPLE_LPSTAT_D_ZH), "Lenovo-LJ2200")
    zh_printers = pl.parse_lpstat_p(SAMPLE_LPSTAT_P_ZH)
    check("lpstat -p 中文输出解析出 1 台", len(zh_printers), 1)
    check("lpstat -p 中文输出的队列名", zh_printers[0]["name"] if zh_printers else "", "Lenovo-LJ2200")
    check("lpstat -p 中文输出的状态", zh_printers[0]["status"] if zh_printers else "", "就绪")
    check_true("lpstat -p 中文输出判定为启用", bool(zh_printers and zh_printers[0]["enabled"]))
    check(
        "lpstat -v 中文输出（用于 X 的设备：URI）",
        pl.parse_lpstat_v(SAMPLE_LPSTAT_V_ZH),
        {"Lenovo-LJ2200": "usb://Lenovo/LJ2200?serial=P01915787"},
    )

    print("== 离线样例测试：占位纸张名与双面关键字（麒麟实测样例） ==")
    placeholder = pl.parse_lpoptions_l(SAMPLE_LPOPTIONS_PLACEHOLDER)
    names = pl.paper_names_from_options(placeholder)
    check("占位纸张名解析出 22 项", len(names), 22)
    check("占位纸张名首项", names[0] if names else "", "CustomPage0")
    check_true("占位纸张名判定为不可用（改用标准纸张表）", not pl.paper_names_usable(names))
    check_true("正常纸张名判定为可用", pl.paper_names_usable(["A4", "Letter"]))
    check_true("空列表判定为不可用", not pl.paper_names_usable([]))
    check_true(
        "PPD 双面关键字 DuplexNoTumble 被识别为支持双面",
        pl.duplex_supported(placeholder),
    )
    check(
        "双面参数使用 PPD 关键字",
        pl.duplex_args_from_options(placeholder, "vertical"),
        ["-o", "Duplex=DuplexNoTumble"],
    )
    check(
        "单面参数同样使用 PPD 关键字（不依赖 CUPS 选项名映射）",
        pl.duplex_args_from_options(placeholder, "simplex"),
        ["-o", "Duplex=None"],
    )
    check(
        "无驱动信息时双面回退 IPP 关键字",
        pl.duplex_args_from_options({}, "vertical"),
        ["-o", "sides=two-sided-long-edge"],
    )
    check(
        "无驱动信息时单面回退 IPP 关键字",
        pl.duplex_args_from_options({}, "simplex"),
        ["-o", "sides=one-sided"],
    )
    check(
        "仅有 IPP sides 时使用 IPP 关键字",
        pl.duplex_args_from_options(pl.parse_lpoptions_l(SAMPLE_LPOPTIONS), "vertical"),
        ["-o", "sides=two-sided-long-edge"],
    )

    print("== 离线样例测试：格式归一化 ==")
    check("tif 归一为 tiff", normalize_ext("tif"), "tiff")
    check("带点与大写扩展名", normalize_ext(".PDF"), "pdf")
    check_true("pdf 受支持", is_supported("pdf"))
    check_true("wps 属 Office 类", is_office_ext("wps"))
    check_true("不支持的扩展名", not is_supported("exe"))

    print("== 离线样例测试：页码范围解析 ==")
    check("空文本表示全部页面", render.parse_page_range(""), None)
    check("单个页码", render.parse_page_range("5"), [(5, 5)])
    check("闭区间", render.parse_page_range("1-3"), [(1, 3)])
    check("多段与单页混用", render.parse_page_range("1-3,5"), [(1, 3), (5, 5)])
    check("开区间（起始可省）", render.parse_page_range("-3"), [(1, 3)])
    check("半开区间（结束可省）", render.parse_page_range("2-"), [(2, None)])
    check("中文逗号与全角连接号", render.parse_page_range("1－2，4"), [(1, 2), (4, 4)])
    check_true("第 2 页命中 1-3", render.page_selected([(1, 3)], 2))
    check_true("第 4 页不命中 1-3", not render.page_selected([(1, 3)], 4))
    check_true("第 9 页命中开放区间 2-", render.page_selected([(2, None)], 9))
    check_true("全部页面（ranges=None）恒命中", render.page_selected(None, 7))
    for bad in ("abc", "0", "1-2-3", "3-1", "-", "1,,a"):
        try:
            render.parse_page_range(bad)
            check_true("非法页码应报错：%s" % bad, False)
        except ValueError:
            check_true("非法页码已拒绝：%s" % bad, True)

    print("== 离线样例测试：缩放与颜色选项 ==")
    check("适应页面不给出固定比例", render.RenderOptions(scale="fit").scale_ratio(), None)
    check("实际大小比例为 1.0", render.RenderOptions(scale="actual").scale_ratio(), 1.0)
    check(
        "自定义比例换算为小数",
        render.RenderOptions(scale="custom", scale_percent=80).scale_ratio(),
        0.8,
    )
    check(
        "自定义比例上限收敛到 4.0",
        render.RenderOptions(scale="custom", scale_percent=9999).scale_ratio(),
        4.0,
    )
    check(
        "自定义比例下限收敛到 0.1",
        render.RenderOptions(scale="custom", scale_percent=1).scale_ratio(),
        0.1,
    )
    check_true("mono 判定为黑白", render.RenderOptions(color="mono").is_mono())
    check_true("color 判定为彩色", not render.RenderOptions(color="color").is_mono())
    check("质量预设分辨率", render.QUALITY_DPI, {"draft": 150, "normal": 300, "high": 600})

    print("== 离线样例测试：出纸方向与旋转 ==")
    check("横向：宽高互换", render.oriented_size(595.28, 841.89, "landscape"), (841.89, 595.28))
    check("纵向：保持原样", render.oriented_size(595.28, 841.89, "portrait"), (595.28, 841.89))
    check("纵向：传入倒置尺寸也能纠正", render.oriented_size(841.89, 595.28, "portrait"), (595.28, 841.89))
    check("未知取值按纵向处理", render.oriented_size(595.28, 841.89, ""), (595.28, 841.89))
    check_true("纵向位图 + 纵向纸张：不旋转", not render.should_rotate(2481, 3508, False))
    check_true("纵向位图 + 横向纸张：旋转", render.should_rotate(2481, 3508, True))
    check_true("横向位图 + 横向纸张：不旋转", not render.should_rotate(3508, 2481, True))
    check_true("横向位图 + 纵向纸张：旋转", render.should_rotate(3508, 2481, False))
    check_true("正方形位图：不旋转", not render.should_rotate(1000, 1000, True))
    check_true("尺寸为 0：不旋转", not render.should_rotate(0, 0, True))
    check("方向参数-横向", pl.orientation_args("landscape"), ["-o", "orientation-requested=4"])
    check("方向参数-纵向不传参", pl.orientation_args("portrait"), [])
    check("方向参数-auto 不传参", pl.orientation_args("auto"), [])
    check("方向参数-大写与空格容错", pl.orientation_args(" Landscape "), ["-o", "orientation-requested=4"])
    check(
        "选项默认方向为自动且表格适配开启",
        (render.RenderOptions().orientation, render.RenderOptions().fit_table),
        ("auto", True),
    )
    check_true("横向选项判定", render.RenderOptions(orientation="landscape").wants_landscape() is True)
    check_true("纵向选项判定", render.RenderOptions(orientation="portrait").wants_landscape() is False)
    check_true("自动选项判定", render.RenderOptions(orientation="auto").wants_landscape() is None)
    check_true("表格类扩展名判定", is_spreadsheet_ext("xlsx") and is_spreadsheet_ext("et"))
    check_true("非表格不判定为表格", not is_spreadsheet_ext("docx"))

    print("== 离线样例测试：打印中取消（CUPS cancel） ==")
    check("取消参数-常规作业号", pl.cancel_args("Brother-42"), ["cancel", "Brother-42"])
    check("取消参数-纯数字作业号", pl.cancel_args("42"), ["cancel", "42"])
    check("取消参数-空作业号不生成参数", pl.cancel_args(""), [])
    check_true("取消接口存在且可调用", callable(pl.cancel_job))
    check_true("取消异常继承 RuntimeError", issubclass(render.PrintCanceled, RuntimeError))
    check("取消异常默认说明", str(render.PrintCanceled()), "用户已取消打印")
    check("取消异常自定义说明", str(render.PrintCanceled("已中止")), "已中止")

    print("== 离线样例测试：打印落位裁剪（GDI 超大目标矩形防护） ==")
    # 适应页面：位图完整可见，居中
    box = render.visible_blit_box(1000, 1000, 0, 900, 4800, 4800, 4800, 6600)
    check("适应页面-裁剪框", box[0], (0, 0, 1000, 1000))
    check("适应页面-目标位置", (box[1], box[2]), (0, 900))
    check("适应页面-目标尺寸", (box[3], box[4]), (4800, 4800))
    # 放大到超出可打印区：必须被裁剪，目标矩形不得越界
    box = render.visible_blit_box(1000, 1000, -1600, -700, 8000, 8000, 4800, 6600)
    check("超出区域-裁剪框", box[0], (200, 87, 800, 912))
    check("超出区域-目标位置", (box[1], box[2]), (0, 0))
    check("超出区域-目标尺寸收敛到可打印区", (box[3], box[4]), (4800, 6600))
    # 部分超出：只裁掉超出的一侧
    box = render.visible_blit_box(1000, 1000, -100, 0, 1000, 1000, 4800, 6600)
    check("部分超出-裁剪框", box[0], (100, 0, 1000, 1000))
    check("部分超出-目标尺寸", (box[3], box[4]), (900, 1000))
    # 完全落在区域外 / 退化尺寸：返回 None（跳过该页绘制）
    check("完全在区域右侧", render.visible_blit_box(1000, 1000, 5000, 0, 1000, 1000, 4800, 6600), None)
    check("完全在区域下方", render.visible_blit_box(1000, 1000, 0, 7000, 1000, 1000, 4800, 6600), None)
    check("位图尺寸为 0", render.visible_blit_box(0, 0, 0, 0, 100, 100, 4800, 6600), None)
    check("目标尺寸为 0", render.visible_blit_box(100, 100, 0, 0, 0, 0, 4800, 6600), None)
    # 任意缩放下目标矩形都必须落在可打印区内（GDI 逻辑坐标只有 16 位，越界会报 StretchBlt failed）
    overflow = []
    for percent in (10, 50, 100, 200, 400, 1000):
        for area in ((2481, 3508), (4760, 6814), (14043, 19866)):
            scale = percent / 100.0
            draw_w = 1000 * scale
            draw_h = 1000 * scale
            item = render.visible_blit_box(
                1000, 1000, (area[0] - draw_w) / 2, (area[1] - draw_h) / 2,
                draw_w, draw_h, area[0], area[1],
            )
            if item is None:
                continue
            if item[1] + item[3] > area[0] or item[2] + item[4] > area[1]:
                overflow.append((percent, area, item))
    check("各缩放档位目标矩形均不越界", overflow, [])

    print("== 离线样例测试：页面过滤与黑白转换 ==")
    source = [
        Image.new("RGB", (8, 8), (10, 20, 30)),
        Image.new("RGB", (8, 8), (200, 100, 50)),
        Image.new("RGB", (8, 8), (255, 255, 255)),
    ]
    picked = list(render.apply_options(iter(source), render.RenderOptions(pages="1,3")))
    check("只保留所选页面", len(picked), 2)
    check_true("保留下来的第一页为原第 1 页", picked[0].getpixel((0, 0)) == (10, 20, 30))
    check_true("保留下来的第二页为原第 3 页", picked[1].getpixel((0, 0)) == (255, 255, 255))
    for item in picked:
        item.close()

    gray = list(
        render.apply_options(
            iter([Image.new("RGB", (4, 4), (10, 20, 30))]),
            render.RenderOptions(color="mono", pages=""),
        )
    )
    check("黑白模式位图转为 L 模式", gray[0].mode, "L")
    gray[0].close()

    try:
        list(render.apply_options(iter(source), render.RenderOptions(pages="9-10")))
        check_true("页码范围超出文档页数应报错", False)
    except RuntimeError as exc:
        check_true("页码范围超出文档页数已报错：%s" % exc, True)

    print("== 离线样例测试：打印量统计（口径与聚合） ==")
    check("单面：3 页 1 份 = 3 张", stats.sheets_for(3, 1, "simplex"), 3)
    check("双面：3 页 1 份 = 2 张（向上取整）", stats.sheets_for(3, 1, "vertical"), 2)
    check("双面：4 页 2 份 = 4 张", stats.sheets_for(4, 2, "vertical"), 4)
    check("单面：4 页 2 份 = 8 张", stats.sheets_for(4, 2, "simplex"), 8)
    check("双面：1 页 1 份 = 1 张（不足两张按 1 张）", stats.sheets_for(1, 1, "vertical"), 1)
    check_true(
        "单双面识别同时接受内部值与日志中文值",
        stats.is_duplex("vertical") and stats.is_duplex("双面") and not stats.is_duplex("simplex"),
    )

    # 用「今天 / 昨天」构造样例，避免把用例写死成某个日期（换天执行也不会失败）
    import datetime as _datetime

    _now = _datetime.datetime.now()
    _today = _now.strftime("%Y-%m-%d")
    _yesterday = (_now - _datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    rows = [
        {"time": _today + " 09:00:00", "status": "已完成", "copies": "2", "paper": "A4", "duplex": "双面", "pages": "3"},
        {"time": _today + " 09:10:00", "status": "已完成", "copies": "1", "paper": "A4", "duplex": "单面", "pages": "4"},
        {"time": _today + " 09:20:00", "status": "已完成", "copies": "1", "paper": "A3", "duplex": "单面", "pages": "1"},
        {"time": _today + " 09:30:00", "status": "失败", "copies": "5", "paper": "A4", "duplex": "单面", "pages": "9"},
        {"time": _today + " 09:40:00", "status": "已取消", "copies": "3", "paper": "A4", "duplex": "单面", "pages": "7"},
        {"time": _yesterday + " 10:00:00", "status": "已完成", "copies": "1", "paper": "A4", "duplex": "单面", "pages": "2"},
        {"time": "无时间", "status": "已完成", "copies": "1", "paper": "A4", "duplex": "单面", "pages": "5"},
        {"time": _yesterday + " 08:00:00", "status": "登录", "copies": "", "paper": "", "duplex": "", "pages": ""},
    ]
    days = stats.aggregate_rows(rows)
    check("只按「已完成」聚合（失败/取消/登录不算，脏行跳过）", len(days), 2)
    today = days.get(_today) or stats.new_bucket()
    check("当日任务数", today.get("tasks"), 3)
    check("当日份数", today.get("copies"), 4)
    check("当日页数（每份页数 × 份数：6+4+1）", today.get("pages"), 11)
    check("当日用纸张数（双面 3 页=2 张 ×2 份 + 4 + 1）", today.get("sheets"), 9)
    check("按纸张分组（A4）", today.get("papers", {}).get("A4"), {"copies": 3, "pages": 10, "sheets": 8})
    check("按单双面分组（双面）", today.get("duplex", {}).get("双面"), {"copies": 2, "pages": 6, "sheets": 4})
    check("按单双面分组（单面）", today.get("duplex", {}).get("单面"), {"copies": 2, "pages": 5, "sheets": 5})

    check("累计：两天合并的任务数", stats.summarize(days, None).get("tasks"), 4)
    check("累计：两天合并的页数", stats.summarize(days, None).get("pages"), 13)
    check("窗口=1 天只含今天", stats.summarize(days, 1).get("tasks"), 3)
    check("窗口=7 天含两天", stats.summarize(days, 7).get("tasks"), 4)
    check("合并后分布表也累加（累计 A4 份数）", stats.summarize(days, None).get("papers", {}).get("A4", {}).get("copies"), 4)

    print("== 离线样例测试：A3 拆 A4（服务端矢量裁剪的纯函数） ==")
    check("单条 0.5 → 两段", split.segment_ranges([0.5]), [(0.0, 0.5), (0.5, 1.0)])
    check(
        "乱序 + 重复会去重排序",
        split.segment_ranges([0.66, 0.33, 0.66]),
        [(0.0, 0.33), (0.33, 0.66), (0.66, 1.0)],
    )
    check("无裁切线 → 整页一段", split.segment_ranges([]), [(0.0, 1.0)])
    check("越界比例被忽略（0 / 1 / >1 / <0）", split.segment_ranges([0, 1, 1.5, -1, 0.5]), [(0.0, 0.5), (0.5, 1.0)])
    check("超出页数的裁切项被裁掉", split.normalize_cuts([[0.5], [0.4], [0.3]], 2), [[0.5], [0.4]])
    check("缺页按「整页」补齐", split.normalize_cuts([[0.5]], 3), [[0.5], [], []])
    check("空参数 = 每页整页", split.normalize_cuts(None, 2), [[], []])
    check("预估输出页数（2 + 1 + 3）", split.count_output_pages([[0.5], [], [0.33, 0.66]], 3), 6)
    check("拆分说明的 per_page", split.summarize([[0.5], []], 2)["per_page"], [2, 1])
    check("A4 尺寸常量（595×842 点）", (round(split.A4_WIDTH_PT), round(split.A4_HEIGHT_PT)), (595, 842))

    for bad, label in (([[1.5]], "比例 >1"), ([[-0.2]], "比例 <0"), ([["x"]], "非数字"), ("oops", "整体不是数组")):
        try:
            split.normalize_cuts(bad, 1)
            check_true("非法裁切参数应报错（%s）" % label, False)
        except ValueError as exc:
            check_true("非法裁切参数已报错（%s）：%s" % (label, exc), True)


def run_live() -> None:
    """用目标机真实命令输出做校验（仅 Linux 有效）。

    实机校验以「解析器不抛异常且能给出结构化结果」为准，同时打印观测值供人工比对；
    不把「本机没有打印机」「未设置默认打印机」这类环境问题记为解析失败。

    参数：无
    返回：
        None
    """
    print("== 实机校验（真实命令输出） ==")
    if not pl.cups_available():
        print("  [跳过] 未检测到 lpstat / lp，无法实机校验")
        return

    for label, args, parser in (
        ("lpstat -d", ["lpstat", "-d"], pl.parse_lpstat_d),
        ("lpstat -p", ["lpstat", "-p"], pl.parse_lpstat_p),
        ("lpstat -v", ["lpstat", "-v"], pl.parse_lpstat_v),
        ("lpstat -o", ["lpstat", "-o"], pl.parse_lpstat_o),
    ):
        try:
            text = pl.run_command(args)
        except pl.CupsError as exc:
            check(f"{label} 可执行", str(exc), "无异常")
            continue
        try:
            parsed = parser(text)
        except Exception as exc:  # noqa: BLE001 - 自检脚本需要捕获全部异常并报告
            check(f"{label} 解析不抛异常", repr(exc), "无异常")
            continue
        if isinstance(parsed, (list, dict)) and not parsed and text.strip():
            # 命令有输出却解析不出任何条目 —— 最典型的原因是输出被本地化，
            # 这里把原始输出打出来，便于据此调整解析函数
            check(f"{label} 解析出结构化数据", "空（原始输出非空，疑似格式不符）", "非空")
            for line in text.splitlines()[:3]:
                print(f"        原始输出：{line}")
            continue
        if isinstance(parsed, list):
            summary = f"{len(parsed)} 项"
        elif isinstance(parsed, dict):
            summary = f"{len(parsed)} 条"
        else:
            summary = parsed or "（未设置）"
        check_true(f"{label} 解析成功：{summary}", True)

    default_printer = pl.get_default_printer()
    print(f"  默认打印机：{default_printer or '（未设置）'}")
    if default_printer:
        try:
            options = pl.parse_lpoptions_l(pl.run_command(["lpoptions", "-p", default_printer, "-l"]))
        except pl.CupsError as exc:
            print(f"  [跳过] lpoptions 不可用：{exc}")
        else:
            check_true(f"lpoptions 解析出 {len(options)} 个驱动选项", bool(options))
            print(f"  纸张选项：{pl.paper_names_from_options(options)[:8]}")
            print(f"  双面支持：{pl.duplex_supported(options)}")
        print(f"  打印机状态：{pl.status_of(default_printer)}")
        print(f"  队列作业数：{pl.count_jobs(default_printer)}")

    adapters = sl.list_adapters()
    check_true(f"网卡信息可读（{len(adapters)} 块）", bool(adapters))
    if adapters:
        print(f"  网卡：{sl.network_summary()}")
    print(f"  LibreOffice：{_soffice_version() or '未安装（Office/WPS 格式不可用）'}")


def _soffice_version() -> str:
    """读取 LibreOffice 版本字符串（未安装时返回空字符串）。

    参数：无
    返回：
        str，版本信息
    """
    from core import converter_linux

    path = converter_linux.find_soffice()
    if not path:
        return ""
    try:
        result = subprocess.run([path, "--version"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return path
    return (result.stdout or b"").decode("utf-8", errors="replace").strip().splitlines()[0] if result.stdout else path


def main() -> int:
    """执行自检主流程。

    参数：无
    返回：
        int，0 全部通过；1 存在失败项
    """
    print("=" * 60)
    print("  jyprinter 解析自检")
    print("=" * 60)
    run_offline()
    if "--live" in sys.argv[1:]:
        run_live()
    print("-" * 60)
    print(f"  通过 {PASS} 项　失败 {FAIL} 项")
    if FAIL:
        print("  存在失败项：解析逻辑与预期不符，请检查 core/printers_linux.py 与 core/system_linux.py")
        return 1
    print("  解析自检全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
