# -*- coding: utf-8 -*-
"""临时分析脚本：聚合 logs 目录所有搜索日志，比较综合情况与展开速度。"""
import glob
import os
import re
import statistics

LOGS_DIR = r"C:\Users\22501\PycharmProjects\pythonProject\机器学习\红龙贼计算器\logs"


def parse_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return {"error": "read"}

    rec = {"file": os.path.basename(path)}
    m = re.search(r"(\d{8})_(\d{6})", os.path.basename(path))
    if m:
        rec["ts"] = m.group(1) + m.group(2)

    m = re.search(r"搜索方式：(.+)", text)
    rec["mode"] = m.group(1).strip() if m else None

    m = re.search(r"最大伤害：(\d+)，最大龙数：(\d+)", text)
    if m:
        rec["max_damage"] = int(m.group(1))
        rec["max_dragons"] = int(m.group(2))

    m = re.search(r"展开节点：(\d+)，路径数：(\d+)", text)
    if m:
        rec["expansions_hdr"] = int(m.group(1))
        rec["paths"] = int(m.group(2))

    m = re.search(r"搜索参数：束宽=(.+?) 深度=(\d+) 时间=(.+?) 龙数=([\d.]+)\.\.(\d+) 路径上限=(\d+)", text)
    if m:
        beam_text = m.group(1).strip()
        beam_m = re.match(r"(\d+)", beam_text)
        rec["beam"] = int(beam_m.group(1)) if beam_m else None
        rec["beam_auto"] = "自动" in beam_text
        rec["depth"] = int(m.group(2))
        rec["time_text"] = m.group(3).strip()
        rec["min_alex"] = float(m.group(4))
        rec["max_alex"] = float(m.group(5))
        rec["max_paths"] = int(m.group(6))

    stats = {}
    tail = text.split("统计：", 1)[1] if "统计：" in text else ""
    for km in re.finditer(r"^\s+([^：:\d\s][^：:\n]*?)[：:]\s*([^\n]+)", tail, re.M):
        key = km.group(1).strip()
        val = km.group(2).strip()
        try:
            stats[key] = float(val) if re.match(r"^-?\d+\.?\d*$", val) else val
        except ValueError:
            stats[key] = val
    rec["stats"] = stats
    return rec


def num(v):
    return v if isinstance(v, (int, float)) else None


def fmt(v, spec=".2f"):
    return format(v, spec) if v is not None else "N/A"


def med_eps(rs):
    vals = [num(r["stats"].get("展开/秒")) for r in rs if num(r["stats"].get("展开/秒")) is not None]
    return statistics.median(vals) if vals else None


def main():
    files = sorted(glob.glob(os.path.join(LOGS_DIR, "*.txt")))
    recs = [parse_file(f) for f in files]
    ok = [r for r in recs if "error" not in r]
    print(f"总日志数: {len(files)}  可解析: {len(ok)}")

    def med(rs, key, default=None):
        vals = [num(r["stats"].get(key)) for r in rs if num(r["stats"].get(key)) is not None]
        return statistics.median(vals) if vals else default

    def pct(rs, key, p):
        vals = sorted(num(r["stats"].get(key)) for r in rs if num(r["stats"].get(key)) is not None)
        if not vals:
            return None
        i = min(len(vals) - 1, int(len(vals) * p))
        return vals[i]

    def mean(rs, key):
        vals = [num(r["stats"].get(key)) for r in rs if num(r["stats"].get(key)) is not None]
        return statistics.mean(vals) if vals else None

    print("\n===== 综合情况（全部日志） =====")
    print(f"总耗时(秒)  : 中位 {fmt(med(ok,'总耗时(秒)'))}  均值 {fmt(mean(ok,'总耗时(秒)'))}  "
          f"P90 {fmt(pct(ok,'总耗时(秒)',0.90))}  P95 {fmt(pct(ok,'总耗时(秒)',0.95))}  "
          f"最大 {max((num(r['stats'].get('总耗时(秒)')) or 0) for r in ok):.2f}")
    print(f"展开节点    : 中位 {fmt(med(ok,'展开节点'),',.0f')}  均值 {fmt(mean(ok,'展开节点'),',.0f')}  "
          f"最大 {max((num(r['stats'].get('展开节点')) or 0) for r in ok):,.0f}")
    eps = [num(r["stats"].get("展开/秒")) for r in ok if num(r["stats"].get("展开/秒")) is not None]
    print(f"展开/秒     : 中位 {statistics.median(eps):,.0f}  均值 {statistics.mean(eps):,.0f}  P10 {pct(ok,'展开/秒',0.10):,.0f}  P90 {pct(ok,'展开/秒',0.90):,.0f}")
    print(f"路径数>0    : {sum(1 for r in ok if (r.get('paths') or 0) > 0)}/{len(ok)}  ({100*sum(1 for r in ok if (r.get('paths') or 0) > 0)/len(ok):.1f}%)")
    print(f"最大伤害分布: 0伤 {sum(1 for r in ok if (r.get('max_damage') or 0)==0)}  | 16 {sum(1 for r in ok if r.get('max_damage')==16)}  | 32 {sum(1 for r in ok if r.get('max_damage')==32)}  "
          f"| 48 {sum(1 for r in ok if r.get('max_damage')==48)}  | 64 {sum(1 for r in ok if r.get('max_damage')==64)}  | >=96 {sum(1 for r in ok if (r.get('max_damage') or 0)>=96)}")

    by_mode = {}
    for r in ok:
        by_mode.setdefault(r.get("mode") or "未知", []).append(r)
    print("\n===== 按搜索方式（版本演进） =====")
    for mode, rs in sorted(by_mode.items(), key=lambda kv: -len(kv[1])):
        print(f"{mode}: {len(rs)}份  总耗时中位 {fmt(med(rs,'总耗时(秒)'))}s  展开/秒中位 {fmt(med_eps(rs),',.0f')}  "
              f"展开节点中位 {fmt(med(rs,'展开节点'),',.0f')}  路径>0 {sum(1 for r in rs if (r.get('paths') or 0)>0)}/{len(rs)}")

    by_beam = {}
    for r in ok:
        if r.get("beam") is None:
            key = "未知"
        elif r.get("beam") == 0:
            key = "0=自动四通道"
        elif r.get("beam") >= 10000:
            key = "≥10000"
        elif r.get("beam") >= 1000:
            key = "1000~9999"
        elif r.get("beam") >= 100:
            key = "100~999"
        else:
            key = "1~99"
        by_beam.setdefault(key, []).append(r)
    print("\n===== 按束宽设置 =====")
    for key in ["0=自动四通道", "1~99", "100~999", "1000~9999", "≥10000", "未知"]:
        rs = by_beam.get(key)
        if not rs:
            continue
        print(f"束宽 {key}: {len(rs)}份  总耗时中位 {fmt(med(rs,'总耗时(秒)'))}s  展开/秒中位 {fmt(med_eps(rs),',.0f')}  "
              f"展开节点中位 {fmt(med(rs,'展开节点'),',.0f')}  路径>0 {sum(1 for r in rs if (r.get('paths') or 0)>0)}/{len(rs)}")

    by_time = {}
    for r in ok:
        t = r.get("time_text") or "?"
        key = "不限时" if "不限时" in t else ("3秒" if "3秒" in t else ("2秒" if "2秒" in t else ("1秒" if "1秒" in t else "其他")))
        by_time.setdefault(key, []).append(r)
    print("\n===== 按时间限制 =====")
    for key, rs in sorted(by_time.items(), key=lambda kv: -len(kv[1])):
        print(f"时间 {key}: {len(rs)}份  总耗时中位 {fmt(med(rs,'总耗时(秒)'))}s  展开/秒中位 {fmt(med_eps(rs),',.0f')}  "
              f"展开节点中位 {fmt(med(rs,'展开节点'),',.0f')}  路径>0 {sum(1 for r in rs if (r.get('paths') or 0)>0)}/{len(rs)}")

    by_heu = {}
    for r in ok:
        h = r["stats"].get("启发函数")
        by_heu.setdefault(str(h) if h is not None else "无", []).append(r)
    print("\n===== 按启发函数 =====")
    for h, rs in sorted(by_heu.items(), key=lambda kv: -len(kv[1])):
        print(f"启发 {h}: {len(rs)}份  总耗时中位 {fmt(med(rs,'总耗时(秒)'))}s  展开/秒中位 {fmt(med_eps(rs),',.0f')}  "
              f"路径>0 {sum(1 for r in rs if (r.get('paths') or 0)>0)}/{len(rs)}")

    by_date = {}
    for r in ok:
        d = (r.get("ts") or "?")[:8]
        by_date.setdefault(d, []).append(r)
    print("\n===== 按日期 =====")
    for d in sorted(by_date):
        rs = by_date[d]
        print(f"{d}: {len(rs)}份  总耗时中位 {fmt(med(rs,'总耗时(秒)'))}s  展开/秒中位 {fmt(med_eps(rs),',.0f')}  "
              f"展开节点中位 {fmt(med(rs,'展开节点'),',.0f')}  路径>0 {sum(1 for r in rs if (r.get('paths') or 0)>0)}/{len(rs)}")


if __name__ == "__main__":
    main()
