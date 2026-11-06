#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""程序化生成 multi-bot-adapter 技能图标（本地兜底，不依赖外部服务）。

设计：圆角方形深蓝→青色渐变底，中央适配器枢纽节点，
左右两个聊天气泡（代表 QQ / 微信）通过连线接入枢纽，无文字、无厂商 Logo。

用法：
    python tools/make_icon.py
产出：
    assets/icon-1024.png
    assets/icon-512.png
    assets/icon-256.png
    assets/icon-128.png
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 1024
SS = 2  # supersampling


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(int(round(lerp(c1[i], c2[i], t))) for i in range(3))


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def gradient(size: int, top_left, bottom_right) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x / (size - 1) * 0.55 + y / (size - 1) * 0.45)
            px[x, y] = mix(top_left, bottom_right, t)
    return img


def radial_glow(size: int, center, radius: float, color, alpha: int) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    steps = 28
    for i in range(steps, 0, -1):
        r = radius * i / steps
        a = int(alpha * (1 - i / steps) ** 1.7)
        d.ellipse((center[0] - r, center[1] - r, center[0] + r, center[1] + r),
                  fill=(*color, a))
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.08))


def bubble(draw: ImageDraw.ImageDraw, box, tail_side: str, fill, outline=None, width=0):
    x0, y0, x1, y1 = box
    r = (y1 - y0) * 0.28
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)
    # 尾巴
    tw = (x1 - x0) * 0.18
    th = (y1 - y0) * 0.16
    if tail_side == "left":
        pts = [(x0 + (x1 - x0) * 0.18, y1 - 2),
               (x0 + (x1 - x0) * 0.10, y1 + th),
               (x0 + (x1 - x0) * 0.34, y1 - 2)]
    else:
        pts = [(x1 - (x1 - x0) * 0.18, y1 - 2),
               (x1 - (x1 - x0) * 0.10, y1 + th),
               (x1 - (x1 - x0) * 0.34, y1 - 2)]
    draw.polygon(pts, fill=fill)


def make_icon() -> Image.Image:
    s = SIZE * SS
    base = gradient(s, (10, 22, 48), (14, 165, 233))
    # 右下角偏青
    base = Image.blend(base, gradient(s, (8, 18, 40), (34, 211, 238)), 0.35)

    # 底部径向光
    glow = radial_glow(s, (int(s * 0.68), int(s * 0.72)), s * 0.50, (34, 211, 238), 90)
    base = Image.alpha_composite(base.convert("RGBA"), glow)

    # 细网格
    grid = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = s // 16
    for i in range(1, 16):
        a = 16 if i % 4 else 26
        gd.line((i * step, 0, i * step, s), fill=(255, 255, 255, a), width=max(1, s // 700))
        gd.line((0, i * step, s, i * step), fill=(255, 255, 255, a), width=max(1, s // 700))
    base = Image.alpha_composite(base, grid)

    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    cx, cy = s // 2, int(s * 0.52)

    # 连线
    line_w = int(s * 0.018)
    left_anchor = (int(s * 0.345), int(s * 0.50))
    right_anchor = (int(s * 0.655), int(s * 0.50))
    d.line((left_anchor[0], left_anchor[1], cx, cy), fill=(255, 255, 255, 150), width=line_w)
    d.line((cx, cy, right_anchor[0], right_anchor[1]), fill=(255, 255, 255, 150), width=line_w)
    d.line((cx, cy, cx, int(s * 0.30)), fill=(255, 255, 255, 110), width=line_w)

    # 中央枢纽：六边形 + 内圆
    hex_r = s * 0.155
    pts = []
    for i in range(6):
        ang = math.pi / 6 + i * math.pi / 3
        pts.append((cx + hex_r * math.cos(ang), cy + hex_r * math.sin(ang)))
    d.polygon(pts, fill=(255, 255, 255, 235), outline=(255, 255, 255, 255))
    inner = hex_r * 0.58
    pts2 = []
    for i in range(6):
        ang = math.pi / 6 + i * math.pi / 3
        pts2.append((cx + inner * math.cos(ang), cy + inner * math.sin(ang)))
    d.polygon(pts2, fill=(10, 30, 60, 255))
    # 枢纽点
    dot = s * 0.040
    d.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=(34, 211, 238, 255))
    d.ellipse((cx - dot * 0.45, cy - dot * 0.45, cx + dot * 0.45, cy + dot * 0.45),
              fill=(255, 255, 255, 255))

    # 左侧气泡（蓝）
    lw, lh = s * 0.26, s * 0.19
    lb = (int(s * 0.115), int(s * 0.39), int(s * 0.115 + lw), int(s * 0.39 + lh))
    bubble(d, lb, "left", (59, 130, 246, 255))
    # 气泡内三条线
    for i, frac in enumerate((0.30, 0.50, 0.70)):
        y = int(lb[1] + lh * frac)
        d.rounded_rectangle((lb[0] + lw * 0.18, y, lb[0] + lw * (0.78 - i * 0.12), y + s * 0.014),
                            radius=s * 0.007, fill=(255, 255, 255, 220))

    # 右侧气泡（绿）
    rw, rh = s * 0.26, s * 0.19
    rb = (int(s * 0.625), int(s * 0.39), int(s * 0.625 + rw), int(s * 0.39 + rh))
    bubble(d, rb, "right", (34, 197, 94, 255))
    for i, frac in enumerate((0.30, 0.50, 0.70)):
        y = int(rb[1] + rh * frac)
        d.rounded_rectangle((rb[0] + rw * 0.22, y, rb[0] + rw * (0.82 - i * 0.12), y + s * 0.014),
                            radius=s * 0.007, fill=(255, 255, 255, 220))

    # 顶部小节点
    n = s * 0.028
    d.ellipse((cx - n, int(s * 0.30) - n, cx + n, int(s * 0.30) + n),
              fill=(34, 211, 238, 255), outline=(255, 255, 255, 200), width=max(1, s // 400))

    # 细小粒子
    particles = [(0.18, 0.22), (0.82, 0.20), (0.12, 0.74), (0.88, 0.70),
                 (0.28, 0.86), (0.72, 0.88), (0.50, 0.12)]
    for px, py in particles:
        rr = s * 0.008
        x, y = int(s * px), int(s * py)
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(255, 255, 255, 120))

    base = Image.alpha_composite(base, layer)

    # 顶部高光
    hi = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hi)
    hd.ellipse((-s * 0.25, -s * 0.55, s * 0.85, s * 0.35), fill=(255, 255, 255, 26))
    hi = hi.filter(ImageFilter.GaussianBlur(s * 0.06))
    base = Image.alpha_composite(base, hi)

    # 圆角裁切
    mask = rounded_mask(s, int(s * 0.22))
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(base, (0, 0), mask)
    return out.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = make_icon()
    icon.save(ASSETS / "icon-1024.png", "PNG")
    for size in (512, 256, 128):
        icon.resize((size, size), Image.LANCZOS).save(ASSETS / f"icon-{size}.png", "PNG")
    print("written:", ", ".join(str(p) for p in sorted(ASSETS.glob("icon-*.png"))))


if __name__ == "__main__":
    main()
