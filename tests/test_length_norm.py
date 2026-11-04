"""长记忆按长度打折（v7.18.0，core/search_router.py）

BEAM（消息平均两千多字）上，助手的长回复靠词多压过了真正说了答案的短句：recall@10 0.211，BM25 0.333。
"""
import pytest

from nexsandglass.core import search_router
from nexsandglass.features.sandglass_vault import _query_tokens


@pytest.fixture(autouse=True)
def _no_shadow(monkeypatch):
    from nexsandglass.features import shadow_sand
    monkeypatch.setattr(shadow_sand, "shadow_boost", lambda lines, limit=10: [])


def test_short_memories_are_not_ranked_by_length():
    """聊天短句（一行注入放得下的）之间不分长短：像 BM25 那样一起归一，LoCoMo recall@5 0.547 → 0.508"""
    assert search_router._length_norm(10) == search_router._length_norm(300) == 1.0
    assert search_router._length_norm(1200) == pytest.approx(0.5)


def test_long_reply_does_not_outrank_the_short_answer():
    q = "When is the database migration deadline for the billing service?"
    filler = ("Moving a database is mostly about planning. A migration needs a rollback path, a deadline that "
              "leaves slack, and a clear owner for each service. ")
    long_reply = filler * 12 + "For billing in particular, test with production-sized data first."
    short = "The database migration deadline is June 3."
    toks = _query_tokens(q)
    low_long, low_short = long_reply.lower(), short.lower()
    # 长回复把问句的词全占了，短句没有 billing / service：只看覆盖率时长回复排第一
    assert all(t in low_long for t in ("database", "migration", "deadline", "billing", "service"))
    assert "billing" not in low_short
    ranked = search_router.sand_density([(1, "2024-01-01", long_reply), (2, "2024-01-02", short)], toks, q)
    assert [r[0] for r in ranked] == [2, 1]


def test_equal_length_order_is_unchanged():
    q = "what is the name of my dog"
    a = "My dog's name is Biscuit, he is a corgi."
    b = "I walked the dog in the park today."
    ranked = search_router.sand_density([(1, "t", b), (2, "t", a)], _query_tokens(q), q)
    assert ranked[0][0] == 2
