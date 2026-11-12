"""Derived IDs must identify their bound raw memory without timestamp guessing."""
import json

import pytest


@pytest.fixture()
def hub(monkeypatch, tmp_path):
    from nexsandglass.core import memid
    from nexsandglass.engram import bridge

    home = tmp_path / "nb"
    home.mkdir()
    monkeypatch.setenv("NEXSANDBASE_HOME", str(home))
    monkeypatch.setattr(bridge, "_STORE", str(home / "engram_store.jsonl"))
    memid.set_db_path(str(home / "nyx.db"))
    yield memid, bridge, home
    memid.set_db_path(str(tmp_path / "unused.db"))


def test_stable_derived_id_resolves_its_source_not_ingestion_time(hub):
    memid, bridge, _ = hub
    source = memid.allocate("original historical observation", sender="user", ts="2020-01-01 00:00:00")
    modern = bridge.ingest_classified("processed later", source_mem_id=source)["id"]
    assert memid.resolve(modern) == source
    assert memid.resolve("engram:" + bridge.recent()[0]["ts"]) == source


def test_two_same_second_rows_keep_distinct_sources(hub):
    memid, bridge, _ = hub
    ts = "2026-01-01 00:00:00"
    sources = [memid.allocate(s, sender="user", ts=ts) for s in ("first raw", "second raw")]
    derived = [bridge.ingest_classified(s, source_mem_id=mid)["id"]
               for s, mid in zip(("first derivative", "second derivative"), sources)]
    assert [memid.resolve(ref) for ref in derived] == sources
    # Force a shared legacy alias independently of wall-clock timing.
    with open(bridge._STORE, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    for row in rows:
        row["ts"] = ts
    with open(bridge._STORE, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="ambiguous engram"):
        memid.resolve("engram:" + ts)


def test_legacy_ambiguity_is_rejected_even_without_a_derived_store(hub):
    memid, _, _ = hub
    ts = "2026-01-01 00:00:00"
    first = memid.allocate("first", sender="user", ts=ts)
    memid.allocate("second", sender="agent", ts=ts)
    with pytest.raises(ValueError, match="ambiguous engram"):
        memid.resolve("engram:" + ts)
    memid.tombstone(first)
    with pytest.raises(ValueError, match="ambiguous engram"):
        memid.resolve("engram:" + ts)


def test_unique_legacy_row_without_source_keeps_compatibility(hub):
    memid, bridge, home = hub
    ts = "2026-01-01 00:00:00"
    source = memid.allocate("legacy", sender="user", ts=ts)
    store = home / "engram_store.jsonl"
    store.write_text(json.dumps({"ts": ts, "content": "legacy", "type": "semantic"}) + "\n")
    before = store.read_bytes()
    assert memid.resolve("engram:" + ts) == source
    assert store.read_bytes() == before
    memid.tombstone(source)
    assert memid.resolve("engram:" + ts) is None
    assert memid.resolve("engram:" + ts, allow_deleted=True) == source


def test_modern_row_without_binding_does_not_guess_timestamp(hub):
    memid, bridge, _ = hub
    ref = bridge.ingest_classified("unbound derived memory")["id"]
    ts = bridge.recent()[0]["ts"]
    memid.allocate("unrelated raw memory", sender="user", ts=ts)
    assert memid.resolve(ref) is None
    assert memid.resolve("engram:" + ts) is None


def test_bound_deleted_source_is_not_redirected_to_same_second_live_record(hub):
    memid, bridge, _ = hub
    ts = "2026-01-01 00:00:00"
    source = memid.allocate("delete this", sender="user", ts=ts)
    other = memid.allocate("keep this", sender="user", ts=ts)
    ref = bridge.ingest_classified("derived", source_mem_id=source)["id"]
    memid.tombstone(source)
    assert memid.resolve(ref) is None
    assert memid.resolve(ref, allow_deleted=True) == source
    assert memid.get(other)["text"] == "keep this"


def test_unknown_or_invalid_binding_never_resolves_other_memory(hub):
    memid, bridge, home = hub
    source = memid.allocate("keep", sender="user")
    ref = "engram:" + "1" * 32
    (home / "engram_store.jsonl").write_text(json.dumps({
        "memory_id": ref, "ts": memid.get(source)["ts"], "content": "bad binding",
        "source_mem_id": "engram:" + "2" * 32,
    }) + "\n")
    assert memid.resolve(ref) is None
    assert memid.resolve("engram:" + "3" * 32) is None


def test_ambiguous_alias_prevents_facade_forget(hub):
    memid, bridge, _ = hub
    from nexsandglass.runtime import facade
    ts = "2026-01-01 00:00:00"
    ids = [memid.allocate(s, sender="user", ts=ts) for s in ("first", "second")]
    result = facade.forget({"memory_id": "engram:" + ts})
    assert not result["ok"]
    assert "ambiguous engram" in result["error"]
    assert all(memid.get(mid) for mid in ids)
