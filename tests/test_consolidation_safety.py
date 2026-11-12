"""Consolidation copies obey forgetting and report actual mutations."""
import json
from pathlib import Path

import pytest

from tests.test_hermes_provider import env  # noqa: F401
from nexsandglass.core import memid, erasure, consolidation_snapshots
from nexsandglass.engram import bridge
from nexsandglass.engram.types import Memory, MemoryObject
from nexsandglass.runtime.consolidation import ConsolidationEngine, _snapshot, restore_snapshot


def _memory(mid, text, **kwargs):
    return Memory(memory_id=mid, type="episodic", content=text, **kwargs)


def test_snapshot_names_are_unique_and_tags_cannot_escape(env):  # noqa: F811
    first = _snapshot([_memory("first", "one")], "../same/tag")
    second = _snapshot([_memory("second", "two")], "../same/tag")
    assert first != second
    assert Path(first).parent == Path(consolidation_snapshots.directory())
    assert restore_snapshot(first)[0].content == "one"
    assert restore_snapshot(second)[0].content == "two"


@pytest.mark.parametrize("needle", ['private "quoted" marker', "private\\backslash", "private\nnewline"])
def test_snapshot_verification_searches_decoded_strings(env, needle):  # noqa: F811
    path = _snapshot([_memory("unlinked-derived", needle)], "escaped")
    assert json.loads(Path(path).read_text())[0]["content"] == needle
    check = erasure.verify_erasure([needle])
    assert not check["fully_purged"]
    assert "snapshots" in check["found_in"]


def test_explicit_snapshot_directory_override_is_shared_by_writer_and_scanner(env, monkeypatch, tmp_path):  # noqa: F811
    from nexsandglass.runtime import consolidation
    destination = tmp_path / "custom-snapshots"
    monkeypatch.setattr(consolidation, "_SNAPSHOT_DIR", str(destination))
    path = _snapshot([_memory("unlinked", "override-needle")], "override")
    assert Path(path).parent == destination
    assert not erasure.verify_erasure(["override-needle"])["fully_purged"]


@pytest.mark.parametrize("mode", ["purge", "quarantine"])
def test_forget_removes_snapshot_copy_and_preserves_unrelated(env, mode):  # noqa: F811
    marker = "snapshot-private-needle-814"
    mid = memid.allocate(marker, sender="user")
    other = memid.allocate("unrelated-kept-needle-219", sender="user")
    path = _snapshot([_memory(mid, marker), _memory(other, "unrelated-kept-needle-219")], "forget")
    report = erasure.forget({"mem_id": mid}, apply=True, mode=mode)
    assert report["ok"], report
    assert report["snapshots"]["removed"] == 1
    assert marker not in Path(path).read_text()
    assert restore_snapshot(path)[0].memory_id == other
    check = erasure.verify_erasure([marker])
    assert check["clean"], check
    assert check["fully_purged"] == (mode == "purge")
    if mode == "quarantine":
        assert erasure.restore(mid)["ok"]
        assert memid.get(mid)["text"] == marker


def test_malformed_snapshot_is_never_reported_as_fully_purged(env):  # noqa: F811
    mid = memid.allocate("malformed-private-needle", sender="user")
    root = Path(consolidation_snapshots.directory())
    root.mkdir()
    broken = root / "broken.json"
    broken.write_text('{"incomplete":')
    report = erasure.forget({"mem_id": mid}, apply=True, mode="purge")
    assert not report["ok"]
    assert report["snapshots"]["errors"]
    assert broken.read_text() == '{"incomplete":'
    check = erasure.verify_erasure(["malformed-private-needle"])
    assert not check["fully_purged"]
    assert "snapshots_error" in check["found_in"]


def test_legacy_same_second_snapshot_rows_need_content_match(env):  # noqa: F811
    ts = "2026-01-01 00:00:00"
    mid = memid.allocate("legacy-removed-text", sender="user", ts=ts)
    other = memid.allocate("legacy-kept-text", sender="user", ts=ts)
    path = _snapshot([_memory("engram:" + ts, "legacy-removed-text"),
                      _memory("engram:" + ts, "legacy-kept-text")], "legacy")
    assert erasure.forget({"mem_id": mid}, apply=True, mode="purge")["ok"]
    restored = restore_snapshot(path)
    assert [m.content for m in restored] == ["legacy-kept-text"]
    assert memid.get(other)["text"] == "legacy-kept-text"


def test_stable_derived_ids_are_cleaned_without_deleting_same_second_neighbors(env):  # noqa: F811
    ts = "2026-01-01 00:00:00"
    mid = memid.allocate("canonical source one", sender="user", ts=ts)
    other = memid.allocate("canonical source two", sender="user", ts=ts)
    bridge._append_row({"memory_id": "engram:unique-one", "source_mem_id": mid,
                        "ts": ts, "content": "derived one", "type": "semantic"})
    bridge._append_row({"memory_id": "engram:unique-two", "source_mem_id": other,
                        "ts": ts, "content": "derived two", "type": "semantic"})
    path = _snapshot([_memory("engram:unique-one", "derived one"),
                      _memory("engram:unique-two", "derived two")], "derived")
    report = erasure.forget({"mem_id": mid}, apply=True, mode="purge")
    assert report["ok"], report
    assert [m.content for m in restore_snapshot(path)] == ["derived two"]
    assert [r["source_mem_id"] for r in bridge.recent(20)] == [other]


def test_legacy_bound_engram_time_can_differ_from_raw_time(env):  # noqa: F811
    marker = "historical-bound-legacy-marker-615"
    mid = memid.allocate(marker, sender="user", ts="2020-01-01 00:00:00")
    ts = "2026-01-01 00:00:00"
    Path(bridge._STORE).write_text(json.dumps({"ts": ts, "source_mem_id": mid,
                                             "content": marker, "type": "semantic"}) + "\n")
    path = _snapshot([_memory("engram:" + ts, marker)], "cross-second")
    report = erasure.forget({"mem_id": mid}, apply=True, mode="purge")
    assert report["ok"], report
    assert restore_snapshot(path) == []
    assert erasure.verify_erasure([marker])["fully_purged"]


def test_unbound_legacy_engram_same_second_requires_content(env):  # noqa: F811
    ts = "2026-01-01 00:00:00"
    mid = memid.allocate("unbound removed", sender="user", ts=ts)
    other = memid.allocate("unbound kept", sender="user", ts=ts)
    rows = [{"ts": ts, "content": text, "type": "semantic"} for text in ("unbound removed", "unbound kept")]
    Path(bridge._STORE).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    report = erasure.forget({"mem_id": mid}, apply=True)
    assert report["ok"], report
    assert [r["content"] for r in bridge.recent(20)] == ["unbound kept"]
    assert memid.get(other)["text"] == "unbound kept"
    assert erasure.restore(mid)["ok"]
    assert sorted(r["content"] for r in bridge.recent(20)) == ["unbound kept", "unbound removed"]


def test_ambiguous_legacy_content_is_retained_and_reported(env):  # noqa: F811
    ts = "2026-01-01 00:00:00"
    prefix = "same-prefix-" * 30
    mid = memid.allocate(prefix + " selected", sender="user", ts=ts)
    other = memid.allocate(prefix + " unrelated", sender="user", ts=ts)
    content = prefix[:300]
    Path(bridge._STORE).write_text(json.dumps({"ts": ts, "content": content, "type": "semantic"}) + "\n")
    path = _snapshot([_memory("engram:" + ts, content)], "ambiguous")
    report = erasure.forget({"mem_id": mid}, apply=True, mode="purge")
    assert not report["ok"]
    assert report["engram_retained"]
    assert bridge.recent(20)[0]["content"] == content
    assert restore_snapshot(path)[0].content == content
    assert memid.get(other)["text"] == prefix + " unrelated"


def test_snapshot_preserves_dependency_metadata_and_drops_affected_aggregate(env):  # noqa: F811
    mid = memid.allocate("aggregate source", sender="user")
    obj = MemoryObject(memory_id="abstract-one", content="derived aggregate", source_id=mid)
    obj.source_ids = [mid, "another-source"]
    path = _snapshot([obj, _memory("unrelated", "keep this")], "aggregate")
    row = json.loads(Path(path).read_text())[0]
    assert row["source_id"] == mid and row["source_ids"] == [mid, "another-source"]
    assert erasure.forget({"mem_id": mid}, apply=True, mode="purge")["ok"]
    assert [m.content for m in restore_snapshot(path)] == ["keep this"]


def test_snapshot_cannot_reintroduce_an_already_forgotten_source(env):  # noqa: F811
    mid = memid.allocate("stale snapshot input", sender="user")
    obj = _memory(mid, "stale snapshot input")
    assert erasure.forget({"mem_id": mid}, apply=True, mode="purge")["ok"]
    path = _snapshot([obj], "late")
    assert restore_snapshot(path) == []


def test_expiry_purge_also_cleans_snapshots_from_older_versions(env):  # noqa: F811
    marker = "old-version-retained-snapshot-783"
    mid = memid.allocate(marker, sender="user")
    assert erasure.forget({"mem_id": mid}, apply=True)["ok"]
    # Simulate a snapshot retained by an older release. New _snapshot correctly
    # refuses a tombstoned source, so construct this legacy disk fixture directly.
    root = Path(consolidation_snapshots.directory())
    root.mkdir(exist_ok=True)
    path = root / "old.json"
    path.write_text(json.dumps([{"memory_id": mid, "type": "episodic", "content": marker}]))
    assert not erasure.verify_erasure([marker])["fully_purged"]
    report = erasure.purge([mid], apply=True)
    assert report["ok"], report
    assert marker not in path.read_text()
    assert erasure.verify_erasure([marker])["fully_purged"]


def test_engine_reports_validated_proposals_separately_from_mutations(env):  # noqa: F811
    memories = [_memory("one", "I drink tea every morning.", created_at="2020-01-01"),
                _memory("two", "I drink tea every morning.", created_at="2020-01-02")]
    result = ConsolidationEngine().run(memories, "truthful")
    assert "abstract" in result["validated_actions"]
    assert "abstract" in result["deferred_actions"]
    assert result["applied_actions"] == ["decay", "decay"]
    assert result["applied"] == 2
    assert result["persisted"] == 0 and result["applied_scope"] == "in_memory"
    assert len(result["decay_updates"]) == 2
    assert [m.decay_weight for m in memories] == [0.7, 0.7]


def test_facade_consolidation_does_not_decay_static_types(env):  # noqa: F811
    from nexsandglass.runtime import facade
    for kind in ("semantic", "procedural"):
        bridge._append_row({"memory_id": "engram:static-" + kind, "ts": "2020-01-01 00:00:00",
                            "type": kind, "content": "stable " + kind,
                            "decay_weight": 1.0, "access_count": 0})
    report = facade.consolidate(tag="static")
    assert report["ok"], report
    assert report["detail"]["persisted"] == 0
    assert all(r["decay_weight"] == 1.0 and r["access_count"] == 0 for r in bridge.recent(20))
