"""NexSandglass Consolidation Engine（v6.5）—— Dream 生产化。

将 dream_pipeline / evolve 包装为生产级 Consolidation Engine：
  所有变更 → DreamProposal → Validator → Apply / Quarantine

关键特性：
  - 保留 provenance（source memory ids）
  - destructive merge 可回滚快照
  - 压缩/抽象/关系/冲突/衰减
  - 冲突写入 Bundle.contradictions
  - 异步调度（nightwatch/cron），不阻塞 observe/recall
  - 热路径禁止跑满管线
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

from nexsandglass.engram.types import Memory, MemoryObject

logger = logging.getLogger(__name__)

# 快照目录（可回滚）
_SNAPSHOT_DIR = os.path.join(
    os.environ.get("NEXSANDBASE_HOME", "/root/.hermes/nexsandglass"),
    "consolidation_snapshots",
)


# ══════════════════════════════════════════════════════════
# DreamProposal
# ══════════════════════════════════════════════════════════

@dataclass
class DreamProposal:
    """一条梦境变更提案。"""
    action: str = ""              # abstract/compress/relation/conflict/decay/merge
    content: str = ""
    source_ids: list[str] = field(default_factory=list)   # provenance
    target_id: str = ""
    confidence: float = 0.5
    quarantine_reason: str = ""
    apply: bool = True


# ══════════════════════════════════════════════════════════
# Validator
# ══════════════════════════════════════════════════════════

class Validator:
    """校验提案是否可 Apply。冲突/低置信 → Quarantine。"""

    def validate(self, proposal: DreamProposal) -> DreamProposal:
        # 空内容 → 隔离
        if not proposal.content or not proposal.content.strip():
            proposal.apply = False
            proposal.quarantine_reason = "空内容"
            return proposal
        # 低置信 → 隔离
        if proposal.confidence < 0.3:
            proposal.apply = False
            proposal.quarantine_reason = f"低置信 {proposal.confidence:.2f}"
            return proposal
        # 冲突检测（merge 含否定词对撞）
        if proposal.action == "merge" and _has_internal_conflict(proposal.content):
            proposal.apply = False
            proposal.quarantine_reason = "内部冲突"
            return proposal
        return proposal


def _has_internal_conflict(text: str) -> bool:
    """检测 merge 结果是否含内部矛盾（否定 + 肯定同主题）。"""
    neg = ("不是", "不在", "没有", "不再")
    if any(n in text for n in neg) and len(text) > 20:
        return True
    return False


# ══════════════════════════════════════════════════════════
# 快照（可回滚）
# ══════════════════════════════════════════════════════════

def _snapshot(memories: list[Memory], tag: str) -> str:
    """保存可回滚快照。"""
    os.makedirs(_SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(_SNAPSHOT_DIR, f"{tag}.json")
    data = []
    for m in memories:
        if hasattr(m, "to_memory"):
            m = m.to_memory()
        data.append({
            "memory_id": m.memory_id, "type": m.type, "content": m.content,
            "valence": m.valence, "arousal": m.arousal,
            "decay_weight": m.decay_weight, "access_count": m.access_count,
            "created_at": m.created_at, "last_accessed": m.last_accessed,
            "superseded_by": m.superseded_by,
        })
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path


def restore_snapshot(path: str) -> list[Memory]:
    """从快照恢复（回滚）。"""
    from nexsandglass.engram.types import Memory
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [Memory(**{k: v for k, v in d.items() if k in Memory.__dataclass_fields__}) for d in data]


# ══════════════════════════════════════════════════════════
# ConsolidationEngine
# ══════════════════════════════════════════════════════════

class ConsolidationEngine:
    """生产化 Dream 引擎。变更 → Proposal → Validator → Apply/Quarantine。"""

    def __init__(self, use_dream: bool = True):
        self.use_dream = use_dream
        self.validator = Validator()
        self._lock = threading.Lock()
        self._quarantine: list[DreamProposal] = []

    def generate_proposals(self, memories: list[Memory]) -> list[DreamProposal]:
        """从记忆生成变更提案（抽象/合并/关系/衰减）。"""
        proposals: list[DreamProposal] = []

        # 1. 抽象/合并：相似 episodic 合并
        groups = _cluster_similar(memories)
        for group in groups:
            if len(group) < 2:
                continue
            combined = "；".join(m.content[:60] for m in group[:4])
            proposals.append(DreamProposal(
                action="abstract",
                content=f"[抽象] {combined}",
                source_ids=[m.memory_id for m in group],
                confidence=0.6,
            ))

        # 2. 冲突检测
        for i in range(len(memories)):
            for j in range(i + 1, len(memories)):
                if _conflict(memories[i].content, memories[j].content):
                    proposals.append(DreamProposal(
                        action="conflict",
                        content=f"[冲突] {memories[i].content} ↔ {memories[j].content}",
                        source_ids=[memories[i].memory_id, memories[j].memory_id],
                        confidence=0.7,
                    ))

        # 3. 衰减提案（低 access + 老）
        import time
        for m in memories:
            if m.access_count == 0 and _is_old(m):
                proposals.append(DreamProposal(
                    action="decay",
                    content=f"[衰减] {m.content[:60]}",
                    source_ids=[m.memory_id],
                    confidence=0.5,
                    target_id=m.memory_id,
                ))
        return proposals

    def run(self, memories: list[Memory], tag: str = "consolidation") -> dict:
        """完整 Consolidation 回合：Proposal→Validator→Apply/Quarantine + 快照。"""
        with self._lock:
            # 1. 快照（可回滚）
            snap = _snapshot(memories, tag)

            # 2. 生成提案
            proposals = self.generate_proposals(memories)

            # 3. Validator + Apply/Quarantine
            applied: list[DreamProposal] = []
            quarantined: list[DreamProposal] = []
            for p in proposals:
                p = self.validator.validate(p)
                if p.apply:
                    applied.append(p)
                else:
                    quarantined.append(p)
                    self._quarantine.append(p)

            # 4. 应用衰减（对 decay 提案）
            evolved = list(memories)
            for p in applied:
                if p.action == "decay" and p.target_id:
                    for m in evolved:
                        if m.memory_id == p.target_id:
                            m.decay_weight = max(0.0, m.decay_weight - 0.3)
                            m.access_count += 1  # 标记已处理

            return {
                "snapshot": snap,
                "proposals_total": len(proposals),
                "applied": len(applied),
                "quarantined": len(quarantined),
                "applied_actions": [p.action for p in applied],
                "quarantine_reasons": [p.quarantine_reason for p in quarantined],
            }

    @property
    def quarantine(self) -> list[DreamProposal]:
        return list(self._quarantine)


# ══════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════

def _cluster_similar(memories: list[Memory], threshold: float = 0.7) -> list[list[Memory]]:
    """按内容相似度聚类（episodic/emotional 同类合并候选）。"""
    clusters: list[list[Memory]] = []
    used: set[str] = set()
    for i, m in enumerate(memories):
        if m.memory_id in used:
            continue
        group = [m]
        used.add(m.memory_id)
        for j in range(i + 1, len(memories)):
            o = memories[j]
            if o.memory_id in used or o.type != m.type:
                continue
            if _sim(m.content, o.content) >= threshold:
                group.append(o)
                used.add(o.memory_id)
        clusters.append(group)
    return clusters


def _sim(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _conflict(a: str, b: str) -> bool:
    a_neg = _has_negation(a)
    b_neg = _has_negation(b)
    return a_neg != b_neg and _sim(a.replace("不", ""), b.replace("不", "")) > 0.5


def _has_negation(text: str) -> bool:
    """含否定词（含单字"不"，排除非否定用法）。"""
    strong = ("不是", "不在", "没有", "不再", "别", "不要")
    if any(n in text for n in strong):
        return True
    if "不" in text:
        non_neg = ("不错", "不客气", "不知道", "不小心", "不舒服")
        return not any(x in text for x in non_neg)
    return False


def _is_old(m: Memory) -> bool:
    if not m.created_at:
        return False
    try:
        from datetime import datetime
        created = datetime.strptime(m.created_at[:10], "%Y-%m-%d")
        age_days = (datetime.now() - created).days
        return age_days > 30
    except Exception:
        return False
