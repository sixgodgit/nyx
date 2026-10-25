#!/usr/bin/env python3
"""端到端问答评测（v7.14.0）—— 大模型只读 nyx 注入的那段记忆来作答，再由大模型裁判判对错

Mem0 / MemOS / Memori / OpenViking 报的 LongMemEval、LoCoMo 分数都是这个口径（问答准确率），
nyx 以前只报检索指标，在任何对比表里都缺席。这里按同一个流程跑：

  1. 上下文（零 LLM）：复用 longmemeval_eval.py / locomo_eval.py 的真实写入路径（Hermes 插件 sync_turn），
     对每道题取 **Hermes 默认设置下真正注入给 agent 的那段文字**（provider.prefetch，prefetch_tokens=600：
     12 条，问「一共几次 / 隔了多久 / 哪个先」时 20 条，带说话日期）。不是把召回结果另外拼一遍
  2. 作答：固定的作答模型只看这段上下文 + 问题 + 当前日期（OpenAI 兼容 chat/completions，temperature 0）
  3. 判分：
       LongMemEval —— **官方**裁判提示词，逐字照搬 LongMemEval 仓库 src/evaluation/evaluate_qa.py（MIT），
                      按题型选模板，`_abs` 题用拒答模板；回复里含 "yes" 即算对（与官方一致）
       LoCoMo     —— 没有官方裁判提示词（Mem0 的评测脚本已不在其仓库里）。用 LongMemEval 官方的通用模板，
                      时间类题用它的时间模板（差一天不扣分）。比 Mem0 那种「宽松裁判」严：
                      第三方审计发现宽松裁判会接受多达 63% 故意给错的答案
                      （https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer）
                      LoCoMo 第 5 类（对抗题）按惯例不计

对照组（--conditions）：
  nyx   nyx 注入的上下文（默认）
  none  什么记忆都不给 —— 量出「问题本身能猜对多少」
  full  整段聊天历史全部塞进去 —— 长上下文的上限参照（LongMemEval-S 每题约 11.5 万 token，花钱，默认不跑）

模型（环境变量；key 只从环境读，不写进任何文件）：
  NYX_EVAL_API_URL / NYX_EVAL_API_KEY / NYX_EVAL_MODEL      作答
  NYX_JUDGE_API_URL / NYX_JUDGE_API_KEY / NYX_JUDGE_MODEL   裁判（不设就用作答那一套）
  只设了 DEEPSEEK_API_KEY 时默认 https://api.deepseek.com/chat/completions + deepseek-chat；
  只设了 OPENROUTER_API_KEY 时默认 https://openrouter.ai/api/v1/chat/completions（模型要自己指定）
  LongMemEval 官方裁判是 gpt-4o；换了裁判模型，报告里会写明，分数只能和同一个裁判比

断点续跑：每次调用模型的结果按（地址、模型、消息）的哈希存进 --cache，中断后重跑只补没跑完的。

用法
  python3 benchmarks/qa_eval.py longmemeval data/longmemeval_s_cleaned.json --workers 4 \\
      --out benchmarks/results/longmemeval_s_qa.json
  python3 benchmarks/qa_eval.py locomo data/locomo10.json --workers 4 --out benchmarks/results/locomo_qa.json
  --contexts-only   只做第 1 步（零 LLM、不联网），把上下文写到 --contexts
  --limit N         只跑前 N 题（LongMemEval）/ 前 N 段对话（LoCoMo）

结果文件只存题号、题型、对错和模型答案（LoCoMo 是 CC BY-NC 数据，不存题目与标准答案原文）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# ── 官方裁判提示词：LongMemEval src/evaluation/evaluate_qa.py，get_anscheck_prompt（MIT），逐字 ─────────
_J_DEFAULT = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the "
              "response contains the correct answer. Otherwise, answer no. If the response is equivalent to the "
              "correct answer or contains all the intermediate steps to get the correct answer, you should also "
              "answer yes. If the response only contains a subset of the information required by the answer, "
              "answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response "
              "correct? Answer yes or no only.")
_J_TEMPORAL = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the "
               "response contains the correct answer. Otherwise, answer no. If the response is equivalent to the "
               "correct answer or contains all the intermediate steps to get the correct answer, you should also "
               "answer yes. If the response only contains a subset of the information required by the answer, "
               "answer no. In addition, do not penalize off-by-one errors for the number of days. If the question "
               "asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., "
               "predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: "
               "{}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no "
               "only.")
_J_UPDATE = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the "
             "response contains the correct answer. Otherwise, answer no. If the response contains some previous "
             "information along with an updated answer, the response should be considered as correct as long as "
             "the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: "
             "{}\n\nIs the model response correct? Answer yes or no only.")
_J_PREF = ("I will give you a question, a rubric for desired personalized response, and a response from a model. "
           "Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does "
           "not need to reflect all the points in the rubric. The response is correct as long as it recalls and "
           "utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: "
           "{}\n\nIs the model response correct? Answer yes or no only.")
_J_ABS = ("I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes "
          "if the model correctly identifies the question as unanswerable. The model could say that the information "
          "is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\n"
          "Explanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? "
          "Answer yes or no only.")


def judge_prompt(dataset: str, qtype: str, question: str, answer: str, response: str, abstention: bool) -> str:
    if abstention:
        t = _J_ABS
    elif dataset == "locomo":
        t = _J_TEMPORAL if qtype == "temporal" else _J_DEFAULT
    elif qtype in ("single-session-user", "single-session-assistant", "multi-session"):
        t = _J_DEFAULT
    elif qtype == "temporal-reasoning":
        t = _J_TEMPORAL
    elif qtype == "knowledge-update":
        t = _J_UPDATE
    elif qtype == "single-session-preference":
        t = _J_PREF
    else:
        raise NotImplementedError(qtype)
    return t.format(question, answer, response)


def judged_correct(reply: str) -> bool:
    return "yes" in (reply or "").strip().lower()          # 官方就是这么判的（子串）


# ── 作答 ──────────────────────────────────────────────────

_SYSTEM = "You are a personal assistant with long-term memory of past conversations with the user."
_USER = ("{context}\n\nCurrent date: {date}\nQuestion: {question}\n"
         "Answer the question using the memories above (dates in [brackets] are when each was said). "
         "Be concise. If the memories do not contain the answer, say you don't know.")
_NONE = "(no memories recalled)"


def answer_messages(context: str, question: str, date: str) -> list:
    return [{"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _USER.format(context=context or _NONE, date=date, question=question)}]


# ── 模型调用：OpenAI 兼容、重试、缓存 ───────────────────────────

def endpoint(kind: str) -> dict:
    """kind = eval / judge。返回 {url, key, model}；key 只在内存里。"""
    pre = "NYX_JUDGE_" if kind == "judge" else "NYX_EVAL_"
    url, key, model = (os.environ.get(pre + "API_URL"), os.environ.get(pre + "API_KEY"), os.environ.get(pre + "MODEL"))
    if kind == "judge" and not (url or key or model):
        return endpoint("eval")
    if not url:
        if os.environ.get("DEEPSEEK_API_KEY"):
            url, key = "https://api.deepseek.com/chat/completions", key or os.environ["DEEPSEEK_API_KEY"]
            model = model or "deepseek-chat"
        elif os.environ.get("OPENROUTER_API_KEY"):
            url, key = "https://openrouter.ai/api/v1/chat/completions", key or os.environ["OPENROUTER_API_KEY"]
    if not url or not model:
        raise SystemExit(f"没有配置{'裁判' if kind == 'judge' else '作答'}模型：设 {pre}API_URL / {pre}API_KEY / "
                         f"{pre}MODEL，或者 DEEPSEEK_API_KEY")
    return {"url": url, "key": key or "", "model": model}


class LLM:
    def __init__(self, cache_path: str):
        self.cache_path = cache_path
        self.cache = {}
        self.lock = threading.Lock()
        self.calls = self.hits = 0
        self.usage = defaultdict(int)
        if cache_path and os.path.exists(cache_path):
            for line in open(cache_path, encoding="utf-8"):
                try:
                    row = json.loads(line)
                    self.cache[row["key"]] = row
                except Exception:
                    continue

    @staticmethod
    def _key(ep: dict, messages: list, max_tokens: int) -> str:
        raw = json.dumps([ep["url"], ep["model"], messages, max_tokens], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def chat(self, ep: dict, messages: list, max_tokens: int, role: str) -> str:
        k = self._key(ep, messages, max_tokens)
        with self.lock:
            if k in self.cache:
                self.hits += 1
                return self.cache[k]["content"]
        body = {"model": ep["model"], "messages": messages, "temperature": 0, "max_tokens": max_tokens}
        delay = 2.0
        for attempt in range(6):
            req = urllib.request.Request(ep["url"], data=json.dumps(body).encode("utf-8"), headers={
                "Content-Type": "application/json", "Authorization": "Bearer " + ep["key"]})
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.load(resp)
                content = (data["choices"][0]["message"].get("content") or "").strip()
                usage = data.get("usage") or {}
                break
            except urllib.error.HTTPError as e:
                if e.code not in (408, 409, 429, 500, 502, 503, 504) or attempt == 5:
                    raise RuntimeError(f"{role} 模型返回 HTTP {e.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 5:
                    raise
            time.sleep(delay * (0.5 + random.random()))
            delay = min(delay * 2, 60)
        with self.lock:
            self.calls += 1
            for u in ("prompt_tokens", "completion_tokens"):
                self.usage[f"{role}_{u}"] += int(usage.get(u) or 0)
            row = {"key": k, "role": role, "model": ep["model"], "content": content}
            self.cache[k] = row
            if self.cache_path:
                with open(self.cache_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return content


# ── 第 1 步：上下文 ─────────────────────────────────────────

def _fmt_lme_history(inst: dict) -> str:
    from longmemeval_eval import parse_date
    out = []
    for sid, date, turns in sorted(zip(inst["haystack_session_ids"], inst["haystack_dates"],
                                       inst["haystack_sessions"]), key=lambda x: parse_date(x[1])):
        out.append(f"### Session on {date}")
        out += [f"{t.get('role', '?')}: {t.get('content', '')}" for t in turns]
    return "\n".join(out)


def _fmt_locomo_history(sample: dict) -> str:
    from locomo_eval import sessions, turn_text
    out = []
    for n, when, turns in sessions(sample["conversation"]):
        out.append(f"### Session {n} on {when:%Y-%m-%d %H:%M}")
        out += [turn_text(t) for t in turns]
    return "\n".join(out)


def contexts_longmemeval(data: list, args) -> list:
    import longmemeval_eval as L
    a = SimpleNamespace(understand_llm=False, no_semantic=args.no_semantic, budget=1500,
                        prefetch_tokens=args.prefetch_tokens, qa=True, timeout=args.timeout)
    with tempfile.TemporaryDirectory(prefix="nyx-qa-lme-") as tmp:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            res = list(ex.map(lambda d: L._spawn(d, a, tmp), data))
    by_id = {d["question_id"]: d for d in data}
    items = []
    for r in res:
        d = by_id[r["question_id"]]
        items.append({"id": str(r["question_id"]), "type": d.get("question_type", "?"),
                      "abstention": str(d["question_id"]).endswith("_abs"),
                      "question": d["question"], "answer": str(d.get("answer", "")), "date": d["question_date"],
                      "context": r.get("context", ""), "error": r.get("error"),
                      "answer_in_view": r.get("answer_in_view"), "n_answer_turns": r.get("n_answer_turns"),
                      "full": _fmt_lme_history(d) if "full" in args.conditions else None})
    return items


def contexts_locomo(data: list, args) -> list:
    import locomo_eval as C
    from datetime import timedelta
    a = SimpleNamespace(no_semantic=args.no_semantic, budget=1500, prefetch_tokens=args.prefetch_tokens, qa=True,
                        timeout=args.timeout)
    with tempfile.TemporaryDirectory(prefix="nyx-qa-locomo-") as tmp:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            res = list(ex.map(lambda s: C._spawn(s, a, tmp), data))
    items = []
    for sample, r in zip(data, res):
        sess = C.sessions(sample["conversation"])
        date = f"{(sess[-1][1] + timedelta(days=1)):%Y-%m-%d}" if sess else ""
        full = _fmt_locomo_history(sample) if "full" in args.conditions else None
        qs = sample.get("qa") or []
        rows = r.get("questions") or [{"i": i, "error": r.get("error", "缺结果")} for i in range(len(qs))]
        for row in rows:
            q = qs[row["i"]]
            cat = q.get("category")
            if cat not in C.SCORED:
                continue
            items.append({"id": f"{sample.get('sample_id')}#{row['i']}", "type": C.CATEGORIES[cat],
                          "abstention": False, "question": str(q.get("question") or ""),
                          "answer": str(q.get("answer", "")), "date": date, "context": row.get("context", ""),
                          "error": r.get("error"), "full": full})
    return items


# ── 第 2、3 步 ────────────────────────────────────────────

def run_llm(items: list, args, llm: LLM, dataset: str) -> list:
    ans_ep, judge_ep = endpoint("eval"), endpoint("judge")
    jobs = [(it, c) for it in items if not it.get("error") for c in args.conditions]

    def one(job):
        it, cond = job
        ctx = {"nyx": it["context"], "none": "", "full": it.get("full") or ""}[cond]
        hyp = llm.chat(ans_ep, answer_messages(ctx, it["question"], it["date"]), 512, "answer")
        reply = llm.chat(judge_ep, [{"role": "user", "content": judge_prompt(
            dataset, it["type"], it["question"], it["answer"], hyp, it["abstention"])}], 10, "judge")
        return {"id": it["id"], "type": it["type"], "abstention": it["abstention"], "condition": cond,
                "correct": judged_correct(reply), "hypothesis": hyp, "context_chars": len(ctx)}

    done, out = 0, []
    with ThreadPoolExecutor(max_workers=args.llm_workers) as ex:
        for row in ex.map(one, jobs):
            out.append(row)
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(jobs)}", file=sys.stderr)
    return out


def score(rows: list) -> dict:
    g = defaultdict(lambda: defaultdict(list))
    for r in rows:
        g[r["condition"]][r["type"]].append(r["correct"])
        g[r["condition"]]["_overall"].append(r["correct"])
        if r["abstention"]:
            g[r["condition"]]["_abstention"].append(r["correct"])
        else:
            g[r["condition"]]["_answerable"].append(r["correct"])
    return {c: {t: {"n": len(v), "acc": round(sum(v) / len(v), 4)} for t, v in sorted(types.items())}
            for c, types in g.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dataset", choices=["longmemeval", "locomo"])
    ap.add_argument("data")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="第 1 步的并行进程数")
    ap.add_argument("--llm-workers", type=int, default=8, help="同时在途的模型调用数")
    ap.add_argument("--conditions", default="nyx,none", help="nyx,none,full 任选")
    ap.add_argument("--prefetch-tokens", type=int, default=600, help="Hermes 插件的默认值是 600（12 条）")
    ap.add_argument("--no-semantic", action="store_true")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--contexts", default="", help="第 1 步结果（jsonl）：存在就直接读，不重跑")
    ap.add_argument("--contexts-only", action="store_true", help="只做第 1 步（零 LLM、不联网）")
    ap.add_argument("--cache", default="qa_cache.jsonl", help="模型调用缓存（断点续跑）")
    ap.add_argument("--out", default="", help="结果 JSON")
    args = ap.parse_args(argv)
    args.conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    bad = set(args.conditions) - {"nyx", "none", "full"}
    if bad:
        ap.error(f"未知对照组：{bad}")

    t0 = time.time()
    if args.contexts and os.path.exists(args.contexts):
        items = [json.loads(l) for l in open(args.contexts, encoding="utf-8")]
    else:
        data = json.load(open(args.data, encoding="utf-8"))
        if args.limit:
            data = data[: args.limit]
        items = (contexts_longmemeval if args.dataset == "longmemeval" else contexts_locomo)(data, args)
        if args.contexts:
            with open(args.contexts, "w", encoding="utf-8") as f:
                for it in items:
                    f.write(json.dumps(it, ensure_ascii=False) + "\n")
    errors = [it for it in items if it.get("error")]
    print(f"第 1 步：{len(items)} 题，出错 {len(errors)}，用时 {time.time() - t0:.0f}s", file=sys.stderr)
    if args.contexts_only:
        return 1 if errors else 0

    llm = LLM(args.cache)
    rows = run_llm(items, args, llm, args.dataset)
    report = score(rows)
    from nexsandglass import __version__
    ans_ep, judge_ep = endpoint("eval"), endpoint("judge")
    meta = {"dataset": args.dataset, "data": os.path.basename(args.data), "nyx_version": __version__,
            "questions": len(items), "errors": len(errors), "conditions": args.conditions,
            "prefetch_tokens": args.prefetch_tokens, "semantic": not args.no_semantic,
            "answer_model": ans_ep["model"], "judge_model": judge_ep["model"],
            "judge_prompts": "LongMemEval 官方 evaluate_qa.py 模板（逐字）" +
                             ("；LoCoMo 用其通用 / 时间模板" if args.dataset == "locomo" else ""),
            "llm_calls": llm.calls, "cache_hits": llm.hits, "usage": dict(llm.usage),
            "context_chars_mean": {c: round(sum(r["context_chars"] for r in rows if r["condition"] == c)
                                            / max(1, sum(1 for r in rows if r["condition"] == c)))
                                   for c in args.conditions},
            "wall_s": round(time.time() - t0, 1)}
    for cond, types in report.items():
        print(f"\n[{cond}]")
        for t, v in types.items():
            print(f"  {t:28s} n={v['n']:5d}  准确率 {v['acc']:.3f}")
    print(json.dumps(meta, ensure_ascii=False))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        keep = ("id", "type", "abstention", "condition", "correct", "context_chars") + \
               (("hypothesis",) if args.dataset == "longmemeval" else ())
        json.dump({"meta": meta, "scores": report, "items": [{k: r[k] for k in keep} for r in rows]},
                  open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
