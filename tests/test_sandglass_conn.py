"""tests/test_sandglass_conn.py — 全文库复用一个连接、count() 缓存（v7.15.4）

以前每次查询都新开连接（每次重跑 WAL 检查和建表、页缓存是冷的），召回一次要开十来个；
count() 每次整表 COUNT。现在复用一个连接，count() 在库没变时用上次的结果。
这里钉住它必须照旧正确的几种情况：连接被关、库文件被删掉重建（scrub）、别的进程写入、表重建。
"""
import os
import sqlite3

import pytest

from nexsandglass.core import sandglass_sqlite as sq, sqlite_open


@pytest.fixture()
def fts(tmp_path, monkeypatch):
    monkeypatch.setattr(sq, "_DB", str(tmp_path / "sandglass.db"))
    monkeypatch.setattr(sq, "_conn", None)
    monkeypatch.setattr(sq, "_count_cache", None)
    yield sq._DB
    sq._reset_db()


def _add(conn, i, text="hello world"):
    conn.execute("INSERT INTO sandglass(id, ts, sender, text, line_end) VALUES(?,?,?,?,?)",
                 (i, "2026-10-26 10:00", "user", text, i))
    conn.execute("INSERT INTO sandglass_fts(rowid, tokens) VALUES(?,?)", (i, sq._tokenize(text)))
    conn.commit()


def test_one_connection_is_reused(fts):
    a = sq._get_db()
    assert sq._get_db() is a


def test_stray_close_keeps_the_shared_connection_usable(fts):
    a = sq._get_db()
    a.close()                                    # 老代码 / 测试会自己 close：不真关
    assert sq._get_db() is a and a.execute("SELECT COUNT(*) FROM sandglass").fetchall()[0][0] == 0
    sq._reset_db()                               # 真要关
    b = sq._get_db()
    assert b is not a and b.execute("SELECT COUNT(*) FROM sandglass").fetchall()[0][0] == 0


def test_concurrent_close_and_use_do_not_crash(fts):
    """CI 上 Python 3.10 段错误过：一个线程 close() 复用的连接，另一个线程正在用它。"""
    import threading
    errors = []

    def closer():
        for _ in range(200):
            sq._get_db().close()

    def user():
        try:
            for _ in range(200):
                assert sq.count() == 0
                sq.search("anything")
        except Exception as e:                   # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=closer) for _ in range(4)] + [threading.Thread(target=user) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors[0]


def test_deleted_db_file_gets_a_fresh_connection(fts):
    with sq._lock:
        _add(sq._get_db(), 1, "passport in the drawer")
    assert sq.count() == 1 and sq.search("passport")
    with sq._lock:                               # scrub：先关连接再删文件，然后从日志重建
        sq._reset_db()
        for suf in ("", "-wal", "-shm"):
            if os.path.exists(fts + suf):
                os.unlink(fts + suf)
    assert sq.search("passport") == [] and sq.count() == 0


def test_deleted_without_reset_is_noticed(fts):
    """有人没先 _reset_db() 就删了文件：按 inode 认出是新文件，不读旧连接里的旧库。"""
    with sq._lock:
        _add(sq._get_db(), 1, "old secret line")
    assert sq.count() == 1
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(fts + suf):
            os.unlink(fts + suf)
    sqlite3.connect(fts).close()                 # 别的进程重建了一个空库
    assert sq.search("secret") == [] and sq.count() == 0


def test_count_follows_own_and_other_writers(fts):
    assert sq.count() == 0
    with sq._lock:
        _add(sq._get_db(), 1)
    assert sq.count() == 1, "本连接写入（total_changes）"
    other = sqlite_open.connect(fts)             # 另一个进程（MCP 服务）写入
    _add(other, 2)
    assert sq.count() == 2, "别的连接提交（data_version）"
    other.execute("DELETE FROM sandglass WHERE id = 1")
    other.commit()
    other.close()
    assert sq.count() == 1
    with sq._lock:                               # 表重建（sync_all 迁移路径）
        c = sq._get_db()
        c.execute("DROP TABLE sandglass")
        c.execute("CREATE TABLE sandglass (id INTEGER PRIMARY KEY, ts TEXT, sender TEXT, text TEXT, line_end INTEGER)")
        c.commit()
    assert sq.count() == 0, "schema_version 变了"


def test_vacuum_after_reset(fts):
    with sq._lock:
        _add(sq._get_db(), 1, "to be erased")
        c = sq._get_db()
        c.execute("DELETE FROM sandglass WHERE id = 1")
        c.commit()
        sq._reset_db()
    assert sqlite_open.vacuum(fts) is None
    assert sq.count() == 0
