"""
engram/dream_pipeline.py — 梦境管线（hypnos × engram 融合）

将 hypnos-dream-system 的三女神 prompt 流程与 engram 确定性加工
串成一条完整管线，消除重叠、各取所长：

  Phase 1  Mnemosyne（记忆女神）  — 浅睡总结：从 day_log 提取
            已完成/未完成/新知事实/待确认（prompt 层，输出结构化）
  Phase 2  Epimetheus（后见之神） — 深睡内化：确定性加工
            dream_reclassify（episodic≥3 次 → semantic）
            dream_consolidate（相似合并）
            dream_relation_discovery（关系发现 → 织线图谱）
  Phase 3  Prometheus（先见之神） — 快速眼动：灵感联结
            从 emotional 记忆中生成跨域联想（可注入 LLM 回调）
  Phase 4  演化闭环                — 联动 engram.evolve：
            老化降权 + 画像加权 + 图谱同步

设计原则：
- prompt 层负责"理解与创造"（LLM 泛化能力）
- 代码层负责"确定性与持久化"（重分类/合并/衰减/图谱）
- 纯函数 + 注入回调，可测试
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .types import Memory, MemoryType
from .loops.dream_engram import (
    dream_reclassify,
    dream_consolidate,
    dream_relation_discovery,
    DreamReport,
)
from .loops.recall_writer import age_and_demote, RecallFeedbackReport


@dataclass
class MnemosyneSummary:
    """Phase 1 输出：浅睡总结（结构化）"""

    completed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    new_facts: list[str] = field(default_factory=list)
    to_confirm: list[str] = field(default_factory=list)


@dataclass
class DreamPipelineReport:
    """完整梦境管线报告"""

    mnemosyne: MnemosyneSummary = field(default_factory=MnemosyneSummary)
    dream: DreamReport = field(default_factory=DreamReport)
    recall: RecallFeedbackReport = field(default_factory=RecallFeedbackReport)
    inspiration: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


# ── Phase 1：Mnemosyne 浅睡总结（规则式辅助提取）───────────────

def _extract_fact_like(text: str) -> list[str]:
    """从对话记录中提取"事实陈述"候选（规则式，供 prompt 参考）。"""
    facts = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 含"是/住在/使用/有/喜欢/需要"等陈述动词
        if re.search(r"(是|住在|位于|使用|用的是|喜欢|需要|想要|计划|买了|定了|预约了|申请了)", line):
            # 过滤明显非事实（问句/命令）
            if not line.startswith(("?", "！", "怎么", "为什么", "如何")):
                if len(line) >= 6:
                    facts.append(line)
    return facts[:10]


def mnemosyne_summarize(
    day_log: str,
    llm_extract: Optional[Callable[[str], MnemosyneSummary]] = None,
) -> MnemosyneSummary:
    """
    Phase 1：浅睡总结。

    - 有 llm_extract 回调（LLM prompt 层）→ 用它做智能总结
    - 无回调（纯代码模式）→ 规则式提取事实候选
    """
    if llm_extract:
        return llm_extract(day_log)

    facts = _extract_fact_like(day_log)
    return MnemosyneSummary(
        completed=[],
        pending=[],
        new_facts=facts,
        to_confirm=[],
    )


# ── Phase 2：Epimetheus 深睡内化（确定性加工）──────────────────

def epimetheus_internalize(
    memories: list[Memory],
    min_occurrence: int = 3,
    consolidate_threshold: float = 0.92,
) -> DreamReport:
    """
    Phase 2：深睡内化。

    1. dream_reclassify — 高频 episodic → semantic 稳定事实
    2. dream_consolidate — 相似 episodic/emotional 合并
    3. dream_relation_discovery — 提取新图谱关系
    """
    report = DreamReport()

    reclass = dream_reclassify(memories, min_occurrence=min_occurrence)
    report.reclassified = reclass.reclassified

    consol = dream_consolidate(memories, threshold=consolidate_threshold)
    report.consolidated_groups = consol.consolidated_groups

    rel = dream_relation_discovery(memories)
    report.relations_found = rel.relations_found

    return report


# ── Phase 3：Prometheus 快速眼动（灵感联结）────────────────────

def prometheus_inspire(
    memories: list[Memory],
    llm_connect: Optional[Callable[[list[Memory]], list[str]]] = None,
    max_emotional: int = 5,
) -> list[str]:
    """
    Phase 3：快速眼动。

    - 有 llm_connect 回调 → LLM 做跨域联想（hypnos 原 Prometheus prompt）
    - 无回调（代码模式）→ 从高唤醒 emotional 记忆生成简单联结句
    """
    if llm_connect:
        return llm_connect(memories)

    # 代码兜底：高唤醒 emotional 记忆 → 联结句
    emotional = [
        m for m in memories
        if m.type == MemoryType.EMOTIONAL.value and m.arousal >= 0.5
    ]
    emotional.sort(key=lambda m: m.arousal, reverse=True)
    inspirations = []
    for mem in emotional[:max_emotional]:
        inspirations.append(f"情绪印记：{mem.content}")
    return inspirations


# ── 完整管线 ───────────────────────────────────────────────────

def run_dream_pipeline(
    memories: list[Memory],
    day_log: str = "",
    persona_entries: list[str] | None = None,
    thread_store: Optional[Callable[[str, str, str], None]] = None,
    llm_extract: Optional[Callable[[str], MnemosyneSummary]] = None,
    llm_connect: Optional[Callable[[list[Memory]], list[str]]] = None,
    min_occurrence: int = 3,
) -> tuple[list[Memory], DreamPipelineReport]:
    """
    完整梦境管线（hypnos × engram 融合）：

      Phase 1 Mnemosyne   — 浅睡总结（day_log → 结构化）
      Phase 2 Epimetheus  — 深睡内化（重分类/合并/关系发现）
      Phase 3 Prometheus  — 灵感联结（emotional → 联想）
      Phase 4 演化联动     — 老化降权 + 关系写图谱

    Returns:
        (加工后的记忆列表, 管线报告)
    """
    report = DreamPipelineReport()

    # Phase 1
    report.mnemosyne = mnemosyne_summarize(day_log, llm_extract)

    # Phase 2
    report.dream = epimetheus_internalize(memories, min_occurrence=min_occurrence)

    # Phase 3
    report.inspiration = prometheus_inspire(memories, llm_connect)

    # Phase 4: 关系写图谱（Loop 2 → Loop 1 联动）
    if thread_store:
        for subj, rel, obj in report.dream.relations_found:
            thread_store(subj, rel, obj)

    # Phase 4: 老化降权（Loop 4）
    evolved, recall = age_and_demote(memories)
    report.recall = recall

    report.summary = {
        "new_facts": len(report.mnemosyne.new_facts),
        "reclassified": len(report.dream.reclassified),
        "consolidated_groups": len(report.dream.consolidated_groups),
        "relations_found": len(report.dream.relations_found),
        "inspirations": len(report.inspiration),
        "demoted": len(report.recall.demoted_ids),
        "archive_candidates": len(report.recall.archive_candidates),
    }

    return evolved, report
