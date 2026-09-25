"""NexSandglass Orchestrator —— 热路径收编（Phase 1）。

在 Phase 0 facade 之上，收编 Hermes/Provider 的所有记忆热路径：
- 写入：FormationRouter（observe → 底层 store），禁止新代码直写各表
- 读取：RecallPlanner（并行多源聚合 → 统一 MemoryObject 列表）
- 协调：ConsolidationCoordinator（调用 dream_pipeline / evolve）
- 策略：PolicyEngine（模块开关、超时、limit）

设计约束：
- 子源超时/异常 → 跳过该源，不拖垮整体
- 全源失败 → 返回空上下文 + trace
- 保留 dev escape hatch：DirectStoreNotAllowedError 提示直写已被收编
"""
from __future__ import annotations

import logging
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from nexsandglass.engram.types import MemoryObject, LifecycleState
from nexsandglass.runtime.facade import MemoryContext

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# 异常
# ══════════════════════════════════════════════════════════

class DirectStoreNotAllowedError(Exception):
    """新代码直写内部表时抛出（dev escape hatch）。"""


# ══════════════════════════════════════════════════════════
# PolicyEngine
# ══════════════════════════════════════════════════════════

@dataclass
class ModulePolicy:
    enabled: bool = True      # 模块开关
    timeout: float = 3.0      # 单源超时（秒）
    limit: int = 10           # 返回条数上限
    priority: int = 50        # 聚合权重


_DEFAULT_POLICIES: dict[str, ModulePolicy] = {
    "search_router": ModulePolicy(timeout=3.0, limit=10, priority=60),
    "shadow":        ModulePolicy(timeout=1.5, limit=8,  priority=80),
    "wthread":       ModulePolicy(timeout=1.5, limit=8,  priority=40),
    "persona":       ModulePolicy(timeout=2.0, limit=5,  priority=70),
    "nyx":           ModulePolicy(timeout=1.5, limit=8,  priority=50),
    "consolidation": ModulePolicy(timeout=5.0, limit=1,  priority=0, enabled=False),
}


class PolicyEngine:
    """模块开关 / 超时 / limit 的统一策略引擎。"""

    def __init__(self, policies: Optional[dict[str, ModulePolicy]] = None):
        self._policies: dict[str, ModulePolicy] = dict(_DEFAULT_POLICIES)
        if policies:
            self._policies.update(policies)
        # 环境变量覆盖（NYX_POLICY_<MODULE>_ENABLED=0/1, _TIMEOUT, _LIMIT）
        self._apply_env_overrides()

    def _apply_env_overrides(self):
        for mod, pol in list(self._policies.items()):
            key_en = f"NYX_POLICY_{mod.upper()}_ENABLED"
            if key_en in os.environ:
                pol.enabled = os.environ[key_en] == "1"
            key_to = f"NYX_POLICY_{mod.upper()}_TIMEOUT"
            if key_to in os.environ:
                try:
                    pol.timeout = float(os.environ[key_to])
                except ValueError:
                    pass
            key_lim = f"NYX_POLICY_{mod.upper()}_LIMIT"
            if key_lim in os.environ:
                try:
                    pol.limit = int(os.environ[key_lim])
                except ValueError:
                    pass

    def enabled(self, module: str) -> bool:
        return self._policies.get(module, ModulePolicy()).enabled

    def timeout(self, module: str) -> float:
        return self._policies.get(module, ModulePolicy()).timeout

    def limit(self, module: str) -> int:
        return self._policies.get(module, ModulePolicy()).limit

    def priority(self, module: str) -> int:
        return self._policies.get(module, ModulePolicy()).priority

    def snapshot(self) -> dict:
        return {k: {"enabled": p.enabled, "timeout": p.timeout, "limit": p.limit, "priority": p.priority}
                for k, p in self._policies.items()}


# ══════════════════════════════════════════════════════════
# 数据载体
# ══════════════════════════════════════════════════════════

@dataclass
class RecallTrace:
    """单源召回结果 + trace。"""
    source: str
    ok: bool = False
    error: str = ""
    items: list[MemoryObject] = field(default_factory=list)
    elapsed: float = 0.0


@dataclass
class RecallResult:
    """统一召回结果：聚合的 MemoryObject 列表 + 各源 trace。"""
    objects: list[MemoryObject] = field(default_factory=list)
    traces: list[RecallTrace] = field(default_factory=list)
    query: str = ""
    token_budget: int = 0
    est_tokens: int = 0
    meta_intent: object = None   # MemoryIntent（v5.0）
    withheld: list = field(default_factory=list)   # v7.8 信任门扣下的（无正文）

    def to_context(self, separator: str = "\n") -> str:
        """拼成纯文本（供 system prompt 注入）。"""
        return separator.join(obj.content for obj in self.objects if obj.content)

    def as_texts(self) -> list[str]:
        return [obj.content for obj in self.objects if obj.content]


# ══════════════════════════════════════════════════════════
# 写入：FormationRouter
# ══════════════════════════════════════════════════════════

@dataclass
class FormationResult:
    ok: bool = False
    memory_id: str = ""
    memory_type: str = "semantic"
    lifecycle_state: str = "observed"
    routes_written: list[str] = field(default_factory=list)
    errors: dict = field(default_factory=dict)   # {route: err} 单路失败记录
    message: str = ""
    # v7.8 来源信任：trusted / unverified / tainted，以及命中的特征
    trust_signal: str = ""
    trust_flags: list = field(default_factory=list)


class FormationRouter:
    """写入路由：observe → 底层 store（sandglass / engram / shadow 索引）。

    规则级（本阶段）：默认全量 candidate，按类型分派到对应 store。
    禁止新代码直写各表 —— 统一经此入口。
    """

    def __init__(self, policy: Optional[PolicyEngine] = None):
        self.policy = policy or PolicyEngine()
        self._intent_mod = None
        self._promotion = None

    def _get_intent(self):
        if self._intent_mod is None:
            from nexsandglass.runtime import intent as im
            self._intent_mod = im
        return self._intent_mod

    def _get_promotion(self):
        if self._promotion is None:
            from nexsandglass.runtime.promotion import PromotionEngine
            self._promotion = PromotionEngine(use_llm=False, timeout=2.0)
        return self._promotion

    def observe(
        self,
        event: str,
        mem_type: str | None = None,
        source: str | None = None,
        raw_already_logged: bool = False,
        force_promote: bool = False,
    ) -> FormationResult:
        """观察一个事件并写入各底层 store（B3：Memory 是形成的）。

        固定流水线：Observation → Extract → Score → Type → Promote|SessionOnly|Drop。
        只有 promote 才差异化写长期 engram（REINFORCE/DEDUP/OVERRIDE/INSERT）。
        raw sandglass 始终保留作审计日志。force_promote 是内部逃生口（测试/已决策），
        默认关闭，MCP memory_observe 也走同一门禁。
        """
        fr = FormationResult()
        if not event:
            fr.message = "empty event"
            return fr

        try:
            from nexsandglass.core import sandglass_log
            from nexsandglass.core import provenance as _prov
            from nexsandglass.engram import bridge
            from nexsandglass.features.shadow_sand import shadow_index

            # 来源只认一个：落沙时绑定的 sender。以前落沙用 `source or "agent"`、
            # 晋升用 `source or "user"` —— 同一条事件在审计日志里是 agent 说的，
            # 在晋升判断里却被当成主人说的。
            origin = source or "agent"

            # 1. 落沙（原始审计日志）——除非调用方已落
            raw_mem_id = ""
            if not raw_already_logged:
                try:
                    raw_mem_id = sandglass_log.log_message(event, sender=origin,
                                                           return_id=True) or ""
                    fr.routes_written.append("sandglass")
                except Exception as e:
                    fr.errors["sandglass"] = str(e)
                    logger.warning("[Formation] sandglass 写入失败: %s", e)

            # 1.5 来源信任：优先取写入时绑定的那份，没有（调用方已落沙）再现场评估
            prov = (_prov.get(raw_mem_id) if raw_mem_id else None) or _prov.assess(event, origin)
            fr.trust_signal, fr.trust_flags = prov.signal, [f["rule"] for f in prov.flags]

            # tainted：只留审计日志，不进长期记忆的任何一路（engram / 影子 / 图谱）。
            # 这道门在 force_promote 之前，也不受它控制 —— 安全门不设布尔逃生口。
            if prov.signal == _prov.TAINTED:
                fr.ok = True
                fr.lifecycle_state = "observed"
                fr.memory_type = "tainted"
                fr.message = "withheld:tainted:" + ",".join(fr.trust_flags)
                return fr

            # 2. Promotion 决策（B3：Memory 是形成的）
            promotion = self._get_promotion()
            cand = promotion.observe(event, source=origin)
            # 规则与身份只能由主人亲口确立 —— 在任何处置分支之前降级，
            # 否则 session_only / drop 的早返回会把降级前的类型报给调用方
            demoted = ""
            if not prov.may_instruct and cand.mem_type in ("procedural", "identity"):
                demoted, cand.mem_type = cand.mem_type, "semantic"

            # force_promote 逃生口（测试/已决策，显式跳过门禁）
            if force_promote:
                cand.disposition = "promote"

            # 3. 处置
            if cand.disposition == "drop":
                # 闲聊/OTP：只落 raw 审计，不进长期
                fr.ok = True
                fr.lifecycle_state = "observed"
                fr.memory_type = cand.mem_type
                fr.message = "dropped:" + cand.mem_type
                return fr

            if cand.disposition == "session_only":
                # 一次性/临时：只落 raw 审计，不进长期 engram
                fr.ok = True
                fr.lifecycle_state = "observed"
                fr.memory_type = cand.mem_type
                fr.message = "session_only:" + cand.mem_type
                return fr

            # promote → 差异化写入长期（REINFORCE/DEDUP/OVERRIDE/INSERT）
            mtype = mem_type or cand.mem_type or bridge.classify_memory_type(event)
            # 规则与身份只能由主人亲口确立。非主人来源的"记住：以后……"降为普通信息：
            # 可以被召回（带来源标注），但永远进不了「动态行为修正案」。
            if not prov.may_instruct and mtype in ("procedural", "identity"):
                demoted, mtype = demoted or mtype, "semantic"
            fr.memory_type = mtype

            # 差异化决策（复用 writer.classify_write 语义，加载已有记忆做重复/冲突判断）
            try:
                from nexsandglass.engram.types import Memory as _Mem
                recent_rows = bridge.recent(n=50)
                existing = [
                    _Mem(memory_id=f"engram:{r.get('ts','')}",
                         type=r.get("type", "semantic"),
                         content=r.get("content", ""))
                    for r in recent_rows if isinstance(r, dict) and r.get("content")
                ]
                write_action, decision = promotion.classify_promotion(cand, existing)
                write_action = write_action.value if hasattr(write_action, "value") else str(write_action)
                if decision.get("decision") == "conflict_candidate":
                    write_action = "conflict"
            except Exception:
                write_action = cand.meta.get("write_action", "insert")
                if write_action == "noop":
                    write_action = "insert"

            # 落差异化 engram
            try:
                res = bridge.ingest_classified(
                    event,
                    action=write_action.upper(),
                    mem_type=mtype,
                    origin=origin,
                    trust_signal=prov.signal,
                    source_mem_id=raw_mem_id or None,
                )
                fr.routes_written.append("engram")
                fr.memory_id = res.get("id") or f"engram:{event[:20]}"
                if write_action in ("reinforce", "dedup"):
                    fr.message = f"{write_action}:" + cand.mem_type
                else:
                    fr.message = "promoted:" + cand.mem_type
                if demoted:
                    fr.message += f"|demoted:{demoted}->semantic(origin={origin})"
            except Exception as e:
                fr.errors["engram"] = str(e)
                logger.warning("[Formation] engram 差异化写入失败: %s", e)

            # shadow 索引（信任/实体）
            try:
                line_num = _last_sandglass_line()
                shadow_index(event, category=mtype, line_num=line_num)
                fr.routes_written.append("shadow")
            except Exception as e:
                fr.errors["shadow"] = str(e)
                logger.warning("[Formation] shadow 索引失败: %s", e)

            # 织线三元组：抽出来的主语是 "user"，也就是「关于主人的事实」。
            # 只有主人亲口说的才能写成关于主人的事实 —— 以前网页正文里的
            # "改用 X" 会变成「user 使用 X」。
            if prov.origin_class == _prov.PRINCIPAL:
                try:
                    from nexsandglass.features.weavethread import wthread_store
                    wthread_store(event, line_num=_last_sandglass_line(), subject="user",
                                  mem_id=raw_mem_id or None)
                    fr.routes_written.append("wthread")
                except Exception as e:
                    fr.errors["wthread"] = str(e)
                    logger.warning("[Formation] wthread 写入失败: %s", e)

            obj = MemoryObject(
                content=event,
                type=mtype,
                created_at=_now(),
                source_id=source,
                provenance="orchestrator.formation",
                origin=origin,
                trust_signal=prov.signal,
                status=LifecycleState.VALIDATED.value if write_action == "insert" else LifecycleState.ACTIVE.value,
            )
            fr.memory_id = fr.memory_id or (obj.memory_id or obj._fallback_id())
            fr.lifecycle_state = obj.status
            fr.ok = True
            return fr

        except Exception as e:
            logger.warning("[Formation] observe 失败: %s", e)
            fr.message = str(e)
            return fr


def _last_sandglass_line() -> int:
    """读取 sandglass.txt 当前行数（近似行号，供 shadow/wthread 索引）。"""
    try:
        from nexsandglass.core.sandglass_paths import _NB
        path = os.path.join(_NB, "sandglass.txt")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return sum(1 for _ in f)
    except Exception:
        pass
    return 0


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ══════════════════════════════════════════════════════════
# 读取：RecallPlanner
# ══════════════════════════════════════════════════════════

class RecallPlanner:
    """并行多源召回，统一输出 MemoryObject 列表。

    源（受 PolicyEngine 控制，异常/超时 → 跳过）：
      - search_router（SearchRouter 四路）
      - shadow（影子沙）
      - wthread（织线）
      - persona（画像）
      - nyx（nyx_sense / nyx_hunt）
    """

    def __init__(self, policy: Optional[PolicyEngine] = None):
        self.policy = policy or PolicyEngine()
        self._intent_mod = None

    def _get_intent(self):
        if self._intent_mod is None:
            from nexsandglass.runtime import intent as im
            self._intent_mod = im
        return self._intent_mod

    def _adapt_search_router(self, query: str) -> list[MemoryObject]:
        from nexsandglass.core.search_router import SearchRouter
        router = SearchRouter()
        results = router.search(query, limit=self.policy.limit("search_router"))
        return [_from_search_item(item, "search_router") for item in results]

    def _adapt_shadow(self, query: str) -> list[MemoryObject]:
        from nexsandglass.features.shadow_sand import shadow_search
        hits = shadow_search(query, limit=self.policy.limit("shadow"))
        # hits 形如 [(score, line_num), ...] 或 [(line_num, score)]
        return [_make_obj(f"shadow:{ln}", _shadow_text(ln), "shadow", confidence=score,
                          line=ln)
                for score, ln in _normalize_shadow(hits)]

    def _adapt_wthread(self, query: str) -> list[MemoryObject]:
        from nexsandglass.features.weavethread import wthread_query, wthread_weave
        objs = []
        triples = wthread_query(entity=query, limit=self.policy.limit("wthread"))
        for t in triples:
            if isinstance(t, dict) and t.get("retracted_at"):
                continue                     # 已被更精确版本取代的旧版本，不是现在的信念
            text = _triple_text(t)
            if not text:
                continue
            if isinstance(t, dict) and t.get("valid_until"):
                text = f"（曾，至 {str(t['valid_until'])[:10]}）{text}"
            # memory_id 必须逐条唯一：以前全部叫 "wthread"，聚合去重时只会留下第一条
            tid = t.get("id") if isinstance(t, dict) else None
            objs.append(_make_obj(f"wthread:{tid or text}", text, "wthread", confidence=0.6,
                                  line=t.get("source_line") if isinstance(t, dict) else None))
        return objs

    def _adapt_persona(self, query: str) -> list[MemoryObject]:
        from nexsandglass.l3.persona_l3 import persona_canvas
        persona = persona_canvas()[:500]
        if persona:
            return [_make_obj("persona", persona, "persona", confidence=0.8)]
        return []

    def _adapt_nyx(self, query: str) -> list[MemoryObject]:
        from nexsandglass.interfaces.nyx import nyx_sense, nyx_hunt
        objs = []
        sense = nyx_sense(query) or {}
        familiar = sense.get("familiar_ratio", 0.0)
        if isinstance(sense, dict) and familiar > 0:
            known = sense.get("known_tokens") or []
            objs.append(_make_obj(
                "nyx", f"[熟悉度 {familiar}] {query}",
                "nyx", confidence=min(0.9, 0.4 + familiar * 0.5)))
            for tok in known[:3]:
                objs.append(_make_obj(f"nyx:{tok}", f"[已知实体] {tok}", "nyx", confidence=0.6))
        hunt = nyx_hunt(query, limit=self.policy.limit("nyx")) or {}
        phantoms = hunt.get("phantoms") or []
        if isinstance(phantoms, list):
            for p in phantoms[: self.policy.limit("nyx")]:
                if not isinstance(p, dict):
                    continue
                token = p.get("token", "")
                whisper = p.get("whisper", "")
                sightings = p.get("sightings", 0)
                text = whisper or f"[鬼影] {token} (×{sightings})"
                objs.append(_make_obj(f"nyx:{token}", text[:300], "nyx",
                                      confidence=min(0.9, 0.3 + 0.1 * sightings)))
        return objs

    def recent(self, n: int = 10) -> list:
        """最近 n 条原始沙漏记忆（adapter：sandglass_vault.recent）。返回 MemoryObject 列表。"""
        objs: list = []
        try:
            from nexsandglass.features.sandglass_vault import recent as _recent
            rows = _recent(n)
            for ln, ts, txt in rows:
                objs.append(_make_obj(f"sandglass:{ln}", str(txt)[:200], "sandglass", confidence=0.5))
        except Exception as e:
            logger.warning("[RecallPlanner.recent] 失败: %s", e)
        return objs

    def recall(self, query: str, token_budget: int = 1500) -> MemoryContext:
        """并行多源召回。返回 MemoryContext；原始 hits 仅记 debug。

        先解析 MemoryIntent（v5.0），按策略选源 + 排序。
        Intent 失败 → generic_semantic（全源混合降级）。
        """
        result = RecallResult(query=query, token_budget=token_budget)
        im = self._get_intent()
        intent = im.parse_intent(query)
        strategy = intent.strategy

        # 按策略选择源（包住而非替换 SearchRouter）
        sources = [("search_router", self._adapt_search_router)]  # SearchRouter 恒在
        if strategy in ("ownership_history_multi", "ownership_current_multi"):
            sources += [
                ("shadow", self._adapt_shadow),
                ("wthread", self._adapt_wthread),
                ("persona", self._adapt_persona),
                ("nyx", self._adapt_nyx),
            ]
        elif strategy == "nyx_hunt_scene":
            # 熟悉但搜不到 → nyx_hunt 情景通道优先
            sources += [("nyx", self._adapt_nyx), ("shadow", self._adapt_shadow)]
        else:  # generic_semantic：SearchRouter 混合（原有全源）
            sources += [
                ("shadow", self._adapt_shadow),
                ("wthread", self._adapt_wthread),
                ("persona", self._adapt_persona),
                ("nyx", self._adapt_nyx),
            ]
        result.meta_intent = intent

        def run_source(name: str, fn: Callable) -> RecallTrace:
            tr = RecallTrace(source=name)
            if not self.policy.enabled(name):
                tr.error = "disabled by policy"
                return tr
            import time
            t0 = time.time()
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(fn, query)
                    items = fut.result(timeout=self.policy.timeout(name))
                tr.items = items[: self.policy.limit(name)]
                tr.ok = True
            except FutureTimeout:
                tr.error = f"timeout>{self.policy.timeout(name)}s"
            except Exception as e:
                tr.error = str(e)
            tr.elapsed = round(time.time() - t0, 3)
            return tr

        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            futures = {pool.submit(run_source, name, fn): name for name, fn in sources}
            for fut in futures:
                try:
                    result.traces.append(fut.result(timeout=max(self.policy.timeout(n) for n, _ in sources) + 2))
                except Exception as e:
                    result.traces.append(RecallTrace(source=futures[fut], error=str(e)))

        # 聚合：按 priority 排序 + 去重（memory_id）
        seen: set[str] = set()
        ordered: list[MemoryObject] = []
        # 先按源 priority 高→低收集
        for tr in sorted(result.traces, key=lambda t: self.policy.priority(t.source), reverse=True):
            for obj in tr.items:
                mid = obj.memory_id or obj.content
                if mid in seen:
                    continue
                seen.add(mid)
                ordered.append(obj)

        # v5.0：意图感知排序（包住而非替换——在聚合结果之上重排，再截断）
        if ordered and intent.strategy != "generic_semantic":
            ordered = im.rank_objects(ordered, intent, query=query)
        # v7.8 信任门：tainted 不出库，unverified 带来源标注且降为信息（不能当规则）
        ordered, withheld = _apply_trust(ordered)
        result.withheld = withheld
        result.objects = ordered[: token_budget // 10 if token_budget else 20]
        result.est_tokens = sum(len(o.content) // 4 for o in result.objects)
        # hits 仅 debug（不暴露给上层）
        if result.objects:
            logger.debug(
                "[recall] query=%r hits=%d sources=%s",
                query, len(result.objects),
                [t.source for t in result.traces if t.ok],
            )
        return self._to_memory_context(result)

    @staticmethod
    def _to_memory_context(result: RecallResult) -> MemoryContext:
        """将 RecallResult 转成 MemoryContext（B0 结构化，text 可直进 system prompt）。"""
        objects = result.objects or []
        strings = [o.content for o in objects if o.content]
        ids = [o.memory_id or o.content for o in objects]
        im = None
        strategy = "generic_semantic"
        reasons = []
        degraded = any(not t.ok for t in result.traces) or not objects
        intent = getattr(result, "meta_intent", None)
        if intent is not None:
            strategy = getattr(intent, "strategy", "generic_semantic") or "generic_semantic"
            reasons = list(getattr(intent, "reasons", []) or [])
        mc = MemoryContext(
            query=result.query,
            token_budget=result.token_budget,
            strings=strings,
            memory_ids=ids,
            objects=objects,
            traces=list(result.traces),
            est_tokens=result.est_tokens,
            strategy_used=strategy,
            reasons=reasons,
            degraded=degraded,
        )
        mc.text = "\n".join(strings)
        mc.withheld = list(getattr(result, "withheld", []) or [])
        mc.meta_intent = intent
        return mc


def _from_search_item(item, source: str) -> MemoryObject:
    """SearchRouter 返回项 → MemoryObject（item 形如 (line, ts, text)）。"""
    if isinstance(item, (tuple, list)):
        text = item[-1] if item else ""
        line = item[0] if item else None
    else:
        text = str(item)
        line = None
    return _make_obj(f"{source}:{line}" if line else source, text, source, line=line)


def _make_obj(mid: str, text: str, source: str, confidence: float = 0.5,
              line=None) -> MemoryObject:
    """line：日志行号（= 中枢 line_start）。召回侧靠它找回写入时绑定的来源。"""
    return MemoryObject(
        memory_id=mid,
        content=text,
        type="semantic",
        provenance=source,
        confidence=confidence,
        status=LifecycleState.OBSERVED.value,
        source_id=str(line) if line is not None and str(line).isdigit() else None,
    )


def _apply_trust(objs: list) -> tuple:
    """按写入时绑定的来源给召回结果定性。返回 (放行的对象, 扣下的摘要)。

      tainted     扣下，不出库；摘要里只有 id 与命中规则，**不含正文**
      unverified  正文前加来源标注（"（未经证实·来源:tool）"），类型降为 semantic ——
                  它可以作为信息被读到，但永远不会被渲染成规则
      trusted     原样放行

    找不到来源的对象（画像、Déjà Vu 熟悉度这类系统生成物，或 v7.8 之前的旧库）
    不改动、不标注 —— 它们的信任传播是下一步的事，这里不假装已经做到。
    """
    from nexsandglass.core import provenance as _prov
    kept, withheld = [], []
    for o in objs:
        prov = None
        if o.source_id and str(o.source_id).isdigit():
            try:
                prov = _prov.for_line(int(o.source_id))
            except Exception:
                prov = None
        if prov is None:
            kept.append(o)
            continue
        o.origin, o.trust_signal = prov.origin, prov.signal
        if prov.signal == _prov.TAINTED:
            withheld.append({"memory_id": o.memory_id, "origin": prov.origin,
                             "flags": [f["rule"] for f in prov.flags]})
            continue
        if prov.signal != _prov.TRUSTED:
            label = prov.label()
            if label and not o.content.startswith(label):
                o.content = label + o.content
            if o.type in ("procedural", "identity"):
                o.type = "semantic"
        kept.append(o)
    return kept, withheld


def _normalize_shadow(hits) -> list[tuple[float, int]]:
    """归一 shadow_search 返回格式（兼容 (score,line) 或 (line,score)）。"""
    out = []
    for h in hits:
        if isinstance(h, (tuple, list)) and len(h) >= 2:
            a, b = h[0], h[1]
            if isinstance(a, (int, float)) and isinstance(b, int) and 0 <= a <= 1:
                out.append((float(a), b))
            elif isinstance(a, int) and isinstance(b, (int, float)):
                out.append((float(b), a))
    return out


def _shadow_text(line_num: int) -> str:
    try:
        from nexsandglass.features.sandglass_vault import search
        r = search(str(line_num), limit=1)
        if r:
            return r[0][-1][:200]
    except Exception:
        pass
    return f"shadow#{line_num}"


def _triple_text(t) -> str:
    if isinstance(t, dict):
        s = t.get("subject", "")
        # 库里的列叫 relation。以前只读 predicate —— 每条三元组都渲染成空串被丢掉，
        # 知识图谱这一路召回从来没有返回过任何东西。
        p = t.get("predicate") or t.get("relation", "")
        o = t.get("object", "")
        if s and p and o:
            return f"{s} {p} {o}"
        return t.get("text") or t.get("triple") or ""
    return str(t)


# ══════════════════════════════════════════════════════════
# ConsolidationCoordinator
# ══════════════════════════════════════════════════════════

@dataclass
class ConsolidationReport:
    ok: bool = False
    evolved: int = 0
    report: dict = field(default_factory=dict)
    message: str = ""


class ConsolidationCoordinator:
    """调用 dream_pipeline / evolve 的协调入口（受 PolicyEngine 控制）。"""

    def __init__(self, policy: Optional[PolicyEngine] = None):
        self.policy = policy or PolicyEngine()
        self._intent_mod = None

    def _get_intent(self):
        if self._intent_mod is None:
            from nexsandglass.runtime import intent as im
            self._intent_mod = im
        return self._intent_mod

    def run_consolidation(self, memories: Optional[list[MemoryObject]] = None) -> ConsolidationReport:
        cr = ConsolidationReport()
        if not self.policy.enabled("consolidation"):
            cr.message = "consolidation disabled by policy"
            return cr
        try:
            from nexsandglass.engram.evolve import run_evolution_pass
            from nexsandglass.engram.types import Memory

            mems = [m.to_memory() if hasattr(m, "to_memory") else m for m in (memories or [])]
            evolved, report = run_evolution_pass(mems)
            cr.ok = True
            cr.evolved = len(evolved)
            cr.report = report.summary if hasattr(report, "summary") else {}
            cr.message = "consolidated"
        except Exception as e:
            cr.message = str(e)
            logger.debug("[Consolidation] 失败: %s", e)
        return cr


# ══════════════════════════════════════════════════════════
# ContextBuilder (stub)
# ══════════════════════════════════════════════════════════

class ContextBuilder:
    """构建注入上下文的组装器：MemoryBundle 流水线（Phase 3 合一）。

    将 orchestrator.recall 的 MemoryContext 候选 → MemoryBundle → 渲染。
    Provider 四层块作为 Bundle 槽位注入。
    """

    def __init__(self):
        self._bundle_module = None

    def _get_bundle(self):
        if self._bundle_module is None:
            from nexsandglass.runtime import bundle as bm
            self._bundle_module = bm
        return self._bundle_module

    def build_bundle(
        self,
        mc: MemoryContext,
        max_tokens: int = 1500,
        persona_layer: str = "",
        offset_layer: str = "",
        thread_layer: str = "",
        open_loops: str = "",
    ):
        """从 MemoryContext 候选构建 MemoryBundle。"""
        bm = self._get_bundle()
        candidates = [
            MemoryObject(content=s, memory_id=mid if mid else s, type="semantic")
            for s, mid in zip(mc.strings, mc.memory_ids)
        ]
        return bm.build_bundle(
            candidates,
            query=mc.query,
            budget=max_tokens,
            persona_layer=persona_layer,
            offset_layer=offset_layer,
            thread_layer=thread_layer,
            open_loops=open_loops,
        )

    def build(self, mc: MemoryContext, max_tokens: int = 1500, **layers) -> str:
        """构建并渲染最终上下文。"""
        bundle = self.build_bundle(mc, max_tokens, **layers)
        return bundle.render()


# ══════════════════════════════════════════════════════════
# NyxOrchestrator —— 统一门面
# ══════════════════════════════════════════════════════════

class NyxOrchestrator:
    """热路径收编的单一入口。Provider / MCP 都走这里，不再直连内部表。"""

    def __init__(self, policy: Optional[PolicyEngine] = None):
        self.policy = policy or PolicyEngine()
        self.formation = FormationRouter(self.policy)
        self.recall_planner = RecallPlanner(self.policy)
        self.consolidation = ConsolidationCoordinator(self.policy)
        self.context = ContextBuilder()

    # ── 写入热路径 ──
    def observe(self, event: str, mem_type: str | None = None, source: str | None = None,
                raw_already_logged: bool = False, force_promote: bool = False) -> FormationResult:
        return self.formation.observe(event, mem_type, source, raw_already_logged, force_promote)

    # ── 读取热路径 ──
    def recall(self, query: str, token_budget: int = 1500) -> MemoryContext:
        """统一召回：返回 MemoryContext。原始 hits 仅 debug。"""
        return self.recall_planner.recall(query, token_budget)

    def recall_bundle(self, query: str, max_tokens: int = 1500, **layers):
        """召回 + 构建 MemoryBundle（合并出口）。"""
        mc = self.recall_planner.recall(query, max_tokens)
        return self.context.build_bundle(mc, max_tokens, **layers)

    def recent(self, n: int = 10) -> list:
        """最近 n 条原始记忆（adapter：sandglass_vault.recent）。"""
        return self.recall_planner.recent(n)

    def recall_text(self, query: str, max_tokens: int = 1500, **layers) -> str:
        mc = self.recall_planner.recall(query, max_tokens)
        return self.context.build(mc, max_tokens, **layers)

    def cognitive_recall(self, query: str, max_tokens: int = 1500, **layers) -> str:
        """v7.0 端到端：Intent Recall → Bundle → Context（Formation 已由 observe 喂入）。

        返回最终注入 Agent 的上下文文本。
        """
        # 1. Intent Recall（自适应）
        intent = self._parse_intent(query)
        mc = self.recall_planner.recall(query, max_tokens)
        # 2. 若 history intent，补充演变链（通过模块函数）
        if intent and intent.history:
            try:
                from nexsandglass.runtime import intent as im
                hctx = im.history_context(intent)
                if hctx:
                    mc.strings = list(mc.strings) + ["[历史演变]"] + hctx.split("\n")
            except Exception as e:
                logger.warning("cognitive_recall history_context 失败: %s", e)
        # 3. Bundle 构建 + 渲染
        bundle = self.context.build_bundle(mc, max_tokens, **layers)
        return bundle.render()

    def _parse_intent(self, query: str):
        try:
            from nexsandglass.runtime import intent as im
            return im.parse_intent(query)
        except Exception:
            return None

    # ── 协调 ──
    def consolidate(self, memories: Optional[list[MemoryObject]] = None) -> ConsolidationReport:
        return self.consolidation.run_consolidation(memories)

    def policy_snapshot(self) -> dict:
        return self.policy.snapshot()


# 模块级单例（避免每次重建）
_orchestrator: Optional[NyxOrchestrator] = None
_orchestrator_lock = threading.Lock()


def get_orchestrator() -> NyxOrchestrator:
    """获取全局 NyxOrchestrator 单例。"""
    global _orchestrator
    if _orchestrator is None:
        with _orchestrator_lock:
            if _orchestrator is None:
                _orchestrator = NyxOrchestrator()
    return _orchestrator
