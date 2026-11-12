"""Stable derived identity and serialized engram read/modify/write updates."""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from nexsandglass.engram import bridge


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "engram_store.jsonl"
    monkeypatch.setattr(bridge, "_STORE", str(path))
    return path


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def freeze_time(monkeypatch):
    class FrozenDatetime:
        @staticmethod
        def now():
            return datetime(2026, 11, 12, 9, 30)
    monkeypatch.setattr(bridge, "datetime", FrozenDatetime)


def test_same_second_records_have_distinct_stable_ids(store, monkeypatch):
    freeze_time(monkeypatch)
    first = bridge.ingest_classified("first memory", source_mem_id="m_first")
    second = bridge.ingest_classified("second memory", source_mem_id="m_second")
    saved = rows(store)
    assert saved[0]["ts"] == saved[1]["ts"]
    assert first["id"] != second["id"]
    assert [bridge.row_id(row) for row in saved] == [first["id"], second["id"]]
    assert bridge.resolve_row(first["id"])["source_mem_id"] == "m_first"
    obj = bridge.engram_row_to_object(saved[0])
    assert obj.memory_id == first["id"]
    assert obj.source_id == "m_first"
    roundtrip = bridge.object_to_engram_row(obj)
    assert roundtrip["memory_id"] == first["id"]
    assert roundtrip["source_mem_id"] == "m_first"


def test_unique_feedback_only_changes_requested_row(store, monkeypatch):
    freeze_time(monkeypatch)
    first = bridge.ingest_classified("first memory")
    bridge.ingest_classified("second memory")
    result = bridge.apply_feedback([first["id"]], helpful=False)
    assert result["ok"] and result["persisted_changes"] == 1
    assert [r["decay_weight"] for r in rows(store)] == [0.8, 1.0]


def test_ambiguous_legacy_feedback_and_override_do_not_write(store):
    ts = "2026-01-01 00:00:00"
    store.write_text("".join(json.dumps({"ts": ts, "content": text, "type": "semantic"}) + "\n"
                             for text in ("first", "second")))
    before = store.read_bytes()
    result = bridge.apply_feedback(["engram:" + ts], helpful=False)
    assert not result["ok"]
    assert result["ambiguous_ids"] == ["engram:" + ts]
    assert result["persisted_changes"] == 0
    with pytest.raises(ValueError, match="ambiguous engram"):
        bridge.resolve_row("engram:" + ts)
    with pytest.raises(ValueError, match="ambiguous engram"):
        bridge.ingest_classified("replacement", action="OVERRIDE", supersede_id="engram:" + ts)
    assert store.read_bytes() == before


def test_legacy_reads_keep_bytes_and_unambiguous_feedback_works(store):
    old = {"ts": "2026-01-01 00:00:00", "content": "legacy", "type": "semantic"}
    store.write_text(json.dumps(old) + "\n")
    before = store.read_bytes()
    saved = bridge.recent()[0]
    obj = bridge.engram_row_to_object(saved)
    assert obj.memory_id == "engram:" + old["ts"]
    assert bridge.object_to_engram_row(obj) == old
    assert store.read_bytes() == before
    result = bridge.apply_feedback([obj.memory_id], helpful=True)
    assert result["ok"] and result["matched"] == 1
    assert rows(store)[0]["access_count"] == 1
    assert "memory_id" not in rows(store)[0]


def test_legacy_alias_to_modern_rows_is_still_ambiguous(store, monkeypatch):
    freeze_time(monkeypatch)
    bridge.ingest_classified("first memory")
    bridge.ingest_classified("second memory")
    before = store.read_bytes()
    result = bridge.apply_feedback(["engram:" + rows(store)[0]["ts"]], helpful=False)
    assert not result["ok"] and store.read_bytes() == before


def test_mutation_preserves_invalid_lines_and_resolves_aliases_once(store):
    old = {"ts": "2026-01-01 00:00:00", "content": "legacy", "type": "semantic"}
    store.write_text("not json\n\n" + json.dumps(old) + "\n")
    result = bridge.mutate_rows(["engram:" + old["ts"], "missing"], weight_delta=-0.3, access_delta=1)
    assert result["ok"] and result["persisted_changes"] == 1
    assert result["missing_ids"] == ["missing"]
    assert store.read_text().startswith("not json\n\n")
    changed = json.loads(store.read_text().splitlines()[2])
    assert changed["decay_weight"] == pytest.approx(0.7)
    assert changed["access_count"] == 1


def test_concurrent_appends_and_feedback_do_not_lose_updates(store):
    mid = bridge.ingest_classified("base memory")["id"]

    def update(i):
        if i % 2:
            bridge.ingest_classified(f"additional memory {i}")
        else:
            result = bridge.apply_feedback([mid])
            assert result["persisted_changes"] == 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(60)))
    saved = rows(store)
    assert len(saved) == 31
    assert next(r for r in saved if r["memory_id"] == mid)["access_count"] == 30
    assert len({r["memory_id"] for r in saved}) == 31


def test_processes_share_the_same_append_and_feedback_lock(store):
    mid = bridge.ingest_classified("base memory")["id"]
    code = """
import sys
from nexsandglass.engram import bridge
bridge._STORE = sys.argv[1]
for i in range(12):
    bridge.ingest_classified('process '+sys.argv[3]+' memory '+str(i))
    result = bridge.apply_feedback([sys.argv[2]])
    assert result['persisted_changes'] == 1
"""
    processes = [subprocess.Popen([sys.executable, "-c", code, str(store), mid, str(i)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i in range(2)]
    for process in processes:
        out, err = process.communicate(timeout=30)
        assert process.returncode == 0, out + err
    saved = rows(store)
    assert len(saved) == 25
    assert next(r for r in saved if r["memory_id"] == mid)["access_count"] == 24


def test_reinforcement_retains_id_and_override_targets_one_row(store, monkeypatch):
    freeze_time(monkeypatch)
    first = bridge.ingest_classified("first memory")
    second = bridge.ingest_classified("second memory")
    repeat = bridge.ingest_classified("first memory", action="DEDUP")
    assert repeat["matched_id"] == first["id"]
    replacement = bridge.ingest_classified("replacement memory", action="OVERRIDE", supersede_id=first["id"])
    saved = rows(store)
    assert saved[0]["superseded_by"] == replacement["id"]
    assert saved[0]["memory_id"] == first["id"]
    assert saved[1]["memory_id"] == second["id"] and "superseded_by" not in saved[1]
