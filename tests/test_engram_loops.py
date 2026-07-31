"""
engram 演化闭环测试 — Nyx 记忆自我演化

覆盖四个闭环：
  1. Thread ↔ Fact Store（事实→图谱，图谱→验证/冲突）
  2. Dream ↔ Engram（重分类 / 合并 / 关系发现）
  3. Persona ↔ Context（画像加权 / 变更重建）
  4. Recall ↔ Writer（召回反馈 / 老化降权 / 归档候选）
  5. evolve 协调器（run_evolution_pass / fact_pass）
"""

import sys
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    _THIS_DIR,
    os.path.dirname(_THIS_DIR),
    os.path.dirname(os.path.dirname(_THIS_DIR)),
):
    if os.path.isdir(os.path.join(_candidate, "nexsandglass")):
        sys.path.insert(0, _candidate)
        break
if os.path.isdir(os.path.join(_THIS_DIR, "engram")):
    sys.path.insert(0, _THIS_DIR)

try:
    from engram.types import Memory, MemoryType
    from engram.loops.fact_thread import (
        extract_triples,
        fact_to_thread,
        thread_validate_fact,
    )
    from engram.loops.dream_engram import (
        dream_reclassify,
        dream_consolidate,
        dream_relation_discovery,
    )
    from engram.loops.persona_ctx import (
        persona_weight_context,
        persona_trigger_rebuild,
    )
    from engram.loops.recall_writer import (
        recall_feedback,
        age_and_demote,
    )
    from engram.evolve import run_evolution_pass, fact_pass, recall_pass
except ModuleNotFoundError:
    from nexsandglass.engram.types import Memory, MemoryType
    from nexsandglass.engram.loops.fact_thread import (
        extract_triples,
        fact_to_thread,
        thread_validate_fact,
    )
    from nexsandglass.engram.loops.dream_engram import (
        dream_reclassify,
        dream_consolidate,
        dream_relation_discovery,
    )
    from nexsandglass.engram.loops.persona_ctx import (
        persona_weight_context,
        persona_trigger_rebuild,
    )
    from nexsandglass.engram.loops.recall_writer import (
        recall_feedback,
        age_and_demote,
    )
    from nexsandglass.engram.evolve import run_evolution_pass, fact_pass, recall_pass


def _mem(mid, mtype, content, arousal=0.0, weight=1.0, created="2026-07-01T00:00:00+00:00", accessed=None, count=0):
    return Memory(
        memory_id=mid,
        type=mtype,
        content=content,
        arousal=arousal,
        decay_weight=weight,
        created_at=created,
        last_accessed=accessed or created,
        access_count=count,
    )


# ── Loop 1: Thread ↔ Fact Store ───────────────────────────────

def test_extract_triples():
    triples = extract_triples("用户邮箱是 enfys@hvh.expert")
    assert len(triples) >= 1, f"应提取到三元组, got {triples}"
    subj, rel, obj = triples[0]
    assert subj == "user" or subj == "用户"
    assert obj == "enfys@hvh.expert"


def test_fact_to_thread_stores():
    stored = []
    queried = []
    report = fact_to_thread(
        "用户住在海牙",
        thread_store=lambda s, r, o: stored.append((s, r, o)),
        thread_query=lambda e, r, l: queried,
    )
    assert len(report.triples_stored) >= 1
    assert len(report.conflicts) == 0


def test_fact_to_thread_conflict():
    stored = []
    # 图谱已有: (user, 邮箱, old@x.com)
    def thread_query(e, r, l):
        if e in ("user", "我") and r == "邮箱":
            return [{"object": "old@x.com"}]
        return []

    report = fact_to_thread(
        "用户邮箱是 new@y.com",
        thread_store=lambda s, r, o: stored.append((s, r, o)),
        thread_query=thread_query,
    )
    assert len(report.conflicts) >= 1, "应检测到邮箱冲突"
    assert report.conflicts[0]["existing_object"] == "old@x.com"


def test_thread_validate_fact():
    def thread_query(e, r, l):
        if r == "邮箱":
            return [{"object": "enfys@hvh.expert"}]
        return []

    report = thread_validate_fact("用户邮箱是 enfys@hvh.expert", thread_query)
    verdicts = [v["verdict"] for v in report.validations]
    assert "confirm" in verdicts, f"应确认已有事实, got {verdicts}"


# ── Loop 2: Dream ↔ Engram ────────────────────────────────────

def test_dream_reclassify():
    mems = [
        _mem("a1", "episodic", "2026-07-01 用户去了海牙市政厅", created="2026-07-01T00:00:00+00:00"),
        _mem("a2", "episodic", "2026-07-08 用户去了海牙市政厅", created="2026-07-08T00:00:00+00:00"),
        _mem("a3", "episodic", "2026-07-15 用户去了海牙市政厅", created="2026-07-15T00:00:00+00:00"),
    ]
    report = dream_reclassify(mems, min_occurrence=3)
    assert len(report.reclassified) == 1
    assert report.reclassified[0]["new_type"] == "semantic"
    assert report.reclassified[0]["occurrence"] == 3


def test_dream_consolidate():
    mems = [
        _mem("a1", "episodic", "同样的经历内容"),
        _mem("a2", "episodic", "同样的经历内容"),
        _mem("s1", "semantic", "不同的事实"),
    ]
    report = dream_consolidate(mems, threshold=0.92)
    assert len(report.consolidated_groups) == 1
    assert len(report.consolidated_groups[0]) == 2


def test_dream_relation_discovery():
    mems = [_mem("s1", "semantic", "用户住在海牙")]
    report = dream_relation_discovery(mems)
    assert len(report.relations_found) >= 1


# ── Loop 3: Persona ↔ Context ─────────────────────────────────

def test_persona_weight_context():
    mems = [_mem("s1", "semantic", "用户邮箱是 enfys@hvh.expert", weight=0.5)]
    updated, report = persona_weight_context(mems, ["邮箱 enfys@hvh.expert"])
    assert len(report.boosted_ids) >= 1
    assert updated[0].decay_weight > 0.5, "画像确认事实应加权"


def test_persona_trigger_rebuild():
    mems = [_mem("s1", "semantic", "用户邮箱是 old@x.com")]
    report = persona_trigger_rebuild(mems, ["邮箱 new@y.com"], changed_fields=["邮箱"])
    assert len(report.rebuild_candidates) == 1
    assert report.rebuild_candidates[0]["changed_field"] == "邮箱"


# ── Loop 4: Recall ↔ Writer ───────────────────────────────────

def test_recall_feedback_boosts():
    mems = [_mem("a1", "episodic", "某事件", weight=0.5, count=2)]
    updated, report = recall_feedback(mems, ["a1"])
    assert len(report.boosted_ids) == 1
    assert updated[0].decay_weight > 0.5
    assert updated[0].access_count == 3


def test_age_and_demote_idle():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    old_access = (now - timedelta(days=60)).isoformat()
    mems = [
        _mem("e1", "episodic", "旧事件", weight=0.8, accessed=old_access, count=1),
        _mem("s1", "semantic", "稳定事实", weight=0.8, accessed=old_access, count=0),
    ]
    updated, report = age_and_demote(mems, now=now, demote_after_days=30)
    assert "e1" in report.demoted_ids, "长期未访问的 episodic 应降权"
    assert "s1" not in report.demoted_ids, "semantic 不应降权"


def test_age_and_demote_archive():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=100)).isoformat()
    mems = [
        _mem("e1", "episodic", "从未召回的旧事", weight=0.02,
             created=old, accessed=old, count=0),
    ]
    updated, report = age_and_demote(
        mems, now=now, archive_below=0.05, archive_after_days=90
    )
    assert "e1" in report.archive_candidates, "应标记归档候选"


# ── evolve 协调器 ─────────────────────────────────────────────

def test_run_evolution_pass():
    mems = [
        _mem("a1", "episodic", "2026-07-01 用户去了海牙市政厅"),
        _mem("a2", "episodic", "2026-07-08 用户去了海牙市政厅"),
        _mem("a3", "episodic", "2026-07-15 用户去了海牙市政厅"),
        _mem("s1", "semantic", "用户住在海牙", weight=0.5),
    ]
    stored = []
    evolved, report = run_evolution_pass(
        mems,
        persona_entries=["用户住在海牙"],
        thread_store=lambda s, r, o: stored.append((s, r, o)),
        thread_query=lambda e, r, l: [],
    )
    assert report.summary["reclassified"] == 1
    assert report.summary["relations_found"] >= 1
    assert report.summary["persona_boosted"] >= 1
    assert len(evolved) == len(mems)


def test_fact_pass():
    stored = []
    def thread_query(e, r, l):
        return []

    report = fact_pass(
        "用户住在海牙",
        thread_store=lambda s, r, o: stored.append((s, r, o)),
        thread_query=thread_query,
    )
    assert len(report.triples_stored) >= 1


def test_recall_pass():
    mems = [_mem("a1", "episodic", "某事件", weight=0.5, count=0)]
    updated, report = recall_pass(mems, ["a1"])
    assert len(report.boosted_ids) == 1
    assert updated[0].access_count == 1


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
