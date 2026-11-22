#!/usr/bin/env python3
"""schemalens 证据图生成（内容 100% 来自本机实跑输出，排版是唯一创作）"""
from PIL import Image, ImageDraw, ImageFont
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(OUT, exist_ok=True)
FONT_CJK = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT_MONO = "/System/Library/Fonts/Menlo.ttc"
W, H = 1200, 675
BG, BAR, LINE = "#1b1d23", "#2a2d35", "#0d1117"
GREEN, WHITE, GREY, RED, GOLD, BLUE = "#98c379", "#e6edf3", "#7f8794", "#e06c75", "#e5c07b", "#61afef"

_fc = {}
def F(size, mono):
    key = (size, mono)
    if key not in _fc:
        _fc[key] = ImageFont.truetype(FONT_MONO if mono else FONT_CJK, size)
    return _fc[key]

def has_cjk(t):
    return any('\u2e80' <= ch <= '\u9fff' or ch in '，。：；「」（）：；、）｜→·⏳％%─' for ch in t)

def shot(name, lines, title="schemalens — zsh"):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 44], fill=BAR)
    d.ellipse([24, 15, 36, 27], fill="#ff5f57"); d.ellipse([44, 15, 56, 27], fill="#febc2e"); d.ellipse([64, 15, 76, 27], fill="#28c840")
    tw = d.textlength(title, font=F(16, False))
    d.text(((W - tw) / 2, 13), title, font=F(16, False), fill="#8b90a0")
    d.rectangle([0, 44, W, 46], fill=LINE)
    y = 78
    for text, color, size, mono in lines:
        if text:
            font = F(size, mono and not has_cjk(text))
            d.text((56, y), text, font=font, fill=color)
        y += int(size * 1.72)
    img.save(f"{OUT}/{name}.png")
    print(name, "OK")

if __name__ == "__main__":
    # 01 profile：真机文件画像（自托管服务清单.csv 1250 行实跑）
    shot("01_profile", [
        ("$ schemalens profile 自托管服务清单.csv", WHITE, 22, True),
        ("══ 自托管服务清单.csv ══", GOLD, 24, False),
        ("fmt=csv  encoding=utf-8-sig(BOM)  delimiter=','  rows=1250", GREY, 20, True),
        (" 1. 分类   str  空值  0.0%  例:Analytics / Archiving...", WHITE, 20, True),
        (" 2. 名称   str  空值  0.0%  例:ANALOG / Aptabase / AWStats", WHITE, 20, True),
        (" 4. 许可证 str  空值  0.0%  例:MIT / AGPL-3.0 / GPL-3.0", WHITE, 20, True),
        (" 6. 依赖外部专有服务 str 空值 94.7% 枚举[是] 例:是", GOLD, 20, False),
        ("", WHITE, 10, True),
        ("1250 行秒级出画像：列/类型/空值率/枚举，一次看清", GREEN, 21, False),
    ], title="schemalens profile — 真机 1250 行")

    # 02 demo 判决（v1.0.1 真实输出）
    shot("02_demo_verdicts", [
        ("$ schemalens demo", WHITE, 22, True),
        ("基线已拍: orders_v1.csv（5 列 / 60 行 / utf-8）", GREY, 20, False),
        ("", WHITE, 8, True),
        ("[P0] SL-01 列 [note] 消失了（基线第5列）", RED, 23, False),
        ("[P0] SL-03 列序翻转（shop:基2→现5）", RED, 23, False),
        ("[P0] SL-04 列 [amount] 混入异型值 +50%", RED, 23, False),
        ("        （0%→50%，主导类型 float 不变也会炸下游）", RED, 19, False),
        ("[P1] SL-02 冒出新列 [shop_name]（第2列）", GOLD, 23, False),
        ("[P1] SL-09 列 [status] 枚举丢了旧值 paid", GOLD, 23, False),
        ("—— 判决汇总：P0=3  P1=2  P2=2", WHITE, 23, False),
        ("退出码=2：上游悄悄干了五件事，全部当场点名", GREEN, 21, False),
    ], title="schemalens demo — 上游改表")

    # 03 招牌判决：部分列污染
    shot("03_pollution", [
        ("$ head -3 orders_v2.csv   # 上游改了什么？", WHITE, 22, True),
        ("order_id,shop_name,amount,status,shop", GREY, 21, True),
        ('SO-00001,老王五金,"10.50元",pending,南门水果摊', GOLD, 21, True),
        ("SO-00002,南门水果摊,21.00,pending,二楼奶茶", WHITE, 21, True),
        ("", WHITE, 8, True),
        ("amount 列 100 行里 50 行变成「10.50元」：", GREY, 21, False),
        ("主导类型还是 float，常规 schema 检查一声不吭——", GREY, 21, False),
        ("pandas 一把 df.amount.sum() 当场抛 TypeError。", GREY, 21, False),
        ("", WHITE, 8, True),
        ("[P0] SL-04 混入异型值 +50%（0%→50%）", RED, 25, False),
        ("schemalens 逐值分类统计异型占比，跳升就点名", GREEN, 21, False),
    ], title="schemalens — 部分列污染")

    # 04 狗粮：v1.0.0 漏报实录（git show 1a68d72 真实运行输出）
    shot("04_dogfood_v100", [
        ("$ git show 1a68d72:schemalens.py > v100.py   # 回到 v1.0.0", GREY, 19, True),
        ("$ python3 v100.py demo", WHITE, 22, True),
        ("[P0] SL-01 列 [note] 消失了（基线第5列）", RED, 22, False),
        ("[P0] SL-03 列序翻转（shop:基2→现5）", RED, 22, False),
        ("[P1] SL-02 冒出新列 [shop_name]（第2列）", GOLD, 22, False),
        ("（SL-04？没有。amount 一半行已经变成文本，它没吭声）", RED, 20, False),
        ("", WHITE, 8, True),
        ("—— 对照组：没漂移的 v3 ——", GREY, 20, False),
        ("[P2] SL-10 基线里的 [orders_v1.csv] 这次没给现场文件", RED, 21, False),
        ("demo 结束：漂移版退出码=2，干净版退出码=1", RED, 22, True),
        ("自测 39/39 全绿的工具，demo 一跑漏报+误报各一处", GOLD, 21, False),
    ], title="schemalens v1.0.0 — 狗粮现场")

    # 05 修复后
    shot("05_dogfood_v101", [
        ("$ python3 schemalens.py demo   # v1.0.1 修复后", WHITE, 22, True),
        ("[P0] SL-04 列 [amount] 混入异型值 +50%", GREEN, 23, False),
        ("        （0%→50%，主导类型 float 不变也会炸下游）", GREEN, 19, False),
        ("", WHITE, 8, True),
        ("—— 对照组：没漂移的 v3 ——", GREY, 20, False),
        ("PASS：schema 与基线一致，没有漂移。", GREEN, 22, False),
        ("—— 判决汇总：P0=0  P1=0  P2=0", WHITE, 22, False),
        ("", WHITE, 8, True),
        ("demo 结束：漂移版退出码=2，干净版退出码=0", GREEN, 22, True),
        ("漏报清零，误报清零，立案→修复→结案，狗粮是甜的", GREEN, 21, False),
    ], title="schemalens v1.0.1 — 修复结案")

    # 06 自测全绿 + 挂 cron
    shot("06_selftest_cron", [
        ("$ schemalens selftest", WHITE, 22, True),
        ("schemalens selftest v1.0.1：40/40 ALL GREEN", GREEN, 25, True),
        ("", WHITE, 10, True),
        ("# crontab：每天 06:10 给昨天的落地文件做巡检", GREY, 20, False),
        ("10 6 * * * python3 /opt/tools/schemalens.py doctor \\", GOLD, 21, True),
        ('  "/data/inbound/$(date +\\%Y\\%m\\%d).csv" \\', GOLD, 21, True),
        ("  --base /opt/tools/base.json >> /var/log/schemalens.log", GOLD, 21, True),
        ("", WHITE, 10, True),
        ("退出码：0 干净 / 1 有发现 / 2 有P0 / 3 基线缺失", WHITE, 20, False),
        ("告警系统靠退出码就能分诊，P0 半夜也会叫人", GREEN, 21, False),
    ], title="schemalens selftest + crontab")
