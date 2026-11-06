#!/usr/bin/env python3
"""PersonaMem 评测（v7.19.0）—— 长期相处之后，agent 能不能用上关于用户的信息

PersonaMem（Jiang et al., 2025, "Know Me, Respond to Me"）：20 个虚构用户和聊天机器人的多段对话，
用户的偏好会随时间改变。每道题是对话中途用户的一句话，四个回答里选最合适这个用户的那一个
（记得用户说过的事、知道偏好变了、按现在的偏好推荐……共 7 类）。
数据：https://huggingface.co/datasets/bowen-upenn/PersonaMem-v1（**MIT**）。仓库里不放数据，
运行时指向下载的 questions_32k.csv 和 shared_contexts_32k.jsonl。

做法
  第 1 步 视野（零 LLM）：每段对话在独立的 nyx 目录、独立的子进程里，按顺序一问一答调用 sync_turn
    （Hermes 写入的同一条路径）。数据里的 system 消息是给生成器看的人物设定，真实的 agent 没有它：不写入，
    只当作「换了一次话题 / 过了一天」。写到某道题的位置时，开一个新会话，取 system_prompt_block 和这句话的
    prefetch —— agent 实际看到的记忆。存进 --views（含数据集原文，别提交进仓库）。
  第 2 步 作答（--llm）：作答模型只读这段记忆 + 用户这句话 + 四个选项，回一个字母。
    对照组 none：同一个模型、不给记忆。模型走 OpenAI 兼容接口（qa_eval.py 的 NYX_EVAL_API_URL /
    NYX_EVAL_MODEL），本地 llama.cpp server 即可；temperature 0，按请求哈希缓存。
  零 LLM 参照 proxy：选项里「只有它有」的词，有多少出现在记忆里；最多的那个算选中（打平按随机算期望）。

如实说明
  - 论文和别的项目报的是大模型读全文 / 读自己的记忆后的准确率，模型不同，数字不能直接比；
    这里比的是同一个模型「给 nyx 的记忆」和「不给」、以及 nyx 两个版本之间的差别。
  - 结果文件只存题号、题型和对错，不存数据集原文和模型答案。

用法
  python3 benchmarks/personamem_eval.py questions_32k.csv shared_contexts_32k.jsonl --views views.jsonl
  NYX_EVAL_API_URL=http://127.0.0.1:8080/v1/chat/completions NYX_EVAL_MODEL=qwen \\
    python3 benchmarks/personamem_eval.py --answer views.jsonl --llm --cache cache.jsonl --json out.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)

START = datetime(2026, 1, 5, 9, 0)
_PREFIX = re.compile(r"^\s*(user|assistant)\s*:\s*", re.I)
_OPT = re.compile(r"^\s*\(([a-d])\)\s*")


def load_questions(path: str) -> list:
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    for r in rows:
        r["end_index_in_shared_context"] = int(r["end_index_in_shared_context"])
        opts = r["all_options"]
        try:
            r["options"] = json.loads(opts)
        except Exception:
            import ast
            r["options"] = ast.literal_eval(opts)
    return rows


def load_contexts(path: str) -> dict:
    ctx = {}
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                ctx.update(json.loads(ln))
    return ctx


def strip_role(text: str) -> str:
    return _PREFIX.sub("", str(text or ""), count=1).strip()


# ══════════════════════════════════════════════════════════
# 第 1 步：子进程，一段对话
# ══════════════════════════════════════════════════════════

def run_context(messages: list, questions: list) -> list:
    from nexsandglass.core import clock
    from nexsandglass.core.memory_provider import NexSandglassProvider

    when = START
    clock.set_global(when)
    p = NexSandglassProvider()
    p.initialize("history", agent_context="primary", platform="eval")
    out, done, pending = [], 0, None

    def sync(user, assistant):
        p.sync_turn(user or "", assistant or "", session_id="history")

    def flush():
        nonlocal pending
        if pending is not None:
            sync(pending, None)
            pending = None

    for q in sorted(questions, key=lambda r: r["end_index_in_shared_context"]):
        end = q["end_index_in_shared_context"]
        while done < min(end, len(messages)):
            m = messages[done]
            done += 1
            role = m.get("role")
            if role == "system":                      # 换话题：算过了一天
                flush()
                when = when.replace(hour=9, minute=0) + timedelta(days=1)
                clock.set_global(when)
                continue
            when += timedelta(minutes=2)
            clock.set_global(when)
            text = strip_role(m.get("content"))
            if role == "user":
                flush()
                pending = text
            else:
                sync(pending, text)
                pending = None
        flush()
        clock.set_global(when + timedelta(minutes=1))
        s = NexSandglassProvider()
        s.initialize("q", agent_context="primary", platform="eval")
        warm = getattr(s, "_warm_thread", None)
        if warm is not None:
            warm.join(60)
        block = s.system_prompt_block()
        injected = s.prefetch(q["user_question_or_message"], session_id="q")
        out.append({"question_id": q["question_id"], "type": q["question_type"], "block": block,
                    "injected": injected, "end": end})
    clock.set_global(None)
    return out


def _worker(args) -> None:
    job = json.load(open(args.job, encoding="utf-8"))
    res = run_context(job["messages"], job["questions"])
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)


def _spawn(cid: str, messages: list, questions: list, args, tmp: str) -> list:
    home = os.path.join(tmp, re.sub(r"[^\w.-]", "_", cid)[:80])
    os.makedirs(home, exist_ok=True)
    job, out = os.path.join(home, "job.json"), os.path.join(home, "out.json")
    json.dump({"messages": messages, "questions": questions}, open(job, "w", encoding="utf-8"), ensure_ascii=False)
    env = dict(os.environ, NEXSANDBASE_HOME=os.path.join(home, "nb"),
               PYTHONPATH=_REPO + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.setdefault("PYTHONHASHSEED", "0")
    for k in ("NYX_NOW", "HERMES_HOME", "NYX_UNDERSTAND_LLM", "WTHREAD_LLM_EXTRACTION"):
        env.pop(k, None)
    if not args.semantic:
        env["NYX_EMBED"] = "off"
    cmd = [sys.executable, os.path.abspath(__file__), "--worker", "--job", job, "--out", out]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=args.timeout)
    try:
        if r.returncode != 0 or not os.path.exists(out):
            print(f"[{cid}] ERROR\n{(r.stderr or r.stdout)[-2000:]}", file=sys.stderr)
            return []
        return json.load(open(out, encoding="utf-8"))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def views(args) -> None:
    qs = load_questions(args.questions)
    ctx = load_contexts(args.contexts)
    by_ctx = defaultdict(list)
    for q in qs:
        by_ctx[q["shared_context_id"]].append(q)
    tmp = tempfile.mkdtemp(prefix="nyx-pmem-")
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            parts = list(ex.map(lambda c: _spawn(c, ctx[c], by_ctx[c], args, tmp), sorted(by_ctx)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rows = [v for part in parts for v in part]
    with open(args.views, "w", encoding="utf-8") as f:
        for v in rows:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    print(f"views: {len(rows)}/{len(qs)} questions, {time.time() - t0:.0f}s", file=sys.stderr)


# ══════════════════════════════════════════════════════════
# 第 2 步：作答
# ══════════════════════════════════════════════════════════

_SYSTEM = "You are a personal assistant with long-term memory of past conversations with the user."
_USER = ("{memory}\n\nThe user now says: \"{message}\"\n\n"
         "Which of these responses is best for this particular user, given what you remember about them?\n"
         "{options}\n\nAnswer with the letter of one option only, for example (b).")
_NONE = "(no memories recalled)"


def memory_text(view: dict, cond: str) -> str:
    parts = []
    if cond in ("nyx", "block") and view.get("block"):
        parts.append(view["block"])
    if cond in ("nyx", "turn") and view.get("injected"):
        parts.append(view["injected"])
    return "\n\n".join(parts) or _NONE


def parse_letter(text: str) -> str:
    m = re.search(r"\(([a-d])\)", text or "", re.I) or re.search(r"\b([a-d])\b", text or "", re.I)
    return m.group(1).lower() if m else ""


def _words(s: str) -> set:
    from excerpt_variant import _EN_STOP, _stem
    return {_stem(w) for w in re.findall(r"[a-z0-9']+", (s or "").lower()) if w not in _EN_STOP and len(w) > 2}


def proxy_expected(options: list, correct: str, memory: str) -> float:
    """零 LLM 参照：选项独有的词在记忆里出现的比例最高的那个；打平按随机选的期望算。"""
    toks = [_words(_OPT.sub("", o)) for o in options]
    mem = _words(memory)
    scores = []
    for i, t in enumerate(toks):
        others = set().union(*[x for j, x in enumerate(toks) if j != i])
        own = t - others
        scores.append(len(own & mem) / len(own) if own else 0.0)
    best = max(scores)
    tied = [i for i, s in enumerate(scores) if s == best]
    letters = [_OPT.match(o).group(1) if _OPT.match(o) else "abcd"[i] for i, o in enumerate(options)]
    return (1.0 / len(tied)) if correct in [letters[i] for i in tied] else 0.0


def _sign_p(w: int, l: int) -> float:
    n = w + l
    if n == 0:
        return 1.0
    k = min(w, l)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def answer(args) -> int:
    qs = {q["question_id"]: q for q in load_questions(args.questions)} if args.questions else {}
    vs = [json.loads(l) for l in open(args.answer, encoding="utf-8") if l.strip()]
    vs.sort(key=lambda v: v["question_id"])
    if args.limit:                                   # 均匀抽一部分（开发时快速看方向）
        vs = vs[::max(1, len(vs) // args.limit)][: args.limit]
    if not qs:
        raise SystemExit("--answer 也要传 questions_32k.csv（取选项和正确答案）")
    conds = args.conditions
    res = defaultdict(dict)                         # question_id -> cond -> 0/1
    proxy = defaultdict(dict)
    for v in vs:
        q = qs[v["question_id"]]
        correct = parse_letter(q["correct_answer"])
        for c in conds:
            proxy[v["question_id"]][c] = proxy_expected(q["options"], correct, memory_text(v, c) if c != "none" else "")
    if args.llm:
        from qa_eval import LLM, endpoint
        ep = endpoint("eval")
        llm = LLM(args.cache)
        jobs = [(v, c) for v in vs for c in conds]

        def one(job):
            v, c = job
            q = qs[v["question_id"]]
            msgs = [{"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": _USER.format(memory=memory_text(v, c),
                                                             message=q["user_question_or_message"],
                                                             options="\n".join(q["options"]))}]
            out = llm.chat(ep, msgs, args.max_tokens, "answer")
            return v["question_id"], c, int(parse_letter(out) == parse_letter(q["correct_answer"]))

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for i, (qid, c, ok) in enumerate(ex.map(one, jobs), 1):
                res[qid][c] = ok
                if i % 100 == 0:
                    print(f"  {i}/{len(jobs)} answered, {time.time() - t0:.0f}s", file=sys.stderr, flush=True)

    types = defaultdict(list)
    for v in vs:
        types[v["type"]].append(v["question_id"])
    report = {"n": len(vs), "conditions": conds, "proxy": {}, "llm": {}, "by_type": {}}
    for c in conds:
        report["proxy"][c] = round(sum(proxy[q][c] for q in proxy) / len(proxy), 3)
        if args.llm:
            got = [res[q][c] for q in res if c in res[q]]
            report["llm"][c] = round(sum(got) / len(got), 3) if got else None
    if args.llm and "none" in conds:
        for c in conds:
            if c == "none":
                continue
            w = sum(1 for q in res if res[q].get(c) == 1 and res[q].get("none") == 0)
            l = sum(1 for q in res if res[q].get(c) == 0 and res[q].get("none") == 1)
            report["llm"][f"{c}_vs_none"] = {"wins": w, "losses": l, "p": round(_sign_p(w, l), 4)}
    for t, ids in sorted(types.items()):
        row = {"n": len(ids)}
        for c in conds:
            row[f"proxy_{c}"] = round(sum(proxy[q][c] for q in ids) / len(ids), 3)
            if args.llm:
                got = [res[q][c] for q in ids if c in res[q]]
                row[f"llm_{c}"] = round(sum(got) / len(got), 3) if got else None
        report["by_type"][t] = row
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json:
        detail = {q: {"type": next(v["type"] for v in vs if v["question_id"] == q), **res.get(q, {})} for q in proxy}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"report": report, "questions": detail}, f, ensure_ascii=False, indent=1)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("questions", nargs="?")
    ap.add_argument("contexts", nargs="?")
    ap.add_argument("--views", help="第 1 步：每道题 agent 看到的记忆存到这里（含数据集原文）")
    ap.add_argument("--answer", help="第 2 步：读这个 views 文件作答")
    ap.add_argument("--conditions", nargs="+", default=["none", "nyx"],
                    help="none 不给记忆 / nyx 常驻块 + 这一轮注入 / block 只给常驻块 / turn 只给这一轮注入")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--cache")
    ap.add_argument("--max-tokens", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--semantic", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--json")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--job", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.worker:
        _worker(args)
        return 0
    if args.answer:
        return answer(args)
    if not (args.questions and args.contexts and args.views):
        ap.error("第 1 步要 questions.csv、shared_contexts.jsonl 和 --views")
    views(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
