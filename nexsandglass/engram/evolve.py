"""
engram/evolve.py — Nyx 记忆自我演化协调器

将四个闭环串成一条可调用的演化流水线，让记忆从"功能堆积"变成
"自我演化"：

  run_evolution_pass()  — 完整演化回合（梦境/维护时调用）：
      Loop 2: 重分类 + 合并 + 关系发现
      Loop 4: 老化降权 + 归档候选
      Loop 1: 新关系写入图谱
      Loop 3: 画像加权

  recall_pass()         — 检索后调用（轻量）：
      Loop 4: 召回反馈（提升重要性）

  fact_pass()           — 事实写入时调用：
      Loop 1: 事实 → 图谱 + 冲突检测

所有存储操作通过注入的适配器回调执行，本模块保持纯逻辑可测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .types import Memory
from .loops.dream_engram import (
    dream_reclassify,
    dream_consolidate,
    dream_relation_discovery,
    DreamReport,
)
from .loops.recall_writer import (
    recall_feedback,
    age_and_demote,
    RecallFeedbackReport,
)
from .loops.fact_thread import fact_to_thread, thread_validate_fact, FactThreadReport
from .loops.persona_ctx import persona_weight_context, PersonaWeightReport


@dataclass
class EvolutionReport:
    """一次完整演化回合的报告"""

    dream: DreamReport = field(default_factory=DreamReport)
    recall: RecallFeedbackReport = field(default_factory=RecallFeedbackReport)
    fact_thread: FactThreadReport = field(default_factory=FactThreadReport)
    persona: PersonaWeightReport = field(default_factory=PersonaWeightReport)
    summary: dict = field(default_factory=dict)


def run_evolution_pass(
    memories: list[Memory],
    persona_entries: list[str] | None = None,
    thread_store: Optional[Callable[[str, str, str], None]] = None,
    thread_query: Optional[Callable[[Optional[str], Optional[str], int], list]] = None,
) -> tuple[list[Memory], EvolutionReport]:
    """
    完整演化回合（梦境 cron / 每日维护调用）。

    Args:
        memories: 当前活跃记忆列表
        persona_entries: 画像条目（供 Loop 3）
        thread_store / thread_query: 织线图谱适配器（供 Loop 1）

    Returns:
        (演化后的记忆列表, 汇总报告)
    """
    report = EvolutionReport()
    evolved = list(memories)

    # ── Loop 2: 梦境加工（重分类 + 合并 + 关系发现）──
    dream = dream_reclassify(evolved)
    report.dream.reclassified = dream.reclassified
    consolidate = dream_consolidate(evolved)
    report.dream.consolidated_groups = consolidate.consolidated_groups
    rel = dream_relation_discovery(evolved)
    report.dream.relations_found = rel.relations_found

    # 关系发现结果写入图谱（Loop 2 → Loop 1 联动）
    if thread_store and rel.relations_found:
        for subj, relation, obj in rel.relations_found:
            thread_store(subj, relation, obj)

    # ── Loop 4: 老化降权 + 归档候选 ──
    evolved, recall = age_and_demote(evolved)
    report.recall = recall

    # ── Loop 3: 画像加权 ──
    if persona_entries:
        evolved, persona = persona_weight_context(evolved, persona_entries)
        report.persona = persona

    report.summary = {
        "reclassified": len(report.dream.reclassified),
        "consolidated_groups": len(report.dream.consolidated_groups),
        "relations_found": len(report.dream.relations_found),
        "demoted": len(report.recall.demoted_ids),
        "archive_candidates": len(report.recall.archive_candidates),
        "persona_boosted": len(report.persona.boosted_ids),
    }

    return evolved, report


def recall_pass(
    memories: list[Memory],
    recalled_ids: list[str],
) -> tuple[list[Memory], RecallFeedbackReport]:
    """检索后调用：成功召回 → 提升重要性（Loop 4 轻量版）。"""
    return recall_feedback(memories, recalled_ids)


def fact_pass(
    fact_text: str,
    thread_store: Callable[[str, str, str], None],
    thread_query: Callable[[Optional[str], Optional[str], int], list],
    validate_first: bool = True,
) -> FactThreadReport:
    """
    事实写入时调用：先用图谱验证（Loop 1 方向 B），再同步图谱（方向 A）。

    validate_first=True 时，验证结果为 conflict 的三元组不写入图谱
    （返回报告由调用方决定是否仍存储事实）。
    """
    # 方向 B：先验证
    report = FactThreadReport()
    if validate_first:
        report = thread_validate_fact(fact_text, thread_query)

    # 方向 A：同步图谱（仅 unknown / confirm 的写入）
    to_store = fact_to_thread(fact_text, thread_store, thread_query)
    report.triples_extracted = to_store.triples_extracted
    report.triples_stored = to_store.triples_stored
    report.conflicts = to_store.conflicts

    # 合并验证结果到报告
    if validate_first:
        report.validations = report.validations or []

    return report
