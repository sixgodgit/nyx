"""Final provider output must respect its configured estimated token budget."""
from types import SimpleNamespace

import pytest

from nexsandglass.core.memory_provider import NexSandglassProvider, memory_block
from nexsandglass.engram.types import MemoryObject
from nexsandglass.runtime.bundle import estimate_tokens


@pytest.mark.parametrize("budget", [0, 1, 20, 60, 200, 600])
@pytest.mark.parametrize("text", ["咖啡过敏，不能饮用。" * 50, "Do not serve peanuts. " * 50])
def test_final_memory_block_fits_whole_lines(budget, text):
    rendered = memory_block([(text, "2026-01-01"), ("short evidence", None)], token_budget=budget)
    assert estimate_tokens(rendered) <= budget
    if rendered:
        assert all(line.startswith("- ") for line in rendered.splitlines()[1:])
        assert not rendered.endswith("- ")


def test_recall_view_keeps_dates_and_objects_aligned(monkeypatch):
    from nexsandglass.runtime import orchestrator
    from nexsandglass.core import memory_provider as module
    objects = [MemoryObject(memory_id="large", content="cup " * 1000),
               MemoryObject(memory_id="small", content="cup is blue")]
    calls = []
    def recall(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(objects=objects, withheld=[])
    monkeypatch.setattr(orchestrator, "get_orchestrator", lambda: SimpleNamespace(recall=recall))
    monkeypatch.setattr(module, "_said_on", lambda objs: {0: "first", 1: "second"})
    p = NexSandglassProvider()
    p._prefetch_budget = 40
    items, picked, _ = p._recall_view("cup", session_id="s42")
    assert [o.memory_id for o in picked] == ["small"]
    assert items == [("cup is blue", "second")]
    assert calls[0]["session_id"] == "s42" and calls[0]["candidate_mode"]
    assert estimate_tokens(memory_block(items)) <= 40


def test_prefetch_includes_notices_in_budget(monkeypatch):
    from nexsandglass.runtime import orchestrator
    from nexsandglass.core import memory_provider as module
    obj = MemoryObject(memory_id="cup", content="cup is in the kitchen. " * 20)
    monkeypatch.setattr(orchestrator, "get_orchestrator",
                        lambda: SimpleNamespace(recall=lambda *a, **k: SimpleNamespace(objects=[obj], withheld=[])))
    monkeypatch.setattr(module, "_said_on", lambda objs: {})
    monkeypatch.setattr(module._recording, "paused", lambda: None)
    p = NexSandglassProvider()
    p._prefetch_budget = 80
    monkeypatch.setattr(p, "_signal_line", lambda: "Current signal: steady.")
    result = p.prefetch("Where is the cup?", session_id="s42")
    assert estimate_tokens(result) <= 80
    assert "Current signal: steady." in result


def test_zero_budget_disables_all_prefetch_output(monkeypatch):
    p = NexSandglassProvider()
    p._prefetch_budget = 0
    monkeypatch.setattr(p, "_signal_line", lambda: "Current signal: steady.")
    assert p.prefetch("hello") == ""
