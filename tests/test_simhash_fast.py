"""tests/test_simhash_fast.py — simhash 的快速实现与原来的逐位循环逐位相同（v7.15.4）

召回时给每条候选算一次 simhash，原来的 词元数 × 128 次 Python 循环占了一次 prefetch 的六成。
换成按列数 1 之后，指纹必须和原来完全一样：存下来的排序、去重、阈值都依赖它。
"""
import hashlib
import random

from nexsandglass.l3 import l3_search_core as core


def _reference(text, bits=128):
    """v7.15.3 及以前的实现，原样保留作对照。"""
    tokens = core._tokenize_simhash(text)
    if not tokens:
        return -1
    v = [0] * bits
    for token in tokens:
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        for i in range(bits):
            if (h >> (i % 128)) & 1:
                v[i] += 1
            else:
                v[i] -= 1
    fp = 0
    for i in range(bits):
        if v[i] > 0:
            fp |= (1 << i)
    return fp


def _texts():
    rnd = random.Random(7)
    words = ["budget", "passport", "contract", "Kevin", "rent", "coffee", "project", "Spotify", "degree"]
    zh = "我们在杭州讨论了预算表老周提醒我体检报告下周三之前要处理房租已经付款了"
    out = ["", "   ", "a", "ab", "ab ab", "我", "我们", "重复重复重复重复", "x" * 600, "AB cd EF gh " * 50,
           "混合 mixed 文本 text 和 and 数字 123", "I graduated with a degree in Business Administration."]
    for _ in range(300):
        n = rnd.randint(1, 40)
        out.append(" ".join(rnd.choice(words) for _ in range(n)))
        i = rnd.randint(0, len(zh) - 2)
        out.append(zh[i:i + rnd.randint(2, 20)] + " " + rnd.choice(words))
    return out


def test_fast_simhash_matches_reference():
    core._simhash_cache.clear()
    core._simhash_rows.clear()
    for t in _texts():
        assert core.simhash(t) == _reference(t), t[:80]
        assert core.simhash(t) == _reference(t), "第二次走缓存也一样"


def test_other_bit_widths_keep_the_old_loop():
    core._simhash_cache.clear()
    for t in ["预算表 budget", "Kevin 发来的合同"]:
        core._simhash_cache.clear()
        assert core.simhash(t, bits=64) == _reference(t, bits=64)


def test_token_cache_is_bounded(monkeypatch):
    core._simhash_cache.clear()
    core._simhash_rows.clear()
    monkeypatch.setattr(core, "_SIMHASH_ROWS_MAX", 5)
    for i in range(20):
        t = f"word{chr(97 + i)} other{chr(97 + i)} third"
        assert core.simhash(t) == _reference(t)
    assert len(core._simhash_rows) <= 5
