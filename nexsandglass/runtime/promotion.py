"""NexSandglass PromotionEngine —— 候选晋升，替代默认同步整轮 dump。

解决"什么值得记住"：
  默认不再把每轮对话整段写入长期记忆，而是先做候选晋升——
  Observation → Extract → Score → Type → Promote / SessionOnly / Drop。

复用 engram.writer 差异化策略（override / reinforce / dedup / insert）：
  - 重复 → reinforce（不双写）
  - 冲突 → conflict candidate（不静默覆盖）
  - 闲聊 / OTP → 不晋升长期

Golden 基准：
  - "我叫阿文"        → semantic/identity（晋升）
  - 一次性会议时间     → short-lived/session（不晋升长期，仅 session）
  - 重复偏好           → reinforce（不双写）
  - 闲聊              → 不进长期（drop）
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from nexsandglass.engram.types import Memory, MemoryObject, LifecycleState, WriteAction
from nexsandglass.engram import writer

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# 提取候选
# ══════════════════════════════════════════════════════════

@dataclass
class Candidate:
    """一条记忆候选。"""
    content: str
    mem_type: str = "semantic"          # semantic/episodic/emotional/procedural/identity/short-lived
    score: float = 0.0
    disposition: str = "promote"        # promote / session_only / drop
    reason: str = ""
    source: str = ""
    lifecycle: str = "candidate"        # 晋升后走 candidate→validated→active
    meta: dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════
# 闲聊 / OTP / 噪音过滤
# ══════════════════════════════════════════════════════════

# 闲聊词（低信息量）
_CHITCHAT = re.compile(
    r"^(好的|好|嗯|哦|明白了|知道了|没问题|谢谢|不客气|可以|对|是的|是啊|哈哈|晚安|早上好|再见|拜拜|在吗|干嘛呢|没事|随便|都一样|你说呢|真的吗|不错|挺好|好啊|好的呀|嗯嗯|哦哦|行了|知道了呢|行吧|算了|那好吧)"
    r"|(哈哈哈|哈哈|嘻嘻|嘿嘿)"
)
_OTP = re.compile(r"(\b\d{4,8}\b.*(验证码|动态码|code|otp|OTP))|(验证码|动态码).*(\b\d{4,8}\b)")
# 一次性/临时（会议、时间点等，不入长期）
_SESSION_ONLY = re.compile(
    r"(会议|meeting|下午[一二三四五六日]?|明天|后天|本周|下[周月]|几点|时间定|约在|"
    r"(?:周[一二三四五六日])|(?:上午|下午)\s*\d|(\d{1,2}[:：]\d{2}))")
# 身份声明
_IDENTITY = re.compile(r"(我叫|我是|我的名字|名字叫|我是.*(?:工程师|厨师|老板|学生|医生|设计师|导演|作者)|我的职业|我是做)")
# 稳定偏好
_PREFERENCE = re.compile(r"(我喜欢|我偏好|我更喜欢|我讨厌|我喜欢吃|我平时喜欢|我习惯|我一直喜欢)")
# 规则/铁律
_PROCEDURAL = re.compile(r"(切记|记住|务必|一定要|必须|千万别|绝不要|规则是|铁律|流程是|步骤是|教程)")
# 信息量启发
_INFO_MARKERS = ("喜欢", "偏好", "住在", "工作了", "在.*上班", "开了", "买了", "经营", "是.*人",
                 "记得", "忘了", "重要", "决定", "计划", "爱", "讨厌")


def _info_score(text: str) -> float:
    """信息量评分 0-1：长度 + 关键词 + 具体性。"""
    score = 0.1
    text = text.strip()
    if len(text) >= 8:
        score += 0.2
    if len(text) >= 20:
        score += 0.15
    if re.search(r"\d", text):
        score += 0.15  # 含数字=具体
    if re.search(r"(喜欢|住在|职业|工作|买了|经营|开了|名字|住在|来自|家庭|公司|车|店|餐厅)", text):
        score += 0.3
    if "我叫" in text or "我是" in text:
        score += 0.2
    return min(score, 1.0)


# ══════════════════════════════════════════════════════════
# 类型分类
# ══════════════════════════════════════════════════════════

def classify_candidate(text: str) -> str:
    """候选记忆类型分类。"""
    if not text or not text.strip():
        return "semantic"
    if _OTP.search(text):
        return "otp"
    if _IDENTITY.search(text):
        return "identity"
    if _PROCEDURAL.search(text):
        return "procedural"
    if _PREFERENCE.search(text):
        return "semantic"  # 偏好 → semantic（可 reinforce）
    if _SESSION_ONLY.search(text) and _info_score(text) < 0.6:
        return "short-lived"
    if _CHITCHAT.search(text):
        return "chitchat"
    # 默认按信息量
    if _info_score(text) >= 0.5:
        return "semantic"
    return "episodic"


# ══════════════════════════════════════════════════════════
# 流水线
# ══════════════════════════════════════════════════════════

class PromotionEngine:
    """候选晋升引擎。"""

    def __init__(self, use_llm: Optional[bool] = None, timeout: float = 2.0):
        # use_llm: 默认读环境变量 WTHREAD_LLM_EXTRACTION；显式传值可覆盖
        if use_llm is None:
            use_llm = os.environ.get("WTHREAD_LLM_EXTRACTION") == "1"
        self.use_llm = use_llm
        self.timeout = timeout  # 同步路径严格超时

    # ── Observation → Candidate ──
    def observe(self, text: str, source: str = "user") -> Candidate:
        """从一条消息生成记忆候选（提取 + 打分 + 分类 + 定处置）。"""
        cand = Candidate(content=text.strip(), source=source)
        if not cand.content:
            cand.disposition = "drop"
            cand.reason = "empty"
            return cand

        # Type
        cand.mem_type = classify_candidate(cand.content)

        # 闲聊 / OTP → 直接 drop（不进长期）
        if cand.mem_type in ("chitchat", "otp"):
            cand.disposition = "drop"
            cand.reason = f"{cand.mem_type} 不晋升长期"
            cand.score = _info_score(cand.content) * 0.1
            return cand

        # 一次性/临时 → session_only
        if cand.mem_type == "short-lived":
            cand.disposition = "session_only"
            cand.reason = "一次性/临时信息，仅本次会话"
            cand.score = _info_score(cand.content) * 0.4
            return cand

        # Score（晋升候选）
        cand.score = _info_score(cand.content)

        # 身份声明 / 偏好 → 直接 promote（不受分数限制）
        if cand.mem_type == "identity":
            cand.disposition = "promote"
            cand.reason = "身份声明，直接晋升"
            cand.score = max(cand.score, 0.8)
        elif _PREFERENCE.search(cand.content):
            cand.disposition = "promote"
            cand.reason = "偏好声明，直接晋升"
            cand.score = max(cand.score, 0.7)
        # 高信息量 → promote；否则 session_only
        elif cand.score >= 0.5:
            cand.disposition = "promote"
            cand.reason = f"信息量 {cand.score:.2f} ≥ 0.5"
        else:
            cand.disposition = "session_only"
            cand.reason = f"信息量 {cand.score:.2f} 不足，暂不晋升"

        # 可选 LLM 抽取（失败降级规则：返回空则用规则结果）
        if self.use_llm and cand.disposition == "promote":
            extra = self._llm_extract(cand.content)
            if extra:
                cand.meta["llm_facts"] = extra

        return cand

    def _llm_extract(self, text: str) -> list:
        """可选 LLM 抽取，严格超时，失败降级为空。"""
        try:
            from nexsandglass.core.llm_extract import llm_extract_triples
            # llm_extract 内部有 30s 超时，这里同步路径用更严格的控制
            start = time.time()
            result = llm_extract_triples(text)
            if time.time() - start > self.timeout:
                logger.debug("[promotion] LLM 抽取超时，降级为空")
                return []
            return result
        except Exception as e:
            logger.debug("[promotion] LLM 抽取失败(降级): %s", e)
            return []

    # ── 差异化解构（复用 writer.classify_write）──
    def classify_promotion(
        self,
        cand: Candidate,
        existing: list[Memory],
        similarity_fn: Optional[Callable] = None,
    ) -> tuple[WriteAction, dict]:
        """对晋升候选做差异化写入决策（复用 writer 策略）。

        返回 (action, 决策信息)。额外处理：
          - 重复 → reinforce（不双写）
          - 冲突 → conflict candidate（不静默覆盖）
        """
        if cand.disposition != "promote":
            return WriteAction.NOOP, {"decision": cand.disposition}

        mem = Memory(
            memory_id=cand.content,  # 占位，classify_write 内不用
            type=cand.mem_type,
            content=cand.content,
        )
        # 转 MemoryObject 便于比较
        obj = MemoryObject.from_memory(mem)

        # 冲突检测：语义 vs 现有内容冲突（否定词）
        for old in existing:
            if _is_conflict(obj.content, old.content):
                return WriteAction.NOOP, {
                    "decision": "conflict_candidate",
                    "conflict_with": old.memory_id,
                    "reason": "内容冲突，不静默覆盖",
                }

        # 语义重复检测：完全或高度相似内容 → reinforce（不双写）
        for old in existing:
            sim = _content_similarity(obj.content, old.content)
            if sim >= 0.85:
                return WriteAction.REINFORCE, {
                    "decision": "repeat_reinforce",
                    "reinforced_id": old.memory_id,
                    "similarity": round(sim, 3),
                    "reason": "重复偏好，强化不双写",
                }

        # 复用 writer.classify_write
        action, report, target = writer.classify_write(mem, existing, similarity_fn)
        decision = {
            "action": action.value,
            "similarity": report.similarity,
            "superseded": report.superseded_ids,
            "reinforced_id": report.reinforced_id,
            "deduped_id": report.deduped_id,
        }
        # 重复 → 强调为 reinforce（writer 对 procedural/emotional 已做）
        if action in (WriteAction.DEDUP, WriteAction.REINFORCE):
            decision["repeat"] = True
        return action, decision

    def promote(
        self,
        cand: Candidate,
        existing: list[Memory],
        similarity_fn: Optional[Callable] = None,
    ) -> Candidate:
        """晋升候选：差异化写入决策 + 生命周期推进 candidate→validated→active。

        实际写入由调用方（Formation）执行；这里返回带决策的候选。
        """
        if cand.disposition != "promote":
            return cand
        action, decision = self.classify_promotion(cand, existing, similarity_fn)
        cand.meta["write_action"] = action.value
        cand.meta["decision"] = decision
        if action in (WriteAction.INSERT, WriteAction.OVERRIDE):
            cand.lifecycle = "validated"  # 待激活（写入后转 active）
        elif action in (WriteAction.REINFORCE, WriteAction.DEDUP):
            cand.lifecycle = "active"  # 已存在，直接 active
            cand.reason = "重复强化，不双写"
        return cand


def _content_similarity(a: str, b: str) -> float:
    """字符集 Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _is_conflict(a: str, b: str) -> bool:
    """检测两条记忆是否内容冲突（否定 vs 肯定，且共享≥2字符关键实体）。"""
    neg = ("不是", "不在", "没有", "不再", "别", "不要", "不")
    # "不" 单字否定，但排除 "不错/不客气/不知道" 等非冲突用法在 _strip_neg 里处理
    a_neg = _has_negation(a)
    b_neg = _has_negation(b)
    if a_neg == b_neg:
        return False  # 都是肯定或都是否定，不算冲突（完全重复由相似度处理）
    # 一否定一肯定：去掉否定词后找共享关键片段
    a_core = _strip_neg(a)
    b_core = _strip_neg(b)
    if len(a_core) < 2 or len(b_core) < 2:
        return False
    common = _longest_common(a_core, b_core)
    # 共享≥2字符片段，剥离泛词后有效内容≥3字符才判冲突
    if common and len(common) >= 2:
        generic = ("用户", "今天", "然后", "我们", "就是", "一直", "真的", "这个", "那个", "我", "你")
        stripped = common
        for g in generic:
            stripped = stripped.replace(g, "")
        if len(stripped) >= 3:
            return True
    return False


def _has_negation(text: str) -> bool:
    """检测文本是否含否定（单字'不'排除'不错/不客气/不知道'等）。"""
    strong = ("不是", "不在", "没有", "不再", "别", "不要")
    if any(n in text for n in strong):
        return True
    # 单字"不"：排除非否定常用词
    if "不" in text:
        non_neg = ("不错", "不客气", "不知道", "不小心", "不舒服", "不如", "不一定", "不碍事")
        if any(x in text for x in non_neg):
            return False
        return True
    return False


def _strip_neg(text: str) -> str:
    for n in ("不是", "不在", "没有", "不再", "别", "不要", "不"):
        text = text.replace(n, "")
    return text.strip()


def _longest_common(a: str, b: str) -> str:
    """最长公共子串（字符级）。"""
    try:
        from difflib import SequenceMatcher
        sm = SequenceMatcher(None, a, b)
        m = sm.find_longest_match(0, len(a), 0, len(b))
        return a[m.a:m.a + m.size]
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════
# 指标
# ══════════════════════════════════════════════════════════

@dataclass
class PromotionStats:
    """晋升前后指标对比。"""
    total_observed: int = 0
    promoted: int = 0
    session_only: int = 0
    dropped: int = 0
    reinforced: int = 0
    conflicts: int = 0
    mean_score: float = 0.0

    def promote_rate(self) -> float:
        return (self.promoted / self.total_observed) if self.total_observed else 0.0

    def drop_rate(self) -> float:
        return (self.dropped / self.total_observed) if self.total_observed else 0.0

    def reinforce_rate(self) -> float:
        return (self.reinforced / self.promoted) if self.promoted else 0.0

    def report(self) -> dict:
        return {
            "total": self.total_observed,
            "promoted": self.promoted,
            "session_only": self.session_only,
            "dropped": self.dropped,
            "reinforced": self.reinforced,
            "conflicts": self.conflicts,
            "promote_rate": round(self.promote_rate(), 3),
            "drop_rate": round(self.drop_rate(), 3),
            "reinforce_rate": round(self.reinforce_rate(), 3),
            "mean_score": round(self.mean_score, 3),
        }


def analyze(texts: list[str], existing: list[Memory] | None = None) -> PromotionStats:
    """批量分析一组消息的晋升分布（before/after 指标用）。"""
    eng = PromotionEngine(use_llm=False)
    stats = PromotionStats(total_observed=len(texts))
    existing = existing or []
    for t in texts:
        cand = eng.observe(t)
        if cand.disposition == "drop":
            stats.dropped += 1
        elif cand.disposition == "session_only":
            stats.session_only += 1
        else:
            stats.promoted += 1
            eng.promote(cand, existing)
            action = cand.meta.get("write_action", "")
            if action in ("reinforce", "dedup"):
                stats.reinforced += 1
            if cand.meta.get("decision", {}).get("decision") == "conflict_candidate":
                stats.conflicts += 1
        stats.mean_score += cand.score
    if stats.total_observed:
        stats.mean_score /= stats.total_observed
    return stats
