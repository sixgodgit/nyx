"""Issue #39: replacement must follow reader closure, on Windows and POSIX."""
import builtins
import os
from pathlib import Path

import pytest

from nexsandglass.core import erasure, fsutil, memid
from nexsandglass.runtime import StoreContext


def _deny_replacement_of_open_readers(monkeypatch, paths, *, streaming=False):
    targets = {os.path.normcase(os.path.abspath(p)) for p in paths}
    real_open, real_replace = builtins.open, os.replace
    readers, replaced = [], []

    class Reader:
        def __init__(self, raw):
            self.raw = raw
        def __getattr__(self, name):
            return getattr(self.raw, name)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.raw.__exit__(*args)
        def __iter__(self):
            return iter(self.raw)
        def read(self, *args):
            if streaming:
                raise AssertionError("redaction must remain a streaming rewrite")
            return self.raw.read(*args)
        def readlines(self, *args):
            if streaming:
                raise AssertionError("redaction must not buffer the whole journal")
            return self.raw.readlines(*args)

    def tracked_open(path, mode="r", *args, **kwargs):
        raw = real_open(path, mode, *args, **kwargs)
        if not isinstance(path, int) and "r" in mode and os.path.normcase(os.path.abspath(path)) in targets:
            readers.append((os.path.normcase(os.path.abspath(path)), raw))
            return Reader(raw)
        return raw

    def replace(source, destination):
        key = os.path.normcase(os.path.abspath(destination))
        if key in targets:
            if any(p == key and not f.closed for p, f in readers):
                raise PermissionError("Windows-style destination sharing violation")
            replaced.append(key)
        return real_replace(source, destination)

    monkeypatch.setattr(builtins, "open", tracked_open)
    monkeypatch.setattr(fsutil.os, "replace", replace)
    return readers, replaced


@pytest.mark.parametrize("target", ["journal", "backup"])
def test_redaction_closes_readers_before_replace_without_bulk_read(tmp_path, monkeypatch, target):
    journal, backup = tmp_path/"sandglass.txt", tmp_path/"sandglass.backup"
    data = "2026-01-01 | user | keep this\n2026-01-02 | user | private 中文\ncontinuation\n2026-01-03 | user | keep last\n".encode()
    journal.write_bytes(data)
    backup.write_bytes(data)
    rows = [("m_private", 2, 3, "2026-01-02", "user")]
    readers, replaced = _deny_replacement_of_open_readers(monkeypatch, (journal, backup), streaming=True)
    if target == "journal":
        assert erasure._redact_journal(rows, "test", str(journal)) == 2
        result = journal
    else:
        assert erasure._redact_backup(rows, "test", str(journal))["status"] == "redacted"
        result = backup
    assert replaced == [os.path.normcase(os.path.abspath(result))]
    assert all(handle.closed for _, handle in readers)
    lines = result.read_bytes().splitlines()
    assert lines[0] == data.splitlines()[0] and lines[-1] == data.splitlines()[-1]
    assert b"[REDACTED" in lines[1] and b"[REDACTED" in lines[2]
    assert "private 中文".encode() not in result.read_bytes()


@pytest.mark.parametrize("style", ["lf", "crlf", "mixed", "unterminated"])
def test_native_forget_restore_keeps_unrelated_bytes_and_line_endings(tmp_path, style):
    with StoreContext(str(tmp_path/"store")).bind():
        first = memid.allocate("Preserve first 中文", sender="user")
        private = memid.allocate("Private issue39 marker\nsecond private line", sender="user")
        last = memid.allocate("Preserve last café", sender="user")
        journal = Path(memid._journal_path())
        original = journal.read_bytes()
        if style == "crlf":
            original = original.replace(b"\n", b"\r\n")
        elif style == "mixed":
            original = b"".join(line+(b"\r\n" if i%2 else b"\n") for i, line in enumerate(original.splitlines()))
        elif style == "unterminated":
            original = original.rstrip(b"\n")
        journal.write_bytes(original)
        backup = journal.with_name("sandglass.backup")
        backup.write_bytes(original)
        before_first, before_last = memid.get(first), memid.get(last)
        report = erasure.forget({"mem_id": private}, apply=True)
        assert report["ok"] and report["recoverable"], report
        assert report["backup"]["status"] == "redacted"
        assert memid.get(private) is None
        assert b"Private issue39 marker" not in journal.read_bytes()
        assert b"Private issue39 marker" not in backup.read_bytes()
        assert journal.read_bytes().splitlines(keepends=True)[0] == original.splitlines(keepends=True)[0]
        assert journal.read_bytes().splitlines(keepends=True)[-1] == original.splitlines(keepends=True)[-1]
        assert erasure.verify_erasure(["Private issue39 marker"])["clean"]
        restored = erasure.restore(private)
        assert restored["ok"], restored
        assert journal.read_bytes() == original
        assert backup.read_bytes() == original
        assert memid.get(first) == before_first and memid.get(last) == before_last
        assert memid.verify(str(journal))["ok"]


def test_failed_replace_retains_original_and_removes_temporary(tmp_path, monkeypatch):
    path = tmp_path/"sandglass.txt"
    original = b"2026-01-01 | user | preserve until replacement succeeds\n"
    path.write_bytes(original)
    def denied(*args, **kwargs):
        raise PermissionError("persistent external lock")
    monkeypatch.setattr(fsutil.os, "replace", denied)
    with pytest.raises(PermissionError):
        erasure._redact_journal([("m_one", 1, 1, "2026-01-01", "user")], "test", str(path))
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".sandglass.txt.*.tmp"))
    assert not Path(str(path)+".lock").exists()


@pytest.mark.parametrize("target", ["journal", "backup"])
@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
def test_redacting_the_last_line_preserves_its_terminator(tmp_path, target, ending):
    journal, backup = tmp_path/"sandglass.txt", tmp_path/"sandglass.backup"
    original = b"2026-01-01 | user | keep\r\n2026-01-02 | user | delete" + ending
    journal.write_bytes(original)
    backup.write_bytes(original)
    rows = [("m_two", 2, 2, "2026-01-02", "user")]
    if target == "journal":
        erasure._redact_journal(rows, "test", str(journal))
        result = journal.read_bytes()
    else:
        erasure._redact_backup(rows, "test", str(journal))
        result = backup.read_bytes()
    assert result.startswith(original.splitlines(keepends=True)[0])
    assert fsutil.split_line_ending(result.splitlines(keepends=True)[-1])[1] == ending


def test_failed_stream_read_retains_original_and_cleans_temporary(tmp_path, monkeypatch):
    path = tmp_path/"sandglass.txt"
    original = b"2026-01-01 | user | keep\n2026-01-02 | user | delete\n"
    path.write_bytes(original)
    real_open = builtins.open
    handles = []
    class BrokenReader:
        def __init__(self, raw):
            self.raw = raw
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.raw.__exit__(*args)
        def __iter__(self):
            yield self.raw.readline()
            raise OSError("injected stream error")
    def failed_open(file, mode="r", *args, **kwargs):
        raw = real_open(file, mode, *args, **kwargs)
        if os.path.abspath(file) == str(path) and "r" in mode:
            handles.append(raw)
            return BrokenReader(raw)
        return raw
    monkeypatch.setattr(builtins, "open", failed_open)
    with pytest.raises(OSError, match="stream error"):
        erasure._redact_journal([("m_two", 2, 2, "2026-01-02", "user")], "test", str(path))
    assert all(f.closed for f in handles)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".sandglass.txt.*.tmp"))


def test_actual_windows_sharing_violation_is_fail_closed(tmp_path):
    if os.name != "nt":
        pytest.skip("requires native Windows file-sharing semantics")
    path = tmp_path/"journal.txt"
    original = b"a live reader must prevent replacement"
    path.write_bytes(original)
    with path.open("rb"):
        with pytest.raises(PermissionError):
            with fsutil.atomic_write(str(path), mode="wb") as output:
                output.write(b"should not replace the original")
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".journal.txt.*.tmp"))
