"""LANG-03: replay frames, explicit ambiguity, assertion truth and event targets."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

import pytest

from nexsandglass.language import observation
from nexsandglass.language.time import resolve_time, scan_times
from nexsandglass.runtime import LanguageContext, StoreContext, observe, forget, restore
from nexsandglass.core import clock, memid, followups, profile, understand, settings
from nexsandglass.features import weavethread
from nexsandglass.engram.loops import temporal_fact as tf

CASES = json.loads((Path(__file__).resolve().parents[1] / "benchmarks/fixtures/language_time_v1.json").read_text())


@pytest.mark.parametrize("case", CASES["time"], ids=lambda c: c["id"])
def test_frozen_time_policy(case):
    result = resolve_time(case["text"], reference=datetime.fromisoformat(case["reference"]),
                          language=LanguageContext(locale=case.get("locale"), timezone=case.get("timezone")))
    assert result.status == case["status"], result.to_dict()
    if "day" in case:
        assert result.start.isoformat() == case["day"]
    assert result.to_dict()["policy_version"] == "1"


@pytest.mark.parametrize("text,zone,expect", [
    ("2026-03-29 03:30", "Europe/Berlin", "2026-03-29T01:30:00+00:00"),
    ("2026-10-25 03:30", "Europe/Berlin", "2026-10-25T02:30:00+00:00"),
    ("2026-07-01 12:30 pm", "America/New_York", "2026-07-01T16:30:00+00:00"),
])
def test_resolved_clock_uses_zone_rules(text, zone, expect):
    result = resolve_time(text, reference=datetime(2026, 1, 1, tzinfo=timezone.utc),
                          language=LanguageContext(timezone=zone))
    assert result.status == "resolved" and result.instant.isoformat() == expect


@pytest.mark.parametrize("text,expected", [
    ("1 month ago", "2026-02-28"), ("in 1 month", "2026-04-30"),
    ("1年前", "2025-03-31"), ("2周后", "2026-04-14"),
])
def test_calendar_math_is_explicit(text, expected):
    r = resolve_time(text, reference=datetime(2026, 3, 31, tzinfo=timezone.utc),
                     language=LanguageContext(timezone="UTC"))
    assert r.start.isoformat() == expected
    assert "calendar_arithmetic" in r.basis


@pytest.mark.parametrize("text,status", [
    ("2026-02-30", "invalid_date"), ("13/14/2026", "invalid_date"),
    ("2026-03-01 25:20", "invalid_time"), ("Monday or Tuesday", "unknown"),
    ("tomorrow", "needs_timezone"), ("2026-03-01 10:00", "needs_timezone"),
    ("2026-03-01 or 2026-03-02", "ambiguous"),
])
def test_unknown_is_not_an_invented_instant(text, status):
    r = resolve_time(text, reference=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert r.status == status and r.instant is None


def _write(text, when, *, language=None, source="user"):
    with clock.frozen(when):
        report = observe(text, source=source, force_promote=True, language=language)
        assert report.ok, report
        return next(r["mem_id"] for r in memid.iter_all() if r["text"] == text)


def _current():
    profile.refresh_ready(deadline=time.monotonic() + 5)
    view = profile.items()
    return {r[2] for kind in profile.KINDS for r in view[kind]}


@pytest.mark.parametrize("case", CASES["profile"], ids=lambda c: c["id"])
def test_frozen_profile_corrections(tmp_path, case):
    with StoreContext(str(tmp_path / "store")).bind():
        ids = [_write(text, f"2026-10-0{i+1} 09:00:00") for i, text in enumerate(case["records"])]
        assert [i for i, mid in enumerate(ids) if mid in _current()] == case["keep"]
        if case["id"] == "partial_language_change":
            body = " ".join(r[1] for rows in profile.items().values() for r in rows)
            assert "不要用 emoji" in body


@pytest.mark.parametrize("case", CASES["events"], ids=lambda c: c["id"])
def test_frozen_event_targets(tmp_path, case):
    with StoreContext(str(tmp_path / "store")).bind():
        ids = [_write(text, when) for when, text in case["records"]]
        followups.refresh_ready(deadline=time.monotonic() + 5)
        with clock.frozen(case["now"]):
            result = followups.due()
        assert [i for i, mid in enumerate(ids) if mid in {c["mem_id"] for c in result}] == case["keep"], result
        if "expected_day" in case:
            assert case["expected_day"] in {c["start"].isoformat() for c in result}


def test_record_context_replays_original_timezone_after_setting_change(tmp_path):
    a = StoreContext(str(tmp_path / "store"), settings={"NYX_TIMEZONE": "Asia/Shanghai", "NYX_LOCALE": "en-GB"})
    text = "I have an interview tomorrow."
    with a.bind():
        mid = _write(text, "2026-03-01 23:30:00")
        ctx = observation.for_record(memid.get_conn(), mid)
        assert ctx.timezone == "Asia/Shanghai" and ctx.local.date().isoformat() == "2026-03-02"
    b = StoreContext(a.data_dir, settings={"NYX_TIMEZONE": "America/Los_Angeles"})
    with b.bind():
        followups.refresh_ready(deadline=time.monotonic() + 5)
        row = memid.get_conn().execute("SELECT day_from FROM followup_items WHERE mem_id=?", (mid,)).fetchone()
        assert row == ("2026-03-03",)
        assert observation.for_record(memid.get_conn(), mid).timezone == "Asia/Shanghai"


def test_explicit_reference_and_frame_are_committed_without_changing_old_ids(tmp_path):
    with StoreContext(str(tmp_path / "store")).bind():
        old = _write("我喜欢红茶。", "2026-01-01")
        before = memid.get(old)
        ref = datetime.fromisoformat("2026-03-01T23:30:00+00:00")
        lang = LanguageContext(locale="en-GB", timezone="Asia/Shanghai")
        assert observe("我明天有面试。", source="user", language=lang, observed_at=ref).ok
        mid = next(r["mem_id"] for r in memid.iter_all() if r["text"] == "我明天有面试。")
        assert observation.for_record(memid.get_conn(), mid).reference == ref
        followups.refresh()
        assert memid.get_conn().execute("SELECT day_from FROM followup_items WHERE mem_id=?", (mid,)).fetchone()[0] == "2026-03-03"
        assert memid.get(old) == before
        with pytest.raises(ValueError):
            observe("bad reference", observed_at=datetime(2026, 1, 1))


@pytest.mark.parametrize("text", ["I do not live in Paris.", "我不住在巴黎。", "I don't like black tea.", "我不喜欢红茶。"])
def test_denial_is_not_stored_as_a_positive_fact(text):
    facts = understand.extract_rules(text, now=datetime(2026, 1, 1))
    assert facts and all(f.polarity == -1 for f in facts)
    assert all(not f.deferred for f in facts)
    assert all(f.to_dict()["relation"].startswith("not:") and f.to_dict()["polarity"] == -1 for f in facts)


@pytest.mark.parametrize("text", [
    "Maybe I live in Paris.", "If I live in Paris, I will tell you.",
    "Do I live in Paris?", "我可能喜欢红茶。", "我不是不喜欢红茶。",
    "I used to like black tea.", "I live in London since 03/04/2026.",
])
def test_nonassertions_and_ambiguous_dates_are_deferred(text):
    facts = understand.extract_rules(text, now=datetime(2026, 10, 1))
    assert not facts or all(f.deferred for f in facts)


def test_graph_preference_denial_reassertion_forget_restore_and_order(tmp_path):
    with StoreContext(str(tmp_path / "store")).bind():
        old = _write("我喜欢红茶。", "2026-01-01")
        neg = _write("I no longer like black tea.", "2026-03-01")
        def rows():
            return [r for r in tf.get_current(weavethread._db_path(), "user") if r["relation"] in ("偏好", "反感")]
        assert [(r["polarity"], r["source_mem_id"]) for r in rows()] == [(-1, neg)]
        _write("我喜欢红茶。记于二月。", "2026-02-01")  # imported later, older utterance
        assert rows()[0]["source_mem_id"] == neg
        assert forget({"mem_id": neg})["ok"]
        assert all(r["polarity"] == 1 for r in rows())
        assert restore(neg)["ok"]
        assert rows()[0]["polarity"] == -1
        new = _write("I like black tea.", "2026-04-01")
        assert [(r["polarity"], r["source_mem_id"]) for r in rows()] == [(1, new)]
        assert memid.get(old)["text"] == "我喜欢红茶。"


def test_ambiguous_dst_schedule_is_preserved_raw_and_not_scheduled(tmp_path):
    with StoreContext(str(tmp_path / "store")).bind():
        text = "My interview is on 2026-03-29 02:30."
        lang = LanguageContext(timezone="Europe/Berlin")
        mid = _write(text, "2026-03-01", language=lang)
        followups.refresh_ready(deadline=time.monotonic() + 5)
        assert not memid.get_conn().execute("SELECT 1 FROM followup_items WHERE mem_id=?", (mid,)).fetchall()
        assert memid.get(mid)["text"] == text


def test_cancellation_is_source_scoped_and_reversible(tmp_path):
    for name in ("a", "b"):
        with StoreContext(str(tmp_path / name)).bind():
            plan = _write("下周三我有腾讯的面试。", "2026-10-05")
            if name == "a":
                cancel = _write("The Tencent interview was cancelled.", "2026-10-06")
            else:
                _write("The Tencent interview was cancelled.", "2026-10-06", source="external")
            followups.refresh()
            with clock.frozen("2026-10-13"):
                assert bool(followups.due()) == (name == "b")
                if name == "a":
                    assert forget({"mem_id": cancel})["ok"]
                    assert followups.due()[0]["mem_id"] == plan
                    assert restore(cancel)["ok"]
                    assert not followups.due()


def test_context_metadata_rolls_back_with_failed_record(tmp_path, monkeypatch):
    with StoreContext(str(tmp_path / "store")).bind():
        original = observation.capture
        def fail(conn, mid):
            original(conn, mid)
            raise OSError("injected context write fault")
        monkeypatch.setattr(observation, "capture", fail)
        result = observe("new record", source="user", language=LanguageContext(timezone="UTC"))
        assert not result.ok
        c = memid.get_conn()
        assert c.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM observation_context").fetchone()[0] == 0


def test_context_edit_invalidates_old_projection_atomically(tmp_path):
    with StoreContext(str(tmp_path / "store")).bind():
        mid = _write("I have an interview tomorrow.", "2026-03-01 23:30:00",
                     language=LanguageContext(timezone="Asia/Shanghai"))
        followups.refresh()
        c = memid.get_conn()
        assert c.execute("SELECT day_from FROM followup_items").fetchone()[0] == "2026-03-03"
        c.execute("BEGIN")
        c.execute("UPDATE observation_context SET timezone='America/Los_Angeles' WHERE mem_id=?", (mid,))
        assert c.execute("SELECT state FROM projection_jobs WHERE name='followups'").fetchone()[0] == "pending"
        c.rollback()
        assert observation.for_record(c, mid).timezone == "Asia/Shanghai"
        c.execute("UPDATE observation_context SET timezone='America/Los_Angeles' WHERE mem_id=?", (mid,))
        c.commit()
        followups.refresh_ready(deadline=time.monotonic() + 5)
        assert c.execute("SELECT day_from FROM followup_items").fetchone()[0] == "2026-03-02"


def test_parallel_observations_do_not_share_reference_frames(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    stores = [StoreContext(str(tmp_path / str(i))) for i in range(2)]
    def write(i):
        with stores[i].bind():
            lang = LanguageContext(timezone=("Asia/Shanghai", "America/Los_Angeles")[i])
            ref = datetime.fromisoformat("2026-03-01T23:30:00+00:00")
            assert observe("My interview is tomorrow.", source="user", language=lang, observed_at=ref).ok
            followups.refresh_ready(deadline=time.monotonic() + 5)
            return memid.get_conn().execute("SELECT day_from FROM followup_items").fetchone()[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(write, (0, 1))) == ["2026-03-03", "2026-03-02"]
    assert observation.current() is None


def test_month_name_is_not_uncertainty_and_source_frame_handles_new_year(tmp_path):
    assert followups.extract("I have an interview on May 4, 2026.", datetime(2026, 4, 1))
    with observation.bind(observation.TemporalContext(datetime.fromisoformat("2025-12-31T23:30:00+00:00"),
                                                      timezone="Asia/Shanghai", basis="explicit")):
        facts = understand.extract_rules("我去年3月搬到上海。", now=datetime(2025, 12, 31, 23, 30))
        assert facts[0].valid_from.astimezone(__import__('zoneinfo').ZoneInfo("Asia/Shanghai")).year == 2025


def test_cancel_other_subject_and_assistant_claim_do_not_cancel_owner(tmp_path):
    with StoreContext(str(tmp_path / "store")).bind():
        plan = _write("下周三我有腾讯的面试。", "2026-10-05")
        _write("我妈的腾讯面试取消了。", "2026-10-06")
        _write("The Tencent interview was cancelled.", "2026-10-07", source="agent")
        followups.refresh()
        with clock.frozen("2026-10-13"):
            assert followups.due()[0]["mem_id"] == plan


def test_denial_storage_is_not_a_positive_statement_for_legacy_readers(tmp_path):
    import sqlite3
    with StoreContext(str(tmp_path / "store")).bind():
        mid = _write("I don't like black tea.", "2026-03-01")
        assert weavethread.wthread_store("I don't like black tea.", 1, mem_id=mid) == 0
        c = sqlite3.connect(weavethread._db_path())
        assert c.execute("SELECT relation,polarity FROM wthread_triples WHERE source_mem_id=?", (mid,)).fetchall() == [("not:偏好", -1)]
        c.close()
        rows = tf.get_current(weavethread._db_path(), "user", "likes")
        assert rows[0]["relation"] == "偏好" and rows[0]["polarity"] == -1
        assert rows[0]["stored_relation"] == "not:偏好"


def test_frozen_correction_fixture_identity():
    root = Path(__file__).resolve().parents[1]
    baseline = json.loads((root / "benchmarks/results/language_time_v1_baseline.json").read_text())
    assert sha256((root / "benchmarks/fixtures/language_time_v1.json").read_bytes()).hexdigest() == baseline["corpus_sha256"]


def test_declared_unsupported_input_keeps_raw_without_promoting_embedded_english(tmp_path):
    with StoreContext(str(tmp_path / "store")).bind():
        text = "Il dit: I live in Paris. My interview is tomorrow."
        mid = _write(text, "2026-03-01", language=LanguageContext(input_language="fr", timezone="Europe/Paris"))
        assert observation.for_record(memid.get_conn(), mid).input_language == "fr"
        assert memid.get(mid)["text"] == text
        followups.refresh()
        assert not memid.get_conn().execute("SELECT * FROM followup_items").fetchall()
        assert not tf.get_current(weavethread._db_path(), "user")


def test_optional_llm_does_not_bypass_negative_or_uncertain_policy(monkeypatch):
    monkeypatch.setattr(understand, "llm_enabled", lambda: True)
    monkeypatch.setattr(understand, "extract_llm", lambda text, now, subject: [
        understand.Fact("user", "偏好", "black tea", evidence=text, backend="llm")])
    assert understand.extract("I don't like black tea.")[0].polarity == -1
    assert understand.extract("Maybe I like black tea.")[0].deferred


def test_import_does_not_apply_current_profile_zone_to_unknown_history(tmp_path):
    from types import SimpleNamespace
    from nexsandglass import history_import
    from nexsandglass.core.memory_provider import NexSandglassProvider
    when = datetime.fromisoformat("2026-03-01T23:30:00+00:00").timestamp()
    turn = {"ts": when, "last_id": 1, "session": "old-session",
            "user": {"text": "My interview is tomorrow.", "ts": when}, "assistant": None}
    with StoreContext(str(tmp_path / "store"), settings={"NYX_TIMEZONE": "Asia/Shanghai"}).bind():
        # CLI import's provider stays under the already bound destination.
        provider = NexSandglassProvider()
        provider._profile_env = {"NYX_TIMEZONE": "Asia/Shanghai"}
        assert history_import.write_turns(SimpleNamespace(path="source.db"), provider, [turn])["turns"] == 1
        mid = next(r["mem_id"] for r in memid.iter_all())
        context = observation.for_record(memid.get_conn(), mid)
        assert context.reference.timestamp() == when and context.timezone is None
        assert context.basis == "historical_import_unknown_frame"
        followups.refresh()
        assert not memid.get_conn().execute("SELECT * FROM followup_items").fetchall()
