#!/usr/bin/env python3
"""LoCoMo 检索评测（v7.13.5）—— 走 Hermes 插件的真实写入路径，零 LLM

LoCoMo（Maharana et al., ACL 2024）是 Mem0 / Zep 等报分用的另一个公开基准：
10 段两个人之间的长对话（每段 19–32 个会话、369–689 轮，跨数月），1986 个问题，
每个问题标注了答案出自哪几句话（evidence，如 D1:3 = 第 1 个会话第 3 轮）。
数据：https://github.com/snap-research/locomo  data/locomo10.json
**许可证 CC BY-NC 4.0（非商业）**：仓库里不放数据，运行时指向你下载的文件。

做法
  每段对话在**独立的 nyx 目录、独立的子进程**里：按会话时间冻结时钟，逐轮调用
  NexSandglassProvider.sync_turn —— 与 Hermes 写入的是同一条路径。每轮一条记忆，文本是
  「说话人: 原话」（图片轮附上数据集自带的图片描述 blip_caption，与 LoCoMo 论文的基线一致）。
  两个说话人都不是 nyx 的主人；不配 owner_ids（单人模式），所以两人的话都按主人记。
  全部写完后，时钟拨到最后一个会话的次日，对每个问题调 orchestrator.recall，
  把返回的记忆映射回轮次编号（D1:3）。

指标（证据召回，零 LLM、可复现）
  recall@k   前 k 条里找到的证据句占全部证据句的比例（逐题平均）
  hit@k      前 k 条里至少有一句证据
  all@k      前 k 条里证据句全部找到（多跳题靠这个）
  按类别报：1 多跳（98% 的题证据不止一句）、2 时间、3 开放域推理、4 单跳；
  5 是对抗题（问的事对话里没发生），按惯例不计入；没有证据标注的 4 题不计。

参照：BM25
  同一批轮次文本、同一个问题，用最朴素的 BM25（小写、按字母数字切词、去常见虚词，不做词干）排序。
  它不是对手，是底线：nyx 连它都比不过，说明检索链路有问题。

如实说明
  - 这只是检索。端到端问答准确率（别人报的 LoCoMo 分数）需要一个大模型读答案、再由裁判打分，
    这里还没有；而且裁判和脚本不同，分数能差几十个百分点，要按同一套协议跑才能比
  - 语义检索按 NYX_EMBED 自动启用；没有嵌入后端时只有词法 + 图 + 时间三路。加 --no-semantic 强制关闭

用法
  python3 benchmarks/locomo_eval.py path/to/locomo10.json --workers 10 \
      --json benchmarks/results/locomo_retrieval.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from longmemeval_eval import VIEW_LINES, check_semantic, map_to_seqs  # noqa: E402

CATEGORIES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
SCORED = (1, 2, 3, 4)
KS = (1, 5, 10, 20)
_DATE = re.compile(r"(\d{1,2}):(\d{2})\s*([ap]m)\s+on\s+(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", re.I)
_EVID = re.compile(r"D:?(\d+):(\d+)")


def parse_date(s: str) -> datetime:
    """LoCoMo 的「1:56 pm on 8 May, 2023」。"""
    m = _DATE.search(str(s or ""))
    if not m:
        raise ValueError(f"无法解析日期: {s!r}")
    h, mi, ap, d, mon, y = m.groups()
    h = int(h) % 12 + (12 if ap.lower() == "pm" else 0)
    return datetime.strptime(f"{d} {mon[:3]} {y}", "%d %b %Y").replace(hour=h, minute=int(mi))


def evidence(ev) -> list:
    """证据编号规范化：数据里有 6 处写法不规范（「D:11:26」「D8:6; D9:17」），按 D<会话>:<轮> 抽出来。"""
    out = []
    for s, t in _EVID.findall(" ".join(str(e) for e in (ev or []))):
        k = f"D{int(s)}:{int(t)}"
        if k not in out:
            out.append(k)
    return out


def sessions(conv: dict) -> list:
    """[(会话号, 时间, 轮次列表)]，按会话号排序。"""
    out = []
    for k, v in conv.items():
        m = re.fullmatch(r"session_(\d+)", k)
        if m and isinstance(v, list):
            out.append((int(m.group(1)), parse_date(conv[f"session_{m.group(1)}_date_time"]), v))
    return sorted(out)


def turn_text(t: dict) -> str:
    s = f"{t.get('speaker', '?')}: {str(t.get('text') or '').strip()}"
    if t.get("blip_caption"):
        s += f" [shared a photo: {t['blip_caption']}]"
    return s


# ══════════════════════════════════════════════════════════
# BM25 参照
# ══════════════════════════════════════════════════════════

_STOP = set("a an the and or but if of to in on at by for with from as is are was were be been being do does did "
            "have has had i you he she it we they me him her us them my your his its our their this that these "
            "those what which who whom when where why how not no so than too very can will would should could "
            "about into over after before again there here just also any all some".split())


def _bm25_tokens(s: str) -> list:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP]


class BM25:
    def __init__(self, docs: list, k1: float = 1.2, b: float = 0.75):
        self.toks = [Counter(_bm25_tokens(d)) for d in docs]
        self.lens = [sum(c.values()) for c in self.toks]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        df = Counter(w for c in self.toks for w in c)
        n = len(docs)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}
        self.k1, self.b = k1, b

    def rank(self, query: str) -> list:
        q = set(_bm25_tokens(query))
        scores = []
        for i, (c, ln) in enumerate(zip(self.toks, self.lens)):
            s = 0.0
            for w in q:
                f = c.get(w)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / self.avg))
            if s > 0:
                scores.append((-s, i))
        return [i for _, i in sorted(scores)]


# ══════════════════════════════════════════════════════════
# 子进程：一段对话
# ══════════════════════════════════════════════════════════

def run_conversation(sample: dict, *, budget: int) -> dict:
    from nexsandglass.core import clock, memid
    from nexsandglass.core.memory_provider import NexSandglassProvider

    sess = sessions(sample["conversation"])
    p = NexSandglassProvider({"prefetch_tokens": 0})
    clock.set_global(sess[0][1] if sess else None)
    p.initialize("locomo", agent_context="primary", platform="eval")
    conn = memid.get_conn()
    max_seq = lambda: conn.execute("SELECT COALESCE(MAX(seq), 0) FROM memories").fetchone()[0]

    seq_dia, dia_order, texts = {}, [], []
    t0 = time.time()
    for n, when, turns in sess:
        clock.set_global(when)
        for t in turns:
            dia = evidence([t.get("dia_id")])
            dia = dia[0] if dia else f"D{n}:?{len(dia_order)}"
            before = max_seq()
            p.sync_turn(turn_text(t), "", session_id=f"S{n}")
            for (seq,) in conn.execute("SELECT seq FROM memories WHERE seq > ? ORDER BY seq", (before,)):
                seq_dia[seq] = dia
            dia_order.append(dia)
            texts.append(turn_text(t))
    from nexsandglass.core import sandglass_sqlite, semantic
    from nexsandglass.features import sandglass_vault
    sandglass_sqlite._last_sync_mtime = 0
    sandglass_sqlite.sync_all()
    sandglass_vault._idx_cache = None
    sandglass_vault.rebuild_index()
    semantic.wait_idle(600)
    semantic.index_all()
    sem = check_semantic(semantic.stats())          # 后端开着、索引没建成 → 报错，不记成「语义开」
    ingest_s = time.time() - t0

    bm25 = BM25(texts)
    clock.set_global((sess[-1][1] if sess else datetime.now()) + timedelta(days=1))
    from nexsandglass.runtime import intent as _intent
    from nexsandglass.runtime.orchestrator import get_orchestrator
    orch = get_orchestrator()
    qs = []
    for i, q in enumerate(sample.get("qa") or []):
        t1 = time.time()
        mc = orch.recall(str(q.get("question") or ""), token_budget=budget)
        ms = (time.time() - t1) * 1000
        ranked, unmapped = [], 0
        for seq in map_to_seqs(conn, mc.objects):
            d = seq_dia.get(seq) if seq is not None else None
            if d is None:
                unmapped += 1
            elif d not in ranked:
                ranked.append(d)
        base = []
        for j in bm25.rank(str(q.get("question") or "")):
            if dia_order[j] not in base:
                base.append(dia_order[j])
            if len(base) >= 50:
                break
        view_n = max(VIEW_LINES, _intent.breadth_lines(str(q.get("question") or "")))
        qs.append({"i": i, "category": q.get("category"), "evidence": evidence(q.get("evidence")),
                   "ranked": ranked[:50], "bm25": base, "unmapped": unmapped, "recall_ms": round(ms, 1),
                   "view_n": view_n})
    clock.set_global(None)
    return {"sample_id": sample.get("sample_id"), "n_sessions": len(sess), "n_turns": len(dia_order),
            "n_memories": max_seq(), "ingest_s": round(ingest_s, 2),
            "semantic": {k: sem.get(k) for k in ("backend", "indexed", "pending")}, "questions": qs}


def _worker(args) -> None:
    sample = json.load(open(args.instance_file, encoding="utf-8"))
    res = run_conversation(sample, budget=args.budget)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)


# ══════════════════════════════════════════════════════════
# 主进程：分发 + 汇总
# ══════════════════════════════════════════════════════════

def _spawn(sample: dict, args, tmp: str) -> dict:
    sid = re.sub(r"[^\w.-]", "_", str(sample.get("sample_id")))
    home = os.path.join(tmp, sid)
    os.makedirs(home, exist_ok=True)
    inf, outf = os.path.join(home, "sample.json"), os.path.join(home, "result.json")
    json.dump(sample, open(inf, "w", encoding="utf-8"), ensure_ascii=False)
    env = dict(os.environ, NEXSANDBASE_HOME=os.path.join(home, "nb"),
               PYTHONPATH=_REPO + os.pathsep + os.environ.get("PYTHONPATH", ""))
    for k in ("NYX_NOW", "HERMES_HOME", "NYX_UNDERSTAND_LLM"):
        env.pop(k, None)
    if args.no_semantic:
        env["NYX_EMBED"] = "off"
    cmd = [sys.executable, os.path.abspath(__file__), "--worker", "--instance-file", inf, "--out", outf,
           "--budget", str(args.budget)]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=args.timeout)
    if r.returncode != 0 or not os.path.exists(outf):
        return {"sample_id": sample.get("sample_id"), "error": (r.stderr or r.stdout)[-2000:]}
    return json.load(open(outf, encoding="utf-8"))


def _metrics(rows: list, key: str) -> dict:
    out = {}
    for k in KS:
        top = [set(r[key][:k]) for r in rows]
        gold = [set(r["evidence"]) for r in rows]
        out[f"recall@{k}"] = round(sum(len(t & g) / len(g) for t, g in zip(top, gold)) / len(rows), 4)
        out[f"hit@{k}"] = round(sum(bool(t & g) for t, g in zip(top, gold)) / len(rows), 4)
        out[f"all@{k}"] = round(sum(g <= t for t, g in zip(top, gold)) / len(rows), 4)
    return out


def score(results: list) -> dict:
    groups = defaultdict(list)
    skipped = Counter()
    for conv in results:
        if "error" in conv:
            continue
        for q in conv["questions"]:
            if q["category"] not in SCORED:
                skipped["adversarial" if q["category"] == 5 else f"category {q['category']}"] += 1
                continue
            if not q["evidence"]:
                skipped["no evidence"] += 1
                continue
            name = f"{q['category']} {CATEGORIES[q['category']]}"
            groups[name].append(q)
            groups["_overall"].append(q)
    out = {}
    for g, rows in sorted(groups.items()):
        view = [r for r in rows if "view_n" in r]
        out[g] = {"n": len(rows), "nyx": _metrics(rows, "ranked"), "bm25": _metrics(rows, "bm25"),
                  # agent 实际看到的记录里（默认 12 条，汇总 / 时长 / 先后类 20 条）证据句找到了多少（v7.13.7）
                  "nyx_in_view": round(sum(len(set(r["ranked"][:r["view_n"]]) & set(r["evidence"])) / len(r["evidence"])
                                           for r in view) / len(view), 4) if view else None,
                  "nyx_returned_p50": sorted(len(r["ranked"]) for r in rows)[len(rows) // 2],
                  "recall_ms_p50": sorted(r["recall_ms"] for r in rows)[len(rows) // 2]}
    ok = [c for c in results if "error" not in c]
    out["_meta"] = {"conversations": len(results), "errors": len(results) - len(ok),
                    "questions_scored": len(groups.get("_overall", [])), "skipped": dict(skipped),
                    "turns": sum(c["n_turns"] for c in ok),
                    "ingest_s_total": round(sum(c["ingest_s"] for c in ok), 1),
                    "unmapped_objects": sum(q["unmapped"] for c in ok for q in c["questions"]),
                    "semantic_backend": sorted({str((c.get("semantic") or {}).get("backend")) for c in ok})}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("data", nargs="?", help="locomo10.json")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 段对话（0 = 全部）")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--budget", type=int, default=1500, help="recall 的 token 预算（与 LongMemEval 评测相同）")
    ap.add_argument("--timeout", type=int, default=3600, help="单段对话超时（秒）")
    ap.add_argument("--no-semantic", action="store_true", help="关掉语义检索（NYX_EMBED=off）")
    ap.add_argument("--json", default="", help="指标与逐题结果输出")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--instance-file", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.worker:
        _worker(args)
        return 0
    if not args.data:
        ap.error("需要数据文件路径（locomo10.json）")
    data = json.load(open(args.data, encoding="utf-8"))
    if args.limit:
        data = data[: args.limit]

    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="nyx-locomo-") as tmp:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(lambda s: _spawn(s, args, tmp), data))
    report = score(results)
    report["_meta"].update({"data": os.path.basename(args.data), "wall_s": round(time.time() - t0, 1),
                            "budget": args.budget})
    try:
        from nexsandglass import __version__
        report["_meta"]["nyx_version"] = __version__
    except Exception:
        pass

    print(f"{'':16s} {'n':>5s}   {'nyx R@5':>8s} {'BM25 R@5':>8s}   {'nyx R@10':>8s} {'BM25 R@10':>9s}"
          f"   {'nyx all@10':>10s} {'BM25 all@10':>11s}   {'nyx 视野内':>8s}")
    for g, row in report.items():
        if g == "_meta":
            continue
        a, b = row["nyx"], row["bm25"]
        print(f"{g:16s} {row['n']:5d}   {a['recall@5']:8.3f} {b['recall@5']:8.3f}   {a['recall@10']:8.3f}"
              f" {b['recall@10']:9.3f}   {a['all@10']:10.3f} {b['all@10']:11.3f}   {row['nyx_in_view'] or 0:8.3f}")
    print(json.dumps(report["_meta"], ensure_ascii=False))
    for r in results:
        if "error" in r:
            print(f"[error] {r['sample_id']}: {r['error'][-500:]}", file=sys.stderr)

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        json.dump({"scores": report, "results": results},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 1 if report["_meta"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
