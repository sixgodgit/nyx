"""NexSandglass MemoryIntent + Adaptive Recall（v5.0）。

在 RecallPlanner 前解析查询意图（MemoryIntent），据此选择召回策略与排序权重，
替代统一的"整包"召回。包住而非替换 SearchRouter；Vector 只作 index。

MemoryIntent 字段：
  entities / temporal / domain / relation_to_user / types_needed /
  task_type / need_current_truth / history

策略示例：
  - ownership+vehicle+previous → fact/thread/event/history/pref 多源
  - 熟悉但搜不到           → nyx_hunt 情景通道
  - generic                 → SearchRouter 混合

Ranking 权重：semantic, contextual, temporal_validity, importance, confidence,
  recency, user_relevance, lifecycle_penalty, trust(shadow)
输出 reasons[] + strategy_used。Intent 失败 → generic_semantic 降级。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from nexsandglass.engram.types import MemoryObject, LifecycleState

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# MemoryIntent
# ══════════════════════════════════════════════════════════

@dataclass
class MemoryIntent:
    """解析后的记忆查询意图。"""
    raw_query: str = ""
    entities: list[str] = field(default_factory=list)        # 实体（吉利/Geely/车）
    temporal: str = ""                                        # 时间（now/上次/过去/历史）
    domain: str = "generic"                                   # 领域（vehicle/food/...）
    relation_to_user: str = "generic"                         # ownership/activity/pref/...
    types_needed: list[str] = field(default_factory=list)     # fact/thread/event/history/pref
    task_type: str = "recall"                                 # recall/feedback/forget
    need_current_truth: bool = True                           # 是否需要当前事实
    history: bool = False                                     # 是否要历史演变
    strategy: str = "generic_semantic"                        # 路由策略
    reasons: list[str] = field(default_factory=list)          # 排序理由
    confidence: float = 0.5


# ══════════════════════════════════════════════════════════
# 实体 / 领域 / 时间识别
# ══════════════════════════════════════════════════════════

# 领域关键词
_DOMAIN_MAP = {
    "vehicle": ("车", "geely", "吉利", "tesla", "特斯拉", "电动车", "汽车", "starray", "bmw", "奔驰"),
    "food": ("吃", "餐厅", "菜", "饭", "咖啡", "川菜", "中餐"),
    "work": ("工作", "公司", "项目", "客户", "会议", "上班"),
    "home": ("家", "住", "地址", "搬家"),
    "finance": ("钱", "税", "账单", "投资", "账户"),
}

# 时间词 → temporal
_TEMPORAL_MAP = {
    "上次": "previous", "以前": "past", "过去": "past", "之前": "past",
    "最近": "recent", "昨天": "recent", "今天": "now", "现在": "now",
    "当时": "past", "当初": "past", "曾经": "past", "历史": "history",
}

# 关系词 → relation_to_user
_RELATION_MAP = {
    "买了": "ownership", "我的": "ownership", "我买": "ownership", "我的车": "ownership",
    "喜欢": "pref", "爱吃": "pref", "偏好": "pref",
    "去了": "activity", "去过": "activity", "做了": "activity",
    "经营": "activity", "开的餐厅": "activity",
}

# 需要 history 的触发词
_HISTORY_MARKERS = ("以前", "之前", "上次", "过去", "演变", "变化", "之前是什么")

# 需要 current truth 的触发词（默认 True）
_CURRENT_MARKERS = ("现在", "目前", "现在怎么样", "是不是", "还在", "还开")


def _detect_entities(query: str) -> list[str]:
    """提取关键实体（专名 + 常见对象词）。"""
    entities = []
    # 品牌/专名（含中文品牌）
    brand = re.findall(r"(Geely|吉利|Tesla|特斯拉|Starray|BMW|奔驰|比亚迪|蔚来|小鹏)", query, re.I)
    entities.extend(brand)
    # 对象词（车/餐厅/咖啡等）
    for obj in ("车", "餐厅", "咖啡", "房子", "公司", "项目", "股票"):
        if obj in query and obj not in entities:
            entities.append(obj)
    # 去除重复、保序
    seen = set()
    out = []
    for e in entities:
        if e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out


def _detect_domain(query: str) -> str:
    for dom, kws in _DOMAIN_MAP.items():
        if any(k.lower() in query.lower() for k in kws):
            return dom
    return "generic"


def _detect_temporal(query: str) -> str:
    for kw, val in _TEMPORAL_MAP.items():
        if kw in query:
            return val
    return "now"


def _detect_relation(query: str) -> str:
    for kw, val in _RELATION_MAP.items():
        if kw in query:
            return val
    return "generic"


def _detect_types_needed(intent: MemoryIntent) -> list[str]:
    """根据意图推断需要的记忆类型。"""
    types = []
    if intent.domain == "vehicle" or "ownership" in intent.relation_to_user:
        types.extend(["fact", "thread"])
    if intent.temporal in ("previous", "past", "history"):
        types.extend(["event", "history"])
        intent.history = True
        intent.need_current_truth = False
    if intent.relation_to_user == "pref":
        types.append("pref")
    if not types:
        types = ["fact", "event"]
    return types


# ══════════════════════════════════════════════════════════
# 意图解析 + 路由策略
# ══════════════════════════════════════════════════════════

def parse_intent(query: str) -> MemoryIntent:
    """解析查询为 MemoryIntent。失败降级为 generic_semantic。"""
    intent = MemoryIntent(raw_query=query)
    try:
        intent.entities = _detect_entities(query)
        intent.domain = _detect_domain(query)
        intent.temporal = _detect_temporal(query)
        intent.relation_to_user = _detect_relation(query)
        intent.types_needed = _detect_types_needed(intent)
        if any(h in query for h in _HISTORY_MARKERS):
            intent.history = True
            intent.need_current_truth = False
        intent.strategy = _route_strategy(intent)
        intent.confidence = 0.7 if intent.domain != "generic" else 0.5
        intent.reasons.append(f"domain={intent.domain}")
        intent.reasons.append(f"temporal={intent.temporal}")
        if intent.entities:
            intent.reasons.append(f"entities={intent.entities}")
    except Exception as e:
        logger.debug("[intent] 解析失败，降级 generic_semantic: %s", e)
        intent.strategy = "generic_semantic"
        intent.reasons = ["intent_parse_failed_fallback"]
    return intent


def _route_strategy(intent: MemoryIntent) -> str:
    """根据意图选择召回策略。"""
    # ownership + vehicle + previous → 多源（fact/thread/event/history/pref）
    if (intent.relation_to_user == "ownership" or intent.domain == "vehicle") \
            and intent.temporal in ("previous", "past", "history"):
        return "ownership_history_multi"
    # vehicle/ownership 当前 → 混合但偏 fact/thread
    if intent.domain == "vehicle" or intent.relation_to_user == "ownership":
        return "ownership_current_multi"
    # 熟悉但搜不到 → nyx_hunt（由调用方检测"搜不到"）
    if intent.temporal in ("previous", "past") and intent.domain != "generic":
        return "nyx_hunt_scene"
    # generic
    return "generic_semantic"


# ══════════════════════════════════════════════════════════
# Ranking
# ══════════════════════════════════════════════════════════

# 生命周期惩罚（forgotten/archived 降权；active/validated 正常）
_LIFECYCLE_PENALTY = {
    "forgotten": -0.5, "archived": -0.3, "aging": -0.1,
    "active": 0.1, "validated": 0.05, "reinforced": 0.1, "consolidated": 0.05,
}


def rank_objects(
    objects: list[MemoryObject],
    intent: MemoryIntent,
    query: str = "",
    shadow_trust: Optional[dict] = None,
) -> list[MemoryObject]:
    """多因子排序（semantic/contextual/temporal/importance/confidence/recency/
    user_relevance/lifecycle/trust）。返回排序后列表，并写 reasons。

    这是"包住而非替换"——排序发生在各源召回结果之上，不改底层 SearchRouter。
    """
    shadow_trust = shadow_trust or {}

    def score(obj: MemoryObject) -> tuple[float, list[str]]:
        reasons: list[str] = []
        s = 0.0

        # semantic 相关度（字符重叠）
        q = (query or intent.raw_query).lower()
        c = obj.content.lower()
        rel = 0.0
        if q and q in c:
            rel = 0.9
        elif q:
            q_set = set(q) - set("的了是在我你他她它们")
            c_set = set(c)
            inter = len(q_set & c_set)
            rel = 0.5 + 0.4 * (inter / max(len(q_set), 1)) if q_set else 0.5
        s += 0.35 * rel
        if rel > 0.6:
            reasons.append("semantic_high")

        # contextual（实体匹配）
        if intent.entities:
            match = sum(1 for e in intent.entities if e.lower() in c.lower())
            if match:
                s += 0.2 * match
                reasons.append(f"entity_x{match}")

        # temporal validity（need_current_truth 时惩罚过期）
        if intent.need_current_truth and obj.valid_until:
            s -= 0.25
            reasons.append("temporal_expired_penalty")
        if intent.history and obj.valid_from:
            s += 0.1
            reasons.append("temporal_history")

        # importance / confidence
        s += 0.1 * (obj.importance or 0.5)
        s += 0.1 * (obj.confidence or 0.5)

        # recency（有 created_at 近期加分）
        if obj.created_at and "2026-08" in obj.created_at:
            s += 0.1
            reasons.append("recent")

        # user_relevance（relation_to_user 命中）
        if intent.relation_to_user != "generic":
            rel_word = {"ownership": "买了|我的|我的车", "pref": "喜欢|爱吃|偏好",
                        "activity": "去了|做过|经营"}.get(intent.relation_to_user, "")
            if rel_word and re.search(rel_word, c):
                s += 0.1
                reasons.append("user_relevant")

        # lifecycle penalty
        pen = _LIFECYCLE_PENALTY.get(obj.status, 0.0)
        s += pen
        if pen < 0:
            reasons.append(f"lifecycle_{obj.status}_penalty")

        # trust（shadow）
        trust = shadow_trust.get(obj.memory_id, 0.0)
        s += 0.1 * trust

        return round(min(max(s, 0.0), 1.0), 4), reasons

    scored = [(score(o), o) for o in objects]
    scored.sort(key=lambda x: x[0][0], reverse=True)
    # reasons 不写入 MemoryObject（其无 meta 字段）；排序结果即生效
    return [o for _, o in scored]


def history_context(intent: MemoryIntent, limit: int = 5) -> str:
    """当 intent.history=True 时，从时序事实取演变链作为上下文补充。"""
    try:
        from nexsandglass.engram.loops.temporal_fact import evolution_chain
        from nexsandglass.features import weavethread
        db_path = weavethread._DB
        parts = []
        for ent in intent.entities[:2]:
            chain = evolution_chain(db_path, ent, limit=limit)
            if "无历史记录" not in chain:
                parts.append(chain)
        return "\n\n".join(parts)
    except Exception:
        return ""
