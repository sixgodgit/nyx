#!/usr/bin/env python3
"""Déjà Vu 在真实英文对话上的误报：记了 N 轮之后，毫不相干的英文问题有几个被判成「熟悉」（v7.16.1）。

语料是 LongMemEval-S（MIT）的对话，取前 60 题的全部会话，打乱后取前 N 轮（默认只取用户说的话）。
问题是 10 个和这些对话毫不相干的英文问题。``--no-stopwords`` 按 v7.16.0 的分词跑（常用词也算 token），作对照。

用法::

    # https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
    python3 benchmarks/dejavu_english_real.py longmemeval_s_cleaned.json
    python3 benchmarks/dejavu_english_real.py longmemeval_s_cleaned.json --no-stopwords

这份语料是几十个不同的人的对话拼起来的，词汇比一个人的聊天记录宽；轮数一多，teacher / workshop 这种
普通实词都见过 —— 这时的「熟悉」只靠一个普通实词成立，见 docs/dejavu.md「What it is not」第 6 条。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexsandglass.dejavu import DejaVu, core  # noqa: E402

NOISE = [
    "what did we say about Byzantine gold coin minting",
    "do you remember the penguin migration routes in Antarctica",
    "that quantum computing paper we talked about",
    "the ramen bar in Osaka I told you about",
    "what was the name of my accordion teacher",
    "how is the lighthouse renovation going",
    "did I ever tell you about my falconry lessons",
    "what was that podcast about medieval shipbuilding",
    "the glassblowing workshop we discussed",
    "remind me what I said about my grandmother's harpsichord",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", help="longmemeval_s_cleaned.json")
    ap.add_argument("--sizes", default="20,200,2000")
    ap.add_argument("--all-roles", action="store_true", help="assistant 的回复也记（默认只记用户说的）")
    ap.add_argument("--no-stopwords", action="store_true", help="v7.16.0 的分词：常用词也算 token")
    args = ap.parse_args()
    if args.no_stopwords:
        core._EN_STOPWORDS = frozenset()

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)
    turns = [t["content"] for item in data[:60] for sess in item["haystack_sessions"] for t in sess
             if args.all_roles or t["role"] == "user"]
    random.Random(1).shuffle(turns)

    for n in (int(x) for x in args.sizes.split(",")):
        with tempfile.TemporaryDirectory(prefix="dv-en-real-") as d:
            dv = DejaVu(d, autosave=0)
            for i, t in enumerate(turns[:n]):
                dv.imprint(f"t{i}", t)
            res = [dv.sense(q) for q in NOISE]
            dv.close()
        fam = sum(r.familiar for r in res)
        mean = sum(r.score for r in res) / len(res)
        seen = sorted({p for r in res for p in r.phantoms})
        print(f"{n:>6} turns: {fam}/{len(NOISE)} unrelated questions familiar, mean score {mean:.2f}; "
              f"seen: {', '.join(seen[:12])}{' …' if len(seen) > 12 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
