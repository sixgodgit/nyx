"""
engram/loops/persona_ctx.py — 闭环 3：Persona ↔ Context

画像动态影响 Context Composer，而不是仅作为查询结果存在：

  1. persona_weight_context()  — 用画像高置信条目加权 Context 组装：
     画像中确认的 semantic 事实 → 提升其在上下文中的排序权重；
     画像中已过时/矛盾的记忆 → 降权。

  2. persona_trigger_rebuild() — 画像变更时触发：找出与画像冲突的
     旧记忆（如邮箱已换），标记为待 supersede / 降权。

设计：纯函数 + 注入画像提供者，便于测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..types import Memory, MemoryType

# 画像确认事实的权重加成
PERSONA_CONFIRMED_BOOST: float = 0.3
# 画像冲突事实的权重折扣
PERSONA_CONFLICT_DISCOUNT: float = 0.5


@dataclass
class PersonaWeightReport:
    """闭环 3 报告"""

    boosted_ids: list[str] = field(default_factory=list)
    demoted_ids: list[str] = field(default_factory=list)
    persona_changed: bool = False
    rebuild_candidates: list[dict] = field(default_factory=list)


# ── 1. 画像 → 上下文加权 ───────────────────────────────────────

def persona_weight_context(
    memories: list[Memory],
    persona_entries: list[str],
    match_fn: Optional[Callable[[Memory, str], float]] = None,
    boost: float = PERSONA_CONFIRMED_BOOST,
) -> tuple[list[Memory], PersonaWeightReport]:
    """
    用画像条目对记忆做加权：画像中明确存在的语义事实 → 权重加成；
    画像中出现但记忆内容明显矛盾的 → 降权。

    返回 (加权后的记忆列表副本, 报告)。
    """
    report = PersonaWeightReport()
    updated: list[Memory] = []

    def _default_match(mem: Memory, entry: str) -> float:
        # 简易匹配：画像条目关键词是否出现在记忆内容中
        key_terms = [t for t in entry.split() if len(t) >= 2][:5]
        hits = sum(1 for t in key_terms if t in mem.content)
        return hits / max(len(key_terms), 1) if key_terms else 0.0

    fn = match_fn or _default_match

    # 画像关键词索引（用于冲突检测：画像存在但记忆相反的信号词）
    negations = ("不是", "不再", "换成了", "已改", "已换", "改为", "变成")

    for mem in memories:
        new_mem = Memory(
            memory_id=mem.memory_id,
            type=mem.type,
            content=mem.content,
            valence=mem.valence,
            arousal=mem.arousal,
            created_at=mem.created_at,
            last_accessed=mem.last_accessed,
            access_count=mem.access_count,
            decay_weight=mem.decay_weight,
            embedding=mem.embedding,
            source_id=mem.source_id,
            unresolved=mem.unresolved,
            tags=list(mem.tags),
            superseded_by=mem.superseded_by,
        )

        # 只对 semantic / procedural 加权（稳定事实才与画像可比）
        if new_mem.type in (MemoryType.SEMANTIC.value, MemoryType.PROCEDURAL.value):
            best = 0.0
            for entry in persona_entries:
                s = fn(new_mem, entry)
                if s > best:
                    best = s
            if best >= 0.6:
                new_mem.decay_weight = min(new_mem.decay_weight + boost, 1.0)
                report.boosted_ids.append(new_mem.memory_id)
            elif any(neg in new_mem.content for neg in negations):
                # 记忆含"已改/不再"等信号 → 可能已过时
                new_mem.decay_weight *= PERSONA_CONFLICT_DISCOUNT
                report.demoted_ids.append(new_mem.memory_id)

        updated.append(new_mem)

    return updated, report


# ── 2. 画像变更 → 旧记忆重建 ───────────────────────────────────

def persona_trigger_rebuild(
    memories: list[Memory],
    persona_entries: list[str],
    changed_fields: list[str],
    match_fn: Optional[Callable[[Memory, str], float]] = None,
) -> PersonaWeightReport:
    """
    画像字段变更时调用：找出涉及变更字段的旧记忆，标记为重建候选。

    调用方对 candidates 做 supersede 或降权处理。
    """
    report = PersonaWeightReport()
    report.persona_changed = True

    for mem in memories:
        if mem.type != MemoryType.SEMANTIC.value:
            continue
        if mem.superseded_by is not None:
            continue
        for field in changed_fields:
            if field and field in mem.content:
                report.rebuild_candidates.append(
                    {
                        "memory_id": mem.memory_id,
                        "content": mem.content,
                        "changed_field": field,
                    }
                )
                break

    return report
