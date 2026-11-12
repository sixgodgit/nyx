"""Exercise public facade forwarding and durable consolidation behavior."""
import json
from unittest.mock import Mock

from nexsandglass.runtime import facade, orchestrator


def test_public_context_and_session_are_forwarded(monkeypatch):
    orch = Mock()
    monkeypatch.setattr(orchestrator, "get_orchestrator", lambda: orch)
    facade.recall("that one", context="blue cup", session_id="s42", token_budget=40)
    orch.recall.assert_called_once_with("that one", 40, context="blue cup", session_id="s42")
    facade.observe("cup moved", session_id="s42")
    assert orch.observe.call_args.kwargs["session_id"] == "s42"


def test_consolidation_persists_decay_and_does_not_repeat(monkeypatch, tmp_path):
    from nexsandglass.engram import bridge
    from nexsandglass.core import consolidation_snapshots
    store = tmp_path / "engram.jsonl"
    monkeypatch.setattr(bridge, "_STORE", str(store))
    monkeypatch.setattr(consolidation_snapshots, "directory", lambda: str(tmp_path / "snapshots"))
    rows = [{"memory_id": "engram:one", "ts": "2020-01-01 00:00:00", "type": "episodic",
             "content": "I remember the red cup.", "access_count": 0, "decay_weight": 1.0},
            {"memory_id": "engram:two", "ts": "2020-01-02 00:00:00", "type": "episodic",
             "content": "I remember the red cup.", "access_count": 0, "decay_weight": 1.0}]
    store.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    result = facade.consolidate(tag="durable")
    assert result["ok"], result
    assert result["detail"]["persisted"] == result["detail"]["applied"] == 2
    assert "abstract" in result["detail"]["deferred_actions"]
    persisted = [json.loads(line) for line in store.read_text().splitlines()]
    assert all(r["decay_weight"] == 0.7 and r["access_count"] == 1 for r in persisted)
    again = facade.consolidate(tag="durable")
    assert again["ok"] and again["detail"]["persisted"] == 0


def test_feedback_ambiguity_does_not_change_any_row(monkeypatch, tmp_path):
    from nexsandglass.engram import bridge
    store = tmp_path / "engram.jsonl"
    monkeypatch.setattr(bridge, "_STORE", str(store))
    ts = "2026-01-01 00:00:00"
    store.write_text("\n".join(json.dumps({"ts": ts, "content": s}) for s in ["one", "two"]) + "\n")
    before = store.read_bytes()
    result = facade.feedback({"memory_ids": ["engram:" + ts], "helpful": False})
    assert not result["ok"] and result["detail"]["persisted_changes"] == 0
    assert store.read_bytes() == before
