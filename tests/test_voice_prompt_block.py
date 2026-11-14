"""Voice startup reads current trusted memory without Hermes tool instructions."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_hermes_provider import env, _provider  # noqa: F401
from nexsandglass.core import clock, followups, profile


def _say(p, when, text, *, source="user"):
    from nexsandglass.core import memid
    # Raw canonical records exercise the same owner/trust gates as normal reads,
    # without bringing unrelated promotion or background processing into scope.
    with clock.frozen(when):
        return memid.allocate(text, sender=source)


def test_voice_context_contains_current_owner_facts_without_tools(env):
    p = _provider()
    _say(p, "2026-10-01", "我对花生过敏。")
    _say(p, "2026-10-02", "我住在杭州。")
    _say(p, "2026-10-03", "以后回复我用中文。")
    _say(p, "2026-10-04", "以后回复我用英文。")
    _say(p, "2026-10-05", "我喜欢喝红茶。")
    with clock.frozen("2026-10-06"):
        block = p.voice_prompt_block()
    for fact in ("花生过敏", "住在杭州", "用英文", "喝红茶"):
        assert fact in block
    assert "用中文" not in block and "Changed since" not in block
    for unwanted in ("sandglass_search", "nyx_belief", "nyx_forget", "Nyx memory:",
                     "[Who you are]", "[Where you are heading]", "Stage:"):
        assert unwanted not in block


def test_non_owner_sources_are_not_voice_profile(env):
    p = _provider()
    _say(p, "2026-10-01", "我对花生过敏。")
    _say(p, "2026-10-02", "我对虾过敏。", source="participant")
    _say(p, "2026-10-03", "我住在拉萨。", source="agent")
    _say(p, "2026-10-04", "以后回复我用法语。", source="tool")
    block = p.voice_prompt_block()
    assert "花生过敏" in block
    assert all(s not in block for s in ("虾过敏", "拉萨", "法语"))


def test_relatives_fact_keeps_its_subject(env):
    p = _provider()
    _say(p, "2026-10-01", "我妈有糖尿病。")
    block = p.voice_prompt_block()
    assert "我妈有糖尿病" in block and "Family, partner and pets" in block
    assert "Health and diet" not in block


def test_due_followups_are_data_without_behavioral_guidance(env):
    p = _provider()
    _say(p, "2026-10-02 20:00:00", "下周三我有个腾讯的面试。")
    with clock.frozen("2026-10-06 09:00:00"):
        block = p.voice_prompt_block()
    assert "腾讯" in block and "2026-10-07" in block
    assert followups.HEADER in block
    assert followups.GUIDANCE not in block
    _say(p, "2026-10-06 10:00:00", "腾讯的面试取消了。")
    with clock.frozen("2026-10-06 11:00:00"):
        assert followups.HEADER not in p.voice_prompt_block()


def test_followup_switches_and_provider_scope_are_honored(env, monkeypatch):
    _say(None, "2026-10-02 20:00:00", "下周三我有个腾讯的面试。")
    enabled = _provider()
    disabled = _provider()
    disabled._profile_env = {"NYX_FOLLOW_UPS": "off"}
    monkeypatch.setattr(followups, "_path", lambda **kwargs: str(env.home / "follow_ups.json"))
    with clock.frozen("2026-10-06"):
        assert "腾讯" in enabled.voice_prompt_block()
        assert "腾讯" not in disabled.voice_prompt_block()
        assert "腾讯" in enabled.voice_prompt_block()  # No profile-setting leakage.
        followups.switch(False, by="test")
        assert "腾讯" not in enabled.voice_prompt_block()
        followups.switch(True)
        assert "腾讯" in enabled.voice_prompt_block()


def test_forget_restore_updates_voice_context(env):
    from nexsandglass.core import erasure
    p = _provider()
    mid = _say(p, "2026-10-01", "我对花生过敏。")
    assert "花生" in p.voice_prompt_block()
    report = erasure.forget({"mem_id": mid}, apply=True)
    assert report["ok"], report
    assert "花生" not in p.voice_prompt_block()
    assert erasure.restore(mid)["ok"]
    assert "花生" in p.voice_prompt_block()


def test_voice_read_does_not_write_journal_recall_sync_or_llm(env, monkeypatch):
    p = _provider()
    _say(p, "2026-10-01", "我对花生过敏。")
    before = Path(env.journal).read_bytes()
    from nexsandglass.core import sandglass_log
    for name in ("system_prompt_block", "prefetch", "sync_turn", "_warm_async"):
        monkeypatch.setattr(p, name, Mock(side_effect=AssertionError(name + " called")))
    monkeypatch.setattr(sandglass_log, "log_message", Mock(side_effect=AssertionError("write called")))
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", Mock(side_effect=AssertionError("network called")))
    assert "花生" in p.voice_prompt_block()
    assert Path(env.journal).read_bytes() == before


def test_empty_context_has_no_ready_or_tool_boilerplate(env):
    assert _provider().voice_prompt_block() == ""


def test_voice_context_uses_only_complete_lines(env, monkeypatch):
    lines = ["[About the user]", "Health and diet:", "- [2026-10-01] Avoid peanuts.",
             "- [2026-10-02] This is important: do not serve shrimp."]
    monkeypatch.setattr(profile, "render", lambda **kwargs: "\n".join(lines))
    monkeypatch.setattr(followups, "render", lambda **kwargs: "")
    p = _provider()
    limit = len("\n".join(lines[:3])) + 10
    assert p.voice_prompt_block(max_chars=limit) == "\n".join(lines[:3])
    assert p.voice_prompt_block(max_chars=20) == ""
    assert p.voice_prompt_block(max_chars=0) == ""


def test_default_bound_cannot_be_expanded(env, monkeypatch):
    monkeypatch.setattr(profile, "render", lambda **kwargs: "[About the user]\n" + "\n".join("- " + "x" * 100 for _ in range(80)))
    monkeypatch.setattr(followups, "render", lambda **kwargs: "")
    result = _provider().voice_prompt_block(max_chars=100000)
    assert len(result) <= 4000
    assert result.splitlines()[-1] == "- " + "x" * 100


@pytest.mark.parametrize("source", ["profile", "followups"])
def test_one_failed_source_does_not_hide_the_other_or_log_details(env, monkeypatch, caplog, source):
    marker = "private-marker-in-error"
    def failure(**kwargs):
        raise RuntimeError(marker)
    monkeypatch.setattr(profile, "render", failure if source == "profile" else lambda **kwargs: "[Profile]\n- owner fact")
    monkeypatch.setattr(followups, "render", failure if source == "followups" else lambda **kwargs: "[Followups]\n- due event")
    assert _provider().voice_prompt_block()
    assert marker not in caplog.text


def test_mismatched_profile_directory_cannot_read_voice_context(env, monkeypatch):
    p = _provider()
    monkeypatch.setattr(p, "unavailable_reason", lambda **kwargs: "different data directory")
    monkeypatch.setattr(profile, "render", Mock(side_effect=AssertionError("must not read")))
    assert p.voice_prompt_block() == ""


@pytest.mark.parametrize("module", [profile, followups])
def test_busy_index_is_explicit_timeout(env, module):
    p = _provider()
    module._REFRESH_LOCK.acquire()
    try:
        with pytest.raises(TimeoutError):
            p.voice_prompt_block(ready_timeout=0.02)
    finally:
        module._REFRESH_LOCK.release()
    assert isinstance(p.voice_prompt_block(), str)


def test_cold_backlog_includes_late_preference_correction(env):
    from nexsandglass.core import memid
    p = _provider()
    _say(p, "2026-10-01", "以后回复我用中文。")
    for i in range(2005):
        memid.allocate(f"普通记录 {i}", sender="user")
    _say(p, "2026-10-03", "以后回复我用英文。")
    block = p.voice_prompt_block()
    assert "用英文" in block and "用中文" not in block
