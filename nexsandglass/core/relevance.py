"""这一轮该不该注入（v7.19.0）—— 只因为虚词撞上的记忆不注入；问句里没有一个实词对得上，一条都不注入

检索把中文问句切成两字片段、每个片段同等对待：「今晚吃什么好？给我推荐几个菜」里的「什么」「给我」「几个」
在工作对话里到处都是，于是问晚饭注入了 20 条「看看为什么…」「可以先拆成几个小函数…」。英文的词内 4-gram
（tonight → nigh / ight）同理会撞上 night / right / eight。召回排序不动（检索评测的口径不变），
只在**注入给 agent 之前**再看一眼：这条记忆里至少要出现问句里的一个实词。

实词
  中文：问句的相邻两字组，去掉含虚字（的了吗呢吧我你们这那是在…）的、常用虚词（什么、怎么、一下、明天…）、
        「几个 / 一封」这类数量词，和请求套话（看看、帮忙、请问…）—— 中文虚词规则与 dejavu 相同（v7.16.2 实测过）
  英文：去掉停用词和请求套话（help / could / tell…）的词，按词首匹配（kayak ↔ kayaking）；不用 4-gram
  编号 / 版本号 / 邮箱这类复合标识整体算一个实词；纯数字少于 3 位的不算（行号、数量到处都是）

不受这道门限制的：知识图谱按问句意图补上的信念（「我住哪」→ user lives in 上海，字面本来就对不上），
语义检索（开了嵌入时）的命中 —— 它们已经过了「明显比背景更像」的门槛。
"""
from __future__ import annotations

import re

from nexsandglass.dejavu.core import _EN_STOPWORDS as _DV_EN_STOP, _ZH_STOPWORDS, _zh_common

_ZH_EXTRA = frozenset("""
看看 帮忙 请问 问题 需要 一封 一本 一张 一条 一份 一些 几个 几本 几天 几次 哪儿 哪里 哪个 哪些 哪家 有没 能不 是不 要不
好不 行不 怎么办 一般 比较 应该 大概 多少 多久 多长 推荐一 介绍一 解释一 分析一 整理一 告诉 想问 有点 一点
回事 办法 较好 比较好 东西 情况
""".split())
_MEASURE = re.compile(r"^[一二两三几每某这那哪][个件封本张条只位次些下点份家种天周年月]$")
_EN_EXTRA = frozenset("""
help could would should can will please tell show give let also any some get got make want need know think
like just really thing things way ways lot lots kind sort good great nice best better new old yes okay sure
today tonight tomorrow yesterday now soon later ever still already
""".split())


def _en_stop() -> frozenset:
    try:
        from nexsandglass.features.sandglass_vault import _EN_STOP
    except Exception:
        _EN_STOP = frozenset()
    return _DV_EN_STOP | _EN_STOP | _EN_EXTRA


_EN_STOP_ALL = None


def _stem(w: str) -> str:
    for suf in ("ings", "ing", "ies", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


_ZH_FUNC_EXTRA = frozenset("什怎")          # 只出现在「什么 / 为什么 / 怎么 / 怎样」里的字


def _zh_weak(pair: str) -> bool:
    return (pair in _ZH_EXTRA or pair in _ZH_STOPWORDS or _zh_common(pair) or bool(_MEASURE.match(pair))
            or pair[0] in _ZH_FUNC_EXTRA or pair[1] in _ZH_FUNC_EXTRA)


def content_terms(query: str) -> list:
    """问句里的实词：[(词, 'zh' | 'en' | 'id')]。"""
    global _EN_STOP_ALL
    if _EN_STOP_ALL is None:
        _EN_STOP_ALL = _en_stop()
    q = str(query or "")
    low = q.lower()
    out, seen = [], set()

    def add(t, kind):
        if t and t not in seen:
            seen.add(t)
            out.append((t, kind))

    for run in re.findall(r"[一-鿿]+", q):
        if len(run) == 1:
            continue
        for i in range(len(run) - 1):
            pair = run[i:i + 2]
            if not _zh_weak(pair):
                add(pair, "zh")
    for m in re.finditer(r"[a-z0-9]+(?:[-_.@/:][a-z0-9]+)+", low):
        add(m.group(0), "id")
    for w in re.findall(r"[a-z][a-z0-9']*|[0-9]{3,}", low):
        w = w.strip("'")
        if w.endswith("'s"):
            w = w[:-2]
        if len(w) < 3 or w in _EN_STOP_ALL:
            continue
        add(_stem(w), "en")
    return out


def matches(terms: list, text: str) -> int:
    """这条记忆里出现了几个实词。"""
    low = str(text or "").lower()
    n = 0
    for t, kind in terms:
        if kind == "en":
            pat = r"\b" + re.escape(t) + (r"" if len(t) >= 4 else r"\b")
            if re.search(pat, low):
                n += 1
        elif t in low:
            n += 1
    return n


def keep(query: str, objs: list) -> list:
    """注入前过一道门：保留至少对上一个实词的、按意图补上的信念、语义检索命中的；顺序不变。

    语义命中不另设分数线：semantic.search 只返回「明显比背景更像」的（稳健 z 分数 ≥ NYX_EMBED_Z，默认 3）。
    """
    terms = content_terms(query)
    out = []
    for o in objs:
        if getattr(o, "provenance", "") == "wthread":
            out.append(o)
            continue
        if getattr(o, "vector_score", None) is not None:
            out.append(o)
            continue
        text = str(getattr(o, "content", "") or "")
        if text.startswith("(unverified") and ") " in text:      # 来源标注不算正文
            text = text.split(") ", 1)[1]
        if terms and matches(terms, text):
            out.append(o)
    return out
