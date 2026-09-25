"""NexSandglass 稳定门面（Cognitive Memory OS 地基）。

为上层（Provider / Hermes / 未来认知内核）提供稳定、最小、面向意图的
记忆操作接口。内部委托现有函数（sandglass_log / bridge / SearchRouter），
对外不暴露实现细节。

四个稳定操作：
  observe(event)        — 观察一个事件（写入记忆）
  recall(query, context, token_budget) -> MemoryContext
                        — 召回相关记忆（当前 MemoryContext 透传字符串）
  feedback(outcome)     — 反馈一次召回结果（强化/弱化）
  forget(selector)      — 遗忘选中的记忆

设计约束（任务要求）：
  - 不重写 SearchRouter / Dream
  - 不新增并行记忆引擎
  - 内部先委托现有函数
  - MemoryContext 可先透传字符串
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

# B0 运行时开关：NYX_RUNTIME=0 紧急回滚旧路径（打 deprecated 日志）
NYX_RUNTIME = os.environ.get("NYX_RUNTIME", "1") == "1"
from typing import Optional

from nexsandglass.engram.types import MemoryObject

logger = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    """召回结果上下文（B0 唯一召回出口，结构化）。

    - text: 最终 render 结果，可直接进 system prompt
    - bundle: MemoryBundle | None（结构化槽位）
    - objects: 召回的唯一 canonical MemoryObject 列表
    - strategy_used / reasons / traces: 意图策略与各源追踪
    - est_tokens: 估算 token
    - degraded: 是否降级（子源失败/意图失败）
    """
    text: str = ""
    bundle: object = None            # MemoryBundle | None
    objects: list = field(default_factory=list)   # list[MemoryObject]
    strategy_used: str = "generic_semantic"
    reasons: list = field(default_factory=list)
    traces: list = field(default_factory=list)
    est_tokens: int = 0
    degraded: bool = False
    # ── 兼容字段（旧消费者可用）──
    strings: list[str] = field(default_factory=list)
    memory_ids: list[str] = field(default_factory=list)
    query: str = ""
    token_budget: int = 0
    meta_intent: object = None
    # v7.8：被信任门扣下的召回结果（只有 id / 来源 / 命中规则，不含正文）
    withheld: list = field(default_factory=list)

    def to_text(self, separator: str = "\n") -> str:
        """将上下文拼成纯文本（供 system prompt 注入）。优先用 text。"""
        return self.text or separator.join(self.strings)


@dataclass
class ObserveReport:
    """observe() 的结果报告。"""
    ok: bool = False
    memory_id: str = ""
    memory_type: str = "semantic"
    lifecycle_state: str = "observed"
    message: str = ""


# ══════════════════════════════════════════════════════════
# 门面核心
# ══════════════════════════════════════════════════════════

def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def observe(event: str, *, source: str = "runtime", session_id: str | None = None,
             mem_type: str | None = None, raw_already_logged: bool = False,
             force_promote: bool = False) -> ObserveReport:
    """观察一个事件并写入记忆（B0/B3 唯一写入 API）。

    event 必填；source / session_id / mem_type 关键字参数。
    委托 orchestrator（FormationRouter）单一写入路径，经 Promotion 门禁（B3）。
    force_promote 为内部逃生口（测试/已决策）。返回 ObserveReport。
    """
    if not NYX_RUNTIME:
        logger.warning("[B0] NYX_RUNTIME=0，observe 回滚旧路径（deprecated）")
        return _legacy_observe(event, mem_type, source)
    try:
        from nexsandglass.runtime.orchestrator import get_orchestrator
        orch = get_orchestrator()
        fr = orch.observe(event, mem_type, source, raw_already_logged, force_promote)
        return ObserveReport(
            ok=fr.ok,
            memory_id=fr.memory_id,
            memory_type=fr.memory_type,
            lifecycle_state=fr.lifecycle_state,
            message=fr.message,
        )
    except Exception as e:
        logger.warning("[facade.observe] 失败: %s", e)
        return ObserveReport(ok=False, message=str(e))


def recall(
    query: str,
    *,
    context: str | None = None,
    token_budget: int = 1500,
    session_id: str | None = None,
) -> MemoryContext:
    """召回与 query 相关的记忆（B0 唯一召回 API）。

    委托 orchestrator（Intent→RecallPlanner→Rank→Bundle→Context）全链路，
    返回结构化 MemoryContext（text 可直接进 system prompt）。
    """
    try:
        from nexsandglass.runtime.orchestrator import get_orchestrator
        orch = get_orchestrator()
        return orch.recall(query, token_budget)
    except Exception as e:
        logger.warning("[facade.recall] 降级为空上下文: %s", e)
        return MemoryContext(
            query=query, token_budget=token_budget, degraded=True,
            text="", strings=[],
        )


def feedback(outcome: dict) -> dict:
    """反馈一次召回/观察结果，用于强化或弱化记忆。

    从 engram store 加载对应 Memory → recall_feedback 提升/降低权重 → 持久化回写。
    helpful=false 走 weaken（降低 decay_weight）。返回处理报告。
    """
    report = {"ok": False, "action": "noop", "detail": {}}
    try:
        from nexsandglass.engram.loops.recall_writer import recall_feedback
        from nexsandglass.engram import bridge
        from nexsandglass.engram.types import Memory
        import json as _json

        recalled_ids = outcome.get("memory_ids") or outcome.get("ids") or []
        helpful = bool(outcome.get("helpful", True))
        if not recalled_ids:
            report["ok"] = True
            report["action"] = "noop"
            report["detail"] = {"reason": "no recalled ids"}
            return report

        store = bridge._STORE
        id_set = set(recalled_ids)
        # 1. 从 engram store 加载匹配行 → Memory（memory_id=engram:{ts}）
        matched: list[Memory] = []
        updated_rows = []
        changed = 0
        if os.path.exists(store):
            with open(store, encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                except Exception:
                    continue
                mid = f"engram:{row.get('ts', '')}"
                mem = Memory(
                    memory_id=mid, type=row.get("type", "semantic"),
                    content=row.get("content", ""),
                    access_count=row.get("access_count", 0),
                    decay_weight=row.get("decay_weight", 1.0),
                    created_at=row.get("ts", ""),
                )
                if mid in id_set:
                    matched.append(mem)
            # 2. 用 recall_feedback 提升/降低
            if matched:
                if helpful:
                    boosted, _ = recall_feedback(matched, id_set)
                else:
                    # weaken：降低 decay_weight + 不升 access
                    boosted = []
                    for m in matched:
                        import dataclasses
                        nm = Memory(**{f.name: getattr(m, f.name) for f in dataclasses.fields(Memory)})
                        nm.decay_weight = max(0.0, nm.decay_weight - 0.2)
                        boosted.append(nm)
                # 3. 持久化回写（weight 字段）
                weight_map = {m.memory_id: m.decay_weight for m in boosted}
                out_lines = []
                for line in lines:
                    line_s = line.strip()
                    if not line_s:
                        out_lines.append(line)
                        continue
                    try:
                        row = _json.loads(line_s)
                    except Exception:
                        out_lines.append(line)
                        continue
                    mid = f"engram:{row.get('ts', '')}"
                    if mid in weight_map:
                        row["decay_weight"] = weight_map[mid]
                        row["access_count"] = row.get("access_count", 0) + (1 if helpful else 0)
                        out_lines.append(_json.dumps(row, ensure_ascii=False))
                        changed += 1
                    else:
                        out_lines.append(line)
                with open(store, "w", encoding="utf-8") as f:
                    f.write("\n".join(out_lines) + ("\n" if out_lines else ""))

        report["ok"] = True
        report["action"] = "reinforce" if helpful else "weaken"
        report["detail"] = {"recalled_ids": recalled_ids, "helpful": helpful,
                            "matched": len(matched), "persisted_changes": changed}
    except Exception as e:
        logger.warning("[facade.feedback] 失败: %s", e)
        report["detail"] = {"error": str(e)}
    return report


def forget(selector: dict) -> dict:
    """遗忘选中的记忆 —— **真删，不是只打个标记**。

    selector 支持:
      - {"mem_id": "m_..."} / {"mem_ids": [...]}
      - {"seq": N} / {"seqs": [...]}
      - {"contains": "子串"}   正文精确子串匹配
      - {"all": true}          清空（谨慎）
      - {"source_id": "..."}   兼容旧形态，经 memid.resolve 解析
      - {"dry_run": true}      只列出会删什么，不动任何东西
      - {"purge_now": true}    不留隔离区，当场不可逆（法务 / 他人隐私 / 误粘贴凭据）
      - {"retention_days": N}  覆盖本次的隔离期天数

    旧实现只遍历 engram_store.jsonl，而召回路径读的是 sandglass.txt ——
    于是 forget 返回 ok、内容原封不动、下一轮照样被召回。
    现在委托 erasure.forget()，级联覆盖 7 处：中枢 / 日志正文 / FTS5 /
    倒排 / 影子沙 / 知识图谱 / engram，并留墓碑防止重建时复活。
    """
    try:
        from nexsandglass.core import erasure, memid

        sel = dict(selector or {})
        dry = bool(sel.pop("dry_run", False))
        reason = sel.pop("reason", "user_forget")
        mode = "purge" if sel.pop("purge_now", False) else "quarantine"
        days = sel.pop("retention_days", None)

        # 兼容旧调用：source_id 可能是 "engram:<ts>" / "shadow:<line>" / 裸行号
        src = sel.pop("source_id", None)
        if src is not None:
            mid = memid.resolve(src)
            if mid:
                sel.setdefault("mem_ids", []).append(mid)

        rep = erasure.forget(sel, reason=reason, apply=not dry, mode=mode,
                             retention_days=days)
        return {
            "ok": True,
            "action": "dry_run" if dry else "erased",
            "removed": rep["hub"],
            "selected": rep["selected"],
            "found": rep["found"],
            "preview": rep["preview"],
            # 调用方（以及最终的用户）必须能看到「还能不能反悔、能反悔到哪天」，
            # 否则"删了"和"删了但还能救"在返回值里长得一模一样。
            "recoverable": rep.get("recoverable", False),
            "purge_after": rep.get("purge_after", ""),
            "detail": {k: rep[k] for k in
                       ("journal_lines", "fts", "idx", "shadow", "engram", "vectors")},
        }
    except Exception as e:
        logger.error("[facade.forget] 失败: %s", e)
        return {"ok": False, "action": "error", "removed": 0, "error": str(e)}


def restore(mem_id: str) -> dict:
    """把一条被 forget 的记忆从隔离区还原（隔离期内有效）。

    这是 B0 契约的第五个操作。加它的理由只有一条：
    v7.4 那次误抹 345 行真实对话之后，仓库里**没有任何一条路径能把它们拿回来**。
    """
    try:
        from nexsandglass.core import erasure
        rep = erasure.restore(mem_id)
        return {"ok": rep["ok"], "action": "restore", "mem_id": mem_id,
                "found": rep["found"], "problems": rep["problems"],
                "notes": rep.get("notes", []),
                "detail": {k: rep[k] for k in
                           ("journal", "hub", "fts", "idx", "engram", "shadow")}}
    except Exception as e:
        logger.error("[facade.restore] 失败: %s", e)
        return {"ok": False, "action": "error", "mem_id": mem_id, "error": str(e)}


def purge_forgotten(*, apply: bool = False, mem_ids: list = None) -> dict:
    """隔离区到期擦除（cron / 维护入口）。apply=False 只预演。

    没有人跑这个，"保留 30 天"就变成"永久留着" ——
    `memid.health()` 的 pending_purge 项正是为了让这件事不被忘记。
    """
    try:
        from nexsandglass.core import erasure
        rep = erasure.purge(mem_ids=mem_ids, apply=apply)
        return {"ok": rep.get("ok", False), "action": rep.get("action", ""),
                "selected": rep["selected"], "purged": rep["purged"],
                "vacuumed": rep.get("vacuumed", False), "preview": rep["preview"]}
    except Exception as e:
        logger.error("[facade.purge_forgotten] 失败: %s", e)
        return {"ok": False, "action": "error", "purged": 0, "error": str(e)}

def consolidate(*, tag: str = "consolidation") -> dict:
    """维护/异步入口：运行 Dream 生产化 Consolidation（B0）。

    委托 ConsolidationEngine（Proposal→Validator→Apply/Quarantine + 快照）。
    返回处理报告。
    """
    report = {"ok": False, "action": "noop", "detail": {}}
    try:
        from nexsandglass.runtime.consolidation import ConsolidationEngine
        from nexsandglass.engram import bridge
        from nexsandglass.engram.types import Memory

        # 从 engram store 加载最近记忆（用 recent，避免 load_memories 不存在）
        try:
            rows = bridge.recent(n=200)
        except Exception:
            rows = []
        memories = []
        for r in rows:
            if isinstance(r, dict) and r.get("content"):
                try:
                    memories.append(Memory(
                        memory_id=f"engram:{r.get('ts', '')}",
                        type=r.get("type", "semantic"),
                        content=r["content"],
                        created_at=r.get("ts", ""),
                    ))
                except Exception:
                    continue
        if not memories:
            report["ok"] = True
            report["detail"] = {"reason": "no memories to consolidate"}
            return report

        eng = ConsolidationEngine()
        result = eng.run(memories, tag=tag)
        report.update({
            "ok": True,
            "action": "consolidate",
            "detail": {
                "proposals": result["proposals_total"],
                "applied": result["applied"],
                "quarantined": result["quarantined"],
                "actions": result["applied_actions"],
                "snapshot": result["snapshot"],
            },
        })
    except Exception as e:
        logger.warning("[facade.consolidate] 失败: %s", e)
        report["detail"] = {"error": str(e)}
    return report


def _legacy_observe(event: str, mem_type: str | None = None, source: str | None = None) -> ObserveReport:
    """NYX_RUNTIME=0 时的旧路径（deprecated）：直连 sandglass + engram。"""
    try:
        from nexsandglass.core import sandglass_log
        from nexsandglass.engram import bridge
        sandglass_log.log_message(event, sender=source or "agent")
        ingested = bridge.ingest(event)
        mtype = mem_type or bridge.classify_memory_type(event)
        return ObserveReport(ok=True, memory_type=mtype, lifecycle_state="observed",
                             message="legacy-observe")
    except Exception as e:
        logger.warning("[B0] 旧路径 observe 失败: %s", e)
        return ObserveReport(ok=False, message=str(e))
