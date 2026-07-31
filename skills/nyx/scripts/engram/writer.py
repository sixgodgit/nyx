"""
engram/writer.py — 差异化写入策略（记忆分类加工）

按记忆类型执行不同策略（Tulving 模型 + 去重/覆盖/强化）：

  semantic   — 覆盖检测：新事实插入，旧的高相似记忆标记 superseded_by
  emotional  — 强化检测：命中高相似旧记忆 → 提升其 decay_weight；未命中 → 插入
  procedural — 去重检测：近重复 → 只 mark_accessed（计访问），不写入
  episodic   — 直插：直接写入，不可覆盖，只能随时间衰减沉底

本层纯函数：返回 WriteReport + 建议动作，实际存储由调用方执行。
"""

from __future__ import annotations

import hashlib
from typing import Callable, Optional

from .types import Memory, MemoryType, WriteAction, WriteReport

# ── 差异化写入阈值（对齐 EngramTide）────────────────────────

SEMANTIC_OVERRIDE_THRESHOLD: float = 0.85   # semantic 覆盖检测
EMOTIONAL_REINFORCE_THRESHOLD: float = 0.85 # emotional 强化匹配
EMOTIONAL_REINFORCE_BOOST: float = 0.2      # 强化时 decay_weight 提升量
PROCEDURAL_DEDUP_THRESHOLD: float = 0.90    # procedural 写入去重

# 类型默认相似度函数（无 embedding 时用字符重叠的 fallback）
def _char_overlap(a: str, b: str) -> float:
    """无向量时的退化相似度：字符集 Jaccard。"""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5 or 1.0
    nb = sum(x * x for x in b) ** 0.5 or 1.0
    return dot / (na * nb)


def compute_similarity(
    new: Memory,
    old: Memory,
    similarity_fn: Optional[Callable[[list[float], list[float]], float]] = None,
) -> float:
    """优先向量相似度；退化到字符重叠。"""
    if new.embedding and old.embedding:
        fn = similarity_fn or _cosine
        return fn(new.embedding, old.embedding)
    return _char_overlap(new.content, old.content)


def _make_memory_id(content: str, mem_type: str) -> str:
    """稳定 ID：类型 + 内容哈希。"""
    digest = hashlib.sha1(f"{mem_type}:{content}".encode("utf-8")).hexdigest()[:16]
    return f"{mem_type}-{digest}"


def classify_write(
    new_mem: Memory,
    existing: list[Memory],
    similarity_fn: Optional[Callable[[list[float], list[float]], float]] = None,
) -> tuple[WriteAction, WriteReport, Optional[Memory]]:
    """
    核心分类写入逻辑：根据类型 + 与现有记忆的相似度决定动作。

    返回 (action, report, target_old_memory)。
    - INSERT: 直接插入 new_mem（调用方存储）
    - OVERRIDE: new_mem 插入 + existing 中高相似 semantic 标记 superseded
    - REINFORCE: 不插入新记忆，强化 matched 旧记忆（返回 matched）
    - DEDUP: 不写入，只计访问（返回 matched）
    """
    report = WriteReport()

    if new_mem.type == MemoryType.PROCEDURAL.value:
        # 去重检测：近重复只计访问
        best, best_sim = _best_match(new_mem, existing, similarity_fn)
        if best and best_sim >= PROCEDURAL_DEDUP_THRESHOLD:
            report.action = WriteAction.DEDUP
            report.deduped_id = best.memory_id
            report.similarity = round(best_sim, 4)
            return report.action, report, best
        report.action = WriteAction.INSERT
        return report.action, report, None

    if new_mem.type == MemoryType.EMOTIONAL.value:
        # 强化检测
        best, best_sim = _best_match(new_mem, existing, similarity_fn)
        if best and best_sim >= EMOTIONAL_REINFORCE_THRESHOLD:
            report.action = WriteAction.REINFORCE
            report.reinforced_id = best.memory_id
            report.reinforced_boost = EMOTIONAL_REINFORCE_BOOST
            report.similarity = round(best_sim, 4)
            return report.action, report, best
        report.action = WriteAction.INSERT
        return report.action, report, None

    if new_mem.type == MemoryType.SEMANTIC.value:
        # 覆盖检测：找出所有高相似旧 semantic
        superseded: list[str] = []
        for old in existing:
            if old.type != MemoryType.SEMANTIC.value:
                continue
            if old.superseded_by is not None:
                continue
            sim = compute_similarity(new_mem, old, similarity_fn)
            if sim >= SEMANTIC_OVERRIDE_THRESHOLD:
                superseded.append(old.memory_id)
        report.action = WriteAction.OVERRIDE if superseded else WriteAction.INSERT
        report.superseded_ids = superseded
        return report.action, report, None

    # episodic 及未知类型：直插
    report.action = WriteAction.INSERT
    return report.action, report, None


def _best_match(
    new_mem: Memory,
    existing: list[Memory],
    similarity_fn: Optional[Callable[[list[float], list[float]], float]] = None,
) -> tuple[Optional[Memory], float]:
    """在同类记忆中找最相似的一条。"""
    best: Optional[Memory] = None
    best_sim = 0.0
    for old in existing:
        if old.type != new_mem.type:
            continue
        if old.superseded_by is not None:
            continue
        sim = compute_similarity(new_mem, old, similarity_fn)
        if sim > best_sim:
            best_sim = sim
            best = old
    return best, best_sim


def write_memory_classified(
    content: str,
    mem_type: str,
    existing: list[Memory],
    valence: float = 0.0,
    arousal: float = 0.0,
    tags: Optional[list[str]] = None,
    similarity_fn: Optional[Callable[[list[float], list[float]], float]] = None,
) -> tuple[WriteAction, WriteReport, Optional[Memory]]:
    """
    高层入口：从 content 构建 Memory 并执行分类写入。

    调用方拿到 (action, report, target) 后：
      - INSERT / OVERRIDE → 存储 new_mem（OVERRIDE 还需把 report.superseded_ids
        标记为 superseded_by=new_mem.memory_id）
      - REINFORCE → 提升 target.decay_weight += report.reinforced_boost（封顶 1.0）
      - DEDUP → target.access_count += 1（只计访问）
    """
    new_mem = Memory(
        memory_id=_make_memory_id(content, mem_type),
        type=mem_type,
        content=content,
        valence=valence,
        arousal=arousal,
        tags=list(tags or []),
    )
    return classify_write(new_mem, existing, similarity_fn)


def consolidate_similar(
    memories: list[Memory],
    threshold: float = 0.92,
    similarity_fn: Optional[Callable[[list[float], list[float]], float]] = None,
) -> list[list[Memory]]:
    """
    合并相似记忆（贪心配对，只合并同类 episodic/emotional）。

    返回合并后的分组列表；调用方可对每组做 LLM 融合（信息并集）。
    保守阈值：宁可不合并，不错误合并。
    """
    groups: list[list[Memory]] = []
    used: set[str] = set()

    for i, mem in enumerate(memories):
        if mem.memory_id in used:
            continue
        if mem.type not in (MemoryType.EPISODIC.value, MemoryType.EMOTIONAL.value):
            continue
        group = [mem]
        used.add(mem.memory_id)
        for j in range(i + 1, len(memories)):
            other = memories[j]
            if other.memory_id in used:
                continue
            if other.type != mem.type:
                continue
            sim = compute_similarity(mem, other, similarity_fn)
            if sim >= threshold:
                group.append(other)
                used.add(other.memory_id)
        if len(group) > 1:
            groups.append(group)

    return groups


def supersede_memories(
    memories: list[Memory],
    old_ids: list[str],
    new_id: str,
) -> list[Memory]:
    """将 old_ids 对应的记忆标记为 superseded_by=new_id。"""
    out: list[Memory] = []
    for mem in memories:
        if mem.memory_id in old_ids:
            mem.superseded_by = new_id
        out.append(mem)
    return out
