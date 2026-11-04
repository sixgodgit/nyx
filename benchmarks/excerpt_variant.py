"""v7.18.0 试过、没有采用的注入写法：长记忆取与问题相关的那 300 个字符，而不是开头 300 个字符

  - 300 字以内的记忆原样给
  - 更长的：取覆盖本轮问题里词最多的那一段（按句子切，切掉的地方用「…」标出），总长不变
  - 和问题一个词都不沾的（靠语义 / 图谱找到的）照旧取开头；来源标注始终留在最前面

BEAM 上和「取开头」比（benchmarks/beam_format_eval.py，同一批召回结果）：答案的要点出现在同一条注入记忆里的比例
0.492 → 0.498（18 题变好、11 题变差，符号检验 p = 0.26），全部给全文也只有 0.514；本地小模型只读注入文字作答，
8 题变好、8 题变差。没有可测的好处，所以 Hermes 注入仍是每条取开头 300 字（memory_provider.memory_block）。
留在这里，是为了这个结论可以复现、以后换数据可以再比。
"""
from __future__ import annotations

import math
import re
from typing import List, Tuple

LINE_CHARS = 300

# 问句里的虚词、「你还记得 / 我说过」这类话头：不拿来定位
_EN_STOP = frozenset("""
a about above after again all am an and any are as at be because been before being below between both
but by can could did do does doing down during each few for from further had has have having he her here
hers him his how i if in into is it its just me more most my no nor not now of off on once only or other
our out over own same she should so some such than that the their them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would
you your yours yourself tell know remember mentioned say said ever recently last please also get got
""".split())
_ZH_STOP = frozenset("什么 怎么 这个 那个 我们 你们 他们 一个 没有 可以 就是 还是 是不 不是 时候 记得 说过".split())
_WORD = re.compile(r"[a-z0-9]+(?:[-_.@/:][a-z0-9]+)*")
_CJK = re.compile(r"[一-鿿]+")
_LABEL = re.compile(r"^\s*[(（](?:unverified|未经证实)[^)）]*[)）]\s*")
_CUT = re.compile(r"[.!?;]+[\"')\]]*\s+|[。！？；]+|\n+")
_ELLIPSIS = "…"


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def terms(text: str) -> set:
    """定位用的词：英文实词（轻量词干）、数字；response_time / v2.1 这类复合词整体和各段都算；中文按相邻两字。"""
    out = set()
    for w in _WORD.findall((text or "").lower()):
        for part in {w, *re.split(r"[-_.@/:]", w)}:
            if part and part not in _EN_STOP and (len(part) >= 2 or part.isdigit()):
                out.add(_stem(part))
    for run in _CJK.findall(text or ""):
        if len(run) == 1:
            out.add(run)
        out.update(b for b in (run[i:i + 2] for i in range(len(run) - 1)) if b not in _ZH_STOP)
    return out


def _spans(text: str) -> List[Tuple[int, int]]:
    """按句子切成 (起, 止) 区间，首尾相接覆盖全文。"""
    cuts = {0, len(text)}
    cuts.update(m.end() for m in _CUT.finditer(text))
    pts = sorted(c for c in cuts if 0 <= c <= len(text))
    return [(a, b) for a, b in zip(pts, pts[1:]) if b > a]


def _word_start(text: str, i: int, lo: int) -> int:
    """i 往回退到词首（不切半个英文词）；退不超过 20 个字符。"""
    j = i
    while j > lo and j > i - 20 and text[j - 1].isalnum() and text[j].isalnum():
        j -= 1
    return j if j > lo and not (text[j - 1].isalnum() and text[j].isalnum()) else i


def _word_end(text: str, i: int, lo: int) -> int:
    """i 往回退到词尾（截断处不落在英文词中间）；退不超过 20 个字符。"""
    j = i
    while j > lo and j > i - 20 and j < len(text) and text[j - 1].isalnum() and text[j].isalnum():
        j -= 1
    return j if j > lo else i


def excerpt(text: str, query: str, limit: int = LINE_CHARS) -> str:
    """一条记忆在 limit 个字符里给 agent 看哪一段。"""
    text = str(text or "")
    if len(text) <= limit:
        return text
    m = _LABEL.match(text)
    label, body = (text[:m.end()], text[m.end():]) if m else ("", text)
    room = limit - len(label)
    want = terms(query)
    spans = _spans(body)
    if room < 60 or not want or not spans:
        return text[:limit]
    found = [terms(body[a:b]) & want for a, b in spans]
    df = {}
    for f in found:
        for t in f:
            df[t] = df.get(t, 0) + 1
    if not df:
        return text[:limit]                       # 一个词都不沾：照旧取开头
    n = len(spans)
    # 在这条消息里越少见的词越能定位（每句都提到的主题词帮不上忙）
    weight = {t: math.log(1 + n / c) for t, c in df.items()}

    best = None                                    # (分数, -起点, i, j)
    for i in (k for k in range(n) if found[k]):    # 窗口从含问题词的句子起算（贴进来的长日志也只看命中的几处）
        seen, score = set(), 0.0
        for j in range(i, n):
            if j > i and spans[j][1] - spans[i][0] > room - 2:
                break
            new = found[j] - seen
            if new:                                # 增量加分：贴进来的日志一行一句时，每步重算会很慢
                seen |= new
                score += sum(weight[t] for t in new)
            key = (round(score, 9), -spans[i][0])
            if best is None or key > best[0]:
                best = (key, i, j)
    _, i, j = best
    a, b = spans[i][0], spans[j][1]
    if b - a > room - 2:                           # 一句话就超长（代码块、长段落）：句内开窗
        lo, hi = a, b
        hits = [body.lower().find(t, lo, hi) for t in found[i]]
        first = min((h for h in hits if h >= 0), default=lo)
        a = max(lo, min(first - room // 4, hi - (room - 2)))   # 命中处靠前四分之一；到句尾也要占满
        a = _word_start(body, a, lo)
        b = _word_end(body, min(hi, a + room - 2), a + 1)
    else:                                          # 没占满：先往后补（答案常在下一句），再往前补
        while j + 1 < n and spans[j + 1][1] - a <= room - 2:
            j += 1
            b = spans[j][1]
        while i > 0 and b - spans[i - 1][0] <= room - 2:
            i -= 1
            a = spans[i][0]
        if b - a < room - 2 and b < len(body):    # 下一句放不下整句：截到放得下的词尾
            b = _word_end(body, min(len(body), a + room - 2), b)
    piece = body[a:b].strip()
    head = _ELLIPSIS if a > 0 else ""
    tail = _ELLIPSIS if b < len(body) else ""
    return label + head + piece + tail
