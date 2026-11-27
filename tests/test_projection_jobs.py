"""Durable progress, atomic publication, stale-worker fencing and source replay."""
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from nexsandglass.core import projection_jobs as jobs
from nexsandglass.core import memid, profile, followups
from nexsandglass.core.store_context import StoreContext


def test_coalescing_version_and_nested_rollback(tmp_path):
    c = sqlite3.connect(tmp_path / "jobs.db")
    jobs.enqueue(c, "view", "v1", 4)
    assert jobs.run(c, "view", "v1", 4, lambda c, j: (4, 9)) == 9
    assert jobs.ready_through(c, "view", "v1", 4)
    assert jobs.enqueue(c, "view", "v1", 2).target_seq == 4
    c.execute("BEGIN")
    jobs.enqueue(c, "view", "v2", 5)
    assert jobs.get(c, "view").reset_required
    c.rollback()
    assert jobs.get(c, "view").processor_version == "v1"
    job = jobs.enqueue(c, "view", "v2", 5)
    assert job.checkpoint_seq == 0 and job.reset_required


def test_failed_batch_rolls_back_and_retry_is_idempotent(tmp_path):
    c = sqlite3.connect(tmp_path / "jobs.db")
    c.execute("CREATE TABLE result (n INTEGER PRIMARY KEY)")
    def crash(c, lease):
        c.execute("INSERT INTO result VALUES (1)")
        raise ValueError("private-content-never-persist")
    with pytest.raises(ValueError):
        jobs.run(c, "view", "1", 2, crash)
    assert c.execute("SELECT * FROM result").fetchall() == []
    job = jobs.get(c, "view")
    assert job.state == "failed" and job.last_error == "processor_error" and job.checkpoint_seq == 0
    def retry(c, lease):
        c.execute("INSERT INTO result VALUES (1)")
        return lease.target_seq, 1
    assert jobs.run(c, "view", "1", 2, retry) == 1
    assert jobs.run(c, "view", "1", 2, retry) == 0
    assert jobs.get(c, "view").attempts == 2


@pytest.mark.parametrize("supersede", ["lease", "version"])
def test_old_worker_cannot_commit_or_overwrite_new_status(tmp_path, supersede):
    path = tmp_path / "jobs.db"
    a, b = sqlite3.connect(path), sqlite3.connect(path)
    jobs.enqueue(a, "view", "1", 4, now=10)
    old = jobs.claim(a, "view", "1", now=10, lease_seconds=1)
    if supersede == "version":
        jobs.enqueue(b, "view", "2", 4, now=12)
        new = jobs.claim(b, "view", "2", now=12)
    else:
        new = jobs.claim(b, "view", "1", now=12)
    with pytest.raises(jobs.LeaseLost):
        with jobs.transaction(a):
            jobs.finish(a, old, 4, now=12)
    jobs.failed(a, old, now=12)
    assert jobs.get(b, "view").lease_owner == new.lease_owner
    with jobs.transaction(b):
        jobs.finish(b, new, 4, now=13)
    assert jobs.ready_through(a, "view", new.processor_version, 4)


def test_process_death_does_not_publish_partial_projection(tmp_path):
    path = str(tmp_path / "jobs.db")
    code = '''
import os, sqlite3, sys
from nexsandglass.core import projection_jobs as j
c = sqlite3.connect(sys.argv[1])
c.execute("CREATE TABLE result (n INTEGER)")
j.enqueue(c, "view", "1", 2, now=1)
lease = j.claim(c, "view", "1", now=1, lease_seconds=1)
with j.transaction(c):
    c.execute("INSERT INTO result VALUES (1)")
    os._exit(37)
'''
    result = subprocess.run([sys.executable, "-c", code, path], timeout=15)
    assert result.returncode == 37
    c = sqlite3.connect(path)
    assert c.execute("SELECT * FROM result").fetchall() == []
    assert jobs.get(c, "view").state == "running"
    def rebuild(c, lease):
        c.execute("INSERT INTO result VALUES (1)")
        return 2, 1
    assert jobs.run(c, "view", "1", 2, rebuild) == 1
    c.close()
    c = sqlite3.connect(path)
    assert jobs.ready_through(c, "view", "1", 2)
    assert c.execute("SELECT * FROM result").fetchall() == [(1,)]


@pytest.mark.parametrize("module,table,text", [
    (profile, "profile_items", "I am allergic to peanuts."),
    (followups, "followup_items", "I have a dentist appointment tomorrow."),
])
def test_source_mutation_replays_without_callback(tmp_path, module, table, text):
    with StoreContext(str(tmp_path / "store")).bind():
        mid = memid.allocate(text, sender="user", ts="2026-11-27 09:00:00")
        c = memid.get_conn()
        module.refresh_ready(deadline=time.monotonic() + 5)
        assert c.execute("SELECT mem_id FROM " + table).fetchall() == [(mid,)]
        # Simulate an interrupted forget/restore path before reconsider().
        c.execute("UPDATE memories SET deleted_at='now' WHERE mem_id=?", (mid,))
        c.commit()
        module.refresh_ready(deadline=time.monotonic() + 5)
        assert c.execute("SELECT * FROM " + table).fetchall() == []
        c.execute("UPDATE memories SET deleted_at=NULL WHERE mem_id=?", (mid,))
        c.commit()
        memid.close_store_connections()
        module.refresh_ready(deadline=time.monotonic() + 5)
        c = memid.get_conn()
        assert c.execute("SELECT mem_id FROM " + table).fetchall() == [(mid,)]
        c.execute("UPDATE memories SET text='An ordinary message.' WHERE mem_id=?", (mid,))
        c.commit()
        module.refresh_ready(deadline=time.monotonic() + 5)
        assert c.execute("SELECT * FROM " + table).fetchall() == []


def test_canonical_invalidation_is_atomic_and_fences_claim(tmp_path):
    with StoreContext(str(tmp_path / "store")).bind():
        memid.allocate("I live in Oslo.", sender="user")
        c = memid.get_conn()
        profile.refresh()
        jobs.enqueue(c, "profile", profile.RULES_VERSION, 2)
        old = jobs.claim(c, "profile", profile.RULES_VERSION)
        c.execute("BEGIN")
        c.execute("UPDATE memories SET text='I live in Bergen.'")
        assert jobs.get(c, "profile").state == "pending"
        c.rollback()
        assert jobs.get(c, "profile").lease_owner == old.lease_owner
        c.execute("UPDATE provenance SET signal='untrusted'")
        c.commit()
        with pytest.raises(jobs.LeaseLost):
            jobs.finish(c, old, 2)
        profile.refresh()
        assert not profile.items()["identity"]


@pytest.mark.parametrize("module,table,text", [
    (profile, "profile_items", "I am allergic to peanuts."),
    (followups, "followup_items", "I have a dentist appointment tomorrow."),
])
def test_readiness_waits_for_foreign_lease_then_recovers(tmp_path, module, table, text):
    with StoreContext(str(tmp_path / "store")).bind():
        mid = memid.allocate(text, sender="user", ts="2026-11-27 09:00:00")
        c = memid.get_conn()
        name = "profile" if module is profile else "followups"
        jobs.enqueue(c, name, module.RULES_VERSION, 1)
        lease = jobs.claim(c, name, module.RULES_VERSION, lease_seconds=60)
        assert lease is not None
        with pytest.raises(TimeoutError):
            module.refresh_ready(deadline=time.monotonic() + 0.03)
        assert jobs.get(c, name).lease_owner == lease.lease_owner
        assert not c.execute("SELECT * FROM " + table).fetchall()
        # A crashed worker's expired lease can be taken over without dropping
        # the source or claiming that its incomplete view was already ready.
        c.execute("UPDATE projection_jobs SET lease_until=0 WHERE name=?", (name,))
        c.commit()
        module.refresh_ready(deadline=time.monotonic() + 5)
        assert c.execute("SELECT mem_id FROM " + table).fetchall() == [(mid,)]
