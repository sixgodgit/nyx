"""Language context is optional correlation metadata, never authority."""
from dataclasses import FrozenInstanceError, replace
import json

import pytest

from nexsandglass.runtime.contracts import ContextPacket, LanguageContext, RecallRequest, packet_for


def test_old_requests_and_packet_exports_keep_the_original_shape():
    request = RecallRequest("cup")
    packet = packet_for(request, text="cup", est_tokens=1, status="ready")
    assert request.language is None and packet.language is None
    assert packet.can_apply_to(request)
    assert "language" not in packet.to_dict()
    assert packet.schema_version == 1


@pytest.mark.parametrize("language", ["unknown", "mixed", "en", "zh", "zh-Hant-TW", "EN-us"])
def test_input_language_does_not_create_a_response_preference(language, monkeypatch):
    monkeypatch.setenv("NYX_LANG", "zh")
    context = LanguageContext(input_language=language, input_basis="detected")
    assert context.input_language == language.lower()
    assert context.response_language is None and context.response_basis == "unspecified"
    from nexsandglass.core import settings
    assert settings.get("NYX_LANG") == "zh"


def test_explicit_response_preference_survives_changed_input_language():
    language = LanguageContext(input_language="en", input_basis="detected",
                               response_language="zh-Hans", response_basis="user")
    changed = replace(language, input_language="mixed")
    assert changed.response_language == "zh-hans" and changed.response_basis == "user"
    with pytest.raises(FrozenInstanceError):
        language.response_language = "en"


@pytest.mark.parametrize("values", [
    {"input_language": None}, {"input_language": ""},
    {"input_language": "en_US"}, {"input_language": "中文"},
    {"input_language": "en\nprivate query"}, {"input_language": "a" * 129},
    {"input_language": "auto"}, {"input_language": ["en", "zh"]},
    {"input_basis": "private quote from a conversation"}, {"input_basis": {}},
    {"response_language": "en"},
    {"response_language": "en", "response_basis": "detected"},
    {"response_basis": "user"},
    {"response_language": "mixed", "response_basis": "user"},
    {"response_language": "unknown", "response_basis": "configuration"},
    {"locale": "en_US.UTF-8"}, {"locale": "en US"}, {"locale": "mixed"},
    {"timezone": True}, {"timezone": ""}, {"timezone": "private\nquery"},
    {"timezone": "Not/A_Timezone"}, {"timezone": "../../etc/passwd"},
    {"timezone": "/etc/passwd"},
])
def test_invalid_or_free_text_metadata_is_rejected(values):
    with pytest.raises((TypeError, ValueError)):
        LanguageContext(**values)


@pytest.mark.parametrize("zone", ["UTC", "Europe/Berlin", "Asia/Shanghai", "America/New_York"])
def test_host_locale_and_timezone_are_independent_of_the_input_language(zone):
    language = LanguageContext(input_language="en", input_basis="detected", locale="zh-CN", timezone=zone)
    assert language.input_language == "en" and language.locale == "zh-cn"
    assert language.timezone == zone
    assert language.response_language is None


@pytest.mark.parametrize("target", [RecallRequest, ContextPacket])
@pytest.mark.parametrize("invalid", [{"data_dir": "/other-store"}, "en", True, ["en"]])
def test_requests_and_packets_require_the_explicit_typed_context(target, invalid):
    args = {"query": "cup"} if target is RecallRequest else {"request_id": "request"}
    with pytest.raises(TypeError):
        target(**args, language=invalid)


def test_all_language_metadata_round_trips_without_query_or_free_text():
    language = LanguageContext(input_language="mixed", input_basis="detected",
                               response_language="zh", response_basis="configuration",
                               locale="en-GB", timezone="Europe/Berlin")
    request = RecallRequest("private-query-marker", context="private-task-marker", language=language)
    packet = packet_for(request, status="ready")
    assert packet.language is language and packet.can_apply_to(request)
    exported = packet.to_dict()
    assert exported["language"] == language.to_dict()
    serialized = json.dumps(exported)
    assert "private-query-marker" not in serialized and "private-task-marker" not in serialized
    exported["language"]["timezone"] = "Asia/Shanghai"
    assert packet.language.timezone == "Europe/Berlin"


@pytest.mark.parametrize("changes", [
    {"input_language": "zh"}, {"input_basis": "configuration"},
    {"response_language": "zh", "response_basis": "user"},
    {"locale": "en-GB"}, {"timezone": "Asia/Shanghai"},
])
def test_changed_language_context_invalidates_an_old_result_even_with_same_ids(changes):
    language = LanguageContext(input_language="en", input_basis="detected")
    request = RecallRequest("cup", session_id="s", turn_id="t", language=language)
    packet = packet_for(request, status="ready")
    assert packet.can_apply_to(request)
    newer = replace(request, language=replace(language, **changes))
    assert not packet.matches_request(newer) and not packet.can_apply_to(newer)
    assert not replace(packet, language=None).can_apply_to(request)


def test_region_tag_case_is_normalized_without_mutating_request_identity():
    request = RecallRequest("cup", language=LanguageContext(locale="EN-us"))
    packet = packet_for(request, status="ready")
    assert packet.can_apply_to(replace(request, language=LanguageContext(locale="en-US")))


def test_language_basis_cannot_grant_store_or_instruction_permissions():
    with pytest.raises(TypeError):
        LanguageContext(input_language="en", data_dir="/foreign")
    context = LanguageContext(input_language="en", input_basis="user",
                              response_language="en", response_basis="user")
    assert not any(hasattr(context, name) for name in ("data_dir", "principal", "may_instruct", "store"))


def test_metadata_reaches_the_real_typed_evidence_path_without_changing_trust(hub, monkeypatch):
    from nexsandglass.engram.types import MemoryObject
    from tests.test_m1_contracts import _orch
    memid, _ = hub
    mid = memid.allocate("The cobalt cup is on the shelf.", sender="tool")
    row = memid.get(mid)
    obj = MemoryObject(memory_id="search_router:" + str(row["line_start"]), content=row["text"],
                       source_id=str(row["line_start"]), provenance="search_router")
    orch = _orch()
    monkeypatch.setattr(orch.recall_planner, "_adapt_search_router", lambda _: [obj])
    request = RecallRequest("cup")
    original = orch.recall_request(request)
    language = LanguageContext(input_language="mixed", input_basis="user",
                               response_language="zh", response_basis="user", timezone="Europe/Berlin")
    localized = orch.recall_request(replace(request, language=language))
    assert localized.language == language
    assert localized.evidence == original.evidence and localized.text == original.text
    assert localized.evidence[0].source_mem_id == mid
    assert localized.evidence[0].origin == "tool" and localized.evidence[0].trust_signal == "unverified"


# Reuse the actual isolated canonical store fixture, not a second implementation.
from tests.test_memid import hub  # noqa: E402,F401
