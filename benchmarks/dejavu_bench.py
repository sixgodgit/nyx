#!/usr/bin/env python3
"""Déjà Vu 基准测试 —— 全部数字均为实测，不引用理论公式代替测量。

测四组：
  A. Veil 假阳性率：1k / 10k / 100k / 1M 条记忆下，实测 vs 理论
  B. 假阳性率随负载的曲线，标出扩容拐点
  C. imprint / sense / hunt 的 p50 / p95 延迟
  D. Veil + Mist 的磁盘与内存占用
  E. 【关键】「熟悉但检索不到」对照实验：sense() vs 纯 FTS5

用法::

    python3 benchmarks/dejavu_bench.py                 # 全部
    python3 benchmarks/dejavu_bench.py --quick         # 快速模式（不含 1M）
    python3 benchmarks/dejavu_bench.py --section fp    # 只跑某节

结果写入 benchmarks/results/dejavu_bench.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexsandglass.dejavu import DejaVu                      # noqa: E402
from nexsandglass.dejavu.core import scent                  # noqa: E402
from nexsandglass.dejavu.veil import Veil                   # noqa: E402

RESULTS: dict = {}
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "results", "dejavu_bench.json")

# ── 语料生成 ────────────────────────────────────────────────
# 关键：Bloom 的饱和度取决于**唯一 token 数**，不是记忆条数。
# 若语料词汇量太小（例如只有几十个词），无论多少条记忆都只会产生
# 几百个唯一 token，Veil 永远不饱和 —— 实测假阳性恒为 0，这是假象。
# 因此这里生成**高基数**语料：每条记忆都引入新的编号/姓名/金额，
# 使唯一 token 数随记忆数线性增长，符合真实聊天记录的特征。

_ZH_TOPICS = ["川菜馆", "季度复盘", "供应链", "冷库维修", "发票报销", "客户投诉",
              "招聘面试", "租金谈判", "菜单定价", "食品安全", "排班表", "设备保养",
              "外卖平台", "税务申报", "员工培训", "水电改造", "装修报价", "物流配送"]
_ZH_VERBS = ["讨论了", "确认了", "跟进了", "复盘了", "否决了", "敲定了",
             "推迟了", "重提了", "评估了", "汇报了", "核对了", "催了"]
_ZH_SURNAME = ["张", "李", "王", "陈", "刘", "赵", "杨", "黄", "周", "吴",
               "徐", "孙", "马", "朱", "胡", "郭", "何", "高", "林", "罗"]
_ZH_ROLE = ["老板", "经理", "会计", "师傅", "主管", "顾问", "店长", "采购"]
_ZH_TAIL = ["下周三给答复", "月底前完成", "先按老方案走", "需要再确认",
            "预算控制在两万内", "等对方回消息", "已同步给团队", "暂时搁置",
            "等财务审批", "下周再碰一次", "先做个小样", "按合同执行"]

_EN_WORDS = ["budget", "schedule", "supplier", "invoice", "refund", "deadline",
             "quarterly", "review", "contract", "shipment", "inventory", "payroll",
             "compliance", "onboarding", "marketing", "logistics", "procurement",
             "warehouse", "turnover", "forecast"]


# 常用汉字区间（U+4E00 起 2000 字），用于合成高基数语料
_CJK_POOL = [chr(c) for c in range(0x4E00, 0x4E00 + 2000)]


def make_memory(i: int, rng: random.Random = None, synthetic: bool = True) -> str:
    """生成第 i 条记忆。

    **为什么用合成汉字而非自然语句**

    Bloom 的饱和度取决于**唯一 token 数**，不是记忆条数。自然语句受
    限于有限词表：用 36 个词的小词表写 10 万条记忆，唯一 token 恒为
    几百个，Veil 永不饱和，实测假阳性恒为 0 —— 那是个假象，不是特性。

    因此这里用「真实词表 + 合成汉字」混合语料：可读的词头保留语义
    直觉，其余用汉字池生成，使唯一 token 数随条数线性增长，符合真实
    聊天记录的基数特征（实测真实语料约 6 唯一 token/条，本函数约 28/条，
    是更严格的压力测试）。

    每条记忆语义无意义 —— 基准测的是**容量与延迟**，不是语义质量。
    """
    r = random.Random(i * 2654435761 % (2 ** 31))
    head = f"{r.choice(_ZH_SURNAME)}{r.choice(_ZH_ROLE)}{r.choice(_ZH_VERBS)}{r.choice(_ZH_TOPICS)}"
    if not synthetic:
        return f"{head}和{r.choice(_ZH_TOPICS)}的事，{r.choice(_ZH_TAIL)}"
    # 合成部分：约 24 个汉字，贡献主要基数
    body = "".join(r.choice(_CJK_POOL) for _ in range(24))
    tail = r.choice(_ZH_TAIL)
    if i % 5 == 0:
        en = " ".join(r.choice(_EN_WORDS) for _ in range(3))
        return f"{head} {en} {body}，{tail}"
    return f"{head}{body}，{tail}"


def count_unique_tokens(n: int) -> int:
    """前 n 条记忆产生的唯一 token 数。"""
    s = set()
    for i in range(n):
        s.update(scent(make_memory(i)))
    return len(s)


# ═══════════════════════════════════════════════════════════
# A. 假阳性率：实测 vs 理论
# ═══════════════════════════════════════════════════════════

def _fp_probe(v: "Veil", n_probe: int, seed: int = 12345) -> float:
    """对已填充的 Veil 测假阳性率：用确定未写入过的探针。"""
    rng = random.Random(seed)
    fp = 0
    for _ in range(n_probe):
        if v.probe(f"__probe_{seed}_{rng.random():.16f}"):
            fp += 1
    return fp / n_probe


def section_fp(scales, rng, n_probe):
    """A. 假阳性率随**唯一 token 数**的变化。

    分两条路径测量，因为它们的瓶颈完全不同：

    1. **Veil 容量路径**（到 1M+ 唯一 token）：直接用 token 灌 Veil，
       不走 SQLite。这是测 Bloom 假阳性率的正确方式 —— Bloom 只关心
       写入了多少唯一 token，不关心它们怎么来的。
    2. **端到端路径**（到 10 万条记忆）：走完整 ``imprint``，反映真实
       使用下的 token 基数放大与写入开销。

    1M **条记忆**的端到端写入不在测量范围内：按实测 3.98 ms/条需
    66 分钟，且需约 1.7 亿唯一 token，远超单机基准的合理成本。
    真实部署中 Veil 的饱和度由实际 token 基数决定，所以路径 1 才是
    回答「1M 条记忆时假阳性率是多少」的正确方式 —— 前提是给出
    该规模下的 token 基数（见 tokenizer_baseline）。
    """
    print("\n" + "=" * 78)
    print("A. Veil 假阳性率：实测 vs 理论")
    print("=" * 78)
    print("A1. Veil 容量路径（直接灌唯一 token，不走 SQLite）")
    print("-" * 78)
    print(f"{'唯一tokens':>12} {'实测 FP':>12} {'理论 FP':>12} "
          f"{'密度估算':>12} {'填充率':>10} {'位图':>10}")
    print("-" * 78)

    rows = []
    for k in scales:
        v = Veil()
        t0 = time.perf_counter()
        for i in range(k):
            v.touch(f"t{i}")
        fill = time.perf_counter() - t0
        st = v.stats()
        measured = _fp_probe(v, n_probe)
        row = {
            "unique_tokens": k,
            "measured_fp": round(measured, 6),
            "theoretical_fp": round(st["theoretical_fp"], 6),
            "estimated_fp": round(st["estimated_fp"], 6),
            "fill_ratio": round(st["fill_ratio"], 6),
            "veil_bytes": st["bytes"],
            "fill_seconds": round(fill, 3),
            "probes": n_probe,
        }
        rows.append(row)
        print(f"{k:>12,} {measured:>12.6f} {st['theoretical_fp']:>12.6f} "
              f"{st['estimated_fp']:>12.6f} {st['fill_ratio']:>10.4f} "
              f"{st['bytes']:>9,}B")

    RESULTS["false_positive_capacity"] = rows

    # ── A2: 端到端路径（imprint） ──
    print("\nA2. 端到端路径（完整 imprint，含 Mist 写入）")
    print("-" * 78)
    print(f"{'记忆数':>10} {'唯一tokens':>12} {'实测 FP':>12} {'理论 FP':>12} "
          f"{'耗时':>9} {'ms/条':>8}")
    print("-" * 78)
    e2e = []
    # 只到 10k：再往上 Veil 已完全饱和（A1 已从容量侧证实），
    # 而端到端写入 100k 需约 10 分钟，边际信息量为零。
    for n in [x for x in (1_000, 10_000) if x <= max(scales)]:
        with tempfile.TemporaryDirectory(prefix="dv-fp-") as d:
            dv = DejaVu(os.path.join(d, "dv"), autosave=0)
            t0 = time.perf_counter()
            for i in range(n):
                dv.imprint(f"m{i}", make_memory(i))
            fill = time.perf_counter() - t0
            uniq = dv._veil.density
            st = dv.veil.stats()
            measured = _fp_probe(dv.veil, n_probe // 10)
            row = {
                "memories": n,
                "unique_tokens": uniq,
                "measured_fp": round(measured, 6),
                "theoretical_fp": round(st["theoretical_fp"], 6),
                "seconds": round(fill, 2),
                "ms_per_memory": round(fill / n * 1000, 3),
            }
            e2e.append(row)
            print(f"{n:>10,} {uniq:>12,} {measured:>12.6f} "
                  f"{st['theoretical_fp']:>12.6f} {fill:>8.1f}s "
                  f"{row['ms_per_memory']:>8.3f}")
            dv.close()
    RESULTS["false_positive_e2e"] = e2e
    return rows


# ═══════════════════════════════════════════════════════════
# B. 拐点：何时必须扩容
# ═══════════════════════════════════════════════════════════

def section_knee(rng, max_tokens: int = 200_000, step: int = 20_000):
    print("\n" + "=" * 70)
    print("B. 假阳性率随负载的曲线 / 扩容拐点")
    print("=" * 70)

    # 直接往 Veil 灌 token（比走 imprint 快得多），测各负载点
    curve = []
    items = [f"tok_{i}" for i in range(max_tokens)]

    v = Veil()
    for i, it in enumerate(items):
        v.touch(it)
        if (i + 1) % step == 0:
            fp = sum(1 for j in range(20_000)
                     if v.probe(f"__unseen_{i}_{j}")) / 20_000
            curve.append({
                "tokens": i + 1,
                "measured_fp": round(fp, 6),
                "theoretical_fp": round(v.theoretical_fp(), 6),
                "estimated_fp": round(v.estimated_fp(), 6),
                "fill_ratio": round(v.stats()["fill_ratio"], 6),
            })

    # 找拐点：首次超过 1% / 5% / 10%
    knees = {}
    for thresh in (0.01, 0.05, 0.10):
        hit = next((c for c in curve if c["measured_fp"] >= thresh), None)
        knees[f"fp_{int(thresh*100)}pct_at"] = hit["tokens"] if hit else None

    RESULTS["knee_curve"] = curve
    RESULTS["knee_points"] = knees

    print(f"{'tokens':>10} {'实测 FP':>12} {'理论 FP':>12} {'填充率':>10}")
    print("-" * 50)
    for c in curve[::2]:
        print(f"{c['tokens']:>10,} {c['measured_fp']:>12.5f} "
              f"{c['theoretical_fp']:>12.5f} {c['fill_ratio']:>10.4f}")
    print()
    for k, val in knees.items():
        print(f"  {k}: {val:,} tokens" if val else f"  {k}: 未达到")
    return curve


# ═══════════════════════════════════════════════════════════
# C. 延迟
# ═══════════════════════════════════════════════════════════

def _pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round((p / 100) * len(s) + 0.5)) - 1))
    return s[k]


def section_latency(rng, n_mem: int = 10_000, reps: int = 2000):
    print("\n" + "=" * 70)
    print("C. 延迟（p50 / p95，单位 ms）")
    print("=" * 70)

    with tempfile.TemporaryDirectory(prefix="dv-lat-") as d:
        dv = DejaVu(os.path.join(d, "dv"), autosave=0)
        for i in range(n_mem):
            dv.imprint(f"m{i}", make_memory(i))
        dv.persist()

        out = {}
        for name, fn in (
            ("imprint", lambda: dv.imprint(f"m{rng.random()}", "新消息 讨论预算")),
            ("sense",   lambda: dv.sense("张老板 讨论了 川菜馆")),
            ("hunt",    lambda: dv.hunt("张老板 川菜馆")),
        ):
            lat = []
            for _ in range(reps):
                t0 = time.perf_counter()
                fn()
                lat.append((time.perf_counter() - t0) * 1000)
            out[name] = {
                "p50_ms": round(_pct(lat, 50), 4),
                "p95_ms": round(_pct(lat, 95), 4),
                "mean_ms": round(statistics.mean(lat), 4),
                "reps": reps,
            }
            print(f"  {name:<10} p50={out[name]['p50_ms']:>8.3f}ms  "
                  f"p95={out[name]['p95_ms']:>8.3f}ms  "
                  f"mean={out[name]['mean_ms']:>8.3f}ms")

        RESULTS["latency"] = {"memory_count": n_mem, **out}
        dv.close()
    return out


# ═══════════════════════════════════════════════════════════
# D. 磁盘 / 内存占用
# ═══════════════════════════════════════════════════════════

def _rss_kb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:
        pass
    return None


def section_footprint(rng, n_mem: int = 10_000):
    print("\n" + "=" * 70)
    print("D. 磁盘与内存占用")
    print("=" * 70)

    with tempfile.TemporaryDirectory(prefix="dv-foot-") as d:
        base = os.path.join(d, "dv")
        rss0 = _rss_kb()
        dv = DejaVu(base, autosave=0)
        rss1 = _rss_kb()

        for i in range(n_mem):
            dv.imprint(f"m{i}", make_memory(i))
        dv.persist()
        rss2 = _rss_kb()

        veil_p = os.path.join(base, "veil.bin")
        mist_p = os.path.join(base, "mist.db")
        def sz(p):
            return os.path.getsize(p) if os.path.exists(p) else 0
        total = sum(sz(os.path.join(base, f)) for f in os.listdir(base))

        out = {
            "memories": n_mem,
            "veil_bytes": sz(veil_p),
            "mist_bytes": sz(mist_p),
            "total_disk_bytes": total,
            "rss_after_construct_kb": (rss1 - rss0) if rss0 and rss1 else None,
            "rss_after_fill_kb": (rss2 - rss1) if rss1 and rss2 else None,
            "veil_items": dv.gaze().veil_items,
            "mist_total": dv.gaze().mist_total,
        }
        RESULTS["footprint"] = out
        print(f"  记忆条数      : {n_mem:,}")
        print(f"  veil.bin      : {out['veil_bytes']:,} B")
        print(f"  mist.db       : {out['mist_bytes']:,} B")
        print(f"  合计磁盘      : {out['total_disk_bytes']:,} B "
              f"({out['total_disk_bytes']/n_mem:.1f} B/条)")
        print(f"  构造后 RSS 增 : {out['rss_after_construct_kb']} KB")
        print(f"  填充后 RSS 增 : {out['rss_after_fill_kb']} KB")
        dv.close()
    return out


# ═══════════════════════════════════════════════════════════
# E. 【关键】「熟悉但检索不到」对照实验
# ═══════════════════════════════════════════════════════════

def section_recall_gap(rng):
    """构造三类「熟悉但检索不到」场景，对比 sense() 与纯 FTS5。

    三类场景（任务书指定）：
      1. 实体改写   —— 原句用「张老板」，查询用「张总」
      2. 同义表述   —— 原句「聊了川菜馆」，查询「那家做麻辣的店」
      3. 久远提及   —— 目标记忆埋在很久以前的会话里

    判据：检索器能否找到**目标记忆**。
      - FTS5  : 关键词匹配，找不到就是 0 命中
      - sense + hunt : 能否给出「熟悉」信号并寻回痕迹
    """
    print("\n" + "=" * 70)
    print("E. 【关键】「熟悉但检索不到」对照：sense() vs 纯 FTS5")
    print("=" * 70)

    with tempfile.TemporaryDirectory(prefix="dv-gap-") as d:
        base = os.path.join(d, "dv")
        dv = DejaVu(base, autosave=0)

        # 语料：500 条背景 + 若干目标记忆
        bg = [make_memory(i) for i in range(500)]
        for i, t in enumerate(bg):
            dv.imprint(f"bg{i}", t)

        # 目标记忆（会被埋进历史）
        targets = [
            ("t1", "上周三和张老板聊了川菜馆的事，他说要换厨师",
             "张总 上次说的 那家做麻辣的店", "实体改写+同义"),
            ("t2", "跟李经理确认了冷库维修的报价，预算控制在两万内",
             "冷藏设备 检修 花费 上限", "实体改写+同义"),
            ("t3", "陈师傅反映后厨排班表需要调整，周末人手不够",
             "厨师长 提过 值班安排 的问题", "同义表述"),
            ("t4", "赵顾问建议把菜单定价重新评估一遍",
             "餐饮顾问 提的 价格策略 意见", "同义表述"),
        ]
        for key, text, _q, _t in targets:
            dv.imprint(key, text)

        # 再堆 3000 条，把目标记忆推向「久远」
        for i in range(1000):
            dv.imprint(f"later{i}", make_memory(10000 + i))
        dv.persist()

        # ── FTS5 对照 ──
        fts = sqlite3.connect(":memory:")
        fts.execute("CREATE VIRTUAL TABLE m USING fts5(key, text, tokenize=trigram)")
        for key, text, _q, _t in targets:
            fts.execute("INSERT INTO m VALUES (?,?)", (key, text))
        for i, t in enumerate(bg):
            fts.execute("INSERT INTO m VALUES (?,?)", (f"bg{i}", t))
        fts.commit()

        def fts_find(query, want_key):
            """FTS5 能否找到目标 key。先精确，再逐词 OR 降级。"""
            try:
                rows = fts.execute(
                    "SELECT key FROM m WHERE m MATCH ? LIMIT 20", (f'"{query}"',)
                ).fetchall()
                if any(r[0] == want_key for r in rows):
                    return True
            except Exception:
                pass
            # 降级：拆成词分别查
            toks = [w for w in query.split() if len(w) >= 2]
            for tok in toks:
                try:
                    rows = fts.execute(
                        "SELECT key FROM m WHERE m MATCH ? LIMIT 20", (f'"{tok}"',)
                    ).fetchall()
                    if any(r[0] == want_key for r in rows):
                        return True
                except Exception:
                    continue
            return False

        results = []
        for key, text, query, kind in targets:
            s = dv.sense(query)
            ph = dv.hunt(query)
            hunt_hit = any(key in p.refs for p in ph)
            fts_hit = fts_find(query, key)
            row = {
                "target_key": key,
                "kind": kind,
                "target_text": text,
                "query": query,
                "fts5_found": fts_hit,
                "sense_familiar": s.familiar,
                "sense_score": s.score,
                "hunt_phantoms": len(ph),
                "hunt_found_target": hunt_hit,
                "dejavu_lifted_miss": (not fts_hit) and (s.familiar or hunt_hit),
            }
            results.append(row)

        # 反向对照：纯噪音查询（不该有熟悉感）
        noise_queries = [
            "量子计算对密码学的影响",
            "南极洲企鹅的迁徙路线",
            "拜占庭帝国的金币铸造工艺",
        ]
        noise = []
        for q in noise_queries:
            s = dv.sense(q)
            noise.append({"query": q, "familiar": s.familiar, "score": s.score,
                          "phantoms": len(dv.hunt(q))})

        n_lifted = sum(1 for r in results if r["dejavu_lifted_miss"])
        n_fts = sum(1 for r in results if r["fts5_found"])
        n_hunt = sum(1 for r in results if r["hunt_found_target"])
        n_noise_false = sum(1 for x in noise if x["familiar"])

        summary = {
            "n_targets": len(results),
            "fts5_found": n_fts,
            "sense_detected_familiar": sum(1 for r in results if r["sense_familiar"]),
            "hunt_found_target": n_hunt,
            "dejavu_lifted_miss": n_lifted,
            "noise_queries": len(noise),
            "noise_false_familiar": n_noise_false,
            "corpus": {"background": len(bg), "targets": len(targets), "filler": 1000},
        }

        RESULTS["recall_gap"] = {"rows": results, "noise": noise, "summary": summary}

        print(f"{'目标':>4} {'场景':<10} {'FTS5':>6} {'sense':>7} "
              f"{'hunt':>6} {'挽回?':>6}")
        print("-" * 60)
        for r in results:
            print(f"{r['target_key']:>4} {r['kind']:<10} "
                  f"{'命中' if r['fts5_found'] else '未中':>6} "
                  f"{('熟悉' if r['sense_familiar'] else '陌生'):>7} "
                  f"{('找回' if r['hunt_found_target'] else '未回'):>6} "
                  f"{('是' if r['dejavu_lifted_miss'] else '否'):>6}")
        print()
        print(f"  FTS5 命中目标      : {n_fts}/{len(results)}")
        print(f"  sense 判定熟悉     : {summary['sense_detected_familiar']}/{len(results)}")
        print(f"  hunt 找回目标      : {n_hunt}/{len(results)}")
        print(f"  Déjà Vu 挽回的检索失败: {n_lifted}/{len(results)}")
        print(f"  噪音查询误报熟悉   : {n_noise_false}/{len(noise)}")
        fts.close()
        dv.close()
        return summary


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="跳过 1M 量级")
    ap.add_argument("--section", default="all",
                    choices=["all", "fp", "knee", "lat", "foot", "gap"])
    ap.add_argument("--probes", type=int, default=100_000)
    args = ap.parse_args()

    rng = random.Random(20260913)
    t_start = time.time()

    meta = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version.split()[0],
        "veil_bits": (1 << 20),
        "veil_hashes": 7,
        "quick_mode": args.quick,
        "tokenizer": "cjk 2-gram (fixed boundary) + ascii words",
    }
    RESULTS["meta"] = meta

    print("=" * 70)
    print("Déjà Vu 基准测试")
    print(f"Python {meta['python']} | Veil {meta['veil_bits']:,} bits / "
          f"{meta['veil_hashes']} hashes | {meta['timestamp']}")
    print("=" * 70)

    # 分词基线
    probe_txt = make_memory(1)
    RESULTS["tokenizer_baseline"] = {
        "sample": probe_txt,
        "tokens": len(scent(probe_txt)),
        "avg_tokens_per_memory": round(
            sum(len(scent(make_memory(i))) for i in range(200)) / 200, 2),
        "unique_tokens_at_1k": count_unique_tokens(1000),
        "unique_tokens_at_10k": count_unique_tokens(10_000),
    }
    print(f"\n分词基线: 平均 "
          f"{RESULTS['tokenizer_baseline']['avg_tokens_per_memory']} tokens/条记忆")

    sec = args.section
    if sec in ("all", "fp"):
        scales = [1_000, 10_000, 100_000] if args.quick else [1_000, 10_000, 100_000, 1_000_000]
        section_fp(scales, rng, args.probes)
    if sec in ("all", "knee"):
        section_knee(rng, max_tokens=200_000)
    if sec in ("all", "lat"):
        section_latency(rng, n_mem=5_000 if args.quick else 10_000,
                        reps=500 if args.quick else 2000)
    if sec in ("all", "foot"):
        section_footprint(rng, n_mem=5_000 if args.quick else 10_000)
    if sec in ("all", "gap"):
        section_recall_gap(rng)

    RESULTS["meta"]["total_seconds"] = round(time.time() - t_start, 1)

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    # 合并而非覆盖：单节运行（--section fp/lat/gap）时保留其余节的历史结果。
    # 否则跑 --section gap 会把 A/B/C/D 的数据抹掉，文档随之失准。
    merged = {}
    if os.path.exists(OUT_JSON):
        try:
            with open(OUT_JSON, encoding="utf-8") as f:
                merged = json.load(f)
        except Exception:
            merged = {}
    merged.update(RESULTS)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print(f"完成，用时 {RESULTS['meta']['total_seconds']}s")
    print(f"结果: {OUT_JSON}")


if __name__ == "__main__":
    main()
