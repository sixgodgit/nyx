"""L3 Intelligence layer: search, persona, offset, weave, scene, emotion."""

from .emotion_l3 import (
    entropy_ghost,
    entropy_mirror,
    entropy_reminder,
    glass_reminder,
    memo_mode,
)
from .l3_persona import persona_project
from .l3_persona_verify import persona_diff, persona_trace, persona_verify
from .l3_search_core import (
    composite_rerank,
    sand_density,
    sentiment_rerank,
    simhash,
    simhash_search,
)
from .l3_tasks import task_check_trigger, task_defer, task_done, task_pending
from .offset_l3 import (
    comprehensive_offset,
    cross_stage_offset,
    offset_chart,
    offset_check,
    offset_guide,
    shadow_chart,
    stage_mark,
    stage_marks,
)
from .persona_l3 import (
    persona_build,
    persona_canvas,
    persona_freshness,
    persona_update,
    sand_since_update,
    stage_canvas,
    stage_list,
    stage_similarity,
)
from .scene_l3 import (
    novel_scene_detect,
    scene_add,
    scene_current,
    scene_dominance,
    scene_guess,
    scene_history,
    scene_mode,
    scene_remove,
    scene_stage_matrix,
    scene_sync,
    stage_switch_prediction,
)
from .weave_l3 import weave_chain, weave_contradiction, weave_graph, weave_insight

__all__ = [
    # search core
    "simhash",
    "simhash_search",
    "sand_density",
    "composite_rerank",
    "sentiment_rerank",
    # persona
    "persona_build",
    "persona_update",
    "persona_canvas",
    "persona_freshness",
    "stage_list",
    "stage_canvas",
    "stage_similarity",
    "sand_since_update",
    "persona_project",
    # persona verify
    "persona_trace",
    "persona_verify",
    "persona_diff",
    # offset
    "comprehensive_offset",
    "offset_check",
    "offset_guide",
    "cross_stage_offset",
    "offset_chart",
    "shadow_chart",
    "stage_mark",
    "stage_marks",
    # weave
    "weave_insight",
    "weave_contradiction",
    "weave_chain",
    "weave_graph",
    # scene
    "scene_mode",
    "scene_add",
    "scene_current",
    "scene_sync",
    "scene_history",
    "scene_dominance",
    "stage_switch_prediction",
    "scene_stage_matrix",
    "novel_scene_detect",
    "scene_remove",
    "scene_guess",
    # emotion
    "entropy_mirror",
    "entropy_ghost",
    "glass_reminder",
    "entropy_reminder",
    "memo_mode",
    # tasks
    "task_defer",
    "task_pending",
    "task_done",
    "task_check_trigger",
]
