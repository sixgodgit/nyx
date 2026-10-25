"""tests/test_rerank.py — 交叉编码器重排（v7.14.0，默认关）

用一个假的交叉编码器（含关键词就给高分）验证管道，不加载真模型：
  - 默认关：路由照旧只取 limit 条
  - 开了：路由先取 NYX_RERANK_DEPTH 条，重排后取 limit 条 —— 原来排在 limit 之外的答案能进来
  - 三种模式：pure / rrf / pinned（问句词全中的那几条不动）
  - 模型加载失败 / 打分出错：原样返回，不抛
效果要用真实模型在真实数据上测（见 CHANGELOG）。
"""
import pytest

from tests.test_hermes_provider import _provider, _reindex, env  # noqa: F401  复用隔离目录


class _FakeCE:
    def __init__(self, keyword):
        self.keyword, self.calls = keyword, []

    def predict(self, pairs, **kw):
        self.calls.append(len(pairs))
        return [5.0 if self.keyword in t else 0.0 for _, t in pairs]


@pytest.fixture()
def fake(monkeypatch):
    from nexsandglass.core import rerank
    ce = _FakeCE("团子")
    monkeypatch.setattr(rerank, "_load", lambda: ce)
    monkeypatch.setattr(rerank, "ready", lambda: True)
    return ce


ITEMS = [(1, "t", "我养了一只狗"), (2, "t", "今天跑步"), (3, "t", "周末读书"), (4, "t", "领养了一只橘猫叫团子")]


def test_modes(fake):
    from nexsandglass.core import rerank
    assert [it[0] for it in rerank.rerank("猫叫什么", ITEMS, "pure")] == [4, 1, 2, 3]
    # rrf：原名次和交叉编码器名次各投一票 —— 从第 4 升到第 2（原第 1 的两票更多）。这一模式很保守
    assert [it[0] for it in rerank.rerank("猫叫什么", ITEMS, "rrf")][:2] == [1, 4]
    # pinned：问句的词（「跑步」）全中的那条不动，其余按交叉编码器
    assert [it[0] for it in rerank.rerank("跑步", ITEMS, "pinned")] == [2, 4, 1, 3]


def test_not_ready_does_not_block(monkeypatch):
    """模型还没加载完：照原样返回，并在后台加载 —— 不在召回路径上等。"""
    from nexsandglass.core import rerank
    started = []
    monkeypatch.setattr(rerank, "ready", lambda: False)
    monkeypatch.setattr(rerank, "warm_async", lambda: started.append(1))
    monkeypatch.setattr(rerank, "_load", lambda: (_ for _ in ()).throw(AssertionError("不该同步加载")))
    assert rerank.rerank("猫", ITEMS) == ITEMS and started == [1]


def test_failure_keeps_original_order(monkeypatch):
    from nexsandglass.core import rerank
    monkeypatch.setattr(rerank, "ready", lambda: True)
    monkeypatch.setattr(rerank, "_load", lambda: None)
    assert rerank.rerank("猫", ITEMS) == ITEMS

    class Boom:
        def predict(self, *a, **k):
            raise RuntimeError("x")
    monkeypatch.setattr(rerank, "_load", lambda: Boom())
    assert rerank.rerank("猫", ITEMS) == ITEMS


def test_off_by_default_and_deeper_pool_when_on(env, monkeypatch, fake):  # noqa: F811
    from nexsandglass.runtime.orchestrator import get_orchestrator
    monkeypatch.setenv("NYX_EMBED", "off")
    monkeypatch.delenv("NYX_RERANK", raising=False)
    p = _provider()
    for i in range(25):            # 干扰项：「橘猫」出现得比答案还多，词法排序把答案挤到第 10 名以外
        p.sync_turn(f"第 {i} 条：楼下的橘猫，橘猫又来了，橘猫真胖（{i}）", "")
    p.sync_turn("上周末领养了一只橘猫，取名叫团子；它最爱趴在窗台上晒太阳", "")
    _reindex(env)
    orch = get_orchestrator().recall_planner
    q = "我的橘猫叫什么"
    top = [str(o.content) for o in orch._adapt_search_router(q)]
    assert fake.calls == [], "默认关：不该调交叉编码器"
    assert len(top) == orch._router_limit(q) and not any("团子" in t for t in top), "前提：答案不在前 limit 条"
    from nexsandglass.core.search_router import SearchRouter
    assert any("团子" in it[2] for it in SearchRouter().search(q, limit=40)), "前提：答案在更深的候选里"
    monkeypatch.setenv("NYX_RERANK", "local")
    monkeypatch.setenv("NYX_RERANK_DEPTH", "40")
    monkeypatch.setenv("NYX_RERANK_MODE", "pure")
    top2 = [str(o.content) for o in orch._adapt_search_router(q)]
    assert len(top2) == len(top) and sum(fake.calls) > len(top), "先多取再重排（分批打分）"
    assert "团子" in top2[0]


def test_time_budget_reranks_only_what_was_scored(fake, monkeypatch):
    """到了时间预算还没打完：只在打过分的那部分里重排，其余按原顺序接在后面（不让路由撞上 3 秒超时）。"""
    import time
    from nexsandglass.core import rerank
    items = [(i, "t", "无关") for i in range(40)] + [(99, "t", "领养了一只橘猫叫团子")]
    slow = fake.predict

    def predict(pairs, **kw):
        time.sleep(0.05)
        return slow(pairs, **kw)
    monkeypatch.setattr(fake, "predict", predict)
    monkeypatch.setenv("NYX_RERANK_BUDGET_MS", "1")
    out = rerank.rerank("猫", items, "pure")
    assert [it[0] for it in out] == [it[0] for it in items], "只打了第一批（前 16 条），答案在第 41 条：顺序不变"
    assert rerank.status()["cut"] >= 1
    monkeypatch.setenv("NYX_RERANK_BUDGET_MS", "5000")
    assert rerank.rerank("猫", items, "pure")[0][0] == 99
