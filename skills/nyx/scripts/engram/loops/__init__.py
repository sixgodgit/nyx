"""
engram/loops/__init__.py — 演化闭环集合

四个闭环（Nyx 记忆自我演化）：

  Loop 1  fact_thread   — Thread ↔ Fact Store：事实变化自动更新知识图谱，
                          图谱关系反向验证事实（冲突检测）
  Loop 2  dream_engram  — Dream ↔ Engram：梦境触发记忆重分类（episodic→semantic）、
                          相似合并、关系发现
  Loop 3  persona_ctx   — Persona ↔ Context：画像高置信条目动态加权 Context
                          组装，画像变更触发上下文重排
  Loop 4  recall_writer — Recall ↔ Writer：成功召回提升记忆重要性，
                          长期无关记忆加速衰减
"""

from .fact_thread import (
    fact_to_thread,
    thread_validate_fact,
    FactThreadReport,
)
from .dream_engram import (
    dream_reclassify,
    dream_consolidate,
    dream_relation_discovery,
    DreamReport,
)
from .persona_ctx import (
    persona_weight_context,
    persona_trigger_rebuild,
    PersonaWeightReport,
)
from .recall_writer import (
    recall_feedback,
    age_and_demote,
    RecallFeedbackReport,
)
from .temporal_fact import (
    resolve_temporal_conflict,
    ensure_temporal_columns,
    TEMPORAL_RELATIONS,
    TemporalFactReport,
)

__all__ = [
    # Loop 1
    "fact_to_thread",
    "thread_validate_fact",
    "FactThreadReport",
    # Loop 2
    "dream_reclassify",
    "dream_consolidate",
    "dream_relation_discovery",
    "DreamReport",
    # Loop 3
    "persona_weight_context",
    "persona_trigger_rebuild",
    "PersonaWeightReport",
    # Loop 4
    "recall_feedback",
    "age_and_demote",
    "RecallFeedbackReport",
    # Temporal facts (Task 5)
    "resolve_temporal_conflict",
    "ensure_temporal_columns",
    "TEMPORAL_RELATIONS",
    "TemporalFactReport",
]
