"""
engram/loops/dream_engram.py — 闭环 2：Dream ↔ Engram

梦境不再只是"总结今天做了什么"，而是触发记忆的深度加工：

  1. dream_reclassify()    — 重分类：高频重复的 episodic 事件提炼为 semantic 稳定事实
  2. dream_consolidate()   — 合并：相似 episodic/emotional 记忆合并（贪心配对）
  3. dream_relation_discovery() — 关系发现：从合并后的记忆中提取新图谱关系

设计：纯函数 + 注入回调，便于测试与对接现有 nightwatch / weavethread。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..types import Memory, MemoryType

# 提炼阈值：同一语义内容出现 N 次以上 → 可提炼为稳定事实
RECLASSIFY_MIN_OCCURRENCE: int = 3
# 提炼后保留的语义内容前缀
RECLASSIFY_KEEP_PREFIX: str = "事实："


@dataclass
class DreamReport:
    """闭环 2 报告"""

    reclassified: list[dict] = field(default_factory=list)   # {old_id, new_type, new_content}
    consolidated_groups: list[list[str]] = field(default_factory=list)  # [[ids...]]
    relations_found: list[tuple[str, str, str]] = field(default_factory=list)


# ── 1. 重分类：episodic → semantic ─────────────────────────────

def dream_reclassify(
    memories: list[Memory],
    min_occurrence: int = RECLASSIFY_MIN_OCCURRENCE,
    semantic_rule: Optional[Callable[[str], str]] = None,
) -> DreamReport:
    """
    统计 episodic 记忆中语义近似的内容（按内容归一化分组），
    出现 >= min_occurrence 次 → 提炼为 semantic 事实。

    返回报告；调用方决定是否实际执行转换（新 semantic + 旧 episodic 沉底）。
    """
    report = DreamReport()

    # 内容归一化：去时间前缀、去语气词
    def _norm(content: str) -> str:
        c = content.strip()
        # 去掉日期前缀（2026-07-31 或 7月14日）
        import re
        c = re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", c)
        c = re.sub(r"^\d{1,2}月\d{1,2}日\s*", "", c)
        return c

    groups: dict[str, list[Memory]] = {}
    for mem in memories:
        if mem.type != MemoryType.EPISODIC.value:
            continue
        if mem.superseded_by is not None:
            continue
        key = _norm(mem.content)
        if not key:
            continue
        groups.setdefault(key, []).append(mem)

    for key, group in groups.items():
        if len(group) < min_occurrence:
            continue
        # 提炼：取最新一条的内容（或自定义规则）
        latest = max(group, key=lambda m: m.created_at)
        new_content = semantic_rule(key) if semantic_rule else key
        report.reclassified.append(
            {
                "old_ids": [m.memory_id for m in group],
                "new_type": MemoryType.SEMANTIC.value,
                "new_content": new_content,
                "occurrence": len(group),
            }
        )

    return report


# ── 2. 合并：相似记忆贪心配对 ──────────────────────────────────

def dream_consolidate(
    memories: list[Memory],
    threshold: float = 0.92,
    similarity_fn: Optional[Callable[[Memory, Memory], float]] = None,
) -> DreamReport:
    """
    相似 episodic/emotional 记忆合并（贪心配对，只合并同类）。

    返回报告（分组）；调用方可用 LLM 做信息并集融合。
    """
    from ..writer import compute_similarity

    report = DreamReport()
    used: set[str] = set()

    for i, mem in enumerate(memories):
        if mem.memory_id in used:
            continue
        if mem.type not in (MemoryType.EPISODIC.value, MemoryType.EMOTIONAL.value):
            continue
        if mem.superseded_by is not None:
            continue
        group_ids = [mem.memory_id]
        used.add(mem.memory_id)
        for j in range(i + 1, len(memories)):
            other = memories[j]
            if other.memory_id in used:
                continue
            if other.type != mem.type:
                continue
            sim = (
                similarity_fn(mem, other)
                if similarity_fn
                else compute_similarity(mem, other)
            )
            if sim >= threshold:
                group_ids.append(other.memory_id)
                used.add(other.memory_id)
        if len(group_ids) > 1:
            report.consolidated_groups.append(group_ids)

    return report


# ── 3. 关系发现：记忆 → 新图谱关系 ─────────────────────────────

def dream_relation_discovery(
    memories: list[Memory],
    extract_fn: Optional[Callable[[str], list[tuple[str, str, str]]]] = None,
) -> DreamReport:
    """
    从记忆文本中提取新关系（默认用 fact_thread.extract_triples）。

    调用方拿到 relations_found 后可写入织线图谱。
    """
    from .fact_thread import extract_triples

    report = DreamReport()
    fn = extract_fn or extract_triples

    for mem in memories:
        if mem.type not in (
            MemoryType.SEMANTIC.value,
            MemoryType.PROCEDURAL.value,
        ):
            continue
        for triple in fn(mem.content):
            report.relations_found.append(triple)

    return report
