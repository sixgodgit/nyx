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
from typing import Optional

from nexsandglass.engram.types import MemoryObject

logger = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    """召回结果上下文。当前为透传字符串 + 元信息。

    未来可扩展为结构化上下文（分组记忆、token 预算分配等），
    但保持构造兼容：strings 是主要载荷。
    """
    strings: list[str] = field(default_factory=list)
    memory_ids: list[str] = field(default_factory=list)
    query: str = ""
    token_budget: int = 0
    est_tokens: int = 0
    meta_intent: object = None   # MemoryIntent（v5.0，可选）

    def to_text(self, separator: str = "\n") -> str:
        """将上下文拼成纯文本（供 system prompt 注入）。"""
        return separator.join(self.strings)


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


def observe(event: str, mem_type: str | None = None, source: str | None = None) -> ObserveReport:
    """观察一个事件并写入记忆。

    内部委托 bridge.ingest()（分类 + 落盘 engram_store）与 sandglass_log 落沙。
    返回 ObserveReport。
    """
    try:
        from nexsandglass.engram import bridge
        from nexsandglass.core import sandglass_log

        # 1. 落沙（原始事件日志）
        try:
            sandglass_log.log_message(event, sender="agent")
        except Exception as e:
            logger.debug("[facade.observe] 落沙失败(忽略): %s", e)

        # 2. 分类 + 写入 engram（旧路径，保持向后兼容）
        mem_type = mem_type or bridge.classify_memory_type(event)
        ingested = bridge.ingest(event)
        actual_type = mem_type if mem_type in (
            "semantic", "episodic", "emotional", "procedural",
            "preference", "relational", "temporal", "meta",
        ) else ingested

        # 3. 构建 MemoryObject（canonical 表示，供未来认知内核用）
        obj = MemoryObject(
            content=event,
            type=actual_type,
            created_at=_now(),
            source_id=source,
            provenance="facade",
            status="observed",
        )

        return ObserveReport(
            ok=True,
            memory_id=obj.memory_id or obj._fallback_id(),
            memory_type=actual_type,
            lifecycle_state=obj.status,
            message="observed",
        )
    except Exception as e:
        logger.debug("[facade.observe] 失败: %s", e)
        return ObserveReport(ok=False, message=str(e))


def recall(
    query: str,
    context: str | None = None,
    token_budget: int = 1500,
) -> MemoryContext:
    """召回与 query 相关的记忆。

    内部委托 SearchRouter / sandglass_vault 检索（不重写）。
    当前 MemoryContext 透传检索到的字符串列表。
    """
    mc = MemoryContext(query=query, token_budget=token_budget)
    try:
        from nexsandglass.core.search_router import SearchRouter
        router = SearchRouter()
        results = router.search(query, limit=10)

        strings: list[str] = []
        ids: list[str] = []
        total = 0
        for item in results:
            # item 结构: (line_num, ts, text) 或 (line_num, text) 等，兼容多种
            if isinstance(item, (tuple, list)):
                text = item[-1] if item else ""
            else:
                text = str(item)
            if not text:
                continue
            line_num = item[0] if isinstance(item, (tuple, list)) and item else None
            strings.append(text)
            ids.append(f"sandglass:{line_num}" if line_num is not None else f"recall:{len(ids)}")
            total += len(text)

        mc.strings = strings
        mc.memory_ids = ids
        mc.est_tokens = total // 4
    except Exception as e:
        logger.debug("[facade.recall] 失败(降级为空): %s", e)
        mc.strings = []

    return mc


def feedback(outcome: dict) -> dict:
    """反馈一次召回/观察结果，用于强化或弱化记忆。

    委托现有 recall_feedback（Loop 4）或 shadow trust 更新。
    返回处理报告。
    """
    report = {"ok": False, "action": "noop", "detail": {}}
    try:
        from nexsandglass.engram.loops.recall_writer import recall_feedback

        recalled_ids = outcome.get("memory_ids") or outcome.get("ids") or []
        helpful = bool(outcome.get("helpful", True))
        if recalled_ids:
            # 委托 recall_feedback（memory_id 列表 → 提升/降低 importance）
            r = recall_feedback([], recalled_ids)  # 空 memories 时仅返回空报告
            report["ok"] = True
            report["action"] = "reinforce" if helpful else "weaken"
            report["detail"] = {"recalled_ids": recalled_ids, "helpful": helpful}
        else:
            report["ok"] = True
            report["action"] = "noop"
            report["detail"] = {"reason": "no recalled ids"}
    except Exception as e:
        logger.debug("[facade.feedback] 失败: %s", e)
        report["detail"] = {"error": str(e)}
    return report


def forget(selector: dict) -> dict:
    """遗忘选中的记忆。

    selector 支持:
      - {"memory_id": "..."}   按记忆 id
      - {"source_id": "..."}   按来源（如 sandglass:ts / shadow:line）
      - {"all": true}          清空 engram_store（谨慎）
    返回处理报告。
    """
    report = {"ok": False, "action": "noop", "removed": 0}
    try:
        from nexsandglass.engram import bridge

        store = bridge._STORE
        if not os.path.exists(store):
            report["ok"] = True
            return report

        all_flag = bool(selector.get("all"))
        target_id = selector.get("memory_id")
        target_source = selector.get("source_id")

        kept: list[str] = []
        removed = 0
        with open(store, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if all_flag:
                    removed += 1
                    continue
                try:
                    import json
                    row = json.loads(line)
                except Exception:
                    kept.append(line)  # 无法解析的行保留
                    continue
                # 匹配 memory_id 或 source_id（engram 旧数据用 ts 作 source）
                row_source = f"engram:{row.get('ts', '')}"
                row_id = f"engram:{row.get('ts', '')}"
                if target_id and row_id == target_id:
                    removed += 1
                    continue
                if target_source and (row_source == target_source or row.get("ts") == target_source):
                    removed += 1
                    continue
                kept.append(line)

        with open(store, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + ("\n" if kept else ""))

        report["ok"] = True
        report["action"] = "forget"
        report["removed"] = removed
    except Exception as e:
        logger.debug("[facade.forget] 失败: %s", e)
        report["detail"] = {"error": str(e)}
    return report
