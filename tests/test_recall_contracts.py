"""Behavioral regressions for durable formation and bounded recall contracts."""
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from nexsandglass.engram.types import MemoryObject
from nexsandglass.runtime.bundle import estimate_tokens
from nexsandglass.runtime.orchestrator import (
    FormationRouter, ModulePolicy, NyxOrchestrator, PolicyEngine, RecallPlanner,
)


def planner(timeout=1.0):
    names = ("search_router", "shadow", "wthread", "persona", "nyx")
    return RecallPlanner(PolicyEngine({
        n: ModulePolicy(enabled=n == "search_router", timeout=timeout, limit=20)
        for n in names
    }))


@pytest.mark.parametrize("failure", [OSError("disk unavailable"), None])
def test_raw_write_failure_stops_derived_writes(monkeypatch, failure):
    from nexsandglass.core import sandglass_log
    from nexsandglass.engram import bridge
    from nexsandglass.features import shadow_sand, weavethread

    raw = Mock(side_effect=failure) if failure else Mock(return_value="")
    engram, shadow, thread = Mock(), Mock(), Mock()
    monkeypatch.setattr(sandglass_log, "log_message", raw)
    monkeypatch.setattr(bridge, "ingest_classified", engram)
    monkeypatch.setattr(shadow_sand, "shadow_index", shadow)
    monkeypatch.setattr(weavethread, "wthread_store", thread)
    report = FormationRouter().observe("I live in Berlin.", source="user", force_promote=True)
    assert not report.ok
    assert "sandglass" in report.errors
    assert report.routes_written == []
    engram.assert_not_called()
    shadow.assert_not_called()
    thread.assert_not_called()


def test_claimed_raw_record_must_exist(monkeypatch):
    import nexsandglass.runtime.orchestrator as mod
    from nexsandglass.engram import bridge

    monkeypatch.setattr(mod, "_find_logged", lambda *a, **kw: ("", 0))
    ingest = Mock()
    monkeypatch.setattr(bridge, "ingest_classified", ingest)
    report = FormationRouter().observe("No raw record for this sentence.", source="user",
                                      raw_already_logged=True, force_promote=True)
    assert not report.ok
    assert "canonical" in report.errors["sandglass"]
    ingest.assert_not_called()


def test_explicit_raw_identity_survives_interleaved_writes(monkeypatch):
    from nexsandglass.core import memid
    from nexsandglass.engram import bridge

    text = "Canonical source record for the kitchen blue cup."
    mid = memid.allocate(text, sender="user")
    for n in range(5):
        memid.allocate(f"Unrelated interleaved record {n}.", sender="user")
    ingest = Mock(return_value={"id": "engram:unique"})
    monkeypatch.setattr(bridge, "ingest_classified", ingest)
    report = FormationRouter().observe(text, source="user", raw_already_logged=True,
                                      source_mem_id=mid, session_id="session-42",
                                      force_promote=True)
    assert report.ok
    assert report.session_id == "session-42"
    assert ingest.call_args.kwargs["source_mem_id"] == mid


def test_explicit_raw_identity_cannot_relabel_another_source(monkeypatch):
    from nexsandglass.core import memid
    from nexsandglass.engram import bridge

    text = "Remember the blue cup is in the kitchen."
    mid = memid.allocate(text, sender="tool")
    ingest = Mock()
    monkeypatch.setattr(bridge, "ingest_classified", ingest)
    result = FormationRouter().observe(text, source="user", raw_already_logged=True,
                                      source_mem_id=mid, force_promote=True)
    assert not result.ok
    ingest.assert_not_called()


def test_slow_source_deadline_and_repeated_queries_are_bounded(monkeypatch):
    p = planner(timeout=0.025)
    release = threading.Event()
    finished = threading.Event()
    calls = []

    def blocked(query):
        calls.append(query)
        try:
            release.wait(2)
            return [MemoryObject(memory_id="late", content="late evidence")]
        finally:
            finished.set()

    monkeypatch.setattr(p, "_adapt_search_router", blocked)
    try:
        start = time.monotonic()
        first = p.recall("cup")
        assert time.monotonic() - start < 0.3
        assert first.objects == []
        assert "timeout" in first.traces[0].error
        for _ in range(15):
            again = p.recall("another cup")
            assert "already running" in again.traces[0].error
        assert len(calls) == 1
    finally:
        release.set()
        assert finished.wait(1)


def test_fast_source_survives_another_source_deadline(monkeypatch):
    p = planner(timeout=0.02)
    p.policy._policies["shadow"].enabled = True
    release = threading.Event()
    done = threading.Event()

    def slow(_):
        try:
            release.wait(2)
            return []
        finally:
            done.set()

    monkeypatch.setattr(p, "_adapt_search_router", slow)
    monkeypatch.setattr(p, "_adapt_shadow", lambda _: [MemoryObject(memory_id="fast", content="cup")])
    try:
        result = p.recall("cup")
        assert result.memory_ids == ["fast"]
        assert result.traces[1].ok
    finally:
        release.set()
        assert done.wait(1)


def test_concurrent_queries_wait_for_healthy_source_without_sharing_results(monkeypatch):
    import nexsandglass.runtime.orchestrator as mod

    p = planner(timeout=1)
    entered, attempted, release = threading.Event(), threading.Event(), threading.Event()
    original_submit = mod._submit_source

    def source(query):
        if query == "first":
            entered.set()
            assert release.wait(2)
        return [MemoryObject(memory_id=query, content=query)]

    def submit(name, fn, query, deadline):
        result = original_submit(name, fn, query, deadline)
        if query == "second":
            attempted.set()
        return result

    monkeypatch.setattr(p, "_adapt_search_router", source)
    monkeypatch.setattr(mod, "_submit_source", submit)
    with ThreadPoolExecutor(max_workers=2) as callers:
        first = callers.submit(p.recall, "first")
        try:
            assert entered.wait(1)
            second = callers.submit(p.recall, "second")
            assert attempted.wait(1)
        finally:
            release.set()
        assert first.result(timeout=2).text == "first"
        assert second.result(timeout=2).text == "second"


@pytest.mark.parametrize("different_profiles", [False, True])
def test_two_five_source_queries_survive_source_and_worker_contention(monkeypatch, different_profiles):
    import nexsandglass.runtime.orchestrator as mod
    from nexsandglass.core import settings

    names = ("search_router", "shadow", "wthread", "persona", "nyx")
    p = RecallPlanner(PolicyEngine({n: ModulePolicy(timeout=1) for n in names}))
    release, started_first, attempted_second = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    started, attempted = set(), set()
    original_submit = mod._submit_source

    def source(query, name):
        with lock:
            if query == "first":
                started.add(name)
                if len(started) == 5:
                    started_first.set()
        assert release.wait(2)
        return [MemoryObject(memory_id=f"{query}:{name}", content=f"{query} {name}")]

    def submit(name, fn, query, deadline):
        result = original_submit(name, fn, query, deadline)
        if query == "second":
            with lock:
                attempted.add(name)
                if len(attempted) == 5:
                    attempted_second.set()
        return result

    for name in names:
        monkeypatch.setattr(p, f"_adapt_{name}", lambda q, n=name: source(q, n))
    monkeypatch.setattr(mod, "_submit_source", submit)

    def recall(query):
        profile = query if different_profiles else "same"
        with settings.scope({"TEST_PROFILE": profile}):
            return p.recall(query)

    with ThreadPoolExecutor(max_workers=2) as callers:
        first = callers.submit(recall, "first")
        try:
            assert started_first.wait(1)
            second = callers.submit(recall, "second")
            assert attempted_second.wait(1)
        finally:
            release.set()
        a, b = first.result(timeout=2), second.result(timeout=2)
    assert set(a.memory_ids) == {f"first:{name}" for name in names}
    assert set(b.memory_ids) == {f"second:{name}" for name in names}
    assert all(t.ok for t in a.traces + b.traces)


def test_cancelled_source_releases_capacity_and_reports_error(monkeypatch):
    import nexsandglass.runtime.orchestrator as mod

    p = planner()
    cancelled = Future()
    assert cancelled.cancel()
    with monkeypatch.context() as patch:
        patch.setattr(mod, "_recall_executor", Mock(submit=Mock(return_value=cancelled)))
        result = p.recall("cancelled")
    assert result.traces[0].error == "CancelledError"
    monkeypatch.setattr(p, "_adapt_search_router",
                        lambda q: [MemoryObject(memory_id=q, content=q)])
    assert p.recall("next query").text == "next query"


@pytest.mark.parametrize("budget", [0, 1, 20, 100])
def test_budget_limits_complete_text_without_truncating_evidence(monkeypatch, budget):
    p = planner()
    long = MemoryObject(memory_id="long", content="cup " * 1000 + "Do not use it.")
    small = MemoryObject(memory_id="small", content="蓝色杯子在厨房。")
    monkeypatch.setattr(p, "_adapt_search_router", lambda _: [long, small])
    result = p.recall("cup", token_budget=budget)
    assert estimate_tokens(result.text) <= budget
    assert result.est_tokens == estimate_tokens(result.text)
    assert long.content.endswith("Do not use it.")
    assert all(o.content in (long.content, small.content) for o in result.objects)
    if budget == 20:
        assert result.memory_ids == ["small"]


def test_provider_candidate_mode_retains_full_evidence(monkeypatch):
    p = planner()
    text = "cup " * 1000
    monkeypatch.setattr(p, "_adapt_search_router", lambda _: [MemoryObject(memory_id="x", content=text)])
    bounded = p.recall("cup", token_budget=20)
    candidates = p.recall("cup", token_budget=20, candidate_mode=True)
    assert bounded.objects == []
    assert candidates.objects[0].content == text
    assert candidates.candidate_mode is True


def test_context_changes_anaphoric_retrieval_and_keeps_session(monkeypatch):
    p = planner()
    seen = []

    def source(query):
        seen.append(query)
        return [MemoryObject(memory_id=query, content=query)]

    monkeypatch.setattr(p, "_adapt_search_router", source)
    kitchen = p.recall("Where is that?", context="blue cup in kitchen", session_id="s1")
    garage = p.recall("Where is that?", context="red wrench in garage", session_id="s2")
    assert kitchen.text != garage.text
    assert "blue cup" in seen[0] and "red wrench" in seen[1]
    assert kitchen.query == "Where is that?"
    assert kitchen.session_id == "s1"
    assert kitchen.context_used and "task_context_used" in kitchen.reasons


def test_unrelated_context_is_not_blindly_appended(monkeypatch):
    p = planner()
    source = Mock(return_value=[])
    monkeypatch.setattr(p, "_adapt_search_router", source)
    result = p.recall("Where is the blue cup?", context="unrelated medical history")
    source.assert_called_once_with("Where is the blue cup?")
    assert not result.context_used


def test_worker_preserves_settings_and_simulated_time(monkeypatch):
    from nexsandglass.core import clock, settings

    p = planner()
    seen = []

    def source(_):
        seen.append((settings.get("AUDIT_PROFILE"), clock.now().isoformat()))
        return []

    monkeypatch.setattr(p, "_adapt_search_router", source)
    with settings.scope({"AUDIT_PROFILE": "private-profile"}), clock.frozen("2020-01-02 03:04:05"):
        p.recall("cup")
    assert seen == [("private-profile", "2020-01-02T03:04:05")]
    with settings.scope({"AUDIT_PROFILE": "other-profile"}), clock.frozen("2021-01-02 03:04:05"):
        p.recall("cup")
    assert seen[-1] == ("other-profile", "2021-01-02T03:04:05")


def test_policy_environment_overrides_do_not_mutate_defaults(monkeypatch):
    monkeypatch.setenv("NYX_POLICY_PERSONA_ENABLED", "0")
    assert not PolicyEngine().enabled("persona")
    monkeypatch.delenv("NYX_POLICY_PERSONA_ENABLED")
    assert PolicyEngine().enabled("persona")
