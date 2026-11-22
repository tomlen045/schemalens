# schemalens · 数据源 schema 漂移透镜

**上游给你的 CSV / NDJSON，今天和昨天长得还一样吗？**

上游系统不打招呼就改列名、翻列序、把数值列塞进「12.50元」这样的文本、
悄悄丢掉一个枚举值——下游报表到点照跑，然后 KeyError、聚合 NaN、
字典映射断链。等你发现时，错误数据已经进了库。

schemalens 对数据文件做**.schema 体检**：给今天的表拍基线，
之后每一次对照都宣判——列删没删、序翻没翻、类型翻没翻、
空值跳没跳、枚举丢没丢、编码变没变、行数漂没漂。

```
  [P0] SL-01 orders.csv: 列 [note] 消失了（基线第5列）
  [P0] SL-04 orders.csv: 列 [amount] 混入异型值 +50%（0%→50%，主导类型 float 不变也会炸下游）
  [P1] SL-09 orders.csv: 列 [status] 枚举丢了旧值 paid（下游字典/映射会断）
```

（上面是 demo 场景实捕：上游悄悄干了五件事，全部当场点名）

## 它盯什么

两种数据形态，一个不落：

- **CSV / TSV / 任意分隔符**（csv.Sniffer 自动猜分隔符）
- **NDJSON / JSONL**（每行一个 JSON 对象，键集自动并集）

## 12 种判决

| 代码 | 级别 | 含义 |
|---|---|---|
| SL-01 | P0 | 列被删除（下游 KeyError 必炸） |
| SL-02 | P1 | 新增列（严格解析器会拒） |
| SL-03 | P0 | 列序翻转（按 index 取列的读法全灭） |
| SL-04 | P0/P2 | 类型翻转（数值→文本 P0；int↔float 加宽 P2）+ **部分列污染**（主导类型没变但混入异型值：跳升 ≥5% P1、≥20% P0） |
| SL-05 | P1 | 空值率跳变（绝对跳升 ≥0.20，`--null-jump` 可调） |
| SL-06 | P2 | 编码漂移（utf-8 → gbk/latin-1） |
| SL-07 | P2 | 分隔符漂移（, → ; 等） |
| SL-08 | P1 | 行数量级漂移（相对 ±30%，`--row-drift` 可调） |
| SL-09 | P1/P2 | 枚举漂移（丢旧值 P1 / 冒新值 P2） |
| SL-10 | P0 | 基线文件被篡改（自哈希对不上） |
| SL-11 | P1 | 字段数不齐（长行/短行占比 >1%） |
| SL-12 | P1 | NDJSON 解析失败行（>0 即报） |

**部分列污染是 schemalens 的招牌判决**：amount 列 100 行里有 50 行
变成「12.50元」，主导类型还是 float，常规检测一声不吭——
而 pandas 一把 `df.amount.sum()` 就抛 TypeError。
schemalens 逐值分类统计异型占比，跳升就点名。

## 五命令

```
schemalens profile data.csv                    # 看这张表现在长什么样
schemalens baseline data.csv --out base.json   # 给当前 schema 拍基线
schemalens check data.csv --base base.json     # 对照基线查漂移
schemalens doctor data.csv --base base.json    # 日常巡检：只报 P0/P1
schemalens demo                                # 自演示：上游改表全流程
schemalens selftest                            # 40 条不变量自测
```

退出码：0 干净 / 1 有 finding / 2 有 P0 / 3 用法或基线缺失。
`--json` 出机器可读输出，直接接 cron 告警。

上游换文件名（orders_20261120.csv → orders_20261121.csv）是日常：
基线里只有一条目时自动按位比对，不误报「文件不存在」。

## 挂进 cron 的标准姿势

```cron
# 每天 06:10 给昨天的落地文件做漂移巡检
10 6 * * * /usr/bin/python3 /opt/tools/schemalens.py doctor "/data/inbound/$(bin/date +\%Y\%m\%d).csv" --base /opt/tools/base.json >> /var/log/schemalens.log 2>&1
```

P0 出现时退出码 2，告警系统靠退出码就能分诊。

## 零依赖 · 只读 · 单文件

- Python 3.8+ 标准库，一个第三方包都不装
- **只读设计**：profile/check 不写盘，唯一写盘是 baseline --out 声明的路径
- 单文件 800 余行，`scp` 到任何机器就能跑
- profile 采样前 20000 行（大文件秒级出画像，输出里注明采样范围）

## 自测

40 条不变量：12 种判决逐一触发 + 阈值边界静默 + 退出码分级 +
基线篡改检出 + 文件名回退 + CRLF/BOM/中文列名/引号内逗号 + NDJSON 键集漂移。

```
$ python3 schemalens.py selftest
schemalens selftest v1.0.1：40/40 ALL GREEN
```

## 工具链咬合

数据管道的完整防线，四件套各守一段：

- **schemalens**：上游 schema 变没变（本文）
- logwhisperer：下游日志考古（崩了之后翻历史）
- debugkit：立案取证修复（定位到代码行）
- cronguard/joblens：调度任务本身还活着吗

上游变了 schema → schemalens 当晚点名 → 你在报表崩之前改好下游——
而不是等业务群里@你。

## License

MIT
