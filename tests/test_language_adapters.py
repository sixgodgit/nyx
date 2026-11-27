"""Typed aliases, conservative scope, legacy storage and real host evidence."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import time

import pytest

from nexsandglass.language import analyze, entity_key, canonical_predicate, fact_key
from nexsandglass.core import clock, memid, profile, understand
from nexsandglass.runtime import StoreContext, observe, recall_request, RecallRequest, forget, restore


@pytest.mark.parametrize("kind,a,b", [
    ("allergen", "花生", "PEANUTS"), ("allergen", "蝦", "prawns"),
    ("allergen", "腰果", "cashews"), ("allergen", "杏仁", "almonds"),
    ("allergen", "牛奶", "milk"), ("allergen", "乳糖", "lactose"),
    ("preference", "红茶", "black tea"), ("preference", "綠茶", "Green Tea"),
    ("preference", "跑步", "running"), ("vehicle", "特斯拉", "Tesla"),
])
def test_exact_typed_aliases(kind, a, b):
    assert entity_key(a, kind) == entity_key(b, kind)
    assert not entity_key(a, kind).startswith("literal:")


@pytest.mark.parametrize("a,b", [
    ("peanuts", "tree nuts"), ("milk", "lactose"), ("fish", "shellfish"),
    ("wheat", "gluten"), ("almonds", "peanuts"), ("raw peanuts", "peanuts"),
    ("peanut butter", "peanuts"), ("peanut oil", "peanuts"),
])
def test_allergen_categories_and_qualifiers_are_never_guessed_equivalent(a, b):
    assert entity_key(a, "allergen") != entity_key(b, "allergen")


def test_entity_domain_and_subject_are_independent():
    assert entity_key("Tesla", "vehicle") != entity_key("Tesla", "allergen")
    assert entity_key("black tea", "preference") != entity_key("tea", "preference")
    assert fact_key("user", "偏好", "红茶") == fact_key("user", "likes", "black tea")
    assert fact_key("user", "偏好", "红茶") != fact_key("user's daughter", "likes", "black tea")
    assert fact_key("user", "偏好", "红茶") != fact_key("user", "dislikes", "black tea")
    assert "private-value" not in repr(fact_key("private-subject", "custom-private", "private-value"))
    assert "private-subject" not in repr(fact_key("private-subject", "custom-private", "private-value"))


@pytest.mark.parametrize("relation,expected", [
    ("住在", "lives_in"), ("LIVES_IN", "lives_in"),
    ("使用", "uses_vehicle"), ("uses", "uses_vehicle"), ("uses_vehicle", "uses_vehicle"),
    ("邮箱", "email"), ("employer", "employer"),
])
def test_predicates_keep_legacy_spelling_separate(relation, expected):
    assert canonical_predicate(relation) == expected
    assert understand.relation_key(relation) in understand.ONTOLOGY


def test_adapter_preserves_original_facts_and_export_shape():
    text = "我住在上海，我喜欢红茶。"
    result = analyze(text, now=datetime(2026, 1, 1))
    assert result.status == "matched"
    assert any(k.predicate == "lives_in" for k in result.keys)
    assert any(k.object == "drink.black_tea" for k in result.keys)
    for fact, key in zip(result.facts, result.keys):
        assert fact.evidence in text and fact.object in text
        assert fact.semantic_key == key
        assert "semantic_key" not in fact.to_dict()  # additive property, legacy export unchanged
    assert analyze("ordinary chat with no factual claim").status == "no_match"
    assert analyze("Je vis à Lyon.", language="fr-FR").status == "unsupported_language"
    assert not understand.extract_rules("I live in London.", language="fr")


def test_explicit_adapter_can_be_supplied_without_global_registration():
    class Adapter:
        name = "test-fr"
        languages = ("fr",)
        def extract_facts(self, text, *, now, subject):
            return [understand.Fact(subject, "住在", "Lyon", evidence=text)]
    original = "Je vis à Lyon."
    result = analyze(original, subject="reader", language="fr", adapter=Adapter())
    assert result.status == "matched" and result.keys[0].subject == "reader"
    assert result.facts[0].object == "Lyon" and result.facts[0].evidence == original
    assert analyze(original, language="fr").status == "unsupported_language"


@pytest.mark.parametrize("kind,a,b", [
    ("health", "我对花生过敏。", "I am allergic to peanuts."),
    ("likes", "我喜欢喝红茶。", "I like black tea."),
    ("likes", "我不再喜欢跑步了。", "I no longer enjoy running."),
    ("instruction", "以后回复用中文。", "Please always reply in Chinese."),
])
def test_profile_topics_are_language_neutral(kind, a, b):
    assert profile.slots(kind, a) and profile.slots(kind, a) == profile.slots(kind, b)


def _write(text, when="2026-10-01", source="user"):
    with clock.frozen(when):
        report = observe(text, source=source, force_promote=True)
        assert report.ok, report
        return next(r["mem_id"] for r in memid.iter_all() if r["text"] == text)


def _current_ids(kind):
    profile.refresh_ready(deadline=time.monotonic() + 5)
    return {row[2] for row in profile.items()[kind]}


def test_real_cross_language_preference_closure_and_restore(tmp_path):
    with StoreContext(str(tmp_path / "store")).bind():
        old = _write("我喜欢喝红茶。")
        recent = _write("I no longer like black tea.", "2026-10-02")
        assert _current_ids("likes") == {recent}
        # Original statements/IDs are unchanged, and both remain searchable.
        assert memid.get(old)["text"] == "我喜欢喝红茶。"
        packet = recall_request(RecallRequest("black tea", token_budget=500))
        assert "I no longer like black tea." in packet.text
        assert recent in {e.source_mem_id for e in packet.evidence}
        assert forget({"mem_id": recent})["ok"]
        assert _current_ids("likes") == {old}
        assert restore(recent)["ok"]
        assert _current_ids("likes") == {recent}


@pytest.mark.parametrize("qualified,later", [
    ("I am allergic to peanuts, but NOT to almonds.", "我对花生过敏。"),
    ("I might be allergic to peanuts.", "我对花生过敏。"),
    ("我对花生过敏。", "I might be allergic to peanuts."),
    ("我对花生过敏。", "I am allergic to peanuts only when raw."),
])
def test_aliases_do_not_erase_qualified_or_uncertain_health_claims(tmp_path, qualified, later):
    with StoreContext(str(tmp_path / "store")).bind():
        a = _write(qualified)
        b = _write(later, "2026-10-02")
        assert {a, b} <= _current_ids("health")
        for row in profile.items()["health"]:
            if row[2] == a:
                assert row[1] == qualified


def test_subject_and_untrusted_claim_cannot_supersede_owner(tmp_path):
    with StoreContext(str(tmp_path / "store")).bind():
        owner = _write("我喜欢喝红茶。")
        _write("My daughter no longer likes black tea.", "2026-10-02")
        _write("I no longer like black tea.", "2026-10-03", source="external")
        assert _current_ids("likes") == {owner}


def test_legacy_graph_rows_are_read_by_semantic_predicate_without_rewrite(tmp_path):
    from nexsandglass.features import weavethread as graph
    from nexsandglass.engram.loops import temporal_fact as tf
    import sqlite3
    with StoreContext(str(tmp_path / "store")).bind(), clock.frozen("2026-01-01"):
        assert graph.wthread_add("user", "uses_vehicle", "Tesla")
        c = sqlite3.connect(graph._db_path())
        c.execute("UPDATE wthread_triples SET relation='uses'")  # existing English spelling
        c.commit()
        before = c.execute("SELECT * FROM wthread_triples").fetchall()
        for alias in ("uses_vehicle", "使用", "uses"):
            assert graph.wthread_query("user", alias)[0]["object"] == "Tesla"
            assert tf.get_current(graph._db_path(), "user", alias)[0]["object"] == "Tesla"
            assert tf.as_of(graph._db_path(), "2026-01-02", subject="user", predicate=alias)
            assert tf.history_of(graph._db_path(), "user", alias)
        assert c.execute("SELECT * FROM wthread_triples").fetchall() == before
        c.close()


def test_alias_views_do_not_cross_stores_and_versioned_rebuild_keeps_ids(tmp_path):
    a, b = StoreContext(str(tmp_path / "a")), StoreContext(str(tmp_path / "b"))
    with a.bind():
        old = _write("我喜欢喝红茶。")
        before = memid.get(old)
        assert _current_ids("likes") == {old}
        c = memid.get_conn()
        c.execute("UPDATE projection_jobs SET processor_version='4' WHERE name='profile'")
        c.commit()
        assert _current_ids("likes") == {old}
        assert memid.get(old) == before
    with b.bind():
        _write("I no longer like black tea.", "2026-10-03")
    with a.bind():
        assert _current_ids("likes") == {old}


def test_frozen_corpus_identity_and_group_split():
    repo = Path(__file__).resolve().parents[1]
    raw = (repo / "benchmarks/fixtures/language_v1.json").read_bytes()
    assert sha256(raw).hexdigest() == "2c8f9a8f2fd39ac81e7febee3fd0db8595792115c4eeae81e752913b6a4efd49"
    data = json.loads(raw)
    splits = {name: {g["group_id"].split("-", 1)[0] for g in data["groups"] if g["split"] == name}
              for name in ("dev", "heldout")}
    assert not splits["dev"] & splits["heldout"]
    assert sum(len(g["cases"]) for g in data["groups"]) == 60
