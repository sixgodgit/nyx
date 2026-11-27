"""LANG-04 lexical equivalence, real evidence gates and optional fusion."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import time
from types import SimpleNamespace

import pytest

from nexsandglass.language.query import plan
from nexsandglass.core import memid, semantic, relevance
from nexsandglass.core.language_search import AliasSearch
from nexsandglass.core.search_router import SearchRouter
from nexsandglass.runtime import StoreContext, RecallRequest, observe, recall_request, forget, restore
from tests.test_hermes_provider import env, _provider  # noqa: F401


@pytest.mark.parametrize("query,text", [
    ("Am I allergic to peanuts?", "我对花生过敏。"),
    ("我喜欢红茶吗？", "I no longer like black tea."),
    ("我对贝类过敏吗？", "I am allergic to shellfish."),
    ("Where do I live?", "我住在阿尔托。"),
    ("我对什么过敏？", "I am allergic to cashews."),
    ("我提过咖啡吗？", "I do not like coffee."),
    ("What did I say about peanuts and my daughter?", "我对花生过敏。"),
    ("What did I say about peanuts and my daughter?", "我女儿喜欢花生。"),
])
def test_exact_lexical_equivalence(query, text):
    assert plan(query).matches(text)


@pytest.mark.parametrize("query,text", [
    ("Am I allergic to milk?", "我对乳糖不耐受。"),
    ("我对花生过敏吗？", "I am allergic to peanut oil."),
    ("Am I allergic to peanuts?", "我对生花生过敏。"),
    ("我喜欢茶吗？", "I like black tea."),
    ("我喜欢红茶吗？", "I like green tea."),
    ("我对贝类过敏吗？", "I am allergic to fish."),
    ("我提过茶吗？", "The steam engine broke."),
    ("Do I like black tea?", "My daughter likes black tea."),
    ("我女儿喜欢花生吗？", "I like peanuts."),
    ("Am I allergic to peanuts?", "我喜欢花生。"),
    ("我提过虾吗？", "小龙虾在河里生活。"),
    ("我对鱼过敏吗？", "我对金鱼过敏。"),
])
def test_no_alias_match_from_conflicting_types_scopes_or_modifiers(query, text):
    assert not plan(query).matches(text)


@pytest.mark.parametrize("query,text", [
    ("Am I allergic to milk?", "我对乳糖不耐受。"),
    ("Am I allergic to peanuts?", "我只对花生油过敏。"),
    ("Am I allergic to raw peanuts?", "我对花生过敏。"),
    ("Do I like black tea?", "My daughter likes black tea."),
])
def test_semantic_similarity_cannot_override_explicit_typed_conflicts(query, text):
    assert not plan(query).compatible(text)
    assert not relevance.keep(query, [SimpleNamespace(content=text, vector_score=.99)])


def test_unknown_and_oversized_queries_keep_the_legacy_route():
    assert not plan("quokka checksum").alternatives
    assert not plan("peanuts " * 1000).alternatives
    assert not plan("raw peanuts").alternatives
    assert plan("Where is the backup key?").compatible("备用钥匙在陶瓷碗里。")


def _write(text, source="user"):
    assert observe(text, source=source, force_promote=True).ok
    return next(r["mem_id"] for r in memid.iter_all() if r["text"] == text)


def _packet(query, budget=600, **kwargs):
    return recall_request(RecallRequest(query, token_budget=budget, deadline=time.monotonic() + 3, **kwargs))


def test_no_vector_evidence_source_and_forget_restore(tmp_path):
    with StoreContext(str(tmp_path / "a")).bind():
        text = "我对花生过敏，但是花生油的情况没有确认。"
        mid = _write(text)
        before = memid.get(mid)
        packet = _packet("Am I allergic to peanuts?")
        assert text in packet.text
        assert any(e.source_mem_id == mid for e in packet.evidence)
        assert not semantic.enabled()
        assert not (tmp_path / "a" / "vectors.db").exists()
        assert memid.get(mid) == before
        assert forget({"mem_id": mid})["ok"]
        assert mid not in {e.source_mem_id for e in _packet("Am I allergic to peanuts?").evidence}
        assert restore(mid)["ok"]
        assert text in _packet("Am I allergic to peanuts?").text


def test_single_character_chinese_alias_uses_existing_bigram_index(tmp_path):
    with StoreContext(str(tmp_path / "a")).bind():
        mid = _write("我女儿喜欢虾。")
        packet = _packet("What did I say about shrimp and my daughter?")
        assert "我女儿喜欢虾。" in packet.text
        assert any(e.source_mem_id == mid for e in packet.evidence)


def test_full_source_budgets_and_expired_requests(tmp_path):
    with StoreContext(str(tmp_path / "a")).bind():
        text = "我对花生过敏。" + "详细病史尚待医生确认。" * 90
        mid = _write(text)
        packet = _packet("Am I allergic to peanuts?", 15)
        assert text not in packet.text
        assert mid not in {e.source_mem_id for e in packet.evidence}
        expired = recall_request(RecallRequest("Am I allergic to peanuts?", deadline=time.monotonic()-1))
        assert not expired.evidence and expired.degraded


def test_alias_threads_keep_store_scope_and_ignore_request_paths(tmp_path):
    stores = [StoreContext(str(tmp_path / k), k) for k in ("a", "b")]
    texts = ["我对花生过敏。a", "I am allergic to peanuts. b"]
    for store, text in zip(stores, texts):
        with store.bind():
            _write(text)
    def read(i):
        with stores[i].bind():
            query = "Am I allergic to peanuts?" if i == 0 else "我对花生过敏吗？"
            packet = _packet(query, session_id=stores[1-i].data_dir, task_id="../../other")
            assert texts[i] in packet.text
            assert texts[1-i] not in packet.text
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(read, [0, 1, 1, 0, 0, 1]))


def test_alias_candidates_do_not_promote_external_or_tainted_sources(tmp_path):
    from nexsandglass.core import provenance
    with StoreContext(str(tmp_path / "a")).bind():
        mid = _write("我对花生过敏。", "tool")
        packet = _packet("Am I allergic to peanuts?")
        assert "unverified" in packet.text and "花生" in packet.text
        assert all(e.trust_signal != "trusted" for e in packet.evidence if e.source_mem_id == mid)
        c = memid.get_conn()
        c.execute("UPDATE provenance SET signal=? WHERE mem_id=?", (provenance.TAINTED, mid))
        c.commit()
        assert "花生" not in _packet("Am I allergic to peanuts?").text


def test_alias_failure_keeps_exact_lexical_search(tmp_path):
    class Broken:
        __slots__ = ()
        def search(self, query, limit):
            raise OSError("private backend detail")
    with StoreContext(str(tmp_path / "a")).bind():
        text = "A checksum marker-n42 belongs to this record."
        _write(text)
        assert any(text in r[2] for r in SearchRouter(language_search=Broken(), vector=None).search("marker-n42"))


def test_vector_conflicts_are_removed_before_fusion(tmp_path):
    with StoreContext(str(tmp_path / "a")).bind():
        mid = _write("我只对花生油过敏。")
        from nexsandglass.core.sandglass_sqlite import sync_incremental
        sync_incremental()
        line = memid.get(mid)["line_start"]
        vector = SimpleNamespace(search=lambda q, n: [("bad-id", 1), (line, .99)])
        router = SearchRouter(vector=vector)
        assert not router._vector_search_wrapper("Am I allergic to peanuts?", 10)


def test_stale_postings_cannot_return_replaced_canonical_text(tmp_path):
    with StoreContext(str(tmp_path / "a")).bind():
        mid = _write("我对花生过敏。")
        search = AliasSearch()
        assert search.search("Am I allergic to peanuts?")
        c = memid.get_conn()
        c.execute("UPDATE memories SET text='我对牛奶过敏。' WHERE mem_id=?", (mid,))
        c.commit()
        assert not search.search("Am I allergic to peanuts?")


@pytest.mark.parametrize("mutation", ["deleted", "ambiguous"])
def test_stale_postings_require_one_live_canonical_binding(tmp_path, mutation):
    with StoreContext(str(tmp_path / "a")).bind():
        mid = _write("我对花生过敏。")
        assert AliasSearch().search("Am I allergic to peanuts?")
        c = memid.get_conn()
        if mutation == "deleted":
            c.execute("UPDATE memories SET deleted_at='now' WHERE mem_id=?", (mid,))
        else:
            other = _write("普通记录。")
            c.execute("UPDATE memories SET line_start=? WHERE mem_id=?", (memid.get(mid)["line_start"], other))
        c.commit()
        assert not AliasSearch().search("Am I allergic to peanuts?")


def test_bounded_alias_search_reports_incomplete_candidates(tmp_path, monkeypatch):
    from nexsandglass.core import language_search
    with StoreContext(str(tmp_path / "a")).bind():
        _write("我对花生过敏。")
        monkeypatch.setattr(language_search, "MAX_POSTINGS", 0)
        search = AliasSearch()
        assert search.search("Am I allergic to peanuts?") == []
        assert search.status == "bounded"


def test_provider_cross_language_prefetch_preserves_complete_uncertainty(env):
    provider = _provider()
    text = "我对花生过敏，但花生油的情况尚未确认，需要先问医生。"
    provider.sync_turn(text, "收到。")
    context = provider.prefetch("Am I allergic to peanuts?")
    assert text in context
    trace = provider.context_trace("hermes_prefetch")
    assert trace["evidence_ids"] and trace["sent"] is None and trace["applied"] is None
