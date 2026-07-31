"""
engram/decay.py — 时间衰减引擎（Ebbinghaus 遗忘曲线）

核心公式（来自 EngramTide，融合进 Nyx）：

    multiplier = exp(-rate × hours_elapsed / (24 × (1 + ln(1 + access_count))))

  - semantic / procedural → 1.0（不衰减）
  - episodic   → rate = BASE_DECAY_RATE
  - emotional  → rate = BASE_DECAY_RATE × (1 - arousal × EMOTIONAL_DECAY_FACTOR)
                 （唤醒度越高衰减越慢）
  - DECAY_FLOOR：权重下限，记忆永不归零（"想不起来但隐约记得"）

本层为纯函数加工：不直接写存储，由调用方（沙漏归档/梦境）把
衰减后的权重写回数据库。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from .types import DECAY_TYPES, Memory, STATIC_TYPES

# ── 衰减参数（对齐 EngramTide 默认值）───────────────────────

BASE_DECAY_RATE: float = 0.05            # episodic 基础衰减率（每天 ~5%）
EMOTIONAL_DECAY_FACTOR: float = 0.7      # emotional 衰减折扣因子
DECAY_FLOOR: float = 0.01                # 权重下限（永不归零）
MAX_SURFACED_MEMORIES: int = 8           # 单轮浮现上限
MAX_ACTIVATIONS_PER_TURN: int = 30       # 单轮激活上限
CONTEXT_AWARE_ENABLED: bool = True       # 逐轮激活总开关
ACTIVATION_BOOST: float = 0.05           # 每次激活的权重提升量
STRONG_ACTIVATION_THRESHOLD: float = 0.80  # 强激活：计访问的相似度阈值


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_decay_multiplier(
    mem_type: str,
    arousal: float,
    access_count: int,
    hours_elapsed: float,
) -> float:
    """
    计算一次衰减更新中 decay_weight 应乘的系数，范围 (0, 1]。

    - semantic / procedural（及未知类型）→ 1.0（不衰减，fail-safe）
    - episodic:   rate = BASE_DECAY_RATE
    - emotional:  rate = BASE_DECAY_RATE × (1 - arousal × EMOTIONAL_DECAY_FACTOR)
    - stability   = 1 + ln(1 + access_count)   # 访问越多越稳定
    - time_factor = hours_elapsed / (24 × stability)
    - multiplier  = exp(-rate × time_factor)
    - hours_elapsed <= 0 → 1.0（时钟回拨保护）
    """
    if hours_elapsed <= 0:
        return 1.0

    if mem_type in STATIC_TYPES:
        return 1.0

    if mem_type == "episodic":
        rate = BASE_DECAY_RATE
    elif mem_type == "emotional":
        rate = BASE_DECAY_RATE * (1.0 - arousal * EMOTIONAL_DECAY_FACTOR)
        rate = max(rate, 1e-6)  # 防负/零
    else:
        # 未知类型：不衰减 + fail-safe
        return 1.0

    stability = 1.0 + math.log(1.0 + access_count)
    time_factor = hours_elapsed / (24.0 * stability)
    return math.exp(-rate * time_factor)


def apply_decay(
    memories: list[Memory],
    hours_elapsed: float,
    floor: float = DECAY_FLOOR,
) -> tuple[list[Memory], "DecayReport"]:
    """
    对一批记忆应用衰减，返回 (新列表, 报告)。

    新列表是原对象副本（decay_weight 更新），不修改入参。
    """
    report = DecayReport()
    updated: list[Memory] = []

    for mem in memories:
        if mem.type in STATIC_TYPES:
            updated.append(mem)
            continue
        multiplier = compute_decay_multiplier(
            mem.type, mem.arousal, mem.access_count, hours_elapsed
        )
        new_weight = mem.decay_weight * multiplier
        if new_weight < floor:
            new_weight = floor
        new_mem = Memory(
            memory_id=mem.memory_id,
            type=mem.type,
            content=mem.content,
            valence=mem.valence,
            arousal=mem.arousal,
            created_at=mem.created_at,
            last_accessed=mem.last_accessed,
            access_count=mem.access_count,
            decay_weight=round(new_weight, 6),
            embedding=mem.embedding,
            source_id=mem.source_id,
            unresolved=mem.unresolved,
            tags=list(mem.tags),
            superseded_by=mem.superseded_by,
        )
        report.processed += 1
        report.floored += 1 if new_weight == floor else 0
        updated.append(new_mem)

    return updated, report


def get_surfaced_memories(
    memories: list[Memory],
    now: Optional[datetime] = None,
    max_surfaced: int = MAX_SURFACED_MEMORIES,
    recent_days: int = 7,
    min_decay: float = 0.1,
) -> list[Memory]:
    """
    浮现机制：选出当前最"该被想起"的记忆（分层优先级，来自 EngramTide）。

    优先级 R1 > R2 > R3 > R4：
      R1  procedural 规则   → 永远浮现（写入去重保底）
      R2  高唤醒 emotional  → arousal >= 0.7 且 decay_weight > min_decay
      R3  unresolved 未解决 → decay_weight > SURFACE_UNRESOLVED_MIN_DECAY
      R4  近期 episodic     → 7 天内创建 且 decay_weight > 0.2

    截断：按 R1 > R2 > R3 > R4 优先级保留，同规则内按 decay_weight 降序。
    健壮性：
      - superseded_by 非空 → 不浮现（双保险）
      - created_at 解析失败 → 不算"近期"，不因 R4 入选
      - embedding 是否为空不影响浮现（元数据驱动）
    """
    now = now or datetime.now(timezone.utc)
    r1: list[Memory] = []  # procedural
    r2: list[Memory] = []  # high-arousal emotional
    r3: list[Memory] = []  # unresolved
    r4: list[Memory] = []  # recent episodic

    for mem in memories:
        if mem.superseded_by is not None:
            continue

        # R1: procedural 永远浮现
        if mem.type == "procedural":
            r1.append(mem)
            continue

        # R2: 高唤醒 emotional
        if mem.type == "emotional" and mem.arousal >= 0.7 and mem.decay_weight > min_decay:
            r2.append(mem)
            continue

        # R3: unresolved
        if mem.unresolved and mem.decay_weight > min_decay:
            r3.append(mem)
            continue

        # R4: 近期 episodic
        if mem.type == "episodic":
            try:
                created = datetime.fromisoformat(mem.created_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                days = (now - created).total_seconds() / 86400.0
            except (ValueError, TypeError):
                days = float("inf")  # 解析失败：不算近期
            if days <= recent_days and mem.decay_weight > 0.2:
                r4.append(mem)

    # 同规则内按 decay_weight 降序
    r2.sort(key=lambda m: m.decay_weight, reverse=True)
    r3.sort(key=lambda m: m.decay_weight, reverse=True)
    r4.sort(key=lambda m: m.decay_weight, reverse=True)

    # 按优先级合并，直到 max_surfaced
    surfaced: list[Memory] = []
    for bucket in (r1, r2, r3, r4):
        for mem in bucket:
            if len(surfaced) >= max_surfaced:
                return surfaced
            surfaced.append(mem)

    return surfaced


def compute_activations(
    query_embedding: list[float],
    memories: list[Memory],
    similarity_fn: Optional[Callable[[list[float], list[float]], float]] = None,
    max_activations: int = MAX_ACTIVATIONS_PER_TURN,
    strong_threshold: float = STRONG_ACTIVATION_THRESHOLD,
) -> tuple[list[tuple[float, str, float]], "ActivationReport"]:
    """
    逐轮激活：当前输入与记忆的相似度做加法 boost。

    返回 ((new_weight, memory_id, old_weight), report)。

    相似度函数默认余弦（mem.embedding 为 None 时跳过）。
    """
    report = ActivationReport()

    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (na * nb)

    sim_fn = similarity_fn or _cosine

    if not CONTEXT_AWARE_ENABLED:
        return [], report

    activations: list[tuple[float, str, float]] = []
    for mem in memories:
        if mem.embedding is None:
            continue
        sim = sim_fn(query_embedding, mem.embedding)
        if sim <= 0:
            continue
        new_w = mem.decay_weight + ACTIVATION_BOOST * sim
        new_w = min(new_w, 1.0)  # 封顶
        activations.append((new_w, mem.memory_id, mem.decay_weight))
        report.details.append(
            ActivationDetail(
                memory_id=mem.memory_id,
                old_weight=mem.decay_weight,
                new_weight=round(new_w, 6),
                similarity=round(sim, 4),
            )
        )
        if sim >= strong_threshold:
            report.strong_ids.append(mem.memory_id)

    activations.sort(key=lambda x: x[0], reverse=True)
    return activations[:max_activations], report


@dataclass
class DecayReport:
    """衰减报告"""

    processed: int = 0
    floored: int = 0


@dataclass
class ActivationDetail:
    """单条激活明细"""

    memory_id: str
    old_weight: float
    new_weight: float
    similarity: float


@dataclass
class ActivationReport:
    """激活报告"""

    details: list[ActivationDetail] = field(default_factory=list)
    strong_ids: list[str] = field(default_factory=list)
