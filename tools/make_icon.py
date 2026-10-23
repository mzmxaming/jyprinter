"""生成程序图标（可重复执行）。

几何来自 Material Design Icons 的 printer 图标（Apache-2.0，24×24 视图）：
    · 顶部进纸：矩形 (6,3)-(18,7)
    · 机身：带 3px 圆角的机身轮廓，底部中间为出纸口凹陷
    · 出纸：矩形 (8,12)-(16,17)
    · 机身右上：半径 1 的圆形指示灯（圆心 19,12）

输出：
    assets/jyprinter.ico        —— 程序/托盘/控制台图标（Windows，蓝底白图，多尺寸）
    assets/jyprinter.png        —— Linux 桌面图标（供 .desktop 与图标主题使用）
    web/static/favicon.ico      —— 网页图标
    web/static/logo.png         —— 网页页眉 logo（透明底蓝色图形）

用法：
    .venv\\Scripts\\python.exe tools/make_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
STATIC = ROOT / "web" / "static"

BRAND = (47, 111, 237, 255)      # 品牌蓝 #2f6fed
WHITE = (255, 255, 255, 255)
UNIT = 24.0                       # SVG 视图尺寸
SUPERSAMPLE = 8                   # 超采样倍数，用于获得平滑边缘
ARC_STEPS = 24                    # 圆弧采样段数


def _point(x: float, y: float, size: int, scale: float) -> tuple:
    """把 24×24 坐标转换为实际像素坐标。

    参数：
        x, y  - 视图坐标
        size  - 目标边长（像素）
        scale - 超采样倍数
    返回：
        (px, py) 像素坐标元组
    """
    factor = size * scale / UNIT
    return (x * factor, y * factor)


def _rounded_corner(
    points: list, center: tuple, radius: float, start_deg: float, end_deg: float, size: int, scale: float
) -> None:
    """把一段圆弧采样为折线点追加到列表。

    参数：
        points    - 目标点列表（就地修改）
        center    - 圆弧圆心（视图坐标）
        radius    - 圆弧半径（视图坐标）
        start_deg - 起始角度（度，顺时针，0 度为 3 点钟方向，y 轴向下）
        end_deg   - 结束角度
        size      - 目标边长
        scale     - 超采样倍数
    返回：
        None
    """
    span = end_deg - start_deg
    steps = max(2, int(abs(span) / 90 * ARC_STEPS))
    for index in range(steps + 1):
        angle = math.radians(start_deg + span * index / steps)
        points.append(
            _point(center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle), size, scale)
        )


def draw_glyph(color: tuple, size: int) -> Image.Image:
    """绘制打印机图形（透明底）。

    参数：
        color - 图形颜色（RGBA）
        size  - 输出边长（像素）
    返回：
        PIL.Image，RGBA 图像
    """
    canvas = size * SUPERSAMPLE
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 机身轮廓（含 3px 圆角与底部出纸口凹陷）
    body: list = [_point(5, 6, size, SUPERSAMPLE)]
    _rounded_corner(body, (5, 9), 3, 270, 180, size, SUPERSAMPLE)
    body.append(_point(2, 15, size, SUPERSAMPLE))
    body.append(_point(6, 15, size, SUPERSAMPLE))
    body.append(_point(6, 19, size, SUPERSAMPLE))
    body.append(_point(18, 19, size, SUPERSAMPLE))
    body.append(_point(18, 15, size, SUPERSAMPLE))
    body.append(_point(22, 15, size, SUPERSAMPLE))
    body.append(_point(22, 9, size, SUPERSAMPLE))
    _rounded_corner(body, (19, 9), 3, 0, 270, size, SUPERSAMPLE)
    draw.polygon(body, fill=color)

    # 顶部进纸
    draw.rectangle(
        [
            _point(6, 3, size, SUPERSAMPLE),
            _point(18, 7, size, SUPERSAMPLE),
        ],
        fill=color,
    )

    # 出纸
    draw.rectangle(
        [
            _point(8, 12, size, SUPERSAMPLE),
            _point(16, 17, size, SUPERSAMPLE),
        ],
        fill=color,
    )

    # 右上指示灯
    center = _point(19, 12, size, SUPERSAMPLE)
    radius = UNIT and (size * SUPERSAMPLE / UNIT) * 1.0
    draw.ellipse(
        [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
        fill=color,
    )

    return image.resize((size, size), Image.LANCZOS)


def app_icon(size: int) -> Image.Image:
    """绘制带底色的程序图标（蓝底白图）。

    参数：
        size - 输出边长（像素）
    返回：
        PIL.Image，RGBA 图像
    """
    canvas = size * SUPERSAMPLE
    background = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(background)
    radius = canvas * 0.22
    draw.rounded_rectangle([0, 0, canvas - 1, canvas - 1], radius=radius, fill=BRAND)
    background = background.resize((size, size), Image.LANCZOS)

    glyph_size = int(size * 0.68)
    glyph = draw_glyph(WHITE, glyph_size)
    offset = (size - glyph_size) // 2
    background.alpha_composite(glyph, (offset, offset))
    return background


def main() -> None:
    """生成全部图标文件。"""
    ASSETS.mkdir(parents=True, exist_ok=True)
    STATIC.mkdir(parents=True, exist_ok=True)

    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    base = app_icon(256)
    base.save(ASSETS / "jyprinter.ico", format="ICO", sizes=[(s, s) for s in ico_sizes])
    print(f"已生成 {ASSETS / 'jyprinter.ico'}（{len(ico_sizes)} 种尺寸）")

    favicon = app_icon(64)
    favicon.save(STATIC / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"已生成 {STATIC / 'favicon.ico'}")

    # Linux 桌面图标：.desktop 的 Icon= 指向 PNG，保留 256 与 128 两种常用尺寸
    linux_icon = app_icon(256)
    linux_icon.save(ASSETS / "jyprinter.png", format="PNG")
    print(f"已生成 {ASSETS / 'jyprinter.png'}（Linux 桌面图标）")

    linux_icon_small = app_icon(128)
    linux_icon_small.save(ASSETS / "jyprinter-128.png", format="PNG")
    print(f"已生成 {ASSETS / 'jyprinter-128.png'}（Linux 小尺寸图标）")

    logo = draw_glyph(BRAND, 256)
    logo.save(STATIC / "logo.png")
    print(f"已生成 {STATIC / 'logo.png'}")

    preview = app_icon(128)
    preview.save(ASSETS / "icon_preview.png")
    print(f"已生成 {ASSETS / 'icon_preview.png'}（预览用）")


if __name__ == "__main__":
    main()
