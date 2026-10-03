#!/usr/bin/env python3
"""LongMemEval 评测框架（v7.12）—— 走 Hermes 插件的真实路径

LongMemEval（Wu et al., ICLR 2025, MIT）是 Hindsight / Zep / Mem0 等报分用的公开基准：
每题一段带时间戳的多会话聊天历史（haystack），问一个需要长期记忆才能答的问题。
数据：https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
  longmemeval_oracle.json / longmemeval_s_cleaned.json（约 40 会话/题）/ longmemeval_m_cleaned.json

每道题在**独立的 nyx 目录、独立的子进程**里跑（nexsandglass 的数据目录在 import 时定死）：
  1. 按 haystack_dates 冻结时钟，逐会话、逐轮调用 NexSandglassProvider.sync_turn ——
     与 Hermes MemoryManager 写入的是同一条路径（来源绑定、晋升门、事实抽取全在）
  2. 冻结到 question_date，调用 orchestrator.recall（检索指标）与 provider.prefetch（QA 上下文）

两种模式
  检索（默认，零 LLM）：会话级 recall_any@k / recall_all@k（官方定义，abstention 题不计），
      以及轮次级 has_answer 命中率。完全离线、可复现。
  QA（--qa）：用 prefetch 注入的上下文 + 问题调一个 OpenAI 兼容网关生成答案，写出官方格式的
      假设文件（question_id / hypothesis），再用**官方** evaluate_qa.py 打分：
        python3 src/evaluation/evaluate_qa.py gpt-4o 假设文件 data/longmemeval_s_cleaned.json
      不内置裁判：裁判模型与提示词不同，分数就不能和别人的榜单比。

语义检索（v7.13）：按 NYX_EMBED / EMBEDDING_API_URL 自动启用（见 core/semantic.py）；
  加 --no-semantic 跑一遍关闭时的分数，两者之差就是语义这一路的贡献。

用法
  pip install 'nyx-memory[vector]'      # 本地多语言嵌入模型（首次运行下载约 470MB）
  python3 benchmarks/longmemeval_eval.py data/longmemeval_s_cleaned.json --workers 8 \
      --json benchmarks/results/longmemeval_s_retrieval.json
  NYX_EVAL_API_URL=https://…/v1/chat/completions NYX_EVAL_API_KEY=… NYX_EVAL_MODEL=… \
  python3 benchmarks/longmemeval_eval.py data/longmemeval_s_cleaned.json --qa --hyp hyp.jsonl

如实说明
  - 这个环境连不到 HuggingFace，仓库里没有真实数据上的分数；tests/ 里用的是同格式的小样本
  - nyx 的规则抽取以中文为主；LongMemEval 是英文。英文上召回主要靠全文检索与倒排索引
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\D+(\d{1,2}):(\d{2}))?")
_LABEL = re.compile(r"^（未经证实·来源:[^）]*）")


def parse_date(s: str) -> datetime:
    """LongMemEval 的「2023/05/20 (Sat) 02:21」。"""
    m = _DATE.search(str(s or ""))
    if not m:
        raise ValueError(f"无法解析日期: {s!r}")
    y, mo, d, h, mi = m.groups()
    return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0))


def _pairs(turns: list) -> list:
    """会话 → [(user, assistant, user_has_answer, asst_has_answer)]；不成对的轮次单独成对。"""
    out, pending = [], None
    for t in turns or []:
        role, text = t.get("role"), str(t.get("content") or "")
        if role == "user":
            if pending is not None:
                out.append((pending[0], "", pending[1], False))
            pending = (text, bool(t.get("has_answer")))
        else:
            if pending is None:
                out.append(("", text, False, bool(t.get("has_answer"))))
            else:
                out.append((pending[0], text, pending[1], bool(t.get("has_answer"))))
                pending = None
    if pending is not None:
        out.append((pending[0], "", pending[1], False))
    return out


# ══════════════════════════════════════════════════════════
# 子进程：一道题
# ══════════════════════════════════════════════════════════

def run_instance(inst: dict, *, budget: int, prefetch_tokens: int, want_context: bool) -> dict:
    from nexsandglass.core import clock, memid
    from nexsandglass.core.memory_provider import NexSandglassProvider

    p = NexSandglassProvider({"prefetch_tokens": prefetch_tokens})
    clock.set_global(parse_date(inst["haystack_dates"][0]) if inst["haystack_dates"] else None)
    p.initialize("longmemeval", agent_context="primary", platform="eval")
    conn = memid.get_conn()
    max_seq = lambda: conn.execute("SELECT COALESCE(MAX(seq), 0) FROM memories").fetchone()[0]

    sessions = sorted(zip(inst["haystack_session_ids"], inst["haystack_dates"], inst["haystack_sessions"]),
                      key=lambda x: parse_date(x[1]))
    seq_session, answer_seqs, n_turns = {}, set(), 0
    t0 = time.time()
    for sid, date, turns in sessions:
        clock.set_global(parse_date(date))
        for u, a, u_ans, a_ans in _pairs(turns):
            before = max_seq()
            p.sync_turn(u, a, session_id=str(sid))
            n_turns += 1
            rows = conn.execute("SELECT seq, sender FROM memories WHERE seq > ? ORDER BY seq",
                                (before,)).fetchall()
            for seq, sender in rows:
                seq_session[seq] = sid
                if (sender == "agent" and a_ans) or (sender != "agent" and u_ans):
                    answer_seqs.add(seq)
    from nexsandglass.core import sandglass_sqlite
    from nexsandglass.features import sandglass_vault
    sandglass_sqlite._last_sync_mtime = 0
    sandglass_sqlite.sync_all()
    sandglass_vault._idx_cache = None
    sandglass_vault.rebuild_index()
    from nexsandglass.core import semantic
    semantic.wait_idle(600)                  # 后台补索引跑完
    semantic.index_all()                     # 兜底，保证全部入索引
    sem = semantic.stats()
    ingest_s = time.time() - t0

    clock.set_global(parse_date(inst["question_date"]))
    from nexsandglass.runtime.orchestrator import get_orchestrator
    t1 = time.time()
    mc = get_orchestrator().recall(inst["question"], token_budget=budget)
    recall_ms = (time.time() - t1) * 1000

    by_line = dict(conn.execute("SELECT line_start, seq FROM memories WHERE deleted_at IS NULL"))
    by_text = {}
    for seq, text in conn.execute("SELECT seq, text FROM memories WHERE deleted_at IS NULL"):
        by_text.setdefault(text.strip()[:200], seq)
    ranked_sessions, ranked_answer_hits, unmapped = [], [], 0
    for o in mc.objects:
        seq = None
        sid = getattr(o, "source_id", None)
        if sid is not None and str(sid).isdigit():
            seq = by_line.get(int(sid))
        if seq is None:
            seq = by_text.get(_LABEL.sub("", str(o.content or "")).strip()[:200])
        if seq is None or seq not in seq_session:
            unmapped += 1
            continue
        s = seq_session[seq]
        if s not in ranked_sessions:
            ranked_sessions.append(s)
        ranked_answer_hits.append(seq in answer_seqs)

    out = {
        "question_id": inst["question_id"], "question_type": inst.get("question_type", "?"),
        "abstention": str(inst["question_id"]).endswith("_abs"),
        "answer_session_ids": inst.get("answer_session_ids", []),
        "ranked_sessions": ranked_sessions[:50], "answer_turn_hits": ranked_answer_hits[:50],
        "n_answer_turns": len(answer_seqs), "n_objects": len(mc.objects), "unmapped": unmapped,
        "n_sessions": len(sessions), "n_turns": n_turns, "n_memories": max_seq(),
        "ingest_s": round(ingest_s, 3), "recall_ms": round(recall_ms, 1),
        "semantic": {k: sem.get(k) for k in ("backend", "indexed", "pending")},
    }
    if want_context:
        t2 = time.time()
        out["context"] = p.prefetch(inst["question"], session_id="q")
        out["prefetch_ms"] = round((time.time() - t2) * 1000, 1)
    clock.set_global(None)
    return out


def _worker(args) -> None:
    inst = json.load(open(args.instance_file, encoding="utf-8"))
    res = run_instance(inst, budget=args.budget, prefetch_tokens=args.prefetch_tokens,
                       want_context=args.qa)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)


# ══════════════════════════════════════════════════════════
# 主进程：分发 + 汇总
# ══════════════════════════════════════════════════════════

def _spawn(inst: dict, args, tmp: str) -> dict:
    qid = re.sub(r"[^\w.-]", "_", str(inst["question_id"]))
    home = os.path.join(tmp, qid)
    os.makedirs(home, exist_ok=True)
    inf, outf = os.path.join(home, "instance.json"), os.path.join(home, "result.json")
    json.dump(inst, open(inf, "w", encoding="utf-8"), ensure_ascii=False)
    env = dict(os.environ, NEXSANDBASE_HOME=os.path.join(home, "nb"),
               PYTHONPATH=_REPO + os.pathsep + os.environ.get("PYTHONPATH", ""))
    for k in ("NYX_NOW", "HERMES_HOME"):
        env.pop(k, None)
    if not args.understand_llm:
        env.pop("NYX_UNDERSTAND_LLM", None)
    if args.no_semantic:
        env["NYX_EMBED"] = "off"
    cmd = [sys.executable, os.path.abspath(__file__), "--worker", "--instance-file", inf, "--out", outf,
           "--budget", str(args.budget), "--prefetch-tokens", str(args.prefetch_tokens)]
    if args.qa:
        cmd.append("--qa")
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=args.timeout)
    if r.returncode != 0 or not os.path.exists(outf):
        return {"question_id": inst["question_id"], "question_type": inst.get("question_type", "?"),
                "error": (r.stderr or r.stdout)[-2000:]}
    return json.load(open(outf, encoding="utf-8"))


def score(results: list, ks=(1, 3, 5, 10)) -> dict:
    """会话级 recall_any@k / recall_all@k（abstention 题按官方做法不计）+ 轮次级命中。"""
    groups = defaultdict(list)
    for r in results:
        if "error" in r or r.get("abstention"):
            continue
        groups[r["question_type"]].append(r)
        groups["_overall"].append(r)
    out = {}
    for g, rs in sorted(groups.items()):
        row = {"n": len(rs)}
        for k in ks:
            top = [set(r["ranked_sessions"][:k]) for r in rs]
            gold = [set(r["answer_session_ids"]) for r in rs]
            row[f"recall_any@{k}"] = round(sum(bool(t & g) for t, g in zip(top, gold)) / len(rs), 4)
            row[f"recall_all@{k}"] = round(sum(g <= t for t, g in zip(top, gold)) / len(rs), 4)
        with_turns = [r for r in rs if r["n_answer_turns"]]
        if with_turns:
            row["turn_hit@10"] = round(sum(any(r["answer_turn_hits"][:10]) for r in with_turns)
                                       / len(with_turns), 4)
        row["recall_ms_p50"] = sorted(r["recall_ms"] for r in rs)[len(rs) // 2]
        out[g] = row
    errs = [r for r in results if "error" in r]
    out["_meta"] = {"questions": len(results), "errors": len(errs),
                    "abstention_skipped": sum(1 for r in results if r.get("abstention")),
                    "ingest_s_total": round(sum(r.get("ingest_s", 0) for r in results), 1),
                    "unmapped_objects": sum(r.get("unmapped", 0) for r in results)}
    return out


_QA_SYSTEM = "You are a personal assistant with long-term memory of past conversations with the user."
_QA_USER = ("{context}\n\nCurrent date: {date}\nQuestion: {question}\n"
            "Answer the question using the memories above (dates in [brackets] are when each was said). "
            "Be concise. If the memories do not contain the answer, say you don't know.")


def generate(context: str, question: str, date: str) -> str:
    import urllib.request
    url = os.environ["NYX_EVAL_API_URL"]
    body = {"model": os.environ.get("NYX_EVAL_MODEL", "gpt-4o"), "temperature": 0,
            "messages": [{"role": "system", "content": _QA_SYSTEM},
                         {"role": "user", "content": _QA_USER.format(
                             context=context or "(no memories recalled)", date=date, question=question)}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ.get("NYX_EVAL_API_KEY", "")})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)["choices"][0]["message"]["content"].strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("data", nargs="?", help="longmemeval_*.json")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（0 = 全部）")
    ap.add_argument("--types", default="", help="逗号分隔的 question_type 过滤")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--budget", type=int, default=1500, help="recall 的 token 预算（检索指标）")
    ap.add_argument("--prefetch-tokens", type=int, default=2000, help="QA 上下文的 prefetch 预算")
    ap.add_argument("--timeout", type=int, default=1800, help="单题超时（秒）")
    ap.add_argument("--understand-llm", action="store_true", help="保留 NYX_UNDERSTAND_LLM（默认关）")
    ap.add_argument("--no-semantic", action="store_true",
                    help="关掉语义检索（NYX_EMBED=off），用来和开启时对比")
    ap.add_argument("--qa", action="store_true", help="生成答案（需要 NYX_EVAL_API_URL）")
    ap.add_argument("--hyp", default="", help="QA 假设文件输出（官方 jsonl 格式）")
    ap.add_argument("--json", default="", help="检索指标与逐题结果输出")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--instance-file", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.worker:
        _worker(args)
        return 0
    if not args.data:
        ap.error("需要数据文件路径")
    if args.qa and not os.environ.get("NYX_EVAL_API_URL"):
        ap.error("--qa 需要 NYX_EVAL_API_URL（OpenAI 兼容 chat/completions 地址）")

    data = json.load(open(args.data, encoding="utf-8"))
    if args.types:
        keep = {t.strip() for t in args.types.split(",") if t.strip()}
        data = [d for d in data if d.get("question_type") in keep]
    if args.limit:
        data = data[: args.limit]

    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="nyx-lme-") as tmp:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(lambda d: _spawn(d, args, tmp), data))
    report = score(results)
    backends = sorted({str((r.get("semantic") or {}).get("backend")) for r in results if "error" not in r})
    report["_meta"].update({"data": os.path.basename(args.data), "wall_s": round(time.time() - t0, 1),
                            "budget": args.budget, "workers": args.workers, "semantic_backend": backends})
    try:
        from nexsandglass import __version__
        report["_meta"]["nyx_version"] = __version__
    except Exception:
        pass

    for g, row in report.items():
        if g != "_meta":
            print(f"{g:28s} n={row['n']:4d}  any@5={row['recall_any@5']:.3f}  all@5={row['recall_all@5']:.3f}"
                  f"  any@10={row['recall_any@10']:.3f}  all@10={row['recall_all@10']:.3f}"
                  f"  p50={row['recall_ms_p50']}ms")
    print(json.dumps(report["_meta"], ensure_ascii=False))
    for r in results:
        if "error" in r:
            print(f"[error] {r['question_id']}: {r['error'][-300:]}", file=sys.stderr)

    if args.qa:
        by_id = {d["question_id"]: d for d in data}
        hyp_path = args.hyp or "longmemeval_hyp.jsonl"
        with open(hyp_path, "w", encoding="utf-8") as f:
            for r in results:
                d = by_id[r["question_id"]]
                try:
                    h = generate(r.get("context", ""), d["question"], d["question_date"]) \
                        if "error" not in r else ""
                except Exception as e:
                    h, r["qa_error"] = "", str(e)
                f.write(json.dumps({"question_id": r["question_id"], "hypothesis": h},
                                   ensure_ascii=False) + "\n")
        print(f"假设文件：{hyp_path}\n官方打分：python3 src/evaluation/evaluate_qa.py gpt-4o "
              f"{hyp_path} {args.data}")

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        json.dump({"scores": report, "results": [{k: v for k, v in r.items() if k != "context"}
                                                 for r in results]},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 1 if report["_meta"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
