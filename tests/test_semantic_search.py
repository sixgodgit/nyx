"""
测试：embedding_provider + vector_store + vector_search
覆盖：Provider 懒加载、JSON 向量存储 CRUD、向量检索、SearchRouter 混合排序
"""

import sys
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    _THIS_DIR,
    os.path.dirname(_THIS_DIR),
    os.path.dirname(os.path.dirname(_THIS_DIR)),
):
    if os.path.isdir(os.path.join(_candidate, "nexsandglass")):
        sys.path.insert(0, _candidate)
        break
if os.path.isdir(os.path.join(_THIS_DIR, "engram")):
    sys.path.insert(0, _THIS_DIR)

from nexsandglass.core.embedding_provider import NullProvider, SentenceTransformerProvider, get_embedding_provider, reset_provider
from nexsandglass.core.vector_store import JsonVectorStore, get_vector_store


def test_null_provider():
    p = NullProvider()
    assert not p.available
    assert p.encode(["test"]) == []
    assert p.encode_one("test") is None


def test_embedding_provider_import():
    # 不依赖 sentence-transformers 已安装；只测试 NullProvider 路径
    reset_provider()
    p = get_embedding_provider()
    # 无 sentence-transformers 时返回 NullProvider
    assert isinstance(p, (NullProvider, SentenceTransformerProvider))


def test_json_vector_store(tmp_path=None):
    import tempfile
    tmp = tempfile.mktemp(suffix=".json")
    try:
        store = JsonVectorStore(path=tmp)
        assert store.count() == 0
        store.upsert("mem-1", [0.1, 0.2, 0.3])
        store.upsert("mem-2", [0.9, 0.8, 0.7])
        assert store.count() == 2
        results = store.search([0.1, 0.2, 0.3], top_k=2)
        assert len(results) == 2
        assert results[0][0] == "mem-1"  # 最相似
        assert results[0][1] > 0.99  # 余弦相似度接近 1
        # 持久化
        store2 = JsonVectorStore(path=tmp)
        assert store2.count() == 2
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def test_json_vector_store_empty():
    import tempfile
    tmp = tempfile.mktemp(suffix=".json")
    try:
        store = JsonVectorStore(path=tmp)
        assert store.search([0.1, 0.2], top_k=5) == []
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def test_search_router_with_vector():
    from nexsandglass.core.vector_search import VectorSearch, rrf_fusion
    
    # RRF 融合测试
    rrf = rrf_fusion([
        [("a",), ("b",), ("c",)],
        [("b",), ("d",), ("a",)],
    ])
    assert rrf[0] == "b"  # b 在两列表中都靠前
    
    # VectorSearch 无 provider 时返回空
    vs = VectorSearch(embedding_provider=NullProvider())
    assert vs.search("test") == []


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"✅ {fn.__name__}")
            passed += 1
        except Exception:
            print(f"❌ {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
