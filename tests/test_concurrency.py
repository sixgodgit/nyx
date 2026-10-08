"""tests/test_concurrency.py — 多个写入方同时工作时不丢、不乱（v7.13.3）

排查 2026-10-09 诊断报告时一起查出来的同类问题（都是「多个写入方没有协调 + 失败被吞」）：
  1. 遗忘 / 还原改写日志不拿锁：期间追加的那一行被替换掉 —— 并发压测每轮丢 1–5 条真实对话
  2. ID 中枢进程内只有一个连接、多线程共用：事务边界属于连接不属于线程，
     报 `cannot start a transaction within a transaction`，或者把别人的半截写一起提交 / 回滚
  3. 固定名字的临时文件：两个写入方互相截断 / 挪走对方的 .tmp（生产日志 sandglass.idx.tmp）
  4. cold_migration 删日志行 → 之后所有记忆的行号整体错位
v7.13 起后台线程变多（语义补索引、预热、Hermes 串行写线程），这些从偶发变成必然。
"""
import os
import subprocess
import sys
import textwrap
import threading
import traceback

from tests.test_hermes_provider import env  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _lost(memid, journal, ids):
    text = open(journal, encoding="utf-8").read()
    return [m for m in ids if m and memid.get(m) and memid.get(m)["text"] not in text]


def test_appends_survive_concurrent_forget_and_restore(env):  # noqa: F811
    from nexsandglass.core import sandglass_log, erasure, memid, quarantine
    for i in range(200):
        sandglass_log.log_message(f"背景记录 {i}", "user")
    stop, written, errors = threading.Event(), [], []

    def writer():
        i = 0
        while not stop.is_set() and i < 2000:
            try:
                written.append(sandglass_log.log_message(f"新对话 {i} 绝不能丢", "user", return_id=True))
            except Exception:
                errors.append(traceback.format_exc())
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    try:
        for k in range(30):
            sandglass_log.log_message(f"要删的 XYZ{k}", "user")
            r = erasure.forget({"contains": f"XYZ{k}"}, apply=True, mode="quarantine" if k % 2 else "purge")
            if k % 4 == 1:
                assert quarantine.restore(r["preview"][0]["mem_id"])["ok"]
    except Exception:
        errors.append(traceback.format_exc())
    finally:
        stop.set()
        t.join()
    assert not errors, errors[0]
    assert all(written), "有写入失败（锁超时？）"
    assert _lost(memid, env.journal, written) == [], "日志里丢了已经写进中枢的记忆"
    assert memid.verify(env.journal)["ok"]


def test_appends_from_another_process_survive_forget(env):  # noqa: F811
    """Hermes 与 MCP 是两个进程：锁必须跨进程。"""
    from nexsandglass.core import sandglass_log, erasure, memid
    for i in range(100):
        sandglass_log.log_message(f"背景记录 {i}", "user")
    script = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {ROOT!r})
        from nexsandglass.core import memid, sandglass_log
        memid.set_db_path({str(env.home / "nyx.db")!r})
        sandglass_log._SANDGLASS = {env.journal!r}
        ids = [sandglass_log.log_message(f"另一个进程写的第 {{i}} 条", "user", return_id=True) for i in range(300)]
        print("\\n".join(i or "FAIL" for i in ids))
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True,
                            env=dict(os.environ, NEXSANDBASE_HOME=str(env.home)))
    for k in range(25):
        sandglass_log.log_message(f"要删的 ABC{k}", "user")
        erasure.forget({"contains": f"ABC{k}"}, apply=True, mode="purge")
    out, _ = proc.communicate(timeout=300)
    ids = out.split()
    assert len(ids) == 300 and "FAIL" not in ids
    assert _lost(memid, env.journal, ids) == []
    assert memid.verify(env.journal)["ok"]


def test_each_thread_has_its_own_transaction(env):  # noqa: F811
    """线程 B 的 commit 不能把线程 A 未提交的写一起提交；A 的回滚也不能带走 B 的写。"""
    from nexsandglass.core import memid
    memid.get_conn().execute("CREATE TABLE IF NOT EXISTS _t (v TEXT)")
    memid.get_conn().commit()
    a_wrote, b_done, out = threading.Event(), threading.Event(), {}

    def a():
        c = memid.get_conn()
        c.execute("INSERT INTO _t VALUES ('A-uncommitted')")
        a_wrote.set()
        b_done.wait(10)
        c.rollback()

    def b():
        a_wrote.wait(10)
        c = memid.get_conn()
        out["same_conn"] = c is memid.get_conn()
        try:
            c.execute("INSERT INTO _t VALUES ('B')")
            c.commit()
        except Exception as e:           # A 持有写锁，B 会等到超时之前 A 回滚 —— 这里只记录
            out["b_err"] = repr(e)
        b_done.set()

    ta, tb = threading.Thread(target=a), threading.Thread(target=b)
    ta.start()
    tb.start()
    ta.join()
    tb.join()
    rows = [r[0] for r in memid.get_conn().execute("SELECT v FROM _t")]
    assert "A-uncommitted" not in rows, "线程 B 的 commit 把线程 A 未提交的写一起提交了"


def test_threads_get_distinct_connections(env):  # noqa: F811
    from nexsandglass.core import memid
    got = []
    ts = [threading.Thread(target=lambda: got.append(id(memid.get_conn()))) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(got)) == 4
    assert memid.get_conn() is memid.get_conn()            # 同一线程内仍是同一个连接


def test_concurrent_index_writes_never_corrupt(env):  # noqa: F811
    from nexsandglass.features import sandglass_vault as v
    errors = []

    def w(k):
        try:
            for i in range(15):
                v._write_idx({f"tok{k}_{j}": [j + 1] for j in range(400)}, covered_lines=k)
        except Exception:
            errors.append(traceback.format_exc())

    ts = [threading.Thread(target=w, args=(k,)) for k in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors[0]
    lines = open(v._IDX, encoding="utf-8").read().splitlines()
    body = [l for l in lines if l and not l.startswith("#")]
    owners = {l.split("_")[0] for l in body}
    assert len(body) == 400 and len(owners) == 1, "索引文件被两个写入方拼在了一起"
    assert not [f for f in os.listdir(os.path.dirname(v._IDX)) if f.endswith(".tmp")], "留下了临时文件"


def test_cold_migration_refuses_to_shift_line_numbers(env, monkeypatch):  # noqa: F811
    from datetime import datetime, timedelta
    from nexsandglass.core import clock, memid, sandglass_archive, sandglass_log
    with clock.frozen(datetime.now() - timedelta(days=90)):
        sandglass_log.log_message("九十天前的一条记忆", "user")
    sandglass_log.log_message("今天的记忆", "user")
    monkeypatch.setattr(sandglass_archive, "_VAULT", str(env.home))
    before = open(env.journal, encoding="utf-8").read()
    r = sandglass_archive.cold_migration(dry_run=False)
    assert r.get("skipped") and r["moved"] == 0
    assert open(env.journal, encoding="utf-8").read() == before
    assert memid.verify(env.journal)["ok"]
