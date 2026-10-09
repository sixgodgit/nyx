"""tests/test_sqlite_open.py — 多个线程 / 进程同时第一次打开同一个库，谁都不能失败（v7.13.4）

v7.13.3 的发布流水线（Python 3.11）挂在 test_threads_get_distinct_connections：
4 个线程同时 memid.get_conn()，其中一个在 `PRAGMA journal_mode=WAL` 上抛 `database is locked`
—— timeout=30 也没用，SQLite 发现锁成环时不调用 busy handler，直接返回 BUSY。
生产上同样会撞：Hermes 启动时预热线程、语义补索引线程、写入线程几乎同时开库；网关进程和 MCP 进程同时启动。
本地复现：40 轮 × 12 线程，旧代码失败 3 次。

这里对每一个开库入口都做同样的事：很多轮，每轮一个全新的库，一群线程在同一个瞬间（Barrier）开它。
"""
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
import traceback

import pytest

from nexsandglass.core import sqlite_open
from tests.test_hermes_provider import env  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUNDS = 25
THREADS = 10


def _stampede(rounds, threads, make_round):
    """每轮：make_round(round_dir) 返回一个开库函数；threads 个线程在同一瞬间调用它。返回所有异常。"""
    import tempfile
    errors = []
    for r in range(rounds):
        d = tempfile.mkdtemp(prefix=f"nyx-open-{r}-")
        opener = make_round(d)
        gate = threading.Barrier(threads)

        def go(i):
            gate.wait()
            try:
                opener(i)
            except Exception:
                errors.append(traceback.format_exc())

        ts = [threading.Thread(target=go, args=(i,)) for i in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    return errors


def test_memid_hub_concurrent_first_open(env):  # noqa: F811
    from nexsandglass.core import memid

    def make(d):
        memid.set_db_path(os.path.join(d, "nyx.db"))
        return lambda i: memid.get_conn().execute("SELECT COUNT(*) FROM memories").fetchone()

    errors = _stampede(ROUNDS, THREADS, make)
    assert not errors, f"{len(errors)} 次开库失败：\n{errors[0]}"


def test_every_store_concurrent_first_open(env, monkeypatch):  # noqa: F811
    """同一轮里，所有模块的开库入口一起开：ID 中枢、语义库、FTS、影子沙 / 织线 / 双时态（同一个文件）、Déjà Vu。"""
    from nexsandglass.core import memid, sandglass_sqlite, semantic
    from nexsandglass.dejavu.mist import Mist
    from nexsandglass.engram.loops import temporal_fact
    from nexsandglass.features import shadow_sand, weavethread

    def make(d):
        memid.set_db_path(os.path.join(d, "nyx.db"))
        shadow = os.path.join(d, "shadow_sand.db")
        monkeypatch.setattr(sandglass_sqlite, "_DB", os.path.join(d, "sandglass.db"))
        monkeypatch.setattr(weavethread, "_DB", shadow)
        shadow_sand.set_shadow_path(shadow)
        shadow_sand._conn = None
        openers = [
            lambda: memid.get_conn().execute("SELECT COUNT(*) FROM memories").fetchone(),
            lambda: semantic._conn(os.path.join(d, "semantic.db")).close(),
            lambda: sandglass_sqlite._get_db().close(),
            lambda: shadow_sand._get_conn().execute("SELECT COUNT(*) FROM trust").fetchone(),
            lambda: weavethread._ensure_table(),
            lambda: temporal_fact._ensure_table(shadow),
            lambda: Mist(os.path.join(d, "mist.db"))._conn(),
        ]
        return lambda i: openers[i % len(openers)]()

    errors = _stampede(ROUNDS, 14, make)
    assert not errors, f"{len(errors)} 次开库失败：\n{errors[0]}"


def test_processes_open_the_same_new_db_at_once(tmp_path):
    """网关进程与 MCP 进程同时启动：跨进程没有进程内锁可用，靠撞锁重试。"""
    rounds, procs = 6, 6
    start = time.time() + 3.0
    script = textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {ROOT!r})
        from nexsandglass.core import memid
        out = []
        for r in range({rounds}):
            memid.set_db_path(os.path.join({str(tmp_path)!r}, f"r{{r}}", "nyx.db"))
            while time.time() < {start} + r * 0.4:
                time.sleep(0.001)
            try:
                memid.get_conn().execute("SELECT COUNT(*) FROM memories").fetchone()
                out.append("ok")
            except Exception as e:
                out.append("FAIL:" + type(e).__name__ + ":" + str(e).replace(" ", "_"))
        print("\\n".join(out))
    """)
    ps = [subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           env=dict(os.environ, NEXSANDBASE_HOME=str(tmp_path / "home")))
          for _ in range(procs)]
    results = []
    for p in ps:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        results += out.split()
    fails = [r for r in results if r != "ok"]
    assert len(results) == rounds * procs and not fails, fails


# ── sqlite_open 自身的行为 ──────────────────────────────────

def test_retry_on_lock_error_then_succeed():
    calls = []

    def setup(c):
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")

    c = sqlite3.connect(":memory:")
    sqlite_open.retry_locked(c, setup, timeout=5)
    assert len(calls) == 3


def test_other_errors_are_not_retried():
    calls = []

    def setup(c):
        calls.append(1)
        raise sqlite3.OperationalError("no such table: x")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        sqlite_open.retry_locked(sqlite3.connect(":memory:"), setup, timeout=5)
    assert len(calls) == 1


def test_gives_up_after_timeout():
    def setup(c):
        raise sqlite3.OperationalError("database is locked")

    t = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        sqlite_open.retry_locked(sqlite3.connect(":memory:"), setup, timeout=0.3)
    assert time.monotonic() - t < 2


def test_waits_out_another_process_holding_the_lock(tmp_path):
    """另一个连接拿着排他锁 0.5 秒（还是旧的 rollback 日志模式）：切 WAL + 建表要等它，而不是失败。"""
    path = str(tmp_path / "x.db")
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("CREATE TABLE IF NOT EXISTS a (v)")
    holder.execute("BEGIN EXCLUSIVE")
    threading.Timer(0.5, lambda: holder.execute("COMMIT")).start()
    c = sqlite_open.connect(path, timeout=10, setup=lambda c: (c.execute("CREATE TABLE IF NOT EXISTS b (v)"), c.commit()))
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert {r[0] for r in c.execute("SELECT name FROM sqlite_master")} >= {"a", "b"}
    c.close()
    holder.close()


def test_failed_setup_closes_connection(tmp_path):
    seen = []

    def setup(c):
        seen.append(c)
        raise ValueError("boom")

    with pytest.raises(ValueError):
        sqlite_open.connect(str(tmp_path / "y.db"), setup=setup)
    with pytest.raises(sqlite3.ProgrammingError):
        seen[0].execute("SELECT 1")
