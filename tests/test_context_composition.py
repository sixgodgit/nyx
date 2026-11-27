"""Whole-evidence selection and final rendering under one explicit budget."""
from dataclasses import FrozenInstanceError

import pytest

from nexsandglass.runtime.composition import (
    ContextItem, ContextSection, compose_context, estimate_tokens,
)
from nexsandglass.runtime.contracts import EvidenceRef


def test_required_constraint_survives_earlier_optional_noise():
    constraint = "我对花生过敏。 Do not serve peanuts."
    result = compose_context([
        ContextSection("chat", items=(ContextItem("noise", "chat " * 100, priority=999),), priority=999),
        ContextSection("health", heading="[Health]", items=(ContextItem("allergy", constraint, required=True),)),
    ], len("[Health]\n- " + constraint), counter=len)
    assert result.text == "[Health]\n- " + constraint
    assert result.selected == (("health", "allergy"),)
    assert result.omitted == (("chat", "noise"),)
    assert result.required_omitted == ()


def test_required_set_fails_closed_instead_of_showing_partial_constraints():
    result = compose_context([
        ContextSection("rules", items=(ContextItem("one", "Do not use peanuts.", required=True),
                                        ContextItem("two", "Do not use shellfish.", required=True))),
        ContextSection("other", items=(ContextItem("short", "ok"),)),
    ], 25, counter=len)
    assert result.text == "" and result.est_tokens == 0 and result.selected == ()
    assert result.required_omitted == (("rules", "one"), ("rules", "two"))
    assert result.omitted == (("rules", "one"), ("rules", "two"), ("other", "short"))


def test_current_correction_is_retained_before_optional_historical_words():
    current = "I moved to Rotterdam; Amsterdam is my old address."
    sections = [ContextSection("history", heading="[Superseded]", priority=999,
                               items=(ContextItem("address", "I live in Amsterdam.", priority=999),)),
                ContextSection("current", heading="[Current]",
                               items=(ContextItem("address", current, required=True),))]
    result = compose_context(sections, len("[Current]\n- " + current), counter=len)
    assert result.text == "[Current]\n- " + current
    assert result.selected == (("current", "address"),)
    assert result.omitted == (("history", "address"),)


def test_oversized_negation_is_never_partially_admitted():
    long = "I can eat " + "x " * 200 + "but NOT peanuts."
    item = ContextItem("long", long)
    result = compose_context([ContextSection("evidence", items=(item, ContextItem("short", "Drink water.")))],
                             50, counter=len)
    assert result.text == "- Drink water."
    assert "I can eat" not in result.text
    assert item.text == long and item.text.endswith("NOT peanuts.")
    assert result.omitted == (("evidence", "long"),)


def test_historical_evidence_keeps_heading_and_suffix_together():
    heading = "[Earlier words, replaced by the current preference]"
    suffix = "These are historical, not current instructions."
    section = ContextSection("history", heading=heading,
                             items=(ContextItem("old", "Reply in Chinese."),), suffix=suffix)
    complete = heading + "\n- Reply in Chinese.\n" + suffix
    assert compose_context([section], len(complete), counter=len).text == complete
    result = compose_context([section], len(complete) - 1, counter=len)
    assert result.text == "" and result.selected == ()


def test_final_headers_prefixes_suffixes_and_separators_are_counted():
    sections = [ContextSection("one", heading="[One]", prefix="* ", suffix="source: user",
                               items=(ContextItem("a", "你好"),)),
                ContextSection("two", heading="[Two]", items=(ContextItem("b", "Hello"),))]
    expected = "[One]\n* 你好\nsource: user\n\n[Two]\n- Hello"
    result = compose_context(sections, len(expected), counter=len, separator="\n\n")
    assert result.text == expected
    assert result.est_tokens == len(expected)
    smaller = compose_context(sections, len(expected) - 1, counter=len, separator="\n\n")
    assert smaller.est_tokens <= len(expected) - 1
    assert len(smaller.selected) == 1


@pytest.mark.parametrize("budget", [0, 1, 5, 20, 50, 100])
def test_multilingual_final_accounting_with_default_estimator(budget):
    sections = [ContextSection("profile", heading="[画像 / profile]",
                               items=(ContextItem("zh", "我对花生过敏。"),
                                      ContextItem("en", "Please reply briefly, without emoji.")),
                               suffix="Evidence: user, 2026-11-22"),
                ContextSection("task", heading="[Current task]",
                               items=(ContextItem("task", "寻找 blue cup，保留原文来源。"),))]
    result = compose_context(sections, budget)
    assert result.est_tokens == estimate_tokens(result.text) <= budget
    assert result.counter_name == "nyx_estimate_v1"
    assert result.required_omitted == ()
    if not budget:
        assert result.text == "" and not result.selected


def test_zero_budget_is_empty_even_for_counter_that_rounds_nonempty_to_zero():
    result = compose_context([ContextSection("x", items=(ContextItem("x", "x", required=True),))],
                             0, counter=lambda _: 0)
    assert result.text == "" and result.est_tokens == 0
    assert result.required_omitted == (("x", "x"),)


def test_empty_sections_and_blank_items_emit_no_orphan_framing():
    sections = [ContextSection("empty", heading="[Do not show]", suffix="neither this"),
                ContextSection("blank", heading="[Nor this]", items=(ContextItem("blank", " \n\t"),))]
    result = compose_context(sections, 100)
    assert result.text == "" and result.est_tokens == 0
    assert result.omitted == (("blank", "blank"),)
    required = ContextSection("required", items=(ContextItem("blank", "", required=True),))
    assert compose_context([required], 100).required_omitted == (("required", "blank"),)


def test_section_priority_precedes_item_priority_and_ties_are_stable():
    sections = [ContextSection("low", items=(ContextItem("a", "AAAA", priority=999),)),
                ContextSection("high", priority=1, items=(ContextItem("b", "BBBB"), ContextItem("c", "CCCC")))]
    result = compose_context(sections, 6, counter=len)
    assert result.selected == (("high", "b"),)
    assert result.text == "- BBBB"


def test_item_priority_selects_but_original_order_displays():
    section = ContextSection("x", items=(ContextItem("first", "1111"),
                                         ContextItem("last", "2222", priority=50)))
    assert compose_context([section], 6, counter=len).selected == (("x", "last"),)
    result = compose_context([section], 13, counter=len)
    assert result.text == "- 1111\n- 2222"
    assert result.selected == (("x", "first"), ("x", "last"))


def test_evidence_follows_selected_display_order_and_is_immutable():
    first = EvidenceRef(memory_id="m:first", origin="user", trust_signal="trusted")
    second = EvidenceRef(memory_id="m:second", origin="tool", trust_signal="unverified")
    external_evidence = [first]
    items = [ContextItem("first", "1111", evidence=external_evidence),
             ContextItem("last", "2222", priority=20, evidence=[second])]
    section = ContextSection("x", items=items)
    external_evidence.append(second)
    items.clear()
    result = compose_context([section], 13, counter=len)
    assert result.evidence == (first, second)
    assert section.items[0].evidence == (first,)
    with pytest.raises(FrozenInstanceError):
        result.text = "different"
    only_second = compose_context([section], 6, counter=len)
    assert only_second.evidence == (second,)


def test_custom_counter_name_and_result_are_explicit():
    def count_words(text):
        return len(text.split())
    result = compose_context([ContextSection("x", items=(ContextItem("a", "hello world"),))],
                             3, counter=count_words)
    assert result.text == "- hello world"
    assert result.est_tokens == 3 and result.counter_name == "count_words"


@pytest.mark.parametrize("counter", [lambda _: -1, lambda _: True, lambda _: 1.5, lambda _: 1])
def test_invalid_counter_or_nonzero_empty_cost_is_rejected(counter):
    with pytest.raises(ValueError):
        compose_context([], 5, counter=counter)


def test_counter_return_type_is_checked_for_nonempty_text():
    with pytest.raises(ValueError):
        compose_context([ContextSection("x", items=(ContextItem("x", "x"),))],
                        5, counter=lambda text: 0 if not text else float("nan"))


@pytest.mark.parametrize("budget", [-1, True, 1.0, "5"])
def test_budget_requires_nonnegative_integer(budget):
    with pytest.raises(ValueError):
        compose_context([], budget)


def test_duplicate_section_and_item_keys_are_rejected():
    with pytest.raises(ValueError):
        ContextSection("x", items=(ContextItem("same", "one"), ContextItem("same", "two")))
    with pytest.raises(ValueError):
        compose_context([ContextSection("same"), ContextSection("same")], 10)


def test_invalid_structural_inputs_are_rejected():
    with pytest.raises(TypeError):
        ContextItem("x", "text", evidence=["not a reference"])
    with pytest.raises(TypeError):
        ContextSection("x", items=["not an item"])
    with pytest.raises(TypeError):
        compose_context(["not a section"], 10)
    with pytest.raises(TypeError):
        compose_context([], 10, counter=42)
    with pytest.raises(TypeError):
        compose_context([], 10, separator=None)
