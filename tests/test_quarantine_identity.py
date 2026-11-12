"""Restoration and legacy capture must preserve unrelated derived records."""
import json

from tests.test_hermes_provider import env  # noqa: F401
from nexsandglass.core import memid, quarantine


def test_legacy_capture_requires_matching_content(env, tmp_path):
    ts = "2026-01-01 00:00:00"
    mid = memid.allocate("first original", sender="user", ts=ts)
    row = memid.get(mid)
    store = tmp_path / "legacy.jsonl"
    store.write_text("\n".join(json.dumps({"ts": ts, "content": s})
                                for s in ["first original", "unrelated second"]) + "\n")
    record = tuple(row[k] for k in ["mem_id", "line_start", "line_end", "ts", "sender", "seq", "text"])
    payload = quarantine.capture(record, journal_path=env.journal, engram_path=str(store))
    assert [json.loads(line)["content"] for line in payload["engram"]] == ["first original"]


def test_restore_deduplicates_stable_ids_without_overwriting_feedback(tmp_path):
    path = tmp_path / "engram.jsonl"
    original = {"memory_id": "engram:one", "content": "remember", "access_count": 3}
    path.write_text(json.dumps(original) + "\n")
    older = dict(original, access_count=0)
    new = {"memory_id": "engram:two", "content": "another"}
    lines = [json.dumps(older), json.dumps(new), json.dumps(new)]
    assert quarantine._restore_engram(str(path), lines) == 1
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [original, new]
    assert quarantine._restore_engram(str(path), lines) == 0
