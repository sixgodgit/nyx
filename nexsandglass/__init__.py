"""NexSandglass — 记忆感知系统

夜神 Nyx：跨会话记忆、知识图谱、模糊感知（Déjà Vu）
"""

from .core.sandglass_paths import __version__
from .utils import health_summary, tick

from .core import (
    NexSandglassProvider,
    SearchRouter,
    detect,
    log_message,
    register_provider,
    validate,
)
from .l3 import (
    entropy_ghost,
    glass_reminder,
    offset_check,
    persona_build,
    persona_project,
    persona_update,
    simhash,
    stage_list,
    task_pending,
    weave_contradiction,
    weave_graph,
)
from .features import (
    count,
    comprehensive_offset,
    distill,
    entropy_chart,
    export_soul,
    full_sanity,
    log,
    memory_migrate,
    merge_soul,
    night_watch,
    pulse,
    ratio,
    recent,
    search,
    search_semantic,
    session_context,
    shadow_search,
    timeline,
    wthread_add,
    wthread_extract,
    wthread_graph,
    wthread_query,
    wthread_stats,
    wthread_store,
    wthread_weave,
)

__author__ = "sixgod"

__all__ = [
    # core
    "NexSandglassProvider",
    "register_provider",
    "SearchRouter",
    "log_message",
    "validate",
    # storage
    "search",
    "recent",
    "count",
    "timeline",
    "search_semantic",
    "shadow_search",
    # thread graph
    "wthread_extract",
    "wthread_store",
    "wthread_query",
    "wthread_stats",
    "wthread_weave",
    "wthread_add",
    "wthread_graph",
    # persona / offset / emotion
    "comprehensive_offset",
    "offset_check",
    "persona_build",
    "persona_update",
    "persona_project",
    "stage_list",
    "glass_reminder",
    "entropy_ghost",
    "weave_contradiction",
    "weave_graph",
    "simhash",
    # task / think
    "task_pending",
    "distill",
    "session_context",
    "entropy_chart",
    "full_sanity",
    # decision particles
    "log",
    "ratio",
    "detect",
    # migrate / soul
    "memory_migrate",
    "export_soul",
    "merge_soul",
    # runtime
    "pulse",
    "night_watch",
    "tick",
    "health_summary",
]
