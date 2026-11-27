"""LANG-05: display framing does not rewrite evidence, facts or authority."""
from dataclasses import replace
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import time

import pytest

from nexsandglass.runtime import (
    ContextItem, ContextSection, EvidenceRef, LanguageContext, RecallRequest, StoreContext,
    DisplayTranslation, PresentationOptions, present_fact, recall_presented, observe, forget, restore,
)
from nexsandglass.runtime.host_context import render_host_context
from nexsandglass.runtime.presentation import presentation_for, unit_digest
from nexsandglass.core import memid, provenance, settings
from nexsandglass.core.understand import Fact
from tests.test_hermes_provider import env, _provider  # noqa: F401

CASES = json.loads((Path(__file__).resolve().parents[1] / "benchmarks/fixtures/language_presentation_v1.json").read_text())["cases"]


def _sections(text="I do not like black tea.", *, required=False):
    ref = EvidenceRef("e1", source_mem_id="m1", origin="tool", trust_signal="unverified")
    return (ContextSection("retrieval", "## Nyx memory (relevant to this turn; [date] = when it was said)",
                           (ContextItem("a", text, required=required, evidence=(ref,)),)),)


def _translation(text, target="zh", **kwargs):
    return DisplayTranslation("retrieval", "a", ("m1",), unit_digest(text), target,
                              kwargs.pop("translation", "我不再喜欢红茶。"), **kwargs)


def _request(query="tea", target="zh", **kwargs):
    return RecallRequest(query, language=LanguageContext(response_language=target, response_basis="user"), **kwargs)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_frozen_frame_contract_keeps_original_source(tmp_path, case):
    with StoreContext(str(tmp_path / "store")).bind():
        mid = memid.allocate(case["quote"], sender=case["source"])
        original = memid.get(mid)
        prov = provenance.get(mid)
        ref = EvidenceRef(mid, mid, origin=prov.origin, trust_signal=prov.signal)
        language = LanguageContext(input_language=case["input_language"],
                                   response_language=case["response_language"],
                                   response_basis="user" if case["response_language"] else "unspecified")
        request = RecallRequest("same query", language=language)
        section = ContextSection("retrieval", items=(ContextItem("a", prov.label()+case["quote"], evidence=(ref,)),))
        packet, trace = render_host_context(request, (section,), presentation=presentation_for(language))
        assert case["quote"] in packet.text and packet.evidence == (ref,)
        assert packet.matches_request(request)
        assert memid.get(mid) == original
        if case["frame_language"] is None:
            assert "presentation" not in trace.to_dict()
        else:
            assert trace.presentation.frame_language == case["frame_language"]
            assert trace.presentation.requested_language == case["response_language"]
        if case["source"] == "tool":
            assert packet.evidence[0].trust_signal == "unverified" and "unverified" in packet.text


def test_default_export_and_explicit_metadata_remain_legacy_until_opt_in():
    section = _sections()
    request = RecallRequest("tea")
    plain, trace = render_host_context(request, section)
    explicit, _ = render_host_context(replace(request, language=LanguageContext(response_language="zh", response_basis="user")), section)
    assert plain.text == explicit.text
    assert "presentation" not in trace.to_dict() and "language" not in plain.to_dict()
    assert plain.schema_version == explicit.schema_version == 1


def test_host_translation_is_bound_paired_and_never_mutates_inputs():
    source = "I do not like black tea.\nMy doctor has not assessed peanuts."
    sections = _sections(source)
    translation = _translation(source)
    request = _request(token_budget=1000)
    packet, trace = render_host_context(request, sections, presentation=PresentationOptions("zh", (translation,)))
    assert source in packet.text and translation.text in packet.text
    assert "未经核验" in packet.text
    assert packet.evidence == sections[0].items[0].evidence
    assert sections[0].items[0].text == source
    assert trace.presentation.translations_used == 1
    exported_trace = json.dumps(trace.to_dict())
    assert source not in exported_trace and translation.text not in exported_trace
    assert trace.text_sha256 == sha256(packet.text.encode()).hexdigest()


@pytest.mark.parametrize("change", [
    {"source_sha256": "0"*64}, {"source_ids": ("other",)},
    {"target_language": "en"}, {"section_key": "other"}, {"item_key": "other"},
])
def test_stale_foreign_or_wrong_language_sidecars_are_not_used(change):
    source = "I do not like black tea."
    translation = replace(_translation(source), **change)
    packet, trace = render_host_context(_request(), _sections(source),
                                       presentation=PresentationOptions("zh", (translation,)))
    assert source in packet.text and translation.text not in packet.text
    assert trace.presentation.translations_used == 0
    assert trace.presentation.translations_omitted == 1
    assert "translation_unavailable" in packet.reason_codes


def test_translation_cannot_displace_any_admitted_original():
    sections = _sections()
    second = ContextItem("b", "The green cup remains on the top shelf.", evidence=(EvidenceRef("e2", "m2"),))
    sections = (replace(sections[0], items=sections[0].items+(second,)),)
    options = PresentationOptions("zh")
    request = _request(token_budget=220)
    base, _ = render_host_context(request, sections, presentation=options)
    translation = _translation(sections[0].items[0].text, translation="很长的译文。"*1000)
    packet, trace = render_host_context(request, sections, presentation=replace(options, translations=(translation,)))
    assert packet.text == base.text and packet.evidence == base.evidence
    assert trace.presentation.translations_used == 0


def test_multiple_translations_keep_their_own_quotes_and_citations():
    first, second = "I do not like black tea.", "The red mug may be in the cupboard."
    sections = _sections(first)
    ref = EvidenceRef("e2", "m2", origin="tool", trust_signal="unverified")
    sections = (replace(sections[0], items=sections[0].items+(ContextItem("b", second, evidence=(ref,)),)),)
    translations = (DisplayTranslation("retrieval", "b", ("m2",), unit_digest(second), "zh", "红杯子可能在柜子里。"),
                    _translation(first))
    packet, trace = render_host_context(_request(), sections, presentation=PresentationOptions("zh", translations))
    assert first in packet.text and second in packet.text
    assert all(t.text in packet.text for t in translations)
    assert {r.source_mem_id for r in packet.evidence} == {"m1", "m2"}
    assert trace.presentation.translations_used == 2


def test_presentation_preamble_is_charged_and_never_returned_without_evidence():
    sections = _sections(required=True)
    request = _request(token_budget=5)
    packet, trace = render_host_context(request, sections, presentation=PresentationOptions("zh"))
    assert packet.text == "" and not packet.evidence and packet.status == "error"
    assert trace.required_omitted
    empty, _ = render_host_context(_request(), (), presentation=PresentationOptions("zh"))
    assert empty.text == "" and empty.status == "empty"


def test_character_cap_and_expired_request_drop_translation_and_source_together():
    text = "I do not like black tea."
    options = PresentationOptions("zh", (_translation(text),))
    packet, trace = render_host_context(_request(), _sections(text), presentation=options, max_chars=1)
    assert not packet.text and not packet.evidence and trace.presentation.translations_used == 0
    expired, trace = render_host_context(_request(deadline=time.monotonic()-1), _sections(text), presentation=options)
    assert expired.status == "expired" and not expired.text
    assert trace.presentation.translations_used == 0


def test_expiry_during_presentation_does_not_report_a_translation_as_used(monkeypatch):
    from nexsandglass.runtime import host_context
    source = _sections()[0].items[0].text
    options = PresentationOptions("zh", (_translation(source),))
    request = _request(deadline=1)
    values = iter((0, 2))
    with monkeypatch.context() as patch:
        patch.setattr(host_context.time, "monotonic", lambda: next(values))
        packet, trace = render_host_context(request, _sections(source), presentation=options)
    assert packet.status == "expired" and packet.text == "" and not packet.evidence
    assert trace.selected_count == 0 and trace.presentation.translations_used == 0


@pytest.mark.parametrize("language", [None, "en"])
def test_presentation_language_must_be_correlated_with_the_request(language):
    request = RecallRequest("tea") if language is None else _request(target=language)
    with pytest.raises(ValueError):
        render_host_context(request, _sections(), presentation=PresentationOptions("zh"))


@pytest.mark.parametrize("tag", ["auto", "mixed", "unknown", "zh\nprivate", "", None])
def test_free_text_and_special_input_tags_are_not_presentation_languages(tag):
    with pytest.raises((ValueError, TypeError)):
        PresentationOptions(tag)


def test_unsupported_frame_is_explicit_and_never_translates_source():
    packet, trace = render_host_context(_request(target="zh-Hant"), _sections(), presentation=PresentationOptions("zh-Hant"))
    assert trace.presentation.frame_language == "en"
    assert "presentation_language_fallback" in packet.reason_codes
    assert _sections()[0].items[0].text in packet.text


def test_translation_requires_citation_and_unambiguous_target():
    translation = _translation("source")
    with pytest.raises(ValueError):
        PresentationOptions("zh", (translation, translation))
    with pytest.raises(ValueError):
        replace(translation, source_ids=())
    with pytest.raises(ValueError):
        replace(translation, source_sha256="not-a-hash")
    with pytest.raises(ValueError):
        presentation_for(None, translations=(translation,))
    sections = (ContextSection("retrieval", items=(ContextItem("a", "source"),)),)
    packet, trace = render_host_context(_request(), sections, presentation=PresentationOptions("zh", (translation,)))
    assert translation.text not in packet.text and trace.presentation.translations_used == 0


def test_fact_display_localizes_only_presentation_and_retains_polarity_and_time():
    fact = Fact("user", "偏好", "black tea", evidence="I no longer like black tea.", polarity=-1,
                valid_from=datetime(2026, 3, 1), ongoing=False)
    source = fact.to_dict()
    en, zh = present_fact(fact, language="en"), present_fact(fact, language="zh")
    assert en.to_dict()["source"] == zh.to_dict()["source"] == source == fact.to_dict()
    assert source["relation"] == "not:偏好" and source["polarity"] == -1
    assert "does not like" in en.display_text and "不喜欢" in zh.display_text
    assert "2026-03-01" in zh.display_text and "false" in zh.display_text
    assert fact.evidence in en.display_text and fact.evidence in zh.display_text
    en.to_dict()["source"]["object"] = "changed"
    assert en.to_dict()["source"]["object"] == "black tea"


def test_deferred_fact_display_does_not_imply_a_current_belief():
    fact = Fact("user", "住在", "Paris", deferred=True, assertion="conditional", evidence="If I live in Paris...")
    view = present_fact(fact, language="zh")
    assert "暂缓" in view.display_text and view.to_dict()["presentation"]["assertion"] == "conditional"
    assert fact.evidence in view.display_text


def test_provider_response_preference_is_independent_of_interface_and_input(env, monkeypatch):
    provider = _provider()
    provider._profile_env = {"NYX_RESPONSE_LANGUAGE": "zh", "NYX_LANG": "en"}
    provider.sync_turn("I am allergic to peanuts. The assessment is not complete.", "")
    before = [(r["mem_id"], r["text"]) for r in memid.iter_all()]
    context = provider.voice_prompt_block()
    assert "首选回答语言：zh" in context
    assert "I am allergic to peanuts. The assessment is not complete." in context
    assert "[关于用户" in context
    assert provider.context_trace("conduit_voice")["presentation"]["requested_language"] == "zh"
    assert [(r["mem_id"], r["text"]) for r in memid.iter_all()] == before
    with provider._settings_scope():
        assert settings.get("NYX_LANG") == "en"


def test_rendered_recall_is_source_scoped_and_respects_forget_restore(tmp_path):
    with StoreContext(str(tmp_path / "a")).bind():
        text = "我对花生过敏，其他食物的情况还没有确认。"
        assert observe(text, source="user", force_promote=True).ok
        mid = next(r["mem_id"] for r in memid.iter_all() if r["text"] == text)
        request = RecallRequest("Am I allergic to peanuts?", language=LanguageContext(input_language="en", response_language="zh", response_basis="user"))
        packet = recall_presented(request)
        assert text in packet.text and "首选回答语言：zh" in packet.text
        assert any(e.source_mem_id == mid for e in packet.evidence)
        assert forget({"mem_id": mid})["ok"]
        assert text not in recall_presented(request).text
        assert restore(mid)["ok"] and text in recall_presented(request).text


def test_invalid_preference_is_rejected_before_persistent_config_changes(tmp_path):
    from nexsandglass.core.memory_provider import NexSandglassProvider
    import nexsandglass_hermes as nh
    home = str(tmp_path / "hermes")
    nh.save_config({"response_language": "en"}, home)
    before = nh.load_config(home)
    with pytest.raises(ValueError):
        NexSandglassProvider().save_config({"response_language": "zh\nfree-text-instruction"}, home)
    assert nh.load_config(home) == before


def test_reply_preference_does_not_localize_fact_tool_protocol(env):
    provider = _provider()
    provider.sync_turn("I do not like black tea.", "")
    provider._profile_env = {"NYX_RESPONSE_LANGUAGE": "zh"}
    chinese_frame = json.loads(provider.handle_tool_call("nyx_belief", {"relation": "likes"}))
    provider._profile_env = {"NYX_RESPONSE_LANGUAGE": "en"}
    english_frame = json.loads(provider.handle_tool_call("nyx_belief", {"relation": "likes"}))
    assert chinese_frame == english_frame
    assert chinese_frame["facts"][0]["relation"] == "not:likes"
    assert chinese_frame["facts"][0]["polarity"] == -1


def test_real_parallel_profiles_do_not_share_display_preferences_or_sources(tmp_path, monkeypatch):
    from nexsandglass.core.memory_provider import NexSandglassProvider
    import nexsandglass_hermes as nh
    monkeypatch.delenv("NEXSANDBASE_HOME", raising=False)
    providers = []
    for name, target, text in (("a", "zh", "I am allergic to peanuts."), ("b", "en", "我对腰果过敏。")):
        home = str(tmp_path / (name+"-hermes"))
        nh.save_config({"data_dir": str(tmp_path / name), "response_language": target}, home)
        provider = NexSandglassProvider()
        provider.initialize(name, hermes_home=home, agent_context="primary")
        if getattr(provider, "_warm_thread", None):
            provider._warm_thread.join(10)
        provider.sync_turn(text, "")
        providers.append(provider)
    def read(index):
        text = providers[index].voice_prompt_block()
        assert ("首选回答语言：zh" if index == 0 else "Preferred answer language: en") in text
        assert ("peanuts" if index == 0 else "腰果") in text
        assert ("腰果" if index == 0 else "peanuts") not in text
        return text
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(read, [0, 1, 1, 0]))
    finally:
        for provider in providers:
            provider.shutdown()
