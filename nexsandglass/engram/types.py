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


# ══════════════════════════════════════════════════════════
# Canonical MemoryObject schema（Cognitive Memory OS 地基）
# ══════════════════════════════════════════════════════════

# 记忆类型全集：兼容现有四种 + 预留未来类型
OBJECT_MEMORY_TYPES = frozenset({
    "semantic", "episodic", "emotional", "procedural",
    # 预留（未启用，写入时校验但不限定）
    "preference", "relational", "temporal", "meta",
})


class LifecycleState(str, Enum):
    """记忆生命周期状态机。

    合法迁移路径（前向推进，不允许跳级或回退）：
      observed → candidate → validated → active → reinforced
              → consolidated → aging → archived → forgotten
    写入时为 observed；检索命中时 candidate→validated→active 逐步推进；
    强化/合并时 reinforced/consolidated；老化/归档/遗忘时 aging/archived/forgotten。
    """
    OBSERVED = "observed"
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    ACTIVE = "active"
    REINFORCED = "reinforced"
    CONSOLIDATED = "consolidated"
    AGING = "aging"
    ARCHIVED = "archived"
    FORGOTTEN = "forgotten"


# 状态机的合法迁移表：state -> 允许到达的后续状态集合
_LIFECYCLE_TRANSITIONS: dict[str, frozenset[str]] = {
    LifecycleState.OBSERVED.value: frozenset({LifecycleState.CANDIDATE.value}),
    LifecycleState.CANDIDATE.value: frozenset({LifecycleState.VALIDATED.value}),
    LifecycleState.VALIDATED.value: frozenset({LifecycleState.ACTIVE.value}),
    LifecycleState.ACTIVE.value: frozenset({LifecycleState.REINFORCED.value, LifecycleState.CONSOLIDATED.value, LifecycleState.AGING.value}),
    LifecycleState.REINFORCED.value: frozenset({LifecycleState.CONSOLIDATED.value, LifecycleState.AGING.value}),
    LifecycleState.CONSOLIDATED.value: frozenset({LifecycleState.AGING.value}),
    LifecycleState.AGING.value: frozenset({LifecycleState.ARCHIVED.value}),
    LifecycleState.ARCHIVED.value: frozenset({LifecycleState.FORGOTTEN.value}),
    LifecycleState.FORGOTTEN.value: frozenset(),
}


def validate_transition(from_state: str, to_state: str) -> bool:
    """校验状态迁移是否合法。非法迁移返回 False（不抛异常，调用方决定处理）。"""
    allowed = _LIFECYCLE_TRANSITIONS.get(from_state)
    if allowed is None:
        return False
    return to_state in allowed


def next_lifecycle(state: str) -> str:
    """返回状态机中 state 的下一个合法状态；terminal 状态返回自身。"""
    allowed = _LIFECYCLE_TRANSITIONS.get(state)
    if not allowed:
        return state
    order = [
        LifecycleState.OBSERVED.value, LifecycleState.CANDIDATE.value,
        LifecycleState.VALIDATED.value, LifecycleState.ACTIVE.value,
        LifecycleState.REINFORCED.value, LifecycleState.CONSOLIDATED.value,
        LifecycleState.AGING.value, LifecycleState.ARCHIVED.value,
        LifecycleState.FORGOTTEN.value,
    ]
    return next((s for s in order if s in allowed), state)


@dataclass
class SPO:
    """结构化三元组 (subject, predicate, object)，MemoryObject.structured 的可选载体。"""
    subject: str
    predicate: str
    object: str
    confidence: float = 1.0


@dataclass
class MemoryObject:
    """Canonical 记忆对象 schema —— Cognitive Memory OS 的统一数据结构。

    是现有 Memory dataclass 的超集：包含 Memory 的全部字段 + 新增生命周期/
    结构化/来源/关系字段。设计为向后兼容：
      - 从 Memory 升级：MemoryObject.from_memory(mem)
      - 降级回 Memory：obj.to_memory()（丢弃新增字段，仅保留 Memory 能表达的）
      - 直接构造：全字段可选，最小编码只需 content + type
    """

    # ── 身份与内容 ──
    memory_id: str = ""
    type: str = "semantic"                       # ObjectType: semantic|episodic|emotional|procedural|预留
    content: str = ""

    # ── 结构化 ──
    structured: Optional[SPO] = None             # 可选 SPO 三元组

    # ── 评估量 ──
    importance: float = 0.5                      # 重要性 [0,1]
    confidence: float = 0.5                      # 置信度 [0,1]
    valence: float = 0.0                         # 效价 [-1,1]
    arousal: float = 0.0                         # 唤醒度 [0,1]

    # ── 时间窗口 ──
    valid_from: str = ""                         # ISO 时间戳，事实开始有效
    valid_until: str = ""                        # ISO 时间戳，事实失效；空=仍有效

    # ── 生命周期 ──
    status: str = LifecycleState.OBSERVED.value  # 当前生命周期状态
    lifecycle_state: str = ""                    # 生命周期别名（兼容旧字段，默认空）

    # ── 覆盖关系 ──
    supersedes: list[str] = field(default_factory=list)      # 本记忆覆盖了哪些 id
    superseded_by: str | None = None             # 本记忆被哪条覆盖（兼容 Memory）

    # ── 来源与关系 ──
    source_id: str | None = None                 # 来源沙粒/会话 ID（兼容 Memory）
    provenance: str = ""                         # 来源说明（channel/工具名）
    relations: list[dict] = field(default_factory=list)      # [{type, target_id, ...}]

    # ── 统计与检索 ──
    token_estimate: int = 0                      # 预估 token 数
    embedding: Optional[list[float]] = None      # 向量（兼容 Memory）
    access_count: int = 0                        # 访问计数（兼容 Memory）
    decay_weight: float = 1.0                    # 衰减权重（兼容 Memory）
    last_accessed: str = ""                      # 最近访问时间（兼容 Memory）
    unresolved: bool = False                     # 未解决标记（兼容 Memory）

    # ── 标签与时间 ──
    tags: list[str] = field(default_factory=list)
    created_at: str = ""                         # ISO 时间戳
    updated_at: str = ""                         # ISO 时间戳

    # ── 与 Memory dataclass 双向转换 ──

    @classmethod
    def from_memory(cls, mem: Memory) -> "MemoryObject":
        """从现有 Memory 升级为 MemoryObject（字段映射，旧数据可读）。"""
        return cls(
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

    def to_memory(self) -> Memory:
        """降级回 Memory（丢弃 Memory 无法表达的字段）。"""
        return Memory(
            memory_id=self.memory_id or self._fallback_id(),
            type=self.type,
            content=self.content,
            valence=self.valence,
            arousal=self.arousal,
            created_at=self.created_at,
            last_accessed=self.last_accessed,
            access_count=self.access_count,
            decay_weight=self.decay_weight,
            embedding=self.embedding,
            source_id=self.source_id,
            unresolved=self.unresolved,
            tags=list(self.tags),
            superseded_by=self.superseded_by,
        )

    def _fallback_id(self) -> str:
        """无 memory_id 时生成稳定 ID：类型 + 内容哈希。"""
        import hashlib
        digest = hashlib.sha1(f"{self.type}:{self.content}".encode("utf-8")).hexdigest()[:16]
        return f"{self.type}-{digest}"

    def advance_lifecycle(self) -> bool:
        """将生命周期推进到下一个合法状态。成功返回 True，terminal/非法返回 False。"""
        nxt = next_lifecycle(self.status)
        if nxt == self.status:
            return False
        self.status = nxt
        return True

    def advance_to(self, target: str) -> bool:
        """尝试直接迁移到 target。非法迁移返回 False 且状态不变。"""
        if not validate_transition(self.status, target):
            return False
        self.status = target
        return True

    def touches(self):
        """记录一次访问（检索命中时调用）。"""
        from datetime import datetime
        self.access_count += 1
        self.last_accessed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
