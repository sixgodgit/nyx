"""Fault injection at the journal/identity-hub persistence boundary."""
import builtins
import os
import sqlite3

import pytest


@pytest.fixture()
def hub(monkeypatch, tmp_path):
    from nexsandglass.core import memid

    home = tmp_path / "nb"
    home.mkdir()
    monkeypatch.setenv("NEXSANDBASE_HOME", str(home))
    memid.set_db_path(str(home / "nyx.db"))
    yield memid, home / "sandglass.txt"
    memid.set_db_path(str(tmp_path / "unused.db"))


class _ConnectionProxy:
    def __init__(self, conn, commit_first=False):
        self.conn = conn
        self.commit_first = commit_first

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def commit(self):
        if self.commit_first:
            self.conn.commit()
        raise sqlite3.OperationalError("injected commit error")


def _assert_consistent_next_write(memid, journal, existing):
    mid = memid.allocate("after recovery\nsecond line", sender="user")
    row = memid.get(mid)
    assert row["seq"] == len(existing) + 1
    assert row["line_start"] == len(existing) + 1
    assert row["line_end"] == len(existing) + 2
    assert memid.verify(str(journal))["ok"]


@pytest.mark.parametrize("with_existing", [False, True])
def test_commit_failure_removes_only_own_append(hub, monkeypatch, with_existing):
    memid, journal = hub
    existing = [memid.allocate("preserve existing 中文", sender="user")] if with_existing else []
    before = journal.read_bytes() if journal.exists() else b""
    conn = memid.get_conn()
    from nexsandglass.core import journal_mirror
    journal_mirror.records(str(journal))

    with monkeypatch.context() as mp:
        mp.setattr(memid, "get_conn", lambda: _ConnectionProxy(conn))
        with pytest.raises(sqlite3.OperationalError, match="injected commit error"):
            memid.allocate("must not survive\npartial record", sender="user")

    assert journal.read_bytes() == before
    assert not conn.in_transaction
    assert memid.count() == len(existing)
    assert str(journal) not in memid._line_count_cache
    assert os.path.abspath(journal) not in journal_mirror._state
    _assert_consistent_next_write(memid, journal, existing)


@pytest.mark.parametrize("short_return", [False, True])
def test_partial_file_write_is_removed_without_touching_old_bytes(hub, monkeypatch, short_return):
    memid, journal = hub
    existing = [memid.allocate("old complete record", sender="user")]
    before = journal.read_bytes()

    class PartialWriter:
        def __init__(self, file):
            self.file = file

        def __getattr__(self, name):
            return getattr(self.file, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self.file.__exit__(*exc)

        def write(self, payload):
            self.file.write(payload[:17])
            self.file.flush()
            if short_return:
                return 17
            raise OSError("injected partial file write")

    def open_partial(path, mode="r", **kwargs):
        f = builtins.open(path, mode, **kwargs)
        return PartialWriter(f) if str(path) == str(journal) and mode == "a+b" else f

    with monkeypatch.context() as mp:
        mp.setattr(memid, "open", open_partial, raising=False)
        with pytest.raises(OSError):
            memid.allocate("failed record that only writes some bytes", sender="user")

    assert journal.read_bytes() == before
    assert memid.count() == 1
    _assert_consistent_next_write(memid, journal, existing)


def test_provenance_failure_rolls_back_identity_row(hub, monkeypatch):
    memid, journal = hub
    existing = [memid.allocate("old complete record", sender="user")]
    before = journal.read_bytes()

    with monkeypatch.context() as mp:
        mp.setattr(memid._prov, "record", lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("injected provenance error")))
        with pytest.raises(RuntimeError, match="injected provenance error"):
            memid.allocate("not provenance-bound", sender="user")

    assert journal.read_bytes() == before
    assert memid.count() == 1
    assert not memid.get_conn().in_transaction
    _assert_consistent_next_write(memid, journal, existing)


def test_post_commit_exception_does_not_erase_committed_record(hub, monkeypatch):
    memid, journal = hub
    conn = memid.get_conn()
    with monkeypatch.context() as mp:
        mp.setattr(memid, "get_conn", lambda: _ConnectionProxy(conn, commit_first=True))
        with pytest.raises(sqlite3.OperationalError, match="injected commit error"):
            memid.allocate("actually committed", sender="user")

    assert memid.count() == 1
    assert "actually committed" in journal.read_text()
    assert memid.verify(str(journal))["ok"]


def test_migration_commit_failure_rolls_back_without_creating_journal(hub, monkeypatch):
    memid, journal = hub
    conn = memid.get_conn()
    with monkeypatch.context() as mp:
        mp.setattr(memid, "get_conn", lambda: _ConnectionProxy(conn))
        with pytest.raises(sqlite3.OperationalError, match="injected commit error"):
            memid.allocate("migration only", sender="user", write_journal=False)

    assert memid.count() == 0
    assert not conn.in_transaction
    assert not journal.exists()


def test_rollback_refuses_to_truncate_an_unexpected_tail(hub, monkeypatch):
    memid, journal = hub
    memid.allocate("preserved record", sender="user")
    conn = memid.get_conn()

    class ExternalAppend(_ConnectionProxy):
        def commit(self):
            with builtins.open(journal, "ab") as f:
                f.write(b"an unrelated external append\n")
            raise sqlite3.OperationalError("injected commit error")

    with monkeypatch.context() as mp:
        mp.setattr(memid, "get_conn", lambda: ExternalAppend(conn))
        with pytest.raises(RuntimeError, match="回滚未完成"):
            memid.allocate("failed record", sender="user")

    assert memid.count() == 1
    assert b"an unrelated external append\n" in journal.read_bytes()
    assert "preserved record" in journal.read_text()
