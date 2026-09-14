#!/usr/bin/env python3
"""把 benchmarks 的实测结果填入 docs/dejavu.md 与 docs/dejavu.en.md。

文档里的数字**不手抄** —— 手抄会出错，且基准重跑后无法同步。
本脚本读取 benchmarks/results/dejavu_bench.json，渲染 markdown 表格，
替换文档中的占位符。

用法::

    python3 benchmarks/render_docs.py          # 写入
    python3 benchmarks/render_docs.py --check  # 只检查是否已同步
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JSON_PATH = os.path.join(HERE, "results", "dejavu_bench.json")
DOC_ZH = os.path.join(ROOT, "docs", "dejavu.md")
DOC_EN = os.path.join(ROOT, "docs", "dejavu.en.md")

P_FP = "<!-- BENCH_FP_TABLE -->"
P_LAT = "<!-- BENCH_LATENCY -->"
P_FOOT = "<!-- BENCH_FOOTPRINT -->"
P_GAP = "<!-- BENCH_GAP -->"


def load():
    if not os.path.exists(JSON_PATH):
        sys.exit(f"缺少基准结果: {JSON_PATH}\n先跑 python3 benchmarks/dejavu_bench.py")
    with open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def fmt_int(n):
    return f"{n:,}"


def render_fp(d, en=False):
    rows = d.get("false_positive_capacity", [])
    e2e = d.get("false_positive_e2e", [])
    if not rows:
        return "_(缺 A1 数据)_"

    hdr = ("| Unique tokens | Measured FP | Theoretical FP | Fill ratio | Bitmap |"
           if en else "| 唯一 token 数 | 实测假阳性 | 理论假阳性 | 填充率 | 位图占用 |")
    sep = "|---|---|---|---|---|"
    out = [hdr, sep]
    for r in rows:
        out.append(f"| {fmt_int(r['unique_tokens'])} | {r['measured_fp']:.5f} | "
                   f"{r['theoretical_fp']:.5f} | {r['fill_ratio']:.4f} | "
                   f"{fmt_int(r['veil_bytes'])} B |")

    if e2e:
        out.append("")
        if en:
            out.append("End-to-end (full `imprint`, including Mist writes):")
            out.append("")
            out.append("| Memories | Unique tokens | Measured FP | ms/memory |")
            out.append("|---|---|---|---|")
        else:
            out.append("端到端（完整 `imprint`，含 Mist 写入）：")
            out.append("")
            out.append("| 记忆条数 | 唯一 tokens | 实测假阳性 | ms/条 |")
            out.append("|---|---|---|---|")
        for r in e2e:
            out.append(f"| {fmt_int(r['memories'])} | {fmt_int(r['unique_tokens'])} | "
                       f"{r['measured_fp']:.5f} | {r['ms_per_memory']:.2f} |")
    return "\n".join(out)


def render_lat(d, en=False):
    lat = d.get("latency")
    if not lat:
        return "_(缺 C 数据)_"
    head = ("| Operation | p50 | p95 | mean |" if en
            else "| 操作 | p50 | p95 | 均值 |")
    out = [head, "|---|---|---|---|"]
    for op in ("imprint", "sense", "hunt"):
        if op in lat:
            v = lat[op]
            out.append(f"| `{op}()` | {v['p50_ms']:.3f} ms | {v['p95_ms']:.3f} ms | "
                       f"{v['mean_ms']:.3f} ms |")
    n = lat.get("memory_count")
    reps = lat.get("reps", 2000)
    out.append("")
    out.append(f"_{'Measured at' if en else '在'} {fmt_int(n)} "
               f"{f'memories, {reps} reps each' if en else f'条记忆、每项 {reps} 次采样'}._")
    note = lat.get("note")
    if note:
        out.append(f"_{note}._" if not en else f"_{note}._")
    return "\n".join(out)


def render_foot(d, en=False):
    f = d.get("footprint")
    if not f:
        return "_(缺 D 数据)_"
    if en:
        return "\n".join([
            "| Item | Value |", "|---|---|",
            f"| Memories | {fmt_int(f['memories'])} |",
            f"| `veil.bin` | {fmt_int(f['veil_bytes'])} B |",
            f"| `mist.db` | {fmt_int(f['mist_bytes'])} B |",
            f"| Total disk | {fmt_int(f['total_disk_bytes'])} B "
            f"({f['total_disk_bytes']/f['memories']:.1f} B/memory) |",
            f"| RSS after construct | {f['rss_after_construct_kb']} KB |",
            f"| RSS after fill | {f['rss_after_fill_kb']} KB |",
        ])
    return "\n".join([
        "| 项目 | 数值 |", "|---|---|",
        f"| 记忆条数 | {fmt_int(f['memories'])} |",
        f"| `veil.bin` | {fmt_int(f['veil_bytes'])} B |",
        f"| `mist.db` | {fmt_int(f['mist_bytes'])} B |",
        f"| 合计磁盘 | {fmt_int(f['total_disk_bytes'])} B "
        f"（{f['total_disk_bytes']/f['memories']:.1f} B/条）|",
        f"| 构造后 RSS 增量 | {f['rss_after_construct_kb']} KB |",
        f"| 填充后 RSS 增量 | {f['rss_after_fill_kb']} KB |",
    ])


def render_gap(d, en=False):
    g = d.get("recall_gap")
    if not g:
        return "_(缺 E 数据)_"
    s = g["summary"]
    rows = g["rows"]

    if en:
        out = ["| Target | Scenario | FTS5 | sense() | hunt() | Rescued? |",
               "|---|---|---|---|---|---|"]
        for r in rows:
            out.append(
                f"| {r['target_key']} | {r['kind']} | "
                f"{'hit' if r['fts5_found'] else 'miss'} | "
                f"{'familiar' if r['sense_familiar'] else 'unfamiliar'} | "
                f"{'found' if r['hunt_found_target'] else 'none'} | "
                f"{'yes' if r['dejavu_lifted_miss'] else 'no'} |")
        out += ["", "| Metric | Result |", "|---|---|",
                f"| FTS5 found the target | {s['fts5_found']}/{s['n_targets']} |",
                f"| `sense()` flagged familiar | "
                f"{s['sense_detected_familiar']}/{s['n_targets']} |",
                f"| `hunt()` found the target | {s['hunt_found_target']}/{s['n_targets']} |",
                f"| **Misses rescued by Déjà Vu** | "
                f"**{s['dejavu_lifted_miss']}/{s['n_targets']}** |",
                f"| Noise queries falsely familiar | "
                f"{s['noise_false_familiar']}/{s['noise_queries']} |"]
        return "\n".join(out)

    out = ["| 目标 | 场景 | FTS5 | sense() | hunt() | 是否挽回 |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(
            f"| {r['target_key']} | {r['kind']} | "
            f"{'命中' if r['fts5_found'] else '未中'} | "
            f"{'熟悉' if r['sense_familiar'] else '陌生'} | "
            f"{'找回' if r['hunt_found_target'] else '未回'} | "
            f"{'是' if r['dejavu_lifted_miss'] else '否'} |")
    out += ["", "| 指标 | 结果 |", "|---|---|",
            f"| FTS5 命中目标 | {s['fts5_found']}/{s['n_targets']} |",
            f"| `sense()` 判定熟悉 | {s['sense_detected_familiar']}/{s['n_targets']} |",
            f"| `hunt()` 找回目标 | {s['hunt_found_target']}/{s['n_targets']} |",
            f"| **Déjà Vu 挽回的检索失败** | "
            f"**{s['dejavu_lifted_miss']}/{s['n_targets']}** |",
            f"| 噪音查询误报熟悉 | {s['noise_false_familiar']}/{s['noise_queries']} |"]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="只检查文档是否已同步，不写入")
    args = ap.parse_args()

    d = load()
    repl = {
        P_FP: (render_fp(d), render_fp(d, en=True)),
        P_LAT: (render_lat(d), render_lat(d, en=True)),
        P_FOOT: (render_foot(d), render_foot(d, en=True)),
        P_GAP: (render_gap(d), render_gap(d, en=True)),
    }

    stale = []
    for path, idx in ((DOC_ZH, 0), (DOC_EN, 1)):
        if not os.path.exists(path):
            print(f"跳过（不存在）: {path}")
            continue
        s = open(path, encoding="utf-8").read()
        orig = s
        for ph, vals in repl.items():
            if ph in s:
                if s.count(ph) > 1:
                    print(f"  警告: {path} 中占位符 {ph} 出现多次")
                s = s.replace(ph, vals[idx])
        if args.check:
            if s != orig:
                stale.append(path)
        else:
            if s != orig:
                open(path, "w", encoding="utf-8").write(s)
                print(f"已更新: {path}")

    if args.check:
        if stale:
            print("文档未与基准同步:", ", ".join(stale))
            sys.exit(1)
        print("文档已与基准同步")
    else:
        print("完成。文档中的数字均来自 benchmarks/results/dejavu_bench.json")


if __name__ == "__main__":
    main()
