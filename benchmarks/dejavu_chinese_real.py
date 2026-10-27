#!/usr/bin/env python3
"""Déjà Vu 在真实中文闲聊上的误报：记了 N 句之后，毫不相干的中文问题有几个被判成「熟悉」（v7.16.2）。

语料是 LCCC（MIT，清华 CoAI 的中文闲聊语料，微博对话）的验证集，2 万段对话、每句一条记忆，打乱后取前 N 句。
问题是 10 个和闲聊毫不相干的回忆式问题。``--no-stopwords`` 按 v7.16.1 的分词跑（虚词和跨词碎片也算 token），作对照。
同时报告「每句记忆产生多少个新 token」—— Veil 的容量按唯一 token 数算（docs/dejavu.md 的容量表）。

用法::

    # https://huggingface.co/datasets/silver/lccc 的 lccc_base_valid.jsonl.gz（约 1 MB）
    python3 benchmarks/dejavu_chinese_real.py lccc_base_valid.jsonl.gz
    python3 benchmarks/dejavu_chinese_real.py lccc_base_valid.jsonl.gz --no-stopwords
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexsandglass.dejavu import DejaVu, core  # noqa: E402

NOISE = [
    "我们之前聊过的拜占庭金币铸造工艺", "你还记得我说过的南极企鹅迁徙路线吗", "上次说的那篇量子计算论文",
    "我跟你提过的大阪那家拉面店", "我的手风琴老师叫什么来着", "灯塔翻修的事情怎么样了",
    "我之前跟你说过我在学驯鹰吗", "那个讲中世纪造船的播客叫什么", "我们聊过的吹玻璃工作坊",
    "提醒我一下我说过奶奶的大键琴的事",
]
# LCCC 是逐字加空格存的：「好 好 读 书」→「好好读书」（中文字与中文标点之间的空格去掉，数字 / 英文旁的留着）
_SPACED = re.compile(r"(?<=[一-鿿，。？！、；：“”‘’（）])\s+(?=[一-鿿，。？！、；：“”‘’（）])")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", help="lccc_base_valid.jsonl.gz")
    ap.add_argument("--sizes", default="20,200,2000")
    ap.add_argument("--no-stopwords", action="store_true", help="v7.16.1 的分词：虚词和跨词碎片也算 token")
    args = ap.parse_args()
    if args.no_stopwords:
        core._ZH_STOPWORDS = frozenset()
        core._ZH_FUNCTION_CHARS = frozenset()

    utts = []
    with gzip.open(args.data, "rt", encoding="utf-8") as f:
        for line in f:
            utts.extend(_SPACED.sub("", u) for u in json.loads(line))
    random.Random(1).shuffle(utts)

    for n in (int(x) for x in args.sizes.split(",")):
        uniq = set()
        with tempfile.TemporaryDirectory(prefix="dv-zh-real-") as d:
            dv = DejaVu(d, autosave=0)
            for i, u in enumerate(utts[:n]):
                dv.imprint(f"u{i}", u)
                uniq.update(core.scent(u))
            res = [dv.sense(q) for q in NOISE]
            dv.close()
        fam = sum(r.familiar for r in res)
        mean = sum(r.score for r in res) / len(res)
        seen = sorted({p for r in res for p in r.phantoms})
        print(f"{n:>6} utterances: {fam}/{len(NOISE)} unrelated questions familiar, mean score {mean:.2f}; "
              f"{len(uniq) / n:.1f} unique tokens/utterance; seen: {'、'.join(seen[:15])}{' …' if len(seen) > 15 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
