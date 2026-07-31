"""
core/vector_search.py — 向量语义检索（SearchRouter 第五路）

基于 embedding_provider + vector_store 的真正的语义检索。
与现有四路（FTS5/倒排/TF-IDF/SimHash）互补：
- 词法检索：精确关键词匹配，召回率高但无法处理同义改写
- 向量检索：语义相似度，能处理同义改写但依赖模型质量

混合策略：RRF（Reciprocal Rank Fusion）融合五路结果。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VectorSearch:
    """向量语义检索器。"""

    def __init__(self, embedding_provider=None, vector_store=None):
        self._provider = embedding_provider
        self._store = vector_store

    def _get_provider(self):
        if self._provider is not None:
            return self._provider
        from .embedding_provider import get_embedding_provider
        self._provider = get_embedding_provider()
        return self._provider

    def _get_store(self):
        if self._store is not None:
            return self._store
        from .vector_store import get_vector_store
        self._store = get_vector_store()
        return self._store

    def search(self, query: str, limit: int = 10) -> list:
        """
        向量检索。返回 [(line_num, score), ...] 与现有路格式一致。

        注意：向量存储的 key 是 memory_id，需要调用方映射回 line/记录。
        为兼容现有接口，返回 (memory_id, score)。
        """
        try:
            provider = self._get_provider()
            if not provider.available:
                return []
            store = self._get_store()
            if store.count() == 0:
                return []
            query_emb = provider.encode_one(query)
            if not query_emb:
                return []
            results = store.search(query_emb, top_k=max(limit * 2, 20))
            # 返回格式：(id, score)，score 是余弦相似度 [0,1]
            return [(mid, float(score)) for mid, score in results]
        except Exception as e:
            logger.debug("[VectorSearch] 检索失败（退回词法）: %s", e)
            return []

    @property
    def available(self) -> bool:
        provider = self._get_provider()
        store = self._get_store()
        return provider.available and store.count() > 0


def rrf_fusion(ranked_lists: list[list], k: int = 60) -> list:
    """
    Reciprocal Rank Fusion：融合多路排序结果。

    Args:
        ranked_lists: 每路结果列表，每项为 (id, ...) 或只是 id
        k: RRF 常数（默认 60）

    Returns:
        融合后的 id 列表
    """
    scores: dict[str, float] = {}
    for rlist in ranked_lists:
        for rank, item in enumerate(rlist):
            if isinstance(item, tuple):
                doc_id = str(item[0])
            else:
                doc_id = str(item)
            if doc_id not in scores:
                scores[doc_id] = 0.0
            scores[doc_id] += 1.0 / (k + rank + 1)
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [doc_id for doc_id, _ in fused]
