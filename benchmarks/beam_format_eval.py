#!/usr/bin/env python3
"""注入格式 A/B（v7.18.0）—— 同一批召回结果，换一种写法给 agent 看，答案看不看得见、用不用得上

输入是 beam_eval.py --views 存下的「每道题 agent 实际会看到的那几条记忆」（Hermes 默认设置：
prefetch_tokens=600，12 条；汇总 / 时长 / 先后类 20 条），所以比较的只是**写法**，召回完全相同。

写法（--formats）
  head      Hermes 实际注入的写法：每条取开头 300 字（memory_provider.memory_block）
  excerpt   试过没采用：每条在 300 字内取覆盖问题词最多的那一段（excerpt_variant.py）
  chrono    head，但按说话先后排（最早的在前）而不是按相关度
  full      不截断（上限参照：同样这些条目全文给出，看「截断」本身丢了多少）

v7.18.0 的结果（BEAM 10 万–100 万，720 题）：要点看得见 head 0.492 / excerpt 0.498 / full 0.514，excerpt 18 胜 11 负
（p = 0.26）；Qwen2.5-1.5B 只读注入文字作答，信息抽取 + 信息更新各抽 60 题，8 胜 8 负。所以注入写法没改。

第 1 步（零 LLM）：答案看得见吗
  BEAM 的评分要点（rubric）是「LLM response should state: March 29」这种短语。
  看得见 = 短语里的实词（数字、日期、名词）全部出现在注入的**同一条**记忆里（4 个词以上的短语允许缺 1/4）。
  不跨条拼：整段一起算的话，「10 months」会被一条里的「10%」和另一条里的「month」凑出来。
  只算要点是短事实的题型：信息抽取、知识更新、时间推理、跨会话推理。

第 2 步（--llm）：作答模型只读这段文字作答，答案里含不含评分要点（同一套规则）
  模型走 OpenAI 兼容接口（与 qa_eval.py 相同：NYX_EVAL_API_URL / NYX_EVAL_API_KEY / NYX_EVAL_MODEL），
  本地 llama.cpp server 也行。temperature 0，结果按请求哈希缓存（--cache），中断后重跑只补没跑完的。
  逐题配对比较（同一道题、同一个模型、只有写法不同），报胜 / 负 / 平和双侧符号检验的 p 值。

用法
  python3 benchmarks/beam_eval.py 1M.parquet ... --views views.jsonl        # 先存下每道题的视野
  python3 benchmarks/beam_format_eval.py views.jsonl --json benchmarks/results/beam_format_ab.json
  python3 benchmarks/beam_format_eval.py views.jsonl --llm --types information_extraction knowledge_update

结果文件只存题号、题型和对错，不存数据集原文和模型答案（--answers 另存到你指定的地方）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from excerpt_variant import _EN_STOP, _stem, excerpt  # noqa: E402
from nexsandglass.core.memory_provider import MEMORY_HEADER, memory_block  # noqa: E402

FACT_TYPES = ("information_extraction", "knowledge_update", "temporal_reasoning", "multi_session_reasoning")
FORMATS = ("head", "excerpt", "chrono", "full")
_RUBRIC = re.compile(r"^\s*(?:the\s+)?LLM response should\s+(?:state|mention|contain|include|say)\s*:?\s*", re.I)
_NUM = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                         "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_NUM.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "hundred": "100"})
_STOP = _EN_STOP | {"till", "around", "approximately", "roughly", "llm", "response", "st", "nd", "rd",
                                 "th", "s"}


def rubric_phrases(q: dict) -> list:
    out = []
    for r in q.get("rubric") or []:
        s = _RUBRIC.sub("", str(r)).strip().strip(".").strip()
        if s:
            out.append(s)
    return out


def norm_tokens(s: str) -> list:
    s = re.sub(r"(\d)([a-z])", r"\1 \2", (s or "").lower())        # 250ms → 250 ms；29th → 29 th
    s = s.replace(",", "")                                          # 1,500 → 1500
    toks = re.findall(r"[a-z]+|\d+(?:\.\d+)?", s)
    toks = [_NUM.get(t, t) for t in toks]
    return [_stem(t) for t in toks if t not in _STOP]


def covered(phrase: str, toks: set) -> bool:
    need = norm_tokens(phrase)
    if not need:
        return True
    hit = sum(t in toks for t in need)
    return hit >= (len(need) if len(need) <= 3 else math.ceil(0.75 * len(need)))


def score_text(phrases: list, text: str) -> float:
    toks = set(norm_tokens(text))
    return sum(covered(p, toks) for p in phrases) / len(phrases) if phrases else float("nan")


def score_block(phrases: list, block: str) -> float:
    """注入的那段文字里看不看得见：要点的词必须出现在**同一条**记忆里。整段一起算的话，「10 months」会被
    一条里的「10%」和另一条里的「month」凑出来 —— 数字类的短要点尤其容易这样误判。"""
    lines = [set(norm_tokens(ln)) for ln in block.splitlines() if ln.startswith("- ")]
    return sum(any(covered(p, t) for t in lines) for p in phrases) / len(phrases) if phrases else float("nan")


def render(view: dict, fmt: str) -> str:
    items = view["items"]
    if fmt == "chrono":
        items = sorted(items, key=lambda it: (it.get("line") is None, it.get("line") or 0))
    pairs = [(it["text"], it.get("date")) for it in items]
    if fmt in ("head", "chrono"):
        return memory_block(pairs)
    cut = (lambda t: str(t or "")) if fmt == "full" else (lambda t: excerpt(t, view["question"]))
    return "\n".join([MEMORY_HEADER] + [f"- [{d}] {cut(t)}" if d else f"- {cut(t)}" for t, d in pairs])


def _sign_p(w: int, l: int) -> float:
    """双侧符号检验（平局不计）。"""
    n = w + l
    if n == 0:
        return 1.0
    k = min(w, l)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return round(min(1.0, 2 * p), 4)


def stage1(views: list, formats: list) -> dict:
    rows = defaultdict(lambda: defaultdict(list))
    for v in views:
        ph = rubric_phrases(v)
        if not ph or v["type"] not in FACT_TYPES:
            continue
        for fmt in formats:
            vis = score_block(ph, render(v, fmt))
            for g in (v["type"], v["scale"], "_overall"):
                rows[g][fmt].append(vis)
                rows[g]["_chars_" + fmt].append(len(render(v, fmt)))
        ev = set(v.get("evidence") or [])
        seen = {it.get("msg") for it in v["items"]}
        for g in (v["type"], v["scale"], "_overall"):
            rows[g]["_evidence_in_view"].append(1.0 if ev & seen else 0.0)
    out = {}
    for g, d in rows.items():
        out[g] = {"n": len(d[formats[0]])}
        for fmt in formats:
            out[g][fmt] = round(sum(d[fmt]) / len(d[fmt]), 4)
            out[g]["chars_" + fmt] = round(sum(d["_chars_" + fmt]) / len(d["_chars_" + fmt]))
        out[g]["evidence_in_view"] = round(sum(d["_evidence_in_view"]) / len(d["_evidence_in_view"]), 4)
    return out


def stage2(views: list, formats: list, args) -> tuple:
    from qa_eval import LLM, answer_messages, endpoint
    ep = endpoint("eval")
    llm = LLM(args.cache)
    sel = [v for v in views if v["type"] in args.types and rubric_phrases(v)]
    if args.per_type:                      # 每类随机抽 N 题（固定种子，可复现）：小模型在 CPU 上一次作答要好几秒
        import random
        rng = random.Random(args.seed)
        sel = [v for t in args.types for v in
               rng.sample([x for x in sel if x["type"] == t], min(args.per_type, sum(x["type"] == t for x in sel)))]
    if args.limit:
        sel = sel[: args.limit]
    jobs = [(v, f) for v in sel for f in formats]

    def one(job):
        v, fmt = job
        hyp = llm.chat(ep, answer_messages(render(v, fmt), v["question"], v["date"]), args.max_tokens, "answer")
        return {"id": v["id"], "type": v["type"], "scale": v["scale"], "format": fmt,
                "score": score_text(rubric_phrases(v), hyp), "hypothesis": hyp}

    out = []
    with ThreadPoolExecutor(max_workers=args.llm_workers) as ex:
        for i, row in enumerate(ex.map(one, jobs), 1):
            out.append(row)
            if i % 25 == 0:
                print(f"  {i}/{len(jobs)}", file=sys.stderr, flush=True)
    by = defaultdict(dict)
    for r in out:
        by[r["id"]][r["format"]] = r
    summary = {}
    for g in sorted({r["type"] for r in out}) + ["_overall"]:
        ids = [i for i, d in by.items() if g == "_overall" or next(iter(d.values()))["type"] == g]
        row = {"n": len(ids)}
        for fmt in formats:
            row[fmt] = round(sum(by[i][fmt]["score"] for i in ids) / len(ids), 4) if ids else None
            row[fmt + "_all_correct"] = round(sum(by[i][fmt]["score"] == 1.0 for i in ids) / len(ids), 4) if ids else None
        base = formats[0]
        for fmt in formats[1:]:
            w = sum(by[i][fmt]["score"] > by[i][base]["score"] for i in ids)
            l = sum(by[i][fmt]["score"] < by[i][base]["score"] for i in ids)
            row[f"{fmt}_vs_{base}"] = {"wins": w, "losses": l, "ties": len(ids) - w - l, "sign_p": _sign_p(w, l)}
        summary[g] = row
    meta = {"model": ep["model"], "calls": llm.calls, "cache_hits": llm.hits, "usage": dict(llm.usage)}
    return summary, out, meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("views", help="beam_eval.py --views 的输出（jsonl）")
    ap.add_argument("--formats", nargs="+", default=list(FORMATS), choices=FORMATS)
    ap.add_argument("--llm", action="store_true", help="第 2 步：作答模型只读注入的文字作答")
    ap.add_argument("--llm-formats", nargs="+", default=["head", "excerpt"], choices=FORMATS)
    ap.add_argument("--types", nargs="+", default=["information_extraction", "knowledge_update"])
    ap.add_argument("--limit", type=int, default=0, help="第 2 步只跑前 N 题")
    ap.add_argument("--per-type", type=int, default=0, help="第 2 步每类随机抽 N 题（--seed 固定）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--llm-workers", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--cache", default="", help="模型调用缓存（jsonl）")
    ap.add_argument("--answers", default="", help="模型答案另存（含数据集内容，别提交进仓库）")
    ap.add_argument("--json", default="", help="指标输出（只有题号、题型、对错）")
    args = ap.parse_args(argv)

    views = [json.loads(line) for line in open(args.views, encoding="utf-8") if line.strip()]
    report = {"stage1": stage1(views, args.formats)}
    print("stage 1: answer visible in the injected text")
    for g, row in report["stage1"].items():
        print(f"  {g:26s} n={row['n']:4d}  " + "  ".join(f"{f}={row[f]:.3f}" for f in args.formats)
              + f"  evidence_in_view={row['evidence_in_view']:.3f}  chars: "
              + "/".join(str(row['chars_' + f]) for f in args.formats))
    per_q = []
    if args.llm:
        summary, rows, meta = stage2(views, args.llm_formats, args)
        report["stage2"] = summary
        report["stage2_meta"] = meta
        per_q = [{k: r[k] for k in ("id", "type", "scale", "format", "score")} for r in rows]
        print("stage 2: rubric facts in the model's answer")
        print(json.dumps(summary, indent=1))
        if args.answers:
            with open(args.answers, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        json.dump({"scores": report, "per_question": per_q}, open(args.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
