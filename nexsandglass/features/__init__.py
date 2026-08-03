"""Feature modules: vault, think, shadow sand, pulse, etc."""

from .decision_particles import feed_all, feed_persona, log, ratio, read
from .multi_analysis import analyze, analyze_cli
from .nightwatch import night_watch
from .pulse import echo, pulse
from .sandglass_think import (
    comprehensive_offset,
    decision_snapshot,
    decision_stability,
    distill,
    entropy_chart,
    full_sanity,
    memory_migrate,
    persona_maintain,
    search_filter,
    search_semantic,
    search_with_stage_label,
    session_context,
    stage_brief,
    weave_links,
)
from .sandglass_vault import (
    count,
    idx_search,
    recent,
    rebuild_index,
    sandglass_export,
    sandglass_import,
    search,
    timeline,
)
from .shadow_sand import (
    shadow_boost,
    shadow_feedback,
    shadow_index,
    shadow_retrieval_bump,
    shadow_search,
)
from .soul_diff import export_soul, merge_soul
from .weavethread import (
    wthread_add,
    wthread_extract,
    wthread_extract_llm,
    wthread_extract_with_source,
    wthread_graph,
    wthread_query,
    wthread_stats,
    wthread_store,
    wthread_to_weave,
    wthread_weave,
)

__all__ = [
    # vault
    "search",
    "recent",
    "count",
    "rebuild_index",
    "idx_search",
    "timeline",
    "sandglass_import",
    "sandglass_export",
    # think / L3 facade
    "comprehensive_offset",
    "search_semantic",
    "search_filter",
    "distill",
    "session_context",
    "persona_maintain",
    "entropy_chart",
    "memory_migrate",
    "decision_stability",
    "decision_snapshot",
    "stage_brief",
    "full_sanity",
    "weave_links",
    "search_with_stage_label",
    # shadow sand
    "shadow_search",
    "shadow_boost",
    "shadow_index",
    "shadow_feedback",
    "shadow_retrieval_bump",
    # weave thread
    "wthread_extract",
    "wthread_extract_with_source",
    "wthread_extract_llm",
    "wthread_store",
    "wthread_query",
    "wthread_graph",
    "wthread_stats",
    "wthread_weave",
    "wthread_add",
    "wthread_to_weave",
    # pulse / nightwatch
    "pulse",
    "echo",
    "night_watch",
    # decision particles
    "log",
    "read",
    "ratio",
    "feed_all",
    "feed_persona",
    # multi analysis
    "analyze",
    "analyze_cli",
    # soul diff
    "export_soul",
    "merge_soul",
]
