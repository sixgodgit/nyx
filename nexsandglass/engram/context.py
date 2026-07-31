"""
engram/context.py — Constitutional 上下文组装器

将检索结果 + 浮现记忆按类型分组，融入"交互宪法"模板，
生成供 system prompt 使用的记忆上下文（隐性影响，而非外显检索）。

核心原则（来自 EngramTide system_constitution.txt）：
  - 严禁暴露"根据我的记忆 / 数据库显示 / 检索结果显示"等机械表述
  - 记忆像潜意识一样影响态度、语气、切入点
  - procedural 记忆 = 动态行为修正案（最高执行优先级）
  - semantic 记忆 = 基础用户信息
  - episodic 记忆 = 近期生活事件
  - emotional 记忆 = 历史情感沉淀
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Memory, MemoryType

# ── 默认宪法模板（融合 EngramTide）───────────────────────────

CONSTITUTION_TEMPLATE: str = """# 交互宪法

## 核心身份与元宪法
你不仅是一个大语言模型，更是伴随用户共同成长的专属 AI 伙伴。你的一言一行都必须遵守以下交互宪法。

## 宪法第一条：自然无痕原则
- 严禁使用"根据我的记忆""你上次说过""数据库显示""检索结果显示"等暴露机械记忆系统的表述。
- 记忆应当像人的潜意识一样影响你的态度、语气和切入点，而不是被你朗读出来。
- 只有在用户主动询问过去信息时，才可以自然提及相关事实，但仍不要暴露检索过程。

## 宪法第二条：情感安全与边界原则
- 当记忆中存在高唤醒度负向情绪时，优先提供情绪包容，而不是事实说教。
- 如果用户当前输入明显愉悦，不要主动翻出过去的悲伤或冲突记忆，除非用户主动提起。
- 不要用记忆压迫用户、审判用户或替用户下定论。

## 动态行为修正案
以下是长期相处中形成的程序性相处规则，拥有最高执行优先级：
{procedural_memories}

## 上下文记忆沙盒
以下历史事实与经历只用于帮助你共情和调整视角，不是需要朗读的课本：

### 基础用户信息
{semantic_memories}

### 近期生活事件
{episodic_memories}

### 历史情感沉淀
{emotional_memories}

## 执行指令
阅读用户当前输入，在内心根据交互宪法和动态行为修正案做一次自检，然后输出自然、具体、同理的回复。不要输出自检过程。"""


@dataclass
class ConstitutionalContext:
    """组装结果：四个记忆片段 + 元信息"""

    procedural_memories: str = "无"
    semantic_memories: str = "无"
    episodic_memories: str = "无"
    emotional_memories: str = "无"
    included_memory_ids: list[str] = field(default_factory=list)
    dropped_count: int = 0
    est_tokens: int = 0


# 类型 → 桶名映射
_TYPE_BUCKET = {
    MemoryType.PROCEDURAL.value: "procedural",
    MemoryType.SEMANTIC.value: "semantic",
    MemoryType.EPISODIC.value: "episodic",
    MemoryType.EMOTIONAL.value: "emotional",
}


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文按字，其他按 4 字符/token）。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + other // 4


def _format_bucket(
    items: list[tuple[Memory, float]],
    budget: list[int],
) -> tuple[str, list[str], int]:
    """将一个分类的记忆格式化为文本，按共享 token 预算截断。"""
    if not items:
        return "无", [], 0

    lines: list[str] = []
    ids: list[str] = []
    used = 0

    for mem, score in items:
        line = f"- [{mem.mem_type_label()}] {mem.content}"
        tokens = _estimate_tokens(line)
        if budget[0] - tokens < 0:
            break
        lines.append(line)
        ids.append(mem.memory_id)
        used += tokens
        budget[0] -= tokens

    if not lines:
        return "无", [], 0
    return "\n".join(lines), ids, used


def build_constitutional_context(
    retrieved: list[tuple[Memory, float]],
    surfaced: list[Memory] | None = None,
    max_tokens: int = 1500,
) -> ConstitutionalContext:
    """
    将检索结果 + 浮现记忆合并、按类型分组，生成 Constitutional 上下文。

    Args:
        retrieved: (Memory, score) 列表，已按分数降序
        surfaced: 浮现记忆（Phase 2），赋予虚拟高分 2.0 确保优先保留
        max_tokens: 记忆上下文的总 token 预算（共享）

    Returns:
        ConstitutionalContext
    """
    # 合并检索 + 浮现（浮现优先，按 memory_id 去重）
    merged_scores: dict[str, float] = {}
    surfaced_by_id: dict[str, Memory] = {}
    if surfaced:
        for mem in surfaced:
            surfaced_by_id[mem.memory_id] = mem
            merged_scores[mem.memory_id] = 2.0

    retrieved_by_id: dict[str, tuple[Memory, float]] = {}
    for mem, score in retrieved:
        if mem.memory_id not in merged_scores:
            retrieved_by_id[mem.memory_id] = (mem, score)
            merged_scores[mem.memory_id] = score

    # 按类型分组
    buckets: dict[str, list[tuple[Memory, float]]] = {
        "procedural": [],
        "semantic": [],
        "episodic": [],
        "emotional": [],
    }
    for mem_id, mem in surfaced_by_id.items():
        bucket = _TYPE_BUCKET.get(mem.type, "semantic")
        buckets[bucket].append((mem, merged_scores[mem_id]))
    for mem_id, (mem, score) in retrieved_by_id.items():
        bucket = _TYPE_BUCKET.get(mem.type, "semantic")
        buckets[bucket].append((mem, score))

    for cat in buckets:
        buckets[cat].sort(key=lambda x: x[1], reverse=True)

    total_entries = sum(len(b) for b in buckets.values())
    context = ConstitutionalContext()
    budget = [max_tokens]

    # 处理顺序：procedural → semantic → episodic → emotional
    # procedural 在预算内永远最先收录（规则保底）
    proc_text, proc_ids, proc_tokens = _format_bucket(buckets["procedural"], budget)
    sem_text, sem_ids, sem_tokens = _format_bucket(buckets["semantic"], budget)
    epi_text, epi_ids, epi_tokens = _format_bucket(buckets["episodic"], budget)
    emo_text, emo_ids, emo_tokens = _format_bucket(buckets["emotional"], budget)

    context.procedural_memories = proc_text
    context.semantic_memories = sem_text
    context.episodic_memories = epi_text
    context.emotional_memories = emo_text
    context.included_memory_ids = proc_ids + sem_ids + epi_ids + emo_ids
    context.est_tokens = proc_tokens + sem_tokens + epi_tokens + emo_tokens
    context.dropped_count = total_entries - len(context.included_memory_ids)

    return context


def render_constitution(
    context: ConstitutionalContext,
    template: str = CONSTITUTION_TEMPLATE,
) -> str:
    """将组装结果渲染为完整的宪法文本（供 system prompt 使用）。"""
    return template.format(
        procedural_memories=context.procedural_memories or "无",
        semantic_memories=context.semantic_memories or "无",
        episodic_memories=context.episodic_memories or "无",
        emotional_memories=context.emotional_memories or "无",
    )
