"""tests/test_breadth.py — 汇总 / 时长 / 先后类问题多给几条（v7.13.7）

LongMemEval-S 分半验证（留出一半）：agent 看到的记录里含答案轮次的比例 0.810 → 0.870，
多会话汇总 0.729 → 0.794、时间推理 0.692 → 0.808。另一个方案「同一次对话最多 N 条」反而让它降到
0.746 / 0.670 —— 同一次对话的那几条往往正是含答案的句子，所以放宽条数，不去重压缩。
"""
import re

import pytest

from tests.test_hermes_provider import _provider, _reindex, env  # noqa: F401


@pytest.mark.parametrize("q", [
    "How many times did I go to the gym last month?",
    "How long ago did I move to Utrecht?",
    "Which did I buy first, the bike or the helmet?",
    "What did I do after the conference?",
    "我一共去了几次健身房",
    "搬家之前我住哪",
    "我们多久没见了",
    "这几个项目的先后顺序是什么",
])
def test_breadth_questions_detected(q):
    from nexsandglass.runtime import intent
    assert intent.wants_breadth(q)
    assert intent.breadth_lines(q) == intent.BREADTH_LINES


@pytest.mark.parametrize("q", [
    "What is my dog's name?",
    "Where do I work?",
    "我住在哪",
    "推荐一家川菜馆",
    "李医生的电话是多少号码",       # 「多少」也会触发 —— 这句会触发，见下面的断言
])
def test_plain_questions_not_widened(q):
    from nexsandglass.runtime import intent
    if "多少" in q:                    # 中文「多少」是汇总线索，宁可多给几条：这是有意的
        assert intent.wants_breadth(q)
    else:
        assert not intent.wants_breadth(q) and intent.breadth_lines(q) == 0


def test_breadth_switch(monkeypatch):
    from nexsandglass.runtime import intent
    q = "How many times did I mention coffee?"
    monkeypatch.setenv("NYX_BREADTH_LINES", "0")
    assert intent.breadth_lines(q) == 0                     # 关闭 = 与 v7.13.6 相同
    monkeypatch.setenv("NYX_BREADTH_LINES", "30")
    assert intent.breadth_lines(q) == 30
    monkeypatch.setenv("NYX_BREADTH_LINES", "abc")
    assert intent.breadth_lines(q) == intent.BREADTH_LINES


def _lines(ctx):
    return [l for l in ctx.splitlines() if re.match(r"- (\[\d{4}-\d{2}-\d{2}\] )?\S", l)]


def test_prefetch_widens_only_for_breadth_questions(env, monkeypatch):  # noqa: F811
    monkeypatch.delenv("NYX_BREADTH_LINES", raising=False)
    p = _provider()
    for i in range(40):
        p.sync_turn(f"I had coffee with friend number {i} at cafe {i}", "")
    _reindex(env)
    wide = _lines(p.prefetch("How many times did I have coffee with a friend?", session_id="s"))
    plain = _lines(p.prefetch("Did I have coffee with a friend at a cafe?", session_id="s"))
    assert len(wide) == 20, len(wide)
    assert len(plain) <= 12, len(plain)                    # 普通问题不变：SearchRouter 10 条 + 其他来源，上限 12
    monkeypatch.setenv("NYX_BREADTH_LINES", "0")
    assert len(_lines(p.prefetch("How many times did I have coffee with a friend?", session_id="s"))) <= 12
