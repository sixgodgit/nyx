"""
engram 融合层测试 — Nyx × EngramTide

覆盖：
  1. 记忆类型定义
  2. 衰减引擎（Ebbinghaus 曲线、DECAY_FLOOR、时钟回拨保护）
  3. 差异化写入（semantic 覆盖 / emotional 强化 / procedural 去重 / episodic 直插）
  4. 浮现机制
  5. 逐轮激活
  6. Constitutional 上下文组装
"""

import sys
import os
import math
from datetime import datetime, timedelta, timezone

# 兼容两种布局：
#   仓库场景: from nexsandglass.engram.xxx import ...
#   技能场景: from engram.xxx import ...（scripts/engram 直接可用）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    _THIS_DIR,                                   # scripts/ 直接运行
    os.path.dirname(_THIS_DIR),                  # 仓库根目录
    os.path.dirname(os.path.dirname(_THIS_DIR)), # 嵌套目录
):
    if os.path.isdir(os.path.join(_candidate, "nexsandglass")):
        sys.path.insert(0, _candidate)
        break
if os.path.isdir(os.path.join(_THIS_DIR, "engram")):
    sys.path.insert(0, _THIS_DIR)

try:
    # 技能场景（无 nexsandglass 命名空间）
    from engram.types import (
        Memory,
        MemoryType,
        MEMORY_TYPES,
        STATIC_TYPES,
        DECAY_TYPES,
        WriteAction,
    )
    from engram.decay import (
        compute_decay_multiplier,
        apply_decay,
        get_surfaced_memories,
        compute_activations,
        DECAY_FLOOR,
    )
    from engram.writer import (
        write_memory_classified,
        consolidate_similar,
        SEMANTIC_OVERRIDE_THRESHOLD,
        PROCEDURAL_DEDUP_THRESHOLD,
    )
    from engram.context import (
        build_constitutional_context,
        render_constitution,
    )
except ModuleNotFoundError:
    # 仓库场景
    from nexsandglass.engram.types import (
        Memory,
        MemoryType,
        MEMORY_TYPES,
        STATIC_TYPES,
        DECAY_TYPES,
        WriteAction,
    )
    from nexsandglass.engram.decay import (
        compute_decay_multiplier,
        apply_decay,
        get_surfaced_memories,
        compute_activations,
        DECAY_FLOOR,
    )
    from nexsandglass.engram.writer import (
        write_memory_classified,
        consolidate_similar,
        SEMANTIC_OVERRIDE_THRESHOLD,
        PROCEDURAL_DEDUP_THRESHOLD,
    )
    from nexsandglass.engram.context import (
        build_constitutional_context,
        render_constitution,
    )


def _mem(mid, mtype, content, arousal=0.0, weight=1.0, embedding=None, unresolved=False):
    return Memory(
        memory_id=mid,
        type=mtype,
        content=content,
        arousal=arousal,
        decay_weight=weight,
        embedding=embedding,
        unresolved=unresolved,
    )


def test_memory_types():
    assert MemoryType.SEMANTIC.value == "semantic"
    assert STATIC_TYPES == frozenset({"semantic", "procedural"})
    assert DECAY_TYPES == frozenset({"episodic", "emotional"})
    assert len(MEMORY_TYPES) == 4


def test_decay_static_types_never_decay():
    for mtype in ("semantic", "procedural"):
        m = compute_decay_multiplier(mtype, 0.5, 0, 24 * 30)  # 30 天
        assert m == 1.0, f"{mtype} should not decay, got {m}"


def test_decay_episodic_decays():
    m = compute_decay_multiplier("episodic", 0.0, 0, 24 * 30)  # 30 天
    assert 0 < m < 1.0, f"episodic should decay, got {m}"
    # 与 Ebbinghaus 公式一致：exp(-rate × hours/(24 × stability))
    expected = math.exp(-0.05 * 24 * 30 / (24 * 1.0))
    assert abs(m - expected) < 1e-9


def test_decay_emotional_slower_with_arousal():
    low = compute_decay_multiplier("emotional", 0.1, 0, 24 * 30)
    high = compute_decay_multiplier("emotional", 0.9, 0, 24 * 30)
    assert high > low, "高唤醒情绪记忆衰减应更慢"


def test_decay_access_count_stabilizes():
    fresh = compute_decay_multiplier("episodic", 0.0, 0, 24 * 30)
    accessed = compute_decay_multiplier("episodic", 0.0, 10, 24 * 30)
    assert accessed > fresh, "访问越多应越稳定（衰减越少）"


def test_decay_clock_rollback_protection():
    assert compute_decay_multiplier("episodic", 0.0, 0, -5) == 1.0
    assert compute_decay_multiplier("episodic", 0.0, 0, 0) == 1.0


def test_apply_decay_floor():
    mems = [
        _mem("e1", "episodic", "事件1", arousal=0.0),
        _mem("s1", "semantic", "事实1"),
    ]
    updated, report = apply_decay(mems, hours_elapsed=24 * 365 * 10)  # 10 年
    assert updated[0].decay_weight >= DECAY_FLOOR, "衰减不应低于下限"
    assert updated[1].decay_weight == 1.0, "semantic 不应衰减"
    assert report.processed == 1


def test_surfaced_memories_prioritizes_unresolved():
    now = datetime.now(timezone.utc)
    mems = [
        _mem("a", "episodic", "旧事", weight=0.2, unresolved=True),
        _mem("b", "episodic", "新事", weight=0.8, unresolved=False),
    ]
    surfaced = get_surfaced_memories(mems, now=now, max_surfaced=2)
    assert surfaced[0].memory_id == "a", "unresolved 记忆应优先浮现（R3 优先于 R4）"


def test_surfaced_procedural_always():
    now = datetime.now(timezone.utc)
    mems = [
        _mem("p1", "procedural", "铁律：先查再答"),
        _mem("e1", "episodic", "新事件", weight=0.9),
    ]
    surfaced = get_surfaced_memories(mems, now=now, max_surfaced=1)
    assert surfaced[0].memory_id == "p1", "procedural 规则应永远优先浮现（R1）"


def test_surfaced_high_arousal_emotional():
    now = datetime.now(timezone.utc)
    mems = [
        _mem("m1", "emotional", "高唤醒记忆", arousal=0.9, weight=0.5),
        _mem("e1", "episodic", "近期事件", weight=0.9),
    ]
    surfaced = get_surfaced_memories(mems, now=now, max_surfaced=1)
    assert surfaced[0].memory_id == "m1", "高唤醒 emotional 应优先于近期 episodic（R2）"


def test_semantic_override():
    existing = [_mem("s1", "semantic", "用户邮箱是 a@b.com")]
    action, report, _ = write_memory_classified(
        "用户邮箱是 a@b.com", "semantic", existing
    )
    # 字符重叠极高 → 应触发覆盖
    assert action in (WriteAction.OVERRIDE, WriteAction.INSERT)


def test_procedural_dedup():
    existing = [_mem("p1", "procedural", "涉及个人数据必须先检索再回答")]
    action, report, target = write_memory_classified(
        "涉及个人数据必须先检索再回答", "procedural", existing
    )
    assert action == WriteAction.DEDUP
    assert report.deduped_id == "p1"


def test_emotional_reinforce():
    existing = [_mem("e1", "emotional", "用户不喜欢被说教", arousal=0.7)]
    action, report, target = write_memory_classified(
        "用户不喜欢被说教", "emotional", existing
    )
    assert action == WriteAction.REINFORCE
    assert report.reinforced_id == "e1"
    assert report.reinforced_boost > 0


def test_episodic_insert():
    existing = []
    action, report, _ = write_memory_classified(
        "2026-07-31 用户问了 GPT-5.6 价格", "episodic", existing
    )
    assert action == WriteAction.INSERT


def test_activations_boost():
    emb = [1.0, 0.0, 0.0]
    mems = [_mem("a", "episodic", "事件", embedding=emb, weight=0.5)]
    activations, report = compute_activations(emb, mems, max_activations=10)
    assert len(activations) >= 1
    new_w, mid, old_w = activations[0]
    assert new_w > old_w, "激活应提升权重"
    assert new_w <= 1.0, "权重封顶 1.0"


def test_constitutional_context():
    mems = [
        _mem("s1", "semantic", "用户住在海牙"),
        _mem("p1", "procedural", "涉及个人数据必须先检索再回答"),
        _mem("e1", "episodic", "昨天去了市政厅"),
        _mem("m1", "emotional", "用户对 Apple 账户安全很紧张", arousal=0.8),
    ]
    ctx = build_constitutional_context([(m, 0.9) for m in mems], max_tokens=2000)
    assert ctx.semantic_memories != "无"
    assert ctx.procedural_memories != "无"
    assert ctx.episodic_memories != "无"
    assert ctx.emotional_memories != "无"
    assert len(ctx.included_memory_ids) == 4

    rendered = render_constitution(ctx)
    assert "交互宪法" in rendered
    assert "严禁使用" in rendered
    assert "海牙" in rendered


def test_consolidate_similar():
    mems = [
        _mem("a1", "episodic", "同样的内容"),
        _mem("a2", "episodic", "同样的内容"),
        _mem("b1", "semantic", "不同内容"),
    ]
    groups = consolidate_similar(mems, threshold=0.92)
    assert len(groups) == 1
    assert len(groups[0]) == 2


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"✅ {fn.__name__}")
            passed += 1
        except Exception:
            print(f"❌ {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
