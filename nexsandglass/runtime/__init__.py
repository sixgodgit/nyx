"""
NexSandglass Runtime —— 唯一对外 API（B0 契约冻结）。

外部（Hermes / MCP / 未来认知内核）只 import 本包：
    from nexsandglass.runtime import observe, recall, feedback, forget, restore, consolidate

数据流：
    Conversation → observe(Formation) → Store
    Query → recall(Intent→Planner→Rank→Bundle→Context) → Agent
    Cron → consolidate(Dream Proposal pipeline)

类型：MemoryContext / ObserveReport / MemoryObject / MemoryBundle
结构化增量接口：RecallRequest → recall_request() → ContextPacket / EvidenceRef。
旧 recall(str) 保持兼容；见 docs/recall-contracts.md。
"""
from __future__ import annotations

# ── 运行时开关（NYX_RUNTIME=0 紧急回滚旧路径，默认开启）──
import os as _os
NYX_RUNTIME = _os.environ.get("NYX_RUNTIME", "1") == "1"

from .facade import (  # noqa: E402
    observe,
    recall,
    recall_request,
    feedback,
    forget,
    restore,
    purge_forgotten,
    consolidate,
    MemoryContext,
    ObserveReport,
)
from .orchestrator import get_orchestrator  # noqa: E402
from .bundle import MemoryBundle  # noqa: E402
from nexsandglass.engram.types import MemoryObject  # noqa: E402
from .contracts import RecallRequest, EvidenceRef, ContextPacket, LanguageContext  # noqa: E402
from nexsandglass.core.store_context import StoreContext, scope as store_scope  # noqa: E402
from .composition import (  # noqa: E402
    ContextItem, ContextSection, CompositionResult, compose_context,
)

__all__ = [
    "observe", "recall", "recall_request", "feedback", "forget", "restore", "purge_forgotten",
    "consolidate",
    "get_orchestrator",
    "MemoryContext", "ObserveReport", "MemoryBundle", "MemoryObject",
    "RecallRequest", "EvidenceRef", "ContextPacket", "LanguageContext", "StoreContext", "store_scope",
    "ContextItem", "ContextSection", "CompositionResult", "compose_context",
    "NYX_RUNTIME",
]
