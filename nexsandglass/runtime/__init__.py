"""
NexSandglass Runtime —— 唯一对外 API（B0 契约冻结）。

外部（Hermes / MCP / 未来认知内核）只 import 本包：
    from nexsandglass.runtime import observe, recall, feedback, forget, restore, consolidate

数据流：
    Conversation → observe(Formation) → Store
    Query → recall(Intent→Planner→Rank→Bundle→Context) → Agent
    Cron → consolidate(Dream Proposal pipeline)

类型：MemoryContext / ObserveReport / MemoryObject / MemoryBundle
"""
from __future__ import annotations

# ── 运行时开关（NYX_RUNTIME=0 紧急回滚旧路径，默认开启）──
import os as _os
NYX_RUNTIME = _os.environ.get("NYX_RUNTIME", "1") == "1"

from .facade import (  # noqa: E402
    observe,
    recall,
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

__all__ = [
    "observe", "recall", "feedback", "forget", "restore", "purge_forgotten",
    "consolidate",
    "get_orchestrator",
    "MemoryContext", "ObserveReport", "MemoryBundle", "MemoryObject",
    "NYX_RUNTIME",
]
