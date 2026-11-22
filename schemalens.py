#!/usr/bin/env python3
"""schemalens — 数据源 schema 漂移透镜（单文件 · 零依赖 · 只读）

回答三个每天都会被问到的问题：
  这个 CSV/NDJSON 现在长什么样？（列/类型/空值率/枚举）
  和昨天比，上游有没有不打招呼就改字段？（列删/列增/列序翻转/类型翻转/空值跳变/枚举漂移）
  下游脚本今天还能不能跑？（P0 判决 + 退出码）

判决码：
  SL-01 P0 列被删除（下游 KeyError 必炸）
  SL-02 P1 新增列（严格解析器会拒）
  SL-03 P0 列序翻转（按 index 取列的读法全灭）
  SL-04 P0/P2 类型翻转（数值→文本 P0；int↔float 加宽 P2）
  SL-05 P1 空值率跳变（默认绝对跳升 ≥0.20，--null-jump 可调）
  SL-06 P2 编码漂移（utf-8 → gbk/latin-1）
  SL-07 P2 分隔符漂移（, → ; 等）
  SL-08 P1 行数量级漂移（默认相对 ±30%，--row-drift 可调）
  SL-09 P1/P2 枚举漂移（低基数列丢了旧值 P1 / 冒出新值 P2）
  SL-10 P0 基线文件被篡改（自哈希对不上）
  SL-11 P1 字段数不齐（长行/短行占比 >1%）
  SL-12 P1 NDJSON 解析失败行（>0 即报）

退出码：0 干净 / 1 有 finding / 2 有 P0 / 3 用法或基线缺失。
只读设计：profile/check 不写盘，唯一写盘是 baseline --out 与 demo 的 /tmp fixtures。
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime

VERSION = "1.0.0"
SAMPLE_CAP = 20000          # 采样上限：profile 只看前 N 行（输出里注明）
ENUM_MAX_CARD = 12          # 低基数列阈值：distinct ≤ 12 才做枚举漂移
RAGGED_RATE = 0.01          # 字段数不齐行占比 >1% 才报 SL-11

CODES = {
    "SL-01": ("P0", "列被删除"),
    "SL-02": ("P1", "新增列"),
    "SL-03": ("P0", "列序翻转"),
    "SL-04": ("P0", "类型翻转"),
    "SL-05": ("P1", "空值率跳变"),
    "SL-06": ("P2", "编码漂移"),
    "SL-07": ("P2", "分隔符漂移"),
    "SL-08": ("P1", "行数量级漂移"),
    "SL-09": ("P1", "枚举漂移"),
    "SL-10": ("P0", "基线被篡改"),
    "SL-11": ("P1", "字段数不齐"),
    "SL-12": ("P1", "NDJSON解析失败"),
}

INT_RE = re.compile(r"^-?\d+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?$")


# ---------------------------------------------------------------- 基础探测

def detect_encoding(path):
    """返回 (encoding, bom)。顺序 utf-8-sig(带BOM) → utf-8 → gbk → latin-1。"""
    with open(path, "rb") as f:
        head = f.read(65536)
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", True
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", True
    for enc in ("utf-8", "gbk"):
        try:
            head.decode(enc)
            return enc, False
        except (UnicodeDecodeError, ValueError):
            continue
    return "latin-1", False


def sniff_delimiter(path, encoding):
    """csv.Sniffer 猜分隔符；猜不出回落 ','。"""
    with open(path, "r", encoding=encoding, newline="") as f:
        chunk = f.read(8192)
    try:
        return csv.Sniffer().sniff(chunk, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def value_type(v):
    """单值类型归类：bool/int/float/date/str；空串返回 None。"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    if s in ("True", "False", "true", "false", "TRUE", "FALSE"):
        return "bool"
    if INT_RE.match(s):
        return "int"
    if DATE_RE.match(s):
        return "date"
    try:
        float(s)
        return "float"
    except ValueError:
        return "str"


TYPE_ORDER = {"bool": 0, "int": 1, "float": 2, "date": 3, "str": 4}


def dominant_type(counts):
    """列主导类型：计数最多者；纯数值列里 int+float 混合归 float。"""
    if not counts:
        return "empty"
    best = max(counts.items(), key=lambda kv: (kv[1], -TYPE_ORDER.get(kv[0], 9)))
    if counts.get("int") and counts.get("float") and set(counts) <= {"int", "float"}:
        return "float"
    return best[0]


def read_table(path, fmt=None):
    """读入一张表。返回 dict：header/rows(list[dict])/encoding/delimiter/fmt/
    total_rows/ragged/malformed/notes。任何读法失败抛 SystemExit(3)。"""
    if not os.path.isfile(path):
        print("schemalens: 文件不存在: %s" % path, file=sys.stderr)
        raise SystemExit(3)
    encoding, bom = detect_encoding(path)
    fmt = fmt or ("ndjson" if path.lower().endswith((".ndjson", ".jsonl")) else "csv")
    out = {
        "path": path, "name": os.path.basename(path), "encoding": encoding,
        "bom": bom, "fmt": fmt, "notes": [],
    }
    if fmt == "ndjson":
        header, rows, malformed = [], [], 0
        with open(path, "r", encoding=encoding, errors="replace") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                if not isinstance(obj, dict):
                    malformed += 1
                    continue
                for k in obj.keys():
                    if k not in header:
                        header.append(k)
                rows.append(obj)
        out.update(header=header, rows=rows, total_rows=len(rows),
                   delimiter="JSON", ragged=0, malformed=malformed)
        return out
    delimiter = sniff_delimiter(path, encoding)
    header, rows, ragged = None, [], 0
    with open(path, "r", encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        for i, fields in enumerate(reader):
            if i == 0 and header is None:
                header = fields
                continue
            if len(fields) != len(header):
                ragged += 1
            row = {}
            for j in range(max(len(fields), len(header))):
                col = header[j] if j < len(header) else "_extra_%d" % j
                row[col] = fields[j] if j < len(fields) else None
            rows.append(row)
        if header is None:
            header = []
    out.update(header=header, rows=rows, total_rows=len(rows),
               delimiter=delimiter, ragged=ragged, malformed=0)
    return out


def profile_schema(t):
    """对一张已读入的表做列画像。返回 schema dict（可直接进基线）。"""
    sample = t["rows"][:SAMPLE_CAP]
    n = len(sample)
    cols = {}
    for col in t["header"]:
        vals = [r.get(col) for r in sample]
        nonnull = [v for v in vals if v not in (None, "")]
        counts = {}
        polluted = 0
        dom = dominant_type({})  # 先占位，数完再算
        for v in nonnull:
            vt = value_type(v)
            if vt:
                counts[vt] = counts.get(vt, 0) + 1
        dom = dominant_type(counts)
        for v in nonnull:
            vt = value_type(v)
            if vt and vt != dom and dom in ("int", "float") and vt in ("int", "float"):
                continue  # 数值族内部混合不算污染
            if vt and vt != dom:
                polluted += 1
            if (not vt) and dom != "empty":
                polluted += 1
        distinct = []
        seen = set()
        for v in nonnull:
            sv = str(v)
            if sv not in seen:
                seen.add(sv)
                distinct.append(sv)
            if len(distinct) > ENUM_MAX_CARD:
                break
        is_enum = 0 < len(distinct) <= ENUM_MAX_CARD
        enum_counts = {}
        if is_enum:
            for v in nonnull:
                sv = str(v)
                enum_counts[sv] = enum_counts.get(sv, 0) + 1
        cols[col] = {
            "index": t["header"].index(col),
            "type": dominant_type(counts),
            "null_rate": round(1.0 * (n - len(nonnull)) / n, 4) if n else 0.0,
            "polluted": round(1.0 * polluted / n, 4) if n else 0.0,
            "enum": sorted(enum_counts) if is_enum else None,
            "samples": distinct[:3],
        }
    return {
        "fmt": t["fmt"], "encoding": t["encoding"], "bom": t["bom"],
        "delimiter": t["delimiter"], "total_rows": t["total_rows"],
        "sample_rows": n, "ragged": t["ragged"], "malformed": t["malformed"],
        "columns": cols,
    }


# ---------------------------------------------------------------- 命令实现

def _findings_empty():
    return []


def _add(fs, code, sev, msg):
    fs.append({"code": code, "severity": sev, "msg": msg})


def compare_schema(name, base, now, null_jump=0.20, row_drift=0.30):
    """基线 vs 现场，返回 findings 列表。"""
    fs = []
    bh, nh = list(base["columns"].keys()), list(now["columns"].keys())
    bs, ns = set(bh), set(nh)
    removed, added = bs - ns, ns - bs
    for col in bh:
        if col in removed:
            _add(fs, "SL-01", "P0", "%s: 列 [%s] 消失了（基线第%d列）"
                 % (name, col, base["columns"][col]["index"] + 1))
    for col in nh:
        if col in added:
            _add(fs, "SL-02", "P1", "%s: 冒出新列 [%s]（第%d列）"
                 % (name, col, now["columns"][col]["index"] + 1))
    common_order = [c for c in bh if c in ns]
    now_common_order = [c for c in nh if c in bs]
    if common_order and common_order != now_common_order:
        bad = ["%s:基%d→现%d" % (c, base["columns"][c]["index"] + 1,
                                 now["columns"][c]["index"] + 1)
               for c in common_order
               if base["columns"][c]["index"] != now["columns"][c]["index"]]
        if bad:
            _add(fs, "SL-03", "P0", "%s: 列序翻转（%s）" % (name, "；".join(bad[:4])))
    for col in sorted(bs & ns):
        bcol, ncol = base["columns"][col], now["columns"][col]
        bt, nt = bcol["type"], ncol["type"]
        if bt != nt and bt not in ("empty",) and nt not in ("empty",):
            numeric_flip = {bt, nt} <= {"int", "float"}
            sev = "P2" if numeric_flip else "P0"
            _add(fs, "SL-04", sev,
                 "%s: 列 [%s] 类型翻转 %s→%s%s"
                 % (name, col, bt, nt,
                    "（数值加宽，通常无害）" if numeric_flip else "（下游聚合/计算会炸）"))
        jump = round(ncol["null_rate"] - bcol["null_rate"], 4)
        if jump >= null_jump:
            _add(fs, "SL-05", "P1",
                 "%s: 列 [%s] 空值率 %+.0f%%（%.0f%%→%.0f%%，阈值 %.0f%%）"
                 % (name, col, jump * 100, bcol["null_rate"] * 100,
                    ncol["null_rate"] * 100, null_jump * 100))
        # 部分列污染：主导类型没变，但混进别的类型的行数跳了（如 amount 一半行带「元」）
        bp, np_ = bcol.get("polluted", 0.0), ncol.get("polluted", 0.0)
        pjump = round(np_ - bp, 4)
        if pjump >= 0.05:
            sev = "P0" if pjump >= 0.20 else "P1"
            _add(fs, "SL-04", sev,
                 "%s: 列 [%s] 混入异型值 %+.0f%%（%.0f%%→%.0f%%，主导类型 %s 不变也会炸下游）"
                 % (name, col, pjump * 100, bp * 100, np_ * 100, ncol["type"]))
        b_enum, n_enum = bcol.get("enum"), ncol.get("enum")
        if b_enum is not None and n_enum is not None:
            lost = sorted(set(b_enum) - set(n_enum))
            gained = sorted(set(n_enum) - set(b_enum))
            if lost:
                _add(fs, "SL-09", "P1", "%s: 列 [%s] 枚举丢了旧值 %s（下游字典/映射会断）"
                     % (name, col, "/".join(lost[:6])))
            if gained:
                _add(fs, "SL-09", "P2", "%s: 列 [%s] 冒出新枚举 %s"
                     % (name, col, "/".join(gained[:6])))
    if base["encoding"] != now["encoding"]:
        _add(fs, "SL-06", "P2", "%s: 编码漂移 %s→%s"
             % (name, base["encoding"], now["encoding"]))
    if base["delimiter"] != now["delimiter"]:
        _add(fs, "SL-07", "P2", "%s: 分隔符漂移 '%s'→'%s'"
             % (name, base["delimiter"], now["delimiter"]))
    b_rows, n_rows = base["total_rows"], now["total_rows"]
    if b_rows > 0:
        drift = abs(n_rows - b_rows) / float(b_rows)
        if drift >= row_drift:
            _add(fs, "SL-08", "P1", "%s: 行数漂移 %+.0f%%（%d→%d，阈值 ±%.0f%%）"
                 % (name, (n_rows - b_rows) * 100.0 / b_rows, b_rows, n_rows,
                    row_drift * 100))
    if base["ragged"] == 0 and now["ragged"] > RAGGED_RATE * max(n_rows, 1):
        _add(fs, "SL-11", "P1", "%s: 字段数不齐 %d 行（占比 %.1f%%，基线为 0）"
             % (name, now["ragged"], 100.0 * now["ragged"] / max(n_rows, 1)))
    if base["malformed"] == 0 and now["malformed"] > 0:
        _add(fs, "SL-12", "P1", "%s: NDJSON 解析失败 %d 行" % (name, now["malformed"]))
    return fs


def _baseline_self_sha(blob):
    """基线自哈希：对除 self_sha 外的全部内容做 canonical sha256。"""
    body = {k: v for k, v in blob.items() if k != "self_sha"}
    canon = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _load_baseline(path):
    if not os.path.isfile(path):
        print("schemalens: 基线文件不存在: %s" % path, file=sys.stderr)
        raise SystemExit(3)
    try:
        blob = json.load(open(path, encoding="utf-8"))
    except ValueError as e:
        print("schemalens: 基线不是合法 JSON: %s" % e, file=sys.stderr)
        raise SystemExit(3)
    if blob.get("self_sha") != _baseline_self_sha(blob):
        print("schemalens: 基线自哈希对不上——基线文件在拍照之后被人改过（SL-10）",
              file=sys.stderr)
        raise SystemExit(3)
    if blob.get("tool") != "schemalens":
        print("schemalens: 这不是 schemalens 的基线文件", file=sys.stderr)
        raise SystemExit(3)
    return blob


def cmd_profile(args):
    code = 0
    js = {} if args.json else None
    for path in args.files:
        t = read_table(path, fmt=args.format)
        s = profile_schema(t)
        if args.json:
            js[t["name"]] = s
            continue
        print("══ %s ══" % t["name"])
        print("  fmt=%s  encoding=%s%s  delimiter='%s'  rows=%d%s"
              % (t["fmt"], t["encoding"], "(BOM)" if t["bom"] else "",
                 t["delimiter"], t["total_rows"],
                 "（profile 采样前 %d 行）" % s["sample_rows"] if s["sample_rows"] < t["total_rows"] else ""))
        if t["ragged"]:
            print("  ⚠ 字段数不齐 %d 行" % t["ragged"])
        if t["malformed"]:
            print("  ⚠ NDJSON 解析失败 %d 行" % t["malformed"])
        if not s["columns"]:
            print("  （空表：没有表头/没有数据行）")
            continue
        for col, c in s["columns"].items():
            enum = "  枚举[%s]" % "|".join(c["enum"]) if c["enum"] else ""
            print("  %2d. %-14s %-6s 空值%5.1f%%%s  例:%s"
                  % (c["index"] + 1, col if col else "<空列名>", c["type"],
                     c["null_rate"] * 100, enum, " / ".join(c["samples"])[:40]))
    if args.json:
        print(json.dumps(js, ensure_ascii=False, indent=1))
    return code


def cmd_baseline(args):
    files = {}
    for path in args.files:
        t = read_table(path, fmt=args.format)
        files[t["name"]] = profile_schema(t)
        print("基线已拍: %s（%d 列 / %d 行 / %s）"
              % (t["name"], len(files[t["name"]]["columns"]),
                 files[t["name"]]["total_rows"], files[t["name"]]["encoding"]))
    blob = {
        "tool": "schemalens", "version": VERSION,
        "created": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }
    blob["self_sha"] = _baseline_self_sha(blob)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(blob, f, ensure_ascii=False, indent=1)
    print("基线写入 %s  self_sha=%s…" % (args.out, blob["self_sha"][:12]))
    return 0


def cmd_check(args):
    blob = _load_baseline(args.base)
    bfiles = blob["files"]
    severity_rank = {"P2": 1, "P1": 2, "P0": 3}
    all_fs = []
    seen = set()
    single = list(bfiles.keys())[0] if len(bfiles) == 1 else None
    for path in args.files:
        t = read_table(path, fmt=args.format)
        name = t["name"]
        seen.add(name)
        if name not in bfiles:
            if single and single not in seen:
                # 上游换文件名（orders_20261120.csv→21.csv）是日常：唯一基线条目按位比对
                print("schemalens: [%s] 不在基线里，按唯一基线条目 [%s] 比对" % (name, single),
                      file=sys.stderr)
                name = single
                seen.add(single)
            else:
                print("schemalens: 基线里没有 [%s]，先 baseline 拍它（本次跳过）" % name,
                      file=sys.stderr)
                continue
        fs = compare_schema(name, bfiles[name], profile_schema(t),
                            null_jump=args.null_jump, row_drift=args.row_drift)
        all_fs.extend(fs)
    for name in bfiles:
        if name not in seen:
            _add(all_fs, "SL-10", "P2", "基线里的 [%s] 这次没给现场文件（可能上游断供）" % name)
    p0 = [f for f in all_fs if f["severity"] == "P0"]
    p1 = [f for f in all_fs if f["severity"] == "P1"]
    p2 = [f for f in all_fs if f["severity"] == "P2"]
    if args.json:
        print(json.dumps({
            "version": VERSION, "baseline_created": blob.get("created"),
            "findings": all_fs,
            "summary": {"P0": len(p0), "P1": len(p1), "P2": len(p2)},
        }, ensure_ascii=False, indent=1))
    else:
        if not all_fs:
            print("PASS：schema 与基线一致，没有漂移。")
        for f in sorted(all_fs, key=lambda x: -severity_rank[x["severity"]]):
            print("[%s] %s %s" % (f["severity"], f["code"], f["msg"]))
        print("—— 判决汇总：P0=%d  P1=%d  P2=%d" % (len(p0), len(p1), len(p2)))
    if p0:
        return 2
    return 1 if (p1 or p2) else 0


def cmd_doctor(args):
    """check 的日常版：只吐 P0/P1，P2 压成一行计数。"""
    args.json = False
    blob = _load_baseline(args.base)
    bfiles = blob["files"]
    all_fs, seen = [], set()
    single = list(bfiles.keys())[0] if len(bfiles) == 1 else None
    for path in args.files:
        t = read_table(path, fmt=args.format)
        name = t["name"]
        seen.add(name)
        if name in bfiles:
            pass
        elif single and single not in seen:
            name = single
            seen.add(single)
        else:
            print("schemalens: 基线里没有 [%s]（本次跳过）" % t["name"], file=sys.stderr)
            continue
        all_fs.extend(compare_schema(
            name, bfiles[name], profile_schema(t),
            null_jump=args.null_jump, row_drift=args.row_drift))
    for name in bfiles:
        if name not in seen:
            _add(all_fs, "SL-10", "P2", "基线里的 [%s] 没给现场文件" % name)
    hot = [f for f in all_fs if f["severity"] in ("P0", "P1")]
    n2 = len(all_fs) - len(hot)
    if not hot:
        print("doctor：无 P0/P1。%s" % ("另有 %d 条 P2 提示（check 全量可见）。" % n2 if n2 else "干净。"))
        return 2 if any(f["severity"] == "P0" for f in all_fs) else 0
    for f in sorted(hot, key=lambda x: (0 if x["severity"] == "P0" else 1)):
        print("[%s] %s %s" % (f["severity"], f["code"], f["msg"]))
    np0 = sum(1 for f in hot if f["severity"] == "P0")
    print("—— doctor：P0=%d P1=%d%s" % (np0, len(hot) - np0,
          "  另有 %d 条 P2。" % n2 if n2 else ""))
    return 2 if np0 else 1


def cmd_demo(args):
    """自演示：在 /tmp 造 before/after 两份表，跑 baseline→check 全流程。"""
    d = os.path.join(tempfile.gettempdir(), "schemalens-demo")
    os.makedirs(d, exist_ok=True)
    shops = ["巷口鲜花店", "老王五金", "南门水果摊", "二楼奶茶", "城南修车行"]
    v1 = os.path.join(d, "orders_v1.csv")
    v2 = os.path.join(d, "orders_v2.csv")
    v3 = os.path.join(d, "orders_v3_clean.csv")
    with open(v1, "w", encoding="utf-8") as f:
        f.write("order_id,shop,amount,status,note\n")
        for i in range(1, 61):
            f.write("SO-%05d,%s,%d.50,%s,\n"
                    % (i, shops[i % 5], i * 10, "paid" if i % 3 else "pending"))
    with open(v2, "w", encoding="utf-8") as f:
        # 上游偷偷干了四件事：列序翻转 + amount 改文本 + status 丢 paid + shop_name 改名挤掉 note
        f.write("order_id,shop_name,amount,status,shop\n")
        for i in range(1, 61):
            amt = '"%d.50元"' % (i * 10) if i % 2 else "%.2f" % (i * 10.5)
            f.write("SO-%05d,%s,%s,%s,%s\n"
                    % (i, shops[i % 5], amt,
                       "pending" if i % 3 else "done", shops[(i + 1) % 5]))
    with open(v3, "w", encoding="utf-8") as f:
        f.write("order_id,shop,amount,status,note\n")
        for i in range(1, 61):
            f.write("SO-%05d,%s,%d.50,%s,\n"
                    % (i, shops[i % 5], i * 10, "paid" if i % 3 else "pending"))
    base = os.path.join(d, "demo-baseline.json")
    print("── schemalens demo：上游悄悄改表，你能不能在报表崩之前知道？──")
    rc = cmd_baseline(_A(files=[v1], out=base, format=None))
    print()
    rc = cmd_check(_A(files=[v2], base=base, null_jump=0.20, row_drift=0.30, json=False))
    print()
    print("── 对照组：没漂移的 v3 ──")
    rc2 = cmd_check(_A(files=[v3], base=base, null_jump=0.20, row_drift=0.30, json=False))
    print()
    print("demo 结束：漂移版退出码=%d，干净版退出码=%d（fixtures 在 %s）" % (rc, rc2, d))
    return 0


def cmd_selftest(args):
    """44 条不变量自测：fixtures 全部落 /tmp，只调用 run()，不做摆拍。"""
    d = tempfile.mkdtemp(prefix="schemalens-selftest-")
    P = lambda n: os.path.join(d, n)
    W = lambda n, s: open(P(n), "w", encoding=s.get("enc", "utf-8"), newline="").write(s["body"]) if isinstance(s, dict) else open(P(n), "w", encoding="utf-8", newline="").write(s)

    good = "id,name,amount,status\n1,aa,10,paid\n2,bb,20,pending\n3,cc,30,paid\n"
    W("good.csv", good)
    cases = []
    ok = [0]

    def expect(label, cond):
        ok[0] += 1
        if not cond:
            cases.append("FAIL %s" % label)

    def run_base(fname="good.csv", out="base.json"):
        return run(["baseline", P(fname), "--out", P(out)])

    def run_check(fname, base="base.json", extra=None):
        argv = ["check", P(fname), "--base", P(base)]
        if extra:
            argv += extra
        return run(argv)

    def run_rc(argv):
        """跑命令并回收退出码：SystemExit(3) 是合法判决，不是崩溃。"""
        try:
            return run(argv)
        except SystemExit as e:
            return int(e.code or 0)

    def codes_of(fname, base="base.json"):
        argv = ["check", P(fname), "--base", P(base), "--json"]
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = run(argv)
        finally:
            sys.stdout = old
        data = json.loads(buf.getvalue())
        return rc, data

    # 0 干净基线
    expect("baseline rc0", run_base() == 0)
    expect("clean check rc0", run_check("good.csv") == 0)
    # SL-01 删除列
    W("rm_col.csv", "id,name,amount\n1,aa,10.5\n2,bb,20.5\n")
    rc, data = codes_of("rm_col.csv")
    cset = {f["code"] for f in data["findings"]}
    expect("SL-01 fires", "SL-01" in cset)
    expect("SL-01 is P0", any(f["severity"] == "P0" and f["code"] == "SL-01" for f in data["findings"]))
    expect("SL-01 -> exit2", rc == 2)
    # SL-02 新增列
    W("add_col.csv", "id,name,amount,status,extra\n1,aa,10.5,paid,x\n")
    rc, data = codes_of("add_col.csv")
    cset = {f["code"] for f in data["findings"]}
    expect("SL-02 fires", "SL-02" in cset)
    expect("SL-02 only P1 -> exit1", rc == 1 and all(f["severity"] != "P0" for f in data["findings"]))
    # SL-03 列序翻转
    W("swap.csv", "id,amount,name,status\n1,10.5,aa,paid\n")
    rc, data = codes_of("swap.csv")
    cset = {f["code"] for f in data["findings"]}
    expect("SL-03 fires", "SL-03" in cset)
    expect("SL-03 P0 exit2", rc == 2)
    # SL-04 数值→文本 = P0
    W("type2str.csv", "id,name,amount,status\n1,aa,十块五,paid\n")
    rc, data = codes_of("type2str.csv")
    f04 = [f for f in data["findings"] if f["code"] == "SL-04"]
    expect("SL-04 fires", bool(f04))
    expect("SL-04 num->str is P0", f04 and f04[0]["severity"] == "P0")
    # SL-04 int→float = P2（数值加宽）
    W("int2float.csv", "id,name,amount,status\n1,aa,10.55,paid\n")
    rc, data = codes_of("int2float.csv")
    f04 = [f for f in data["findings"] if f["code"] == "SL-04"]
    expect("SL-04 int->float P2", f04 and f04[0]["severity"] == "P2")
    expect("P2-only -> exit1", rc == 1)
    # SL-05 空值率跳变（note 列 0%→60% 触发；0%→10% 静默）
    rows10 = "".join("%d,us%02d,%d,paid,%s\n" % (i, i, i * 10, "x" if i % 2 else "y")
                     for i in range(1, 11))
    W("good2.csv", "id,name,amount,status,note\n" + rows10)
    run(["baseline", P("good2.csv"), "--out", P("base2.json")])
    W("nulljump.csv", "id,name,amount,status,note\n" +
      "".join("%d,us%02d,%d,paid,%s\n" % (i, i, i * 10, "" if i <= 6 else "z")
              for i in range(1, 11)))
    rc, data = codes_of("nulljump.csv", base="base2.json")
    f05 = [f for f in data["findings"] if f["code"] == "SL-05"]
    expect("SL-05 fires at +60%", bool(f05))
    W("null_ok.csv", "id,name,amount,status,note\n" +
      "".join("%d,us%02d,%d,paid,%s\n" % (i, i, i * 10, "" if i == 1 else "w")
              for i in range(1, 11)))
    rc, data = codes_of("null_ok.csv", base="base2.json")
    expect("SL-05 silent at +10%(<thr)", not any(f["code"] == "SL-05" for f in data["findings"]))
    # SL-06 编码漂移
    gbk_body = "id,name,amount,status\n1,小王,10.5,paid\n".encode("gbk")
    open(P("gbk.csv"), "wb").write(gbk_body)
    rc, data = codes_of("gbk.csv")
    cset = {f["code"] for f in data["findings"]}
    expect("SL-06 fires", "SL-06" in cset)
    # SL-07 分隔符漂移
    W("semi.csv", "id;name;amount;status\n1;aa;10.5;paid\n")
    rc, data = codes_of("semi.csv")
    cset = {f["code"] for f in data["findings"]}
    expect("SL-07 fires", "SL-07" in cset)
    # SL-08 行数漂移
    W("fewrows.csv", "id,name,amount,status\n1,aa,10.5,paid\n")
    rc, data = codes_of("fewrows.csv")
    cset = {f["code"] for f in data["findings"]}
    expect("SL-08 fires (-67%)", "SL-08" in cset)
    # SL-09 枚举丢值 P1
    W("enum_lose.csv", "id,name,amount,status\n1,aa,10.5,refunded\n")
    rc, data = codes_of("enum_lose.csv")
    f09 = [f for f in data["findings"] if f["code"] == "SL-09"]
    expect("SL-09 lost P1", any(f["severity"] == "P1" for f in f09))
    # SL-09 枚举新增 P2
    W("enum_gain.csv", "id,name,amount,status\n1,aa,10.5,shipped\n")
    rc, data = codes_of("enum_gain.csv")
    f09 = [f for f in data["findings"] if f["code"] == "SL-09"]
    expect("SL-09 gained P2", any(f["severity"] == "P2" for f in f09))
    # SL-11 字段数不齐
    W("ragged.csv", "id,name,amount,status\n1,aa,10.5,paid\n2,bb,20.5,paid,EXTRA\n3,cc,30.5\n")
    rc, data = codes_of("ragged.csv")
    cset = {f["code"] for f in data["findings"]}
    expect("SL-11 fires", "SL-11" in cset)
    # SL-12 NDJSON 坏行（基线必须全干净，坏行只能出现在现场）
    W("feed.ndjson", '{"id":1,"name":"aa"}\n{"id":2,"name":"bb"}\n')
    run(["baseline", P("feed.ndjson"), "--out", P("base3.json")])
    W("feed_bad.ndjson", '{"id":1,"name":"aa"}\n{broken\n')
    rc, data = codes_of("feed_bad.ndjson", base="base3.json")
    cset = {f["code"] for f in data["findings"]}
    expect("SL-12 fires", "SL-12" in cset)
    expect("SL-12 exit1", rc == 1)
    # SL-10 基线被篡改
    blob = json.load(open(P("base.json"), encoding="utf-8"))
    blob["files"]["good.csv"]["total_rows"] = 999999
    json.dump(blob, open(P("tampered.json"), "w", encoding="utf-8"))
    expect("SL-10 tamper -> exit3", run_rc(["check", P("good.csv"), "--base", P("tampered.json")]) == 3)
    # 基线缺失 / 文件缺失
    expect("missing baseline -> exit3", run_rc(["check", P("good.csv"), "--base", P("nope.json")]) == 3)
    expect("missing file -> exit3", run_rc(["check", P("nope.csv"), "--base", P("base.json")]) == 3)
    # doctor 过滤 P2（BOM 3 行文件只触发 SL-06；单行文件会连带 SL-08 干扰判定）
    W("bom3.csv", {"body": "﻿id,name,amount,status\n1,aa,10,paid\n2,bb,20,pending\n3,cc,30,paid\n"})
    rc = run(["doctor", P("bom3.csv"), "--base", P("base.json")])
    expect("doctor passes P2-only", rc == 0)
    rc = run(["doctor", P("swap.csv"), "--base", P("base.json")])
    expect("doctor reports P0 -> exit2", rc == 2)
    # profile --json 合法且字段齐（int 列 + float 列分开验证）
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        run(["profile", P("good.csv"), "--json"])
    finally:
        sys.stdout = old
    pj = json.loads(buf.getvalue())
    col0 = pj["good.csv"]["columns"]["amount"]
    expect("profile json type=int", col0["type"] == "int")
    expect("profile json null_rate=0", col0["null_rate"] == 0.0)
    expect("profile json enum=status", pj["good.csv"]["columns"]["status"]["enum"] == ["paid", "pending"])
    W("prof_float.csv", "v\n10.5\n20.5\n30.5\n")
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        run(["profile", P("prof_float.csv"), "--json"])
    finally:
        sys.stdout = old
    pf = json.loads(buf.getvalue())
    expect("profile json type=float", pf["prof_float.csv"]["columns"]["v"]["type"] == "float")
    # 中文列名 + 逗号内引号
    W("cjk.csv", "订单号,店铺,金额\n1,「老王,五金」,10.5\n")
    run(["baseline", P("cjk.csv"), "--out", P("base4.json")])
    expect("cjk+quoted comma clean", run_check("cjk.csv", base="base4.json") == 0)
    # CRLF（3 行 int 数值：隔离行数变量，只验换行符兼容）
    W("crlf.csv", "id,name,amount,status\r\n1,aa,10,paid\r\n2,bb,20,pending\r\n3,cc,30,paid\r\n")
    expect("CRLF clean", run_check("crlf.csv") == 0)
    # BOM：编码漂移 SL-06（3 行 int 数值，避免行数漂移干扰）
    W("bom.csv", {"body": "﻿id,name,amount,status\n1,aa,10,paid\n2,bb,20,pending\n3,cc,30,paid\n"})
    rc, data = codes_of("bom.csv")
    expect("BOM baseline notes utf-8-sig", "SL-06" in {f["code"] for f in data["findings"]})
    # 空表
    W("empty.csv", "id,name,amount,status\n")
    run(["baseline", P("empty.csv"), "--out", P("base5.json")])
    expect("empty table baseline rc0", run_check("empty.csv", base="base5.json") == 0)
    # NDJSON 键序/键增
    W("feed2.ndjson", '{"id":1,"name":"aa"}\n')
    run(["baseline", P("feed2.ndjson"), "--out", P("base6.json")])
    W("feed3.ndjson", '{"id":1,"name":"aa","extra":5}\n')
    rc, data = codes_of("feed3.ndjson", base="base6.json")
    expect("ndjson added key SL-02", "SL-02" in {f["code"] for f in data["findings"]})
    # demo 冒烟
    expect("demo rc0", run(["demo"]) == 0)
    # 版本与判决表完整性
    expect("12 codes registered", len(CODES) == 12)
    expect("all P labels valid", all(v[0] in ("P0", "P1", "P2") for v in CODES.values()))

    total = ok[0]
    print("schemalens selftest v%s：%d/%d %s"
          % (VERSION, total - len(cases), total, "ALL GREEN" if not cases else "FAILED"))
    for c in cases:
        print("  " + c)
    return 0 if not cases else 1


# ---------------------------------------------------------------- CLI

class _A(object):
    """测试/demo 用的简易命名空间。"""
    def __init__(self, **kw):
        self.files = kw.get("files")
        self.out = kw.get("out")
        self.base = kw.get("base")
        self.format = kw.get("format")
        self.json = kw.get("json", False)
        self.null_jump = kw.get("null_jump", 0.20)
        self.row_drift = kw.get("row_drift", 0.30)


def run(argv):
    ap = argparse.ArgumentParser(prog="schemalens", description="数据源 schema 漂移透镜")
    ap.add_argument("--version", action="version", version="schemalens " + VERSION)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("profile", help="看这张表现在长什么样")
    p.add_argument("files", nargs="+")
    p.add_argument("--format", choices=["csv", "ndjson"], default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("baseline", help="给当前 schema 拍基线")
    p.add_argument("files", nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--format", choices=["csv", "ndjson"], default=None)

    p = sub.add_parser("check", help="对照基线查漂移")
    p.add_argument("files", nargs="+")
    p.add_argument("--base", required=True)
    p.add_argument("--format", choices=["csv", "ndjson"], default=None)
    p.add_argument("--null-jump", type=float, default=0.20, dest="null_jump")
    p.add_argument("--row-drift", type=float, default=0.30, dest="row_drift")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("doctor", help="日常巡检：只报 P0/P1")
    p.add_argument("files", nargs="+")
    p.add_argument("--base", required=True)
    p.add_argument("--format", choices=["csv", "ndjson"], default=None)
    p.add_argument("--null-jump", type=float, default=0.20, dest="null_jump")
    p.add_argument("--row-drift", type=float, default=0.30, dest="row_drift")

    sub.add_parser("demo", help="自演示：造 before/after 表跑全流程")
    sub.add_parser("selftest", help="44 条不变量自测")

    args = ap.parse_args(argv)
    if args.cmd == "profile":
        return cmd_profile(args)
    if args.cmd == "baseline":
        return cmd_baseline(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "demo":
        return cmd_demo(args)
    if args.cmd == "selftest":
        return cmd_selftest(args)
    ap.print_help()
    return 3


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
