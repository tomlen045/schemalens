#!/usr/bin/env python3
"""schemalens 封面 900x383（PIL 渲染，对话钩子形态，不晒星数晒硬指标）"""
from PIL import Image, ImageDraw, ImageFont
import os

OUT = os.path.expanduser("~/Desktop/schemalens_cover.png")
FONT_CJK = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT_MONO = "/System/Library/Fonts/Menlo.ttc"
W, H = 900, 383

_fc = {}
def F(path, size):
    key = (path, size)
    if key not in _fc:
        _fc[key] = ImageFont.truetype(path, size)
    return _fc[key]

def CJK(size):
    return F(FONT_CJK, size)

def MONO(size):
    return F(FONT_MONO, size)

# 渐变底 #0b0e1a → #1a0f2e（135°）
img = Image.new("RGB", (W, H))
px = img.load()
c0 = (11, 14, 26)
c1 = (26, 15, 46)
for y in range(H):
    for x in range(W):
        t = (x / W + y / H) / 2
        px[x, y] = tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))
d = ImageDraw.Draw(img)

# 金竖条锚
d.rectangle([34, 88, 39, 218], fill=(240, 180, 41))

# 栏目标签（金，字距拉开用逐字画）
label = "小薅薅 · 自研工具"
lx = 48
for ch in label:
    d.text((lx, 36), ch, font=CJK(20), fill=(240, 180, 41))
    lx += d.textlength(ch, font=CJK(20)) + 6

# 双行大字钩子（白）
d.text((48, 84), "上游改了表，", font=CJK(46), fill=(255, 255, 255))
d.text((48, 152), "别等报表崩了才知道", font=CJK(46), fill=(255, 255, 255))

# 右侧数据区
d.text((668, 92), "40/40", font=MONO(56), fill=(240, 180, 41))
d.text((676, 168), "自测全绿", font=CJK(28), fill=(255, 255, 255))
d.text((668, 210), "12 种判决", font=CJK(22), fill=(139, 148, 158))

# 底部红色重点行（黑衬底）
red_line = "一个 demo，逮住自己 1 处漏报 + 1 处误报"
rw = d.textlength(red_line, font=CJK(24))
d.rectangle([40, 268, 48 + rw + 20, 306], fill=(0, 0, 0, 160))
d.text((48, 274), red_line, font=CJK(24), fill=(248, 81, 73))

# 底部信息行
d.text((48, 330), "schemalens · 数据源 schema 漂移透镜 · 零依赖单文件", font=CJK(19), fill=(139, 148, 158))

img.save(OUT)
print("OK:", OUT, os.path.getsize(OUT), "bytes")
