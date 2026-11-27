"""Real file/DB scopes, executor propagation and multi-profile host isolation."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from nexsandglass.core import clock, credentials, memid, settings, secret_vault
from nexsandglass.core.store_context import StoreContext, StoreScopeError, current, scope, spawn_thread, submit


def test_binding_paths_settings_credentials_and_exception_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "ambient")
    a = StoreContext(str(tmp_path / "a"), "a", {"TEST_SWITCH": "A"}, lambda k: "key-A")
    b = StoreContext(str(tmp_path / "b"), "b")
    with a.bind():
        assert current() is a and settings.get("TEST_SWITCH") == "A"
        assert credentials.get("TEST_API_KEY") == "key-A"
        with pytest.raises(RuntimeError), b.bind():
            assert settings.get("TEST_SWITCH") == ""
            assert credentials.get("TEST_API_KEY") == ""
            raise RuntimeError()
        assert current() is a
        assert credentials.get("TEST_API_KEY") == "key-A"
    assert current() is None
    assert os.environ["TEST_API_KEY"] == "ambient"
    with pytest.raises(TypeError):
        a.settings["TEST_SWITCH"] = "changed"


@pytest.mark.parametrize("relative", ["../b/nyx.db", "/tmp/nyx.db", "x/../../b", "bad\x00name"])
def test_paths_fail_closed(tmp_path, relative):
    a = StoreContext(str(tmp_path / "a"))
    with pytest.raises(StoreScopeError):
        a.path(relative)


def test_symlink_and_explicit_override_cannot_escape(tmp_path):
    a, b = StoreContext(str(tmp_path / "a")), StoreContext(str(tmp_path / "b"))
    Path(a.data_dir).mkdir()
    Path(a.data_dir, "outside").symlink_to(b.data_dir)
    with pytest.raises(StoreScopeError):
        a.path("outside/nyx.db")
    with a.bind():
        with pytest.raises(StoreScopeError):
            memid.journal_lock(b.path("sandglass.txt"))
        from nexsandglass.core.store_context import canonical_path
        with pytest.raises(StoreScopeError):
            canonical_path("nyx.db", a.path("other.db"))


def test_nested_store_preserves_transaction_and_file_identity(tmp_path):
    a, b = StoreContext(str(tmp_path / "a")), StoreContext(str(tmp_path / "b"))
    with a.bind():
        c = memid.get_conn()
        c.execute("CREATE TABLE sentinel(n INTEGER)")
        c.execute("INSERT INTO sentinel VALUES (1)")
        with b.bind():
            assert memid.get_conn() is not c
            assert not memid.get_conn().execute("SELECT name FROM sqlite_master WHERE name='sentinel'").fetchall()
        assert memid.get_conn() is c and c.in_transaction
        c.rollback()
        assert c.execute("SELECT * FROM sentinel").fetchall() == []
        # A recreated store must not keep writing through a stale connection.
        memid.close_store_connections(a)
        old = Path(a.path("nyx.db"))
        old.rename(old.with_suffix(".old"))
        new = memid.get_conn()
        assert not new.execute("SELECT name FROM sqlite_master WHERE name='sentinel'").fetchall()


def test_reused_worker_and_background_thread_carry_only_bound_store(tmp_path):
    a = StoreContext(str(tmp_path / "a"), "a", {"TEST_SWITCH": "A"}, lambda k: "A")
    b = StoreContext(str(tmp_path / "b"), "b", {"TEST_SWITCH": "B"}, lambda k: "B")
    def write():
        mid = memid.allocate("Thread message for " + settings.get("TEST_SWITCH"), sender="user")
        return current().data_dir, credentials.get("TEST_API_KEY"), mid
    with ThreadPoolExecutor(max_workers=1) as pool:
        for store, value in ((a, "A"), (b, "B"), (a, "A")):
            with store.bind():
                root, secret, mid = submit(pool, write).result(timeout=10)
                assert root == store.data_dir and secret == value
        assert pool.submit(current).result() is None
    result = []
    with b.bind():
        thread = spawn_thread(lambda: result.append(write()))
    thread.start()
    thread.join(10)
    assert not thread.is_alive() and result[0][0] == b.data_dir
    with a.bind():
        assert all("for A" in r["text"] for r in memid.iter_all())
    with b.bind():
        assert all("for B" in r["text"] for r in memid.iter_all())


def test_clock_propagation_and_followup_switches_do_not_leak(tmp_path):
    from nexsandglass.core import autopurge, followups
    a, b = StoreContext(str(tmp_path / "a")), StoreContext(str(tmp_path / "b"))
    Path(a.data_dir).mkdir()
    Path(b.data_dir).mkdir()
    with a.bind(), clock.frozen("2026-01-01"):
        followups.switch(False, by="a")
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert submit(pool, clock.now).result().isoformat().startswith("2026-01-01")
        assert autopurge._path() == a.path("auto_purge.json")
    with b.bind():
        assert followups.enabled()
        assert autopurge.last_run() is None
    with a.bind():
        assert not followups.enabled()


def test_history_import_progress_and_clock_follow_provider_store(tmp_path, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    import nexsandglass_hermes as nh
    from nexsandglass import history_import
    from nexsandglass.core.memory_provider import NexSandglassProvider
    monkeypatch.delenv("NEXSANDBASE_HOME", raising=False)
    home = tmp_path / "hermes"
    root = tmp_path / "destination"
    nh.save_config({"data_dir": str(root)}, str(home))
    p = NexSandglassProvider()
    p.initialize("import", hermes_home=str(home), platform="cli")
    p._warm_thread.join(15)
    when = datetime(2025, 2, 3, 9).timestamp()
    turns = [{"ts": when, "last_id": 1, "session": "same-session",
              "user": {"text": "I live in the imported city.", "ts": when}, "assistant": None}]
    source = SimpleNamespace(path="source-state.db")
    with clock.frozen("2026-11-27"):
        assert history_import.write_turns(source, p, turns)["turns"] == 1
        assert clock.now().year == 2026
        assert history_import.write_turns(source, p, turns)["skipped_dupes"] == 1
    with p._store_context.bind():
        row = next(r for r in memid.iter_all() if "imported city" in r["text"])
        assert row["ts"] == "2025-02-03 09:00:00"
        assert history_import.done_upto(memid.get_conn(), source.path, "same-session") == 1
    assert not memid.get_conn().execute("SELECT name FROM sqlite_master WHERE name='import_progress'").fetchall()


def test_media_purge_cannot_delete_a_foreign_store_blob(tmp_path):
    from nexsandglass import media
    a, b = StoreContext(str(tmp_path / "a")), StoreContext(str(tmp_path / "b"))
    Path(a.data_dir).mkdir()
    foreign = Path(a.path("foreign.txt"))
    foreign.write_text("keep")
    with b.bind():
        assert media.media_dir() == b.path("media")
        with pytest.raises(StoreScopeError):
            media._delete_blob({"blob": str(foreign), "asset_id": "copied-payload"})
    assert foreign.read_text() == "keep"


def test_vault_session_and_redaction_state_are_store_scoped(tmp_path):
    a, b = StoreContext(str(tmp_path / "a")), StoreContext(str(tmp_path / "b"))
    with a.bind():
        secret_vault.queue_notice("same-session", "only-A")
        code = secret_vault.new_code("same-session", 1)
        secret_vault.remember_revealed(1, "password", "value-only-A")
        key_a = secret_vault.key_path()
    with b.bind():
        assert not secret_vault.has_pending("same-session")
        assert secret_vault.render("same-session", "answer") is None
        assert secret_vault.check_confirm("same-session", "confirm v1 " + code, 1) == "wrong"
        assert secret_vault.redact_revealed("value-only-A") == "value-only-A"
        assert secret_vault.key_path() != key_a
        assert not Path(secret_vault.key_path()).is_relative_to(Path(b.data_dir))
    with a.bind():
        assert secret_vault.render("same-session", "answer").endswith("only-A")
        assert secret_vault.check_confirm("same-session", "confirm v1 " + code, 1) == "ok"
        assert "value-only-A" not in secret_vault.redact_revealed("value-only-A")


def test_real_scoped_pipeline_sources_forget_restore_and_request_identity(tmp_path):
    code = r'''
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from nexsandglass.runtime import StoreContext, observe, RecallRequest, recall_request, forget, restore
from nexsandglass.core import memid, sandglass_sqlite, semantic, consolidation_snapshots
from nexsandglass.features import sandglass_vault, shadow_sand, weavethread
from nexsandglass.engram import bridge
from tests.fixtures.fake_embed import ConceptBag, Renamed

root = Path(sys.argv[1])
stores = [StoreContext(str(root / name), name) for name in ('a', 'b')]
texts = ['I live in Oslo. My cobalt mug is on shelf delta.',
         'I live in Berlin. My amber cup is in drawer gamma.']
models = [ConceptBag(), Renamed()]
ids = []
for store, text, model in zip(stores, texts, models):
    with store.bind():
        semantic.set_provider(model)
        assert observe(text, source='user', session_id='same-session', force_promote=True).ok
        mid = next(r['mem_id'] for r in memid.iter_all() if r['text'] == text)
        ids.append(mid)
        sandglass_sqlite.sync_incremental()
        assert sandglass_vault.search(text, limit=3)
        shadow_sand.shadow_index(text, tags=store.profile_id, line_num=1, mem_id=mid)
        assert shadow_sand.shadow_search(store.profile_id)
        weavethread.wthread_add('user', 'lives_in', store.profile_id, source_line=1)
        bridge.ingest(text)
        assert bridge.recent(1)[0]['content'] == text
        semantic.index_pending()

def read(i):
    with stores[i].bind():
        assert semantic.provider() is models[i]
        assert semantic.stats()['indexed'] == 1
        # User-controlled identities/path-looking text do not switch the store.
        request = RecallRequest('cobalt mug shelf delta amber cup drawer gamma',
                                session_id=stores[1-i].data_dir, task_id='../../a', token_budget=800)
        packet = recall_request(request)
        assert texts[i] in packet.text, packet.to_dict()
        assert texts[1-i] not in packet.text, packet.to_dict()
        assert all(e.source_mem_id != ids[1-i] for e in packet.evidence)
        assert any(row['object'] == stores[i].profile_id for row in weavethread.wthread_query('user'))
        assert all(row['object'] != stores[1-i].profile_id for row in weavethread.wthread_query('user'))
        assert str(consolidation_snapshots.directory()).startswith(stores[i].data_dir)
        return packet.text
with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(read, [0, 1, 0, 1, 1, 0]))
with stores[0].bind():
    assert forget({'mem_id': ids[0]})['ok']
    assert texts[0] not in recall_request(RecallRequest('cobalt mug', token_budget=800)).text
    assert restore(ids[0])['ok']
read(0)
read(1)
print('SCOPED REAL PIPELINE: PASS')
'''
    env = dict(os.environ, NEXSANDBASE_HOME=str(tmp_path / "legacy"),
               NYX_EMBED="off", NYX_AUTO_PURGE="off")
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (result.stdout + result.stderr)[-7000:]
    assert "SCOPED REAL PIPELINE: PASS" in result.stdout


def test_two_initialized_hermes_profiles_in_parallel(tmp_path, monkeypatch):
    import nexsandglass_hermes as nh
    from nexsandglass.core.memory_provider import NexSandglassProvider
    monkeypatch.delenv("NEXSANDBASE_HOME", raising=False)
    providers = []
    for name in ("alpha", "beta"):
        home, root = tmp_path / (name + "-hermes"), tmp_path / name
        nh.save_config({"data_dir": str(root), "context_tokens": 1000}, str(home))
        p = NexSandglassProvider()
        p.initialize("same-session", hermes_home=str(home), platform="cli")
        p.sync_turn("I live in " + name + " city.", "")
        p._warm_thread.join(15)
        assert not p._warm_thread.is_alive()
        providers.append(p)
    def read(i):
        p = providers[i]
        name, other = (("alpha", "beta"), ("beta", "alpha"))[i]
        for block in (p.system_prompt_block(), p.voice_prompt_block(),
                      p.prefetch("Which city do I live in?", session_id="same-session")):
            assert name + " city" in block, block
            assert other + " city" not in block, block
        trace = p.context_trace("hermes_system")
        assert trace and "legacy_layers_unavailable" in trace["reason_codes"]
        assert "error" in json.loads(p.handle_tool_call("sandglass_offset", {}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(read, [0, 1, 0, 1]))
