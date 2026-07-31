"""
engram/ — Nyx × EngramTide 融合层

将 EngramTide 的认知科学记忆机制融入 Nyx 沙漏系统：

  1. 记忆类型分类（semantic / episodic / emotional / procedural）
  2. 差异化写入策略（覆盖 / 直插 / 强化 / 去重）
  3. 指数衰减引擎（Ebbinghaus 遗忘曲线，DECAY_FLOOR 永不归零）
  4. 浮现机制（相似度 + 时间 + 唤醒度加权）
  5. 逐轮激活（当前输入 boost 相关记忆）
  6. Constitutional 上下文（记忆融入 system prompt 隐性影响）

设计原则：
- 纯函数优先，无 I/O 副作用（便于测试与复用）
- 与 Sandglass 的存储/检索解耦：本层只做"记忆加工"，存储仍走沙漏
- 时间衰减只作用于 episodic/emotional，semantic/procedural 保持稳定
"""

from .types import (
    MemoryType,
    MEMORY_TYPES,
    STATIC_TYPES,
    Memory,
    WriteReport,
    WriteAction,
)
from .decay import (
    compute_decay_multiplier,
    apply_decay,
    get_surfaced_memories,
    compute_activations,
    DecayReport,
    ActivationReport,
)
from .writer import (
    write_memory_classified,
    consolidate_similar,
    supersede_memories,
)
from .context import (
    build_constitutional_context,
    render_constitution,
    ConstitutionalContext,
    CONSTITUTION_TEMPLATE,
)

__all__ = [
    # types
    "MemoryType",
    "MEMORY_TYPES",
    "STATIC_TYPES",
    "Memory",
    "WriteReport",
    "WriteAction",
    # decay
    "compute_decay_multiplier",
    "apply_decay",
    "get_surfaced_memories",
    "compute_activations",
    "DecayReport",
    "ActivationReport",
    # writer
    "write_memory_classified",
    "consolidate_similar",
    "supersede_memories",
    # context
    "build_constitutional_context",
    "render_constitution",
    "ConstitutionalContext",
    "CONSTITUTION_TEMPLATE",
]
