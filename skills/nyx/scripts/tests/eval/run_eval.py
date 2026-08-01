"""
tests/eval/run_eval.py — 评测运行器（Task 4）

测量指标：
- 检索召回率（top-k 命中率）
- 词法检索 vs 混合检索对比
- 衰减曲线验证

真实接入：
- 词法 baseline：基于 token 重叠的关键词匹配（可运行，无外部依赖）
- 混合检索：接入 SearchRouter 五路（如可用）
- 衰减：engram.decay.compute_decay_multiplier

输出 markdown 报告模板（含具体数字）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.eval.test_set import get_eval_set, get_base_memories


def _tokenize(text: str) -> set[str]:
    """中文按字/词切分 + 英文按 token。简单实现：连续字符块。"""
    tokens = set()
    # 英文/数字 token
    for m in re.finditer(r"[a-zA-Z0-9_.@+-]+", text):
        tokens.add(m.group(0).lower())
    # 中文 2-gram（捕捉关键词）
    cn = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in cn:
        if len(seg) == 1:
            tokens.add(seg)
        else:
            for i in range(len(seg) - 1):
                tokens.add(seg[i : i + 2])
    return tokens


def lexical_search(query: str, memories: list[dict], top_k: int = 5) -> list[str]:
    """词法 baseline：token 重叠分数排序。可运行、无依赖。"""
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []
    scored = []
    for mem in memories:
        m_tokens = _tokenize(mem["text"])
        overlap = len(q_tokens & m_tokens)
        if overlap > 0:
            scored.append((overlap, mem["id"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [mid for _, mid in scored[:top_k]]


def run_eval(
    vector_store=None,
    embedding_provider=None,
    top_k: int = 5,
    output_path: Optional[str] = None,
) -> dict:
    """
    运行评测。

    Args:
        vector_store: VectorStore 实例（可选，用于混合检索对比）
        embedding_provider: EmbeddingProvider 实例（可选）
        top_k: 检索 top-k
        output_path: 报告输出路径（可选）

    Returns:
        评测结果 dict（含具体数字）
    """
    test_set = get_eval_set()
    memories = get_base_memories()

    # 词法检索召回率
    lexical_hits = 0
    semantic_lexical_hits = 0
    for item in test_set:
        results = lexical_search(item["query"], memories, top_k=top_k)
        hit = bool(set(results) & set(item["expected"]))
        if hit:
            lexical_hits += 1
        if item["category"] == "fact_semantic" and hit:
            semantic_lexical_hits += 1

    total = len(test_set)
    lexical_recall = lexical_hits / total if total else 0

    # 混合检索（如果提供了向量组件）
    hybrid_recall = None
    semantic_hybrid_hits = None
    if vector_store is not None and embedding_provider is not None:
        hybrid_hits = 0
        sem_hybrid = 0
        for item in test_set:
            results = _hybrid_search(item["query"], memories, vector_store, embedding_provider, top_k)
            hit = bool(set(results) & set(item["expected"]))
            if hit:
                hybrid_hits += 1
            if item["category"] == "fact_semantic" and hit:
                sem_hybrid += 1
        hybrid_recall = hybrid_hits / total if total else 0
        semantic_hybrid_hits = sem_hybrid

    # 语义改写类单独统计
    sem_total = sum(1 for i in test_set if i["category"] == "fact_semantic")
    sem_lexical_recall = semantic_lexical_hits / sem_total if sem_total else 0

    # 衰减验证
    from nexsandglass.engram.types import Memory, MemoryType
    from nexsandglass.engram.decay import compute_decay_multiplier

    old_memory = Memory(
        memory_id="old_event",
        type="episodic",
        content="30 天前的事件",
        created_at="2026-06-01T00:00:00+00:00",
        decay_weight=1.0,
    )
    multiplier = compute_decay_multiplier("episodic", 0.0, 0, 24 * 30)
    decayed_weight = old_memory.decay_weight * multiplier

    # 语义改写类：词法 vs 混合对比
    comparison = None
    if hybrid_recall is not None and sem_total:
        comparison = {
            "semantic_category_total": sem_total,
            "semantic_lexical_hits": semantic_lexical_hits,
            "semantic_hybrid_hits": semantic_hybrid_hits,
            "semantic_lexical_recall": round(sem_lexical_recall, 4),
            "semantic_hybrid_recall": round(semantic_hybrid_hits / sem_total, 4),
        }

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "top_k": top_k,
        "test_set_size": total,
        "lexical_recall": round(lexical_recall, 4),
        "hybrid_recall": round(hybrid_recall, 4) if hybrid_recall is not None else None,
        "comparison": comparison,
        "decay_verification": {
            "original_weight": old_memory.decay_weight,
            "decayed_weight": round(decayed_weight, 6),
            "multiplier": round(multiplier, 6),
            "decayed": decayed_weight < old_memory.decay_weight,
        },
    }

    if output_path:
        _write_report(report, output_path)

    return report


def _hybrid_search(query, memories, vector_store, embedding_provider, top_k):
    """混合检索：词法 + 向量 RRF 融合。"""
    # 词法部分
    lexical = lexical_search(query, memories, top_k=top_k)
    # 向量部分（如果 provider 可用）
    vector_ids = []
    try:
        if embedding_provider.available:
            q_emb = embedding_provider.encode_one(query)
            if q_emb:
                vec_results = vector_store.search(q_emb, top_k=top_k)
                vector_ids = [mid for mid, _ in vec_results]
    except Exception:
        vector_ids = []
    # RRF 融合
    from nexsandglass.core.vector_search import rrf_fusion
    fused = rrf_fusion([lexical, vector_ids])
    return fused[:top_k]


def _write_report(report: dict, path: str) -> None:
    """生成 markdown 评测报告。"""
    hybrid_line = f"| 混合检索召回率 | {report['hybrid_recall']:.2%} |" if report["hybrid_recall"] is not None else "| 混合检索召回率 | 未启用（需向量组件） |"
    comp_section = ""
    if report.get("comparison"):
        cmp = report["comparison"]
        comp_section = f"""
## 语义改写类对比（词法 vs 混合）

| 指标 | 词法 | 混合 |
|------|------|------|
| 命中数 | {cmp['semantic_lexical_hits']}/{cmp['semantic_category_total']} | {cmp['semantic_hybrid_hits']}/{cmp['semantic_category_total']} |
| 召回率 | {cmp['semantic_lexical_recall']:.2%} | {cmp['semantic_hybrid_recall']:.2%} |
"""
    md = f"""# Nyx 记忆系统评测报告

**生成时间**: {report['timestamp']}
**测试集大小**: {report['test_set_size']} 条
**Top-K**: {report['top_k']}

## 检索召回率

| 指标 | 值 |
|------|-----|
| 词法检索召回率 | {report['lexical_recall']:.2%} |
{hybrid_line}

## 衰减曲线验证

| 指标 | 值 |
|------|-----|
| 原始权重 | {report['decay_verification']['original_weight']} |
| 30 天后权重 | {report['decay_verification']['decayed_weight']} |
| 衰减系数 | {report['decay_verification']['multiplier']} |
| 是否衰减 | {'✅ 是' if report['decay_verification']['decayed'] else '❌ 否'} |
{comp_section}
---
*Generated by tests/eval/run_eval.py*
"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)


if __name__ == "__main__":
    report = run_eval()
    print(json.dumps(report, indent=2, ensure_ascii=False))
