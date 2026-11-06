#!/usr/bin/env python3
"""BEAM 检索评测（v7.18.0）—— 走 Hermes 插件的真实写入路径，零 LLM，从 10 万到 1000 万 token

BEAM（Tavakoli et al., ICLR 2026, "Beyond a Million Tokens"）：100 段长对话、2000 道题，每段对话
10 万 / 50 万 / 100 万 / 1000 万 token。题分 10 类，除「拒答」外每道题都标注了答案出自哪几条消息
（source_chat_ids）。数据：https://huggingface.co/datasets/Mohammadta/BEAM（及 BEAM-10M），
**许可证 CC BY-SA 4.0**：仓库里不放数据，运行时指向你下载的 parquet 文件（读 parquet 要 pip install pyarrow）。

做法（与 locomo_eval.py 相同）
  每段对话在**独立的 nyx 目录、独立的子进程**里：按数据里的时间锚点（"March-15-2024"）冻结时钟，
  一问一答调用一次 NexSandglassProvider.sync_turn(用户, 助手) —— 与 Hermes 写入的是同一条路径。
  用户消息末尾数据集自带的计划编号（"->-> 1,1"）去掉，正文不动。
  全部写完后时钟拨到最后一个时间锚点的次日，对每道题调 orchestrator.recall，把返回的记忆映射回消息编号。

指标（证据召回，零 LLM、可复现）
  recall@k / hit@k / all@k   同 LoCoMo：前 k 条里找到的证据消息比例 / 至少一条 / 全部
  nyx_in_view                agent 实际看到的那些条（默认 12 条，汇总 / 时长 / 先后类 20 条）里证据找到了多少
  按题型、按对话长度报；「拒答」题没有证据（问的事对话里没有），不计入检索指标
  参照 BM25：同一批消息、同一个问题，最朴素的 BM25（locomo_eval.BM25）

如实说明
  - 这是检索，不是 BEAM 论文里的问答分数（那是大模型读上下文作答、再按评分要点打分）
  - 写入不调用大模型；语义检索按 NYX_EMBED 自动启用，没有嵌入后端时是词法 + 图 + 时间三路。--no-semantic 强制关闭

用法
  python3 benchmarks/beam_eval.py 100K.parquet 500K.parquet 1M.parquet --workers 4 --no-semantic \\
      --json beam.json [--views views.jsonl]
  1000 万档（BEAM-10M 的两个分片）同样传进来；文件名里的 100K / 500K / 1M / 10M 就是分档名。
  --views：每道题 Hermes 默认设置下注入给 agent 的那几条（provider._recall_view），给 beam_format_eval.py 比较写法用；
  里面有数据集原文，别提交进仓库。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from locomo_eval import BM25  # noqa: E402
from longmemeval_eval import VIEW_LINES, _keep, check_semantic, map_to_seqs  # noqa: E402

TYPES = ("abstention", "contradiction_resolution", "event_ordering", "information_extraction",
         "instruction_following", "knowledge_update", "multi_session_reasoning", "preference_following",
         "summarization", "temporal_reasoning")
SCALES = ("100K", "500K", "1M", "10M")
KS = (1, 5, 10, 20)
_MARK = re.compile(r"\s*->->\s*\d+\s*,\s*\d+\s*$")      # 数据集给用户消息加的计划编号


def parse_anchor(s):
    """BEAM 的时间锚点：「March-15-2024」。认不出返回 None（沿用上一个锚点）。"""
    s = str(s or "").strip()
    for fmt in ("%B-%d-%Y", "%b-%d-%Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(hour=12)
        except ValueError:
            continue
    return None


def messages(chat) -> list:
    """对话里的全部消息，按出现顺序。普通档是「会话列表 → 消息」，1000 万档是「计划（plan-1..plan-10）→ 批次 → 轮次 → 消息」。"""
    out = []

    def plan_no(k):
        m = re.search(r"(\d+)$", str(k))
        return int(m.group(1)) if m else 0

    def walk(x):
        if isinstance(x, dict):
            if "role" in x and "content" in x:
                out.append(x)
            elif "turns" in x:
                walk(x["turns"])
            else:
                for k in sorted((k for k in x if x[k] is not None), key=plan_no):
                    walk(x[k])
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)

    walk(chat)
    return out


def text_of(m: dict) -> str:
    s = str(m.get("content") or "")
    return _MARK.sub("", s).strip() if m.get("role") == "user" else s.strip()


def _ids(x) -> list:
    """source_chat_ids 有三种写法：编号列表；分角色的字典（矛盾：first_statement / second_statement，
    知识更新：original_info / updated_info，时间：first_event / second_event）；个别是嵌套列表。"""
    if isinstance(x, dict):
        return [i for v in x.values() for i in _ids(v)]
    if isinstance(x, (list, tuple)):
        return [i for v in x for i in _ids(v)]
    return [int(x)] if str(x).lstrip("-").isdigit() else []


def questions(conv: dict) -> list:
    """[{type, question, evidence, updated}]；probing_questions 在数据里是 Python 字面量字符串。
    updated：知识更新题里「更新后」的那几条（最新的值进没进 agent 的视野，单独看）。"""
    raw = conv.get("probing_questions")
    pq = ast.literal_eval(raw) if isinstance(raw, str) else (raw or {})
    out = []
    for t in TYPES:
        for q in pq.get(t) or []:
            src = q.get("source_chat_ids")
            ev = list(dict.fromkeys(_ids(src)))
            upd = list(dict.fromkeys(_ids(src.get("updated_info")))) if isinstance(src, dict) else []
            out.append({"type": t, "question": str(q.get("question") or ""), "evidence": ev, "updated": upd})
    return out


# ══════════════════════════════════════════════════════════
# 子进程：一段对话
# ══════════════════════════════════════════════════════════

def run_conversation(conv: dict, *, budget: int) -> dict:
    from nexsandglass.core import clock, memid
    from nexsandglass.core.memory_provider import NexSandglassProvider

    msgs = messages(conv["chat"])
    first = next((parse_anchor(m.get("time_anchor")) for m in msgs if parse_anchor(m.get("time_anchor"))), None)
    p = NexSandglassProvider({"prefetch_tokens": 0})
    clock.set_global(first)
    p.initialize("beam", agent_context="primary", platform="eval")
    conn = memid.get_conn()
    max_seq = lambda: conn.execute("SELECT COALESCE(MAX(seq), 0) FROM memories").fetchone()[0]

    seq_msg, ids, texts = {}, [], []
    last = first
    t0 = time.time()
    mp = os.path.join(os.environ.get("NEXSANDBASE_HOME", ""), "eval_map.json")
    reuse = os.environ.get("NYX_EVAL_REUSE") == "1" and os.path.exists(mp)
    if reuse:
        m = json.load(open(mp, encoding="utf-8"))
        seq_msg, ids, texts, last = ({int(k): v for k, v in m["seq_msg"].items()}, m["ids"], m["texts"],
                                     datetime.fromisoformat(m["last"]) if m.get("last") else None)

    pending = None                                   # 还没等到助手回复的用户消息

    def sync(user, assistant):
        before = max_seq()
        p.sync_turn(text_of(user) if user else "", text_of(assistant) if assistant else "", session_id="beam")
        for seq, sender in conn.execute("SELECT seq, sender FROM memories WHERE seq > ? ORDER BY seq", (before,)):
            src = assistant if sender == "agent" else user
            if src is not None:
                seq_msg[seq] = int(src["id"])

    for msg in ([] if reuse else msgs):
        when = parse_anchor(msg.get("time_anchor"))
        if when:
            clock.set_global(when)
            last = max(last, when) if last else when      # 1000 万档各计划各有时间线：提问时按最晚的那天
        ids.append(int(msg["id"]))
        texts.append(text_of(msg))
        if msg.get("role") == "user":
            if pending is not None:
                sync(pending, None)
            pending = msg
        else:
            sync(pending, msg)
            pending = None
    if pending is not None:
        sync(pending, None)

    from nexsandglass.core import sandglass_sqlite, semantic
    from nexsandglass.features import sandglass_vault
    if not reuse:
        sandglass_sqlite._last_sync_mtime = 0
        sandglass_sqlite.sync_all()
        sandglass_vault._idx_cache = None
        sandglass_vault.rebuild_index()
        semantic.wait_idle(3600)
        semantic.index_all()
        json.dump({"seq_msg": seq_msg, "ids": ids, "texts": texts, "last": last.isoformat() if last else None},
                  open(mp, "w", encoding="utf-8"))
    sem = check_semantic(semantic.stats())
    ingest_s = time.time() - t0

    bm25 = BM25(texts)
    clock.set_global((last or datetime.now()) + timedelta(days=1))
    from nexsandglass.runtime import intent as _intent
    from nexsandglass.runtime.orchestrator import get_orchestrator
    orch = get_orchestrator()
    from nexsandglass.core import relevance
    qs = []
    for i, q in enumerate(questions(conv)):
        t1 = time.time()
        mc = orch.recall(q["question"], token_budget=budget)
        ms = (time.time() - t1) * 1000
        ranked, unmapped = [], 0
        for seq in map_to_seqs(conn, mc.objects):
            d = seq_msg.get(seq) if seq is not None else None
            if d is None:
                unmapped += 1
            elif d not in ranked:
                ranked.append(d)
        # 注入前的那道门（v7.19.0，core/relevance.py）：agent 实际看到的是门后的这些
        gated = []
        for seq in map_to_seqs(conn, relevance.keep(q["question"], list(mc.objects or []))):
            d = seq_msg.get(seq) if seq is not None else None
            if d is not None and d not in gated:
                gated.append(d)
        base = []
        for j in bm25.rank(q["question"]):
            if ids[j] not in base:
                base.append(ids[j])
            if len(base) >= 50:
                break
        qs.append({"i": i, "type": q["type"], "evidence": q["evidence"], "updated": q["updated"],
                   "ranked": ranked[:50], "gated": gated[:50], "bm25": base,
                   "n_objects": len(mc.objects), "unmapped": unmapped, "recall_ms": round(ms, 1),
                   "view_n": max(VIEW_LINES, _intent.breadth_lines(q["question"]))})
    views = _views(p, conn, seq_msg, conv) if os.environ.get("NYX_EVAL_VIEWS") == "1" else None
    clock.set_global(None)
    nb = os.environ.get("NEXSANDBASE_HOME", "")
    for c in (conn,):
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    # 数据目录占用：不算评测自己的 eval_map.json 和 SQLite 的临时 WAL / SHM
    disk = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(nb) for f in fs
               if f != "eval_map.json" and not f.endswith(("-wal", "-shm"))) if nb else 0
    journal = os.path.join(nb, "sandglass.txt")
    return {"conversation_id": str(conv.get("conversation_id")), "n_messages": len(ids),
            "n_memories": max_seq(), "chars": sum(len(t) for t in texts), "ingest_s": round(ingest_s, 2),
            "disk_bytes": disk, "journal_bytes": os.path.getsize(journal) if os.path.exists(journal) else 0, "semantic": {k: sem.get(k) for k in ("backend", "indexed", "pending")},
            "questions": qs, **({"views": views} if views is not None else {})}


PREFETCH_TOKENS = 600          # Hermes 默认的 prefetch_tokens


def _views(p, conn, seq_msg: dict, conv: dict) -> list:
    """每道题 Hermes 默认设置下 agent 实际会看到的那几条（provider._recall_view，与 prefetch 同一条路径），
    给 beam_format_eval.py 比较注入写法。含数据集原文：只写到 --views 指定的文件，不进 --json。"""
    from nexsandglass.core import clock
    old = p._prefetch_budget
    p._prefetch_budget = PREFETCH_TOKENS
    out = []
    try:
        for i, q in enumerate(questions(conv)):
            items, objs, _ = p._recall_view(q["question"])
            seqs = map_to_seqs(conn, objs)
            out.append({"i": i, "date": f"{clock.now():%Y-%m-%d}", "items": [
                {"text": text, "date": date,
                 "line": int(o.source_id) if o.source_id and str(o.source_id).isdigit() else None,
                 "msg": seq_msg.get(seq) if seq is not None else None}
                for (text, date), o, seq in zip(items, objs, seqs)]})
    finally:
        p._prefetch_budget = old
    return out


def _worker(args) -> None:
    conv = json.load(open(args.instance_file, encoding="utf-8"))
    res = run_conversation(conv, budget=args.budget)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)


# ══════════════════════════════════════════════════════════
# 主进程：读数据、分发、汇总
# ══════════════════════════════════════════════════════════

def scale_of(path: str) -> str:
    base = os.path.basename(path).upper()
    for s in sorted(SCALES, key=len, reverse=True):
        if base.startswith(s.upper()):
            return s
    return "?"


def load(paths: list) -> list:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise SystemExit("读 BEAM 的 parquet 要 pyarrow：pip install pyarrow")
    out = []
    for path in paths:
        sc = scale_of(path)
        for row in pq.read_table(path).to_pylist():
            out.append((sc, row))
    return out


def _spawn(item, args, tmp: str) -> dict:
    scale, conv = item
    cid = re.sub(r"[^\w.-]", "_", f"{scale}-{conv.get('conversation_id')}")
    home = os.path.join(tmp, cid)
    os.makedirs(home, exist_ok=True)
    inf, outf = os.path.join(home, "conversation.json"), os.path.join(home, "result.json")
    if args.resume:
        if os.path.exists(outf) and not os.path.exists(inf):     # 上次已经跑完：直接用（1000 万档一段要半小时）
            res = json.load(open(outf, encoding="utf-8"))
            res.update(scale=scale, resumed=True)
            print(f"  {scale:>4} {res['conversation_id']:>6}: done before, {res['n_messages']} msgs", file=sys.stderr)
            return res
        shutil.rmtree(os.path.join(home, "nb"), ignore_errors=True)   # 没跑完的残局：从头写，不在半截的库上接着写
    keep = {k: conv.get(k) for k in ("conversation_id", "chat", "probing_questions")}
    json.dump(keep, open(inf, "w", encoding="utf-8"), ensure_ascii=False)
    env = dict(os.environ, NEXSANDBASE_HOME=os.path.join(home, "nb"),
               PYTHONPATH=_REPO + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.setdefault("PYTHONHASHSEED", "0")            # 逐题可复现（分数打平时的先后），同 locomo_eval
    for k in ("NYX_NOW", "HERMES_HOME", "NYX_UNDERSTAND_LLM"):
        env.pop(k, None)
    if args.no_semantic:
        env["NYX_EMBED"] = "off"
    if args.reuse:
        env["NYX_EVAL_REUSE"] = "1"
    if args.views:
        env["NYX_EVAL_VIEWS"] = "1"
    cmd = [sys.executable, os.path.abspath(__file__), "--worker", "--instance-file", inf, "--out", outf,
           "--budget", str(args.budget)]
    t0 = time.time()
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=args.timeout)
    os.remove(inf)                                   # 数据集原文不留在工作目录里（--work 只留 nyx 的库）
    if r.returncode != 0 or not os.path.exists(outf):
        return {"scale": scale, "conversation_id": conv.get("conversation_id"), "error": (r.stderr or r.stdout)[-2000:]}
    res = json.load(open(outf, encoding="utf-8"))
    res.update(scale=scale, wall_s=round(time.time() - t0, 1))
    if not args.work or args.resume:
        # 库用完就删：1000 万档一段就有半个 GB，等全部跑完再删会把盘撑满。--resume 只留 result.json
        shutil.rmtree(os.path.join(home, "nb"), ignore_errors=True)
    print(f"  {scale:>4} {res['conversation_id']:>6}: {res['n_messages']} msgs, ingest {res['ingest_s']:.0f}s",
          file=sys.stderr, flush=True)
    return res


def _metrics(rows: list, key: str) -> dict:
    out = {}
    for k in KS:
        top = [set(r[key][:k]) for r in rows]
        gold = [set(r["evidence"]) for r in rows]
        out[f"recall@{k}"] = round(sum(len(t & g) / len(g) for t, g in zip(top, gold)) / len(rows), 4)
        out[f"hit@{k}"] = round(sum(bool(t & g) for t, g in zip(top, gold)) / len(rows), 4)
        out[f"all@{k}"] = round(sum(g <= t for t, g in zip(top, gold)) / len(rows), 4)
    return out


def _pct(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))] if vals else None


def _group(rows: list) -> dict:
    upd = [r for r in rows if r.get("updated")]
    return {"n": len(rows), "nyx": _metrics(rows, "ranked"), "bm25": _metrics(rows, "bm25"),
            "nyx_in_view": round(sum(len(set(r["ranked"][:r["view_n"]]) & set(r["evidence"])) / len(r["evidence"])
                                     for r in rows) / len(rows), 4),
            # 经过注入前那道门之后（v7.19.0）
            "nyx_in_view_gated": (round(sum(len(set(r["gated"][:r["view_n"]]) & set(r["evidence"])) / len(r["evidence"])
                                            for r in rows if "gated" in r) / max(1, sum(1 for r in rows if "gated" in r)), 4)
                                  if any("gated" in r for r in rows) else None),
            # 知识更新题：更新后的那条进了 agent 的视野（新值看得见，才谈得上用新值）
            "updated_in_view": round(sum(bool(set(r["ranked"][:r["view_n"]]) & set(r["updated"])) for r in upd)
                                     / len(upd), 4) if upd else None,
            "recall_ms_p50": _pct([r["recall_ms"] for r in rows], 50),
            "recall_ms_p95": _pct([r["recall_ms"] for r in rows], 95)}


def score(results: list) -> dict:
    by_type, by_scale = defaultdict(list), defaultdict(list)
    skipped = Counter()
    for conv in results:
        if "error" in conv:
            continue
        for q in conv["questions"]:
            if not q["evidence"]:
                skipped[q["type"]] += 1
                continue
            by_type[q["type"]].append(q)
            by_scale[conv["scale"]].append(q)
            by_type["_overall"].append(q)
    out = {"by_type": {t: _group(rows) for t, rows in sorted(by_type.items())},
           "by_scale": {s: _group(by_scale[s]) for s in SCALES if by_scale.get(s)}}
    # 拒答题（问的事对话里没有，没有证据）：门后一条都不注入的比例（v7.19.0）
    abst = [q for c in results if "error" not in c for q in c["questions"] if q["type"] == "abstention" and "gated" in q]
    if abst:
        out["abstention_gate"] = {"n": len(abst), "nothing_injected": round(sum(1 for q in abst if not q["gated"]) / len(abst), 4)}
    ok = [c for c in results if "error" not in c]
    per_scale = {}
    for s in SCALES:
        cs = [c for c in ok if c["scale"] == s]
        if cs:
            per_scale[s] = {"conversations": len(cs), "messages_mean": round(sum(c["n_messages"] for c in cs) / len(cs)),
                            "chars_mean": round(sum(c["chars"] for c in cs) / len(cs)),
                            "ingest_s_mean": round(sum(c["ingest_s"] for c in cs) / len(cs), 1),
                            "ingest_ms_per_message": round(1000 * sum(c["ingest_s"] for c in cs)
                                                           / max(1, sum(c["n_messages"] for c in cs)), 2),
                            "disk_mb_mean": round(sum(c["disk_bytes"] for c in cs) / len(cs) / 1e6, 1),
                            "disk_per_journal_byte": round(sum(c["disk_bytes"] for c in cs)
                                                           / max(1, sum(c.get("journal_bytes", 0) for c in cs)), 1)}
    out["_meta"] = {"conversations": len(results), "errors": len(results) - len(ok),
                    "questions_scored": len(by_type.get("_overall", [])), "skipped_no_evidence": dict(skipped),
                    "per_scale": per_scale,
                    "unmapped_objects": sum(q["unmapped"] for c in ok for q in c["questions"]),
                    "semantic_backend": sorted({str((c.get("semantic") or {}).get("backend")) for c in ok})}
    return out


def _print(report: dict) -> None:
    head = (f"{'':26s} {'n':>5s}   {'nyx R@5':>8s} {'BM25 R@5':>8s}   {'nyx R@10':>8s} {'BM25 R@10':>9s}"
            f"   {'nyx hit@10':>10s}   {'in view':>7s} {'gated':>6s}   {'p50 ms':>7s}")
    for title, groups in (("by type", report["by_type"]), ("by scale", report["by_scale"])):
        print(f"\n{title}\n{head}")
        for g, row in groups.items():
            a, b = row["nyx"], row["bm25"]
            print(f"{g:26s} {row['n']:5d}   {a['recall@5']:8.3f} {b['recall@5']:8.3f}   {a['recall@10']:8.3f}"
                  f" {b['recall@10']:9.3f}   {a['hit@10']:10.3f}   {row['nyx_in_view']:7.3f}"
                  f" {row.get('nyx_in_view_gated') or 0:6.3f}   {row['recall_ms_p50']:7.1f}")
    if report.get("abstention_gate"):
        print("abstention (gate):", json.dumps(report["abstention_gate"]))
    print(json.dumps(report["_meta"], ensure_ascii=False))


def _write_views(path: str, items: list, results: list) -> None:
    by = {(sc, str(conv.get("conversation_id"))): conv for sc, conv in items}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            views = r.pop("views", None)
            if not views:
                continue
            conv = by.get((r["scale"], str(r["conversation_id"])))
            raw = conv.get("probing_questions")
            pq = ast.literal_eval(raw) if isinstance(raw, str) else (raw or {})
            flat = [(t, q) for t in TYPES for q in pq.get(t) or []]
            qs = questions(conv)
            for v in views:
                t, q = flat[v["i"]]
                f.write(json.dumps({"id": f"{r['scale']}-{r['conversation_id']}-{v['i']}", "scale": r["scale"],
                                    "type": t, "question": qs[v["i"]]["question"], "rubric": q.get("rubric") or [],
                                    "answer": q.get("answer") or q.get("ideal_answer") or q.get("ideal_response") or "",
                                    "evidence": qs[v["i"]]["evidence"], "date": v["date"], "items": v["items"]},
                                   ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("data", nargs="*", help="BEAM parquet 文件（100K / 500K / 1M / 10M）")
    ap.add_argument("--limit", type=int, default=0, help="每个文件只跑前 N 段对话（0 = 全部）")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--budget", type=int, default=1500, help="recall 的 token 预算（与 LoCoMo / LongMemEval 评测相同）")
    ap.add_argument("--timeout", type=int, default=6 * 3600, help="单段对话超时（秒）")
    ap.add_argument("--no-semantic", action="store_true", help="关掉语义检索（NYX_EMBED=off）")
    ap.add_argument("--json", default="", help="指标与逐题结果输出")
    ap.add_argument("--work", default="", help="每段对话的数据目录放这里并保留（默认用完即删）")
    ap.add_argument("--reuse", action="store_true", help="--work 里已经建好的目录直接用，只重跑召回")
    ap.add_argument("--resume", action="store_true", help="断点续跑：--work 里已经有结果的对话直接用，没跑完的从头跑；"
                                                          "跑完的只留 result.json")
    ap.add_argument("--views", default="", help="每道题 agent 会看到的那几条记忆（jsonl，含数据集原文，"
                                                 "给 beam_format_eval.py 用；别提交进仓库）")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--instance-file", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.worker:
        _worker(args)
        return 0
    if not args.data:
        ap.error("需要 BEAM 的 parquet 文件")
    if (args.reuse or args.resume) and not args.work:
        ap.error("--reuse / --resume 要和 --work 一起用")
    if args.reuse and args.resume:
        ap.error("--reuse 和 --resume 二选一")
    items = []
    for path in args.data:
        rows = load([path])
        items.extend(rows[: args.limit] if args.limit else rows)
    items.sort(key=lambda it: -len(json.dumps(it[1].get("chat"), ensure_ascii=False)))   # 长的先跑，免得最后一段拖着

    t0 = time.time()
    with (_keep(args.work) if args.work else tempfile.TemporaryDirectory(prefix="nyx-beam-")) as tmp:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(lambda it: _spawn(it, args, tmp), items))
    if args.views:
        _write_views(args.views, items, results)
    report = score(results)
    report["_meta"].update({"data": [os.path.basename(p) for p in args.data], "wall_s": round(time.time() - t0, 1),
                            "budget": args.budget, "workers": args.workers})
    try:
        from nexsandglass import __version__
        report["_meta"]["nyx_version"] = __version__
    except Exception:
        pass
    _print(report)
    for r in results:
        if "error" in r:
            print(f"[error] {r['scale']} {r['conversation_id']}: {r['error'][-800:]}", file=sys.stderr)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        json.dump({"scores": report, "results": results}, open(args.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    return 1 if report["_meta"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
