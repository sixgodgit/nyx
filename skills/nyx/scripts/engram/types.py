"""
engram/types.py — 记忆类型与数据结构

基于 Tulving 多重记忆系统模型，将记忆分为四类：

  semantic   — 语义记忆：稳定的用户事实、偏好、身份信息（可被新事实覆盖）
  episodic   — 情景记忆：具体事件、时间点发生的事（不可覆盖，随时间衰减）
  emotional  — 情绪记忆：带情绪色彩的经历（唤醒度越高衰减越慢）
  procedural — 程序记忆：相处规则、做事方式、铁律（永不衰减，写入去重）

与 Sandglass 的关系：
  Sandglass 沙粒是"原始事件日志"（episodic 层）；
  本模块为记忆加工层——从沙粒中提炼 semantic / emotional / procedural 记忆，
  并管理它们的衰减、浮现与激活。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MemoryType(str, Enum):
    """记忆类型（Tulving 多重记忆系统）"""

    SEMANTIC = "semantic"      # 语义记忆：稳定事实 / 偏好 / 身份
    EPISODIC = "episodic"      # 情景记忆：具体事件
    EMOTIONAL = "emotional"    # 情绪记忆：带情绪色彩的经历
    PROCEDURAL = "procedural"  # 程序记忆：相处规则 / 做事方式


# 合法类型集合
MEMORY_TYPES = {t.value for t in MemoryType}

# 不衰减的类型（semantic 稳定可覆盖；procedural 规则永不遗忘）
STATIC_TYPES = frozenset({MemoryType.SEMANTIC.value, MemoryType.PROCEDURAL.value})

# 衰减类型（episodic 与 emotional 随时间遗忘）
DECAY_TYPES = frozenset({MemoryType.EPISODIC.value, MemoryType.EMOTIONAL.value})


@dataclass
class Memory:
    """记忆条目（与 Sandglass 沙粒解耦的加工视图）"""

    memory_id: str
    type: str                                # MemoryType.value
    content: str
    valence: float = 0.0                     # 效价：-1（负面）~ +1（正面）
    arousal: float = 0.0                     # 唤醒度：0（平静）~ 1（强烈）
    created_at: str = ""                     # ISO 时间戳
    last_accessed: str = ""
    access_count: int = 0
    decay_weight: float = 1.0                # 当前衰减权重（1.0 = 全新）
    embedding: Optional[list[float]] = None  # 向量（由外部 embedding 服务填充）
    source_id: str | None = None             # 来源沙粒/会话 ID
    unresolved: bool = False                 # 未解决标记（浮现优先级 +1）
    tags: list[str] = field(default_factory=list)
    superseded_by: str | None = None         # 被哪条新记忆覆盖

    def mem_type_label(self) -> str:
        """中文类型标签（供格式化输出用）。"""
        labels = {
            "semantic": "语义",
            "episodic": "情景",
            "emotional": "情绪",
            "procedural": "规则",
        }
        return labels.get(self.type, self.type)


class WriteAction(str, Enum):
    """写入动作（差异化写入策略的结果）"""

    INSERT = "insert"        # 新记忆直插
    OVERRIDE = "override"    # 覆盖旧记忆（semantic）
    REINFORCE = "reinforce"  # 强化旧记忆（emotional）
    DEDUP = "dedup"          # 去重跳过（procedural）
    NOOP = "noop"            # 无操作


@dataclass
class WriteReport:
    """一次差异化写入的报告"""

    action: WriteAction = WriteAction.NOOP
    inserted: int = 0
    superseded_ids: list[str] = field(default_factory=list)
    reinforced_id: str | None = None
    reinforced_boost: float = 0.0
    deduped_id: str | None = None
    similarity: float = 0.0
