"""Real adapter contracts: common scope, complete evidence and honest traces."""
import json
import os
from pathlib import Path
import subprocess
import sys
from hashlib import sha256
from unittest.mock import Mock

import pytest

from tests.test_hermes_provider import env, _provider, _reindex  # noqa: F401
from nexsandglass.core import clock, memid
from nexsandglass.runtime.composition import estimate_tokens


def say(text, when="2026-11-22 09:00:00"):
    with clock.frozen(when):
        return memid.allocate(text, sender="user")


def test_system_context_trace_is_content_free_and_does_not_claim_delivery(env):
    p = _provider()
    mid = say("我对花生过敏，绝不能推荐花生。")
    text = p.system_prompt_block()
    assert "花生过敏" in text
    trace = p.context_trace("hermes_system")
    assert trace["stage"] == "generated" and trace["sent"] is None and trace["applied"] is None
    assert trace["text_sha256"] == sha256(text.encode()).hexdigest()
    assert trace["rendered_tokens"] == estimate_tokens(text) <= trace["token_budget"]
    assert mid in trace["evidence_ids"]
    assert "花生" not in json.dumps(trace, ensure_ascii=False)
    trace["status"] = "mutated"
    assert p.context_trace("hermes_system")["status"] != "mutated"


def test_persistent_and_per_turn_outputs_share_total_envelope(env, monkeypatch):
    p = _provider()
    monkeypatch.setenv("NYX_CONTEXT_TOKENS", "170")
    say("我对花生过敏。")
    say("The cobalt mug is kept on shelf delta.")
    _reindex(env)
    system = p.system_prompt_block()
    turn = p.prefetch("cobalt mug shelf delta", session_id="current")
    assert "花生" in system
    assert estimate_tokens(system) + estimate_tokens(turn) <= 170
    assert p.context_trace("hermes_prefetch")["reserved_tokens"] == estimate_tokens(system)
    assert p.context_trace("hermes_prefetch")["token_budget"] <= 170 - estimate_tokens(system)


def test_zero_total_context_does_not_read_sources(env, monkeypatch):
    p = _provider()
    monkeypatch.setenv("NYX_CONTEXT_TOKENS", "0")
    monkeypatch.setattr(p, "_personal_context", Mock(side_effect=AssertionError("read")))
    monkeypatch.setattr(p, "_recall_view", Mock(side_effect=AssertionError("recall")))
    assert p.system_prompt_block() == ""
    assert p.voice_prompt_block() == ""
    assert p.prefetch("any question") == ""
    assert all(t["rendered_tokens"] == 0 for t in p.context_trace().values())


def test_missing_required_health_context_blocks_ordinary_recall(env, monkeypatch):
    p = _provider()
    say("我对花生过敏。")
    monkeypatch.setenv("NYX_CONTEXT_TOKENS", "1")
    assert p.system_prompt_block() == ""
    assert p.context_trace("hermes_system")["required_omitted"]
    monkeypatch.setattr(p, "_recall_view", Mock(side_effect=AssertionError("must not substitute advice")))
    assert p.prefetch("what should I eat") == ""


def test_profile_and_followup_excerpts_preserve_long_qualifiers(env):
    from nexsandglass.core import profile, followups
    text = "I'm allergic to penicillin, " + "clinical detail " * 20 + "but NOT to peanuts."
    hit = profile.classify(text)
    assert hit and hit[1].endswith("but NOT to peanuts.") and len(hit[1]) > 160
    event = "Tomorrow I have an interview, " + "important detail " * 20 + "but it is NOT a medical appointment."
    assert followups._excerpt({"text": event, "sentence": event}) == event


def test_voice_uses_only_dedicated_facts_and_retains_bound_sources(env, monkeypatch):
    p = _provider()
    mid = say("以后回复我用中文。")
    monkeypatch.setattr(p, "prefetch", Mock(side_effect=AssertionError("generic recall")))
    monkeypatch.setattr(p, "system_prompt_block", Mock(side_effect=AssertionError("tool prompt")))
    before = Path(env.journal).read_bytes()
    text = p.voice_prompt_block()
    assert "用中文" in text and "sandglass_search" not in text and len(text) <= 4000
    trace = p.context_trace("conduit_voice")
    assert mid in trace["evidence_ids"] and trace["applied"] is None
    assert Path(env.journal).read_bytes() == before


def test_conduit_recall_fits_transport_cap_without_clipping_qualifiers(env):
    p = _provider()
    p._platform = "conduit_voice"
    p._prefetch_budget = 3000
    long = say("Cobalt mug storage detail " + "filler " * 650 + "but NOT in the kitchen.")
    short = say("Cobalt mug is on the blue shelf.")
    _reindex(env)
    text = p.prefetch("cobalt mug storage")
    assert "Cobalt mug is on the blue shelf." in text
    assert "filler" not in text and len(text) <= 4000
    trace = p.context_trace("hermes_prefetch")
    assert short in trace["evidence_ids"] and long not in trace["evidence_ids"]


def test_context_trace_cannot_read_a_mismatched_store(env, monkeypatch):
    p = _provider()
    p.system_prompt_block()
    monkeypatch.setattr(p, "unavailable_reason", lambda: "different profile")
    assert p.context_trace() == {}


def test_cached_personal_views_recheck_current_source_trust(env):
    from nexsandglass.core import provenance
    p = _provider()
    mid = say("我对花生过敏。")
    assert "花生过敏" in p.system_prompt_block()
    conn = memid.get_conn()
    provenance.record(conn, mid, provenance.assess("untrusted imported text", "tool"))
    conn.commit()
    assert "花生过敏" not in p.system_prompt_block()
    assert "花生过敏" not in p.voice_prompt_block()
    assert mid not in p.context_trace("conduit_voice")["evidence_ids"]


@pytest.mark.parametrize("expired", [False, True])
def test_retrieval_handles_duplicate_ids_and_expired_planner_results(env, monkeypatch, expired):
    from dataclasses import replace
    from types import SimpleNamespace
    from nexsandglass.engram.types import MemoryObject
    from nexsandglass.runtime import orchestrator
    from nexsandglass.runtime.contracts import RecallRequest
    from nexsandglass.runtime.facade import MemoryContext
    from nexsandglass.runtime.host_context import recall_host_context
    request = RecallRequest("cup", token_budget=20)
    mc = MemoryContext(objects=[MemoryObject(memory_id="cup", content="cup is blue"),
                                MemoryObject(memory_id="cup", content="cup " * 1000)],
                       candidate_mode=True)
    mc.packet = replace(orchestrator._context_packet(mc, request, False), deadline_exceeded=expired)
    monkeypatch.setattr(orchestrator, "get_orchestrator", lambda: SimpleNamespace(
        recall_planner=SimpleNamespace(recall=lambda *a, **k: mc)))
    packet, trace, candidates = recall_host_context(request)
    selected = {ref.memory_id for ref in packet.evidence}
    exported = [obj.content for obj in candidates.objects if obj.memory_id in selected]
    assert estimate_tokens("\n\n".join(exported)) <= 20
    if expired:
        assert not packet.text and not exported and trace.status == "expired"
    else:
        assert exported == ["cup is blue"]


def test_persistent_reservation_never_shrinks_without_new_session(env, monkeypatch):
    p = _provider()
    say("我对花生过敏。")
    full = p.system_prompt_block()
    reserved = estimate_tokens(full)
    monkeypatch.setenv("NYX_CONTEXT_TOKENS", "0")
    assert p.system_prompt_block() == ""
    assert p._system_reserved_tokens == reserved


def test_session_branch_preserves_reservations_and_new_conversation_resets_them(env):
    p = _provider()
    say("我对花生过敏。")
    p.system_prompt_block()
    p.voice_prompt_block()
    reserved = (p._system_reserved_tokens, p._voice_reserved_tokens)
    assert all(reserved)
    p.on_session_switch("branch", parent_session_id="original")
    assert p.context_trace() == {}
    assert (p._system_reserved_tokens, p._voice_reserved_tokens) == reserved
    p._system_required_missing = p._voice_required_missing = True
    p.on_session_switch("new", reset=True)
    assert p._system_reserved_tokens == p._voice_reserved_tokens == 0
    assert not p._system_required_missing and not p._voice_required_missing
    p.on_turn_start(2, "a question")
    p.system_prompt_block()
    trace = p.context_trace("hermes_system")
    assert trace["session_id"] == "new" and trace["turn_id"] == "2"


def test_zero_config_survives_save_and_profile_reload(env, tmp_path, monkeypatch):
    from nexsandglass.core import memory_provider
    monkeypatch.delenv("NYX_CONTEXT_TOKENS", raising=False)
    p = _provider()
    p._hermes_home = str(tmp_path / "hermes")
    p.save_config({"context_tokens": 0}, p._hermes_home)
    monkeypatch.setattr(p, "_personal_context", Mock(side_effect=AssertionError("read")))
    assert p.system_prompt_block() == p.voice_prompt_block() == p.prefetch("test question") == ""
    reloaded = memory_provider.NexSandglassProvider()
    reloaded._hermes_home = p._hermes_home
    reloaded._load_settings()
    assert reloaded._context_tokens() == 0


def test_compression_retains_full_canonical_record_and_uses_remaining_budget(env, monkeypatch):
    p = _provider()
    text = "The cobalt mug is kept on shelf delta, " + "storage detail " * 22 + "but NOT in the kitchen."
    mid = say(text)
    _reindex(env)
    monkeypatch.setenv("NYX_CONTEXT_TOKENS", "1000")
    system = p.system_prompt_block()
    result = p.on_pre_compress([{"role": "user", "content": "cobalt mug shelf delta"}])
    assert text in result and len(text) > 300
    trace = p.context_trace("hermes_compress")
    assert mid in trace["evidence_ids"]
    assert trace["reserved_tokens"] == estimate_tokens(system)
    assert estimate_tokens(system) + estimate_tokens(result) <= 1000
    monkeypatch.setenv("NYX_CONTEXT_TOKENS", "0")
    monkeypatch.setattr("nexsandglass.runtime.host_context.recall_host_context",
                        Mock(side_effect=AssertionError("read")))
    assert p.on_pre_compress([{"content": "cobalt mug"}]) == ""


def test_mcp_real_jsonrpc_preserves_full_evidence_and_reports_only_generation(tmp_path):
    script = r'''
import contextlib,io,json
from nexsandglass.runtime import observe
from nexsandglass.interfaces.sandglass_mcp import _handle_tool
text = "Cobalt mug detail " + "filler " * 70 + "but NOT peanuts."
assert observe(text,source="user",force_promote=True).ok
stream=io.StringIO()
with contextlib.redirect_stdout(stream):
    _handle_tool("memory_recall",{"query":"cobalt mug detail","budget":1000,"limit":10},"m103")
    _handle_tool("memory_recall",{"query":"cobalt mug detail","budget":0},"zero")
    _handle_tool("memory_recall",{"query":"cobalt mug detail","limit":0},"none")
    _handle_tool("memory_recall",{"query":"cobalt mug detail","budget":True},"invalid")
    _handle_tool("memory_recall",{"query":[]},"badquery")
    import nexsandglass.runtime.host_context as host
    def broken(*a, **kw): raise RuntimeError("private-payload-marker")
    host.recall_host_context=broken
    _handle_tool("memory_recall",{"query":"cobalt mug detail"},"error")
responses=[json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
body=json.loads(responses[0]["result"]["content"][0]["text"])
assert any(row["text"]==text for row in body["results"]),body
assert body["trace"]["stage"]=="generated"
assert body["trace"]["sent"] is None and body["trace"]["applied"] is None
assert "filler" not in json.dumps(body["trace"])
assert not json.loads(responses[1]["result"]["content"][0]["text"])["results"]
assert not json.loads(responses[2]["result"]["content"][0]["text"])["results"]
assert all(row["error"]["code"]==-32602 for row in responses[3:5])
assert responses[5]["error"]["code"]==-32000
assert "private-payload-marker" not in json.dumps(responses)
print("M103 MCP: PASS")
'''
    child = dict(os.environ, NEXSANDBASE_HOME=str(tmp_path / "memory"),
                 HERMES_HOME=str(tmp_path / "hermes"), XDG_CONFIG_HOME=str(tmp_path / "config"),
                 NYX_EMBED="off", NYX_AUTO_PURGE="off", DEBUG_NYX="1")
    result = subprocess.run([sys.executable, "-c", script], env=child,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    assert "M103 MCP: PASS" in result.stdout
