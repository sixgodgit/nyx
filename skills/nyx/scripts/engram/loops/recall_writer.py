"""
engram/loops/recall_writer.py — 闭环 4：Recall ↔ Writer

被频繁成功召回的记忆提升重要性；很少使用且长期无关的记忆逐渐衰减。

  1. recall_feedback() — 每次检索/召回成功后调用：命中记忆 access_count +1、
     decay_weight 提升（成功召回的"重要性信号"）。

  2. age_and_demote()  — 定期（梦境/维护 cron）调用：结合上次访问时间，
     对长期未召回的记忆加速衰减；从未被召回且权重低于阈值的记忆
     标记为待归档（可安全移出活跃集）。

设计：纯函数；importance 信号与 decay 引擎正交——recall 提升短期重要性，
age_and_demote 处理长期遗忘。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..types import Memory

# 成功召回一次的权重提升
RECALL_SUCCESS_BOOST: float = 0.05
# 长期未召回：天数阈值（超过则进入加速衰减）
DEMOTE_AFTER_DAYS: int = 30
# 加速衰减系数（相对 BASE_DECAY_RATE）
DEMOTE_ACCELERATION: float = 2.0
# 待归档权重阈值
ARCHIVE_BELOW_WEIGHT: float = 0.05
# 待归档最少天数
ARCHIVE_AFTER_DAYS: int = 90


@dataclass
class RecallFeedbackReport:
    """闭环 4 报告"""

    boosted_ids: list[str] = field(default_factory=list)
    demoted_ids: list[str] = field(default_factory=list)
    archive_candidates: list[str] = field(default_factory=list)


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ── 1. 召回反馈：成功召回 → 提升重要性 ─────────────────────────

def recall_feedback(
    memories: list[Memory],
    recalled_ids: list[str],
    boost: float = RECALL_SUCCESS_BOOST,
) -> tuple[list[Memory], RecallFeedbackReport]:
    """
    检索/召回成功后调用：命中记忆提升权重 + 计访问。

    返回 (更新后的记忆列表副本, 报告)。
    """
    report = RecallFeedbackReport()
    recalled_set = set(recalled_ids)
    updated: list[Memory] = []

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
        if new_mem.memory_id in recalled_set:
            new_mem.access_count += 1
            new_mem.decay_weight = min(new_mem.decay_weight + boost, 1.0)
            new_mem.last_accessed = datetime.now(timezone.utc).isoformat()
            report.boosted_ids.append(new_mem.memory_id)
        updated.append(new_mem)

    return updated, report


# ── 2. 老化降权：长期无关 → 加速衰减 / 归档 ───────────────────

def age_and_demote(
    memories: list[Memory],
    now: Optional[datetime] = None,
    demote_after_days: int = DEMOTE_AFTER_DAYS,
    acceleration: float = DEMOTE_ACCELERATION,
    archive_below: float = ARCHIVE_BELOW_WEIGHT,
    archive_after_days: int = ARCHIVE_AFTER_DAYS,
) -> tuple[list[Memory], RecallFeedbackReport]:
    """
    定期维护调用：长期未召回的 episodic/emotional 记忆加速衰减。

    - 超过 demote_after_days 未访问 → decay_weight 额外 ×(1/acceleration)
    - 从未被访问（access_count==0）且创建超过 archive_after_days、
      权重低于 archive_below → archive_candidates（待归档）
    - semantic/procedural 不参与降权（稳定事实/规则）

    返回 (更新后的列表副本, 报告)。
    """
    report = RecallFeedbackReport()
    now = now or datetime.now(timezone.utc)
    updated: list[Memory] = []

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

        # 稳定类型不参与
        if new_mem.type in ("semantic", "procedural"):
            updated.append(new_mem)
            continue

        # 归档候选：从未召回 + 超时 + 低权重
        created = _parse_iso(new_mem.created_at)
        created_days = (
            (now - created).total_seconds() / 86400.0 if created else 0.0
        )
        if (
            new_mem.access_count == 0
            and created_days >= archive_after_days
            and new_mem.decay_weight < archive_below
        ):
            report.archive_candidates.append(new_mem.memory_id)
            updated.append(new_mem)
            continue

        # 长期未访问 → 加速衰减
        last = _parse_iso(new_mem.last_accessed)
        if last is None:
            last = created or now
        idle_days = (now - last).total_seconds() / 86400.0
        if idle_days >= demote_after_days:
            new_mem.decay_weight *= 1.0 / acceleration
            report.demoted_ids.append(new_mem.memory_id)

        updated.append(new_mem)

    return updated, report
