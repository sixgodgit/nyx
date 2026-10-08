"""tests/test_semantic_store_guard.py — 2026-10-09 诊断报告的回归与防复发（v7.13.3）

报告：数据目录 ~/.hermes/nexsandglass 里，旧 vector_store 先建了 2 列的 vectors 表；语义层建表静默跳过，
此后每次写入 `no such column: model`，失败被吞成 warning，一个月一条向量都没写进去，自检和 ping 全绿。

这里钉住：
  - 报告里的原始现场（同一条建表语句）下，语义检索照常工作，旧文件不被改动
  - v7.13 时期写对了的 vectors.db 会被搬过来，不必重新嵌入
  - 语义库结构不对时挪开重建，而不是每次写入都失败
  - 任何失败都会出现在 stats / health / doctor / ping / Hermes 系统提示里，而且日志只按 ERROR 报一次
"""
import json
import logging
import os
import sqlite3
import subprocess
import sys

import pytest

from tests.fixtures import fake_embed
from tests.test_hermes_provider import env, _provider, _reindex  # noqa: F401

LEGACY_DDL = "CREATE TABLE IF NOT EXISTS vectors (memory_id TEXT PRIMARY KEY, embedding FLOAT[384])"


@pytest.fixture()
def sem(env, monkeypatch):  # noqa: F811
    from nexsandglass.core import semantic
    for k in ("NYX_EMBED", "NYX_EMBED_PROVIDER", "NYX_EMBED_MODEL", "EMBEDDING_API_URL"):
        monkeypatch.delenv(k, raising=False)
    semantic._status.clear()
    semantic._logged.clear()
    semantic._validated.clear()
    semantic.set_provider(fake_embed.ConceptBag())
    yield semantic
    semantic.wait_idle()
    semantic.set_provider(None)
    semantic._status.clear()


def _snapshot(path):
    c = sqlite3.connect(path)
    try:
        return (c.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall(),
                c.execute("SELECT COUNT(*) FROM vectors").fetchone()[0])
    finally:
        c.close()


def test_report_scenario_legacy_table_no_longer_blocks(sem, env):  # noqa: F811
    legacy = str(env.home / "vectors.db")
    c = sqlite3.connect(legacy)
    c.execute(LEGACY_DDL)
    c.commit()
    c.close()
    before = _snapshot(legacy)

    p = _provider()
    p.sync_turn("上周末领养了一只橘猫，取名叫团子", "")
    p.sync_turn("今天第一次跑完半程马拉松", "")
    sem.wait_idle()
    _reindex(env)

    st = sem.stats()
    assert st["indexed"] == 2 and st["pending"] == 0 and not st["last_error"], st
    assert st["db"].endswith("semantic.db")
    assert sem.search("我养的宠物叫什么"), "语义检索仍然不工作"
    assert sem.health_check()["ok"]
    assert _snapshot(legacy) == before, "旧 vector_store 的文件被改动了 —— 那不是我们的文件"


def test_v713_semantic_vectors_db_is_migrated_without_reembedding(sem, env):  # noqa: F811
    """v7.13 时期如果写对了（数据目录不是 ~/.hermes/nexsandglass 的用户），那些向量要搬过来。"""
    p = _provider()
    p.sync_turn("上周末领养了一只橘猫，取名叫团子", "")
    p.sync_turn("今天第一次跑完半程马拉松", "")
    sem.wait_idle()
    # 把刚建的 semantic.db 伪装成 v7.13 的 vectors.db（同样的 6 列，表名 vectors）
    src = sqlite3.connect(sem._db_path())
    rows = src.execute("SELECT * FROM embeddings").fetchall()
    src.close()
    from nexsandglass.core import memid
    rows.append(("m_forgotten_long_ago", 99, rows[0][2], rows[0][3], rows[0][4], rows[0][5]))
    old = sqlite3.connect(str(env.home / "vectors.db"))
    old.execute("CREATE TABLE vectors (mem_id TEXT PRIMARY KEY, line_start INTEGER, model TEXT NOT NULL, "
                "dim INTEGER NOT NULL, vec BLOB NOT NULL, indexed_at TEXT)")
    old.executemany("INSERT INTO vectors VALUES (?,?,?,?,?,?)", rows)
    old.commit()
    old.close()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(sem._db_path() + suffix):
            os.remove(sem._db_path() + suffix)
    sem._validated.clear()

    calls = []

    class Counting(fake_embed.ConceptBag):
        def encode(self, texts):
            calls.append(len(texts))
            return super().encode(texts)

    sem.set_provider(Counting())
    assert sem.index_pending() == 0 and calls == [], "已迁移的向量又被重新嵌入了"
    st = sem.stats()
    assert st["indexed"] == 2 and st["orphans"] == 0, st          # 已遗忘的那条没有搬过来
    assert memid.get("m_forgotten_long_ago") is None


@pytest.mark.parametrize("bad_ddl", [
    "CREATE TABLE embeddings (memory_id TEXT PRIMARY KEY, embedding BLOB)",           # 列不对
    "CREATE TABLE something_else (x INTEGER)",                                         # 别人的库
])
def test_wrong_semantic_db_is_moved_aside_and_rebuilt(sem, env, bad_ddl):  # noqa: F811
    path = sem._db_path()
    c = sqlite3.connect(path)
    c.execute(bad_ddl)
    c.commit()
    c.close()
    p = _provider()
    p.sync_turn("上周末领养了一只橘猫，取名叫团子", "")
    sem.wait_idle()
    assert sem.stats()["indexed"] == 1 and sem.health_check()["ok"]
    aside = [f for f in os.listdir(env.home) if f.startswith("semantic.db.mismatch-")]
    assert aside, "结构不对的库没有被挪开保留"


def _broken_provider():
    class Broken(fake_embed.ConceptBag):
        name = "fake:broken"

        def encode(self, texts):
            raise RuntimeError("embedding backend exploded")
    return Broken()


def test_failures_surface_everywhere_and_log_once(sem, env, caplog, monkeypatch):  # noqa: F811
    sem.set_provider(_broken_provider())
    caplog.set_level(logging.DEBUG)
    p = _provider()
    for i in range(3):
        p.sync_turn(f"第{i}条：领养了一只橘猫", "")
    sem.wait_idle()

    st = sem.stats()
    assert st["pending"] == 3, "失败时 pending 必须如实报告（以前报 0，看起来像全部完成）"
    assert "exploded" in (st["last_error"] or "") and st["failures"] >= 1
    hc = sem.health_check()
    assert hc["ok"] is False and any("exploded" in x for x in hc["problems"])

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "exploded" in r.getMessage()]
    assert len(errors) == 1, f"同一个错误应只按 ERROR 报一次（不刷屏），实际 {len(errors)} 次"

    from nexsandglass.core import memid
    assert memid.health()["checks"]["semantic_index"]["ok"] is False
    assert memid.health()["ok"] is False

    assert "语义检索这一路不可用" in p._health_warning()
    out = json.loads(p.handle_tool_call("nyx_health", {"quick": True}))
    assert out["checks"]["semantic_index"]["ok"] is False

    from nexsandglass.interfaces import sandglass_mcp
    sent = []
    monkeypatch.setattr(sandglass_mcp, "_send", sent.append)
    sandglass_mcp._handle_tool("sandglass_ping", {}, 1)
    assert json.loads(sent[-1]["result"]["content"][0]["text"])["status"] == "degraded"

    # 修好之后一切恢复绿色
    sem.set_provider(fake_embed.ConceptBag())
    assert sem.index_all() == 3
    assert sem.health_check()["ok"] and p._health_warning() == ""


def test_pending_too_long_is_a_failure(sem, env):  # noqa: F811
    """写进去却一直没被索引（比如后台线程死了）—— 超过宽限期就算故障。"""
    from datetime import datetime, timedelta
    from nexsandglass.core import clock, sandglass_log
    t0 = datetime(2026, 10, 1, 12, 0)
    sem.set_provider(None)
    os.environ["NYX_EMBED"] = "off"
    try:
        with clock.frozen(t0):
            sandglass_log.log_message("领养了一只橘猫", "user")       # 语义关着时写入：没有向量
    finally:
        os.environ.pop("NYX_EMBED")
    sem.set_provider(fake_embed.ConceptBag())
    with clock.frozen(t0 + timedelta(minutes=3)):
        assert sem.health_check()["ok"], "宽限期内不算故障"
    with clock.frozen(t0 + timedelta(minutes=30)):
        hc = sem.health_check()
        assert hc["ok"] is False and any("还没有向量" in x for x in hc["problems"]), hc


def test_config_asks_for_semantic_but_backend_missing(env, monkeypatch):  # noqa: F811
    import importlib.util
    from nexsandglass.core import semantic
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a, **k: None if name == "sentence_transformers" else _orig(name, *a, **k))
    monkeypatch.setenv("NYX_EMBED", "local")
    semantic.set_provider(None)
    try:
        hc = semantic.health_check()
        assert hc["ok"] is False and any("sentence-transformers" in x for x in hc["problems"]), hc
    finally:
        monkeypatch.delenv("NYX_EMBED")
        semantic.set_provider(None)
        semantic.provider()


_orig = __import__("importlib.util").util.find_spec


def test_doctor_exit_code_reflects_health(sem, env):  # noqa: F811
    env_vars = dict(os.environ, NEXSANDBASE_HOME=str(env.home),
                    PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    NYX_EMBED_PROVIDER="tests.fixtures.fake_embed:provider")
    _provider().sync_turn("领养了一只橘猫，取名叫团子", "")
    sem.wait_idle()
    _reindex(env)
    r = subprocess.run([sys.executable, "-m", "nexsandglass.doctor", "--json"], env=env_vars,
                       capture_output=True, text=True, timeout=120)
    h = json.loads(r.stdout)
    assert h["checks"]["semantic_index"]["ok"] is True, h["checks"]["semantic_index"]
    assert r.returncode == (0 if h["ok"] else 1)
    env_vars["NYX_EMBED_PROVIDER"] = "tests.fixtures.does_not_exist:provider"
    r2 = subprocess.run([sys.executable, "-m", "nexsandglass.doctor"], env=env_vars,
                        capture_output=True, text=True, timeout=120)
    assert r2.returncode == 1 and "semantic_index" in r2.stdout and "❌" in r2.stdout


def test_standout_threshold_works_with_very_few_memories():
    """新用户只有一两条记忆时，命中也必须能「明显高于背景」。"""
    from nexsandglass.core.semantic import _standout_threshold as cut
    assert 0.99 > cut([0.99, 0.05])                          # 两条：以前门槛 1.9，命中进不来
    assert 0.99 > cut([0.99, 0.05, 0.06]) and 0.06 < cut([0.99, 0.05, 0.06])
    assert 0.5 > cut([0.5])                                  # 只有一条
    assert cut([0.3] * 50) > 0.3                             # 全都一样像：一个也不算证据
    bg = [0.1 + (i % 7) * 0.01 for i in range(200)]
    assert 0.8 > cut(bg + [0.8]) and max(bg) < cut(bg + [0.8])


def test_backfill_in_progress_is_not_a_failure(sem, env):  # noqa: F811
    """升级后回填几千条要几十分钟：只要最近几分钟还在写入向量，就是「回填中」而不是故障；停滞了才报。"""
    from datetime import datetime, timedelta
    from nexsandglass.core import clock, sandglass_log
    t0 = datetime(2026, 10, 1, 12, 0)
    sem.set_provider(None)
    os.environ["NYX_EMBED"] = "off"
    try:
        with clock.frozen(t0):
            for i in range(5):
                sandglass_log.log_message(f"升级前的旧记忆 {i}", "user")
    finally:
        os.environ.pop("NYX_EMBED")
    sem.set_provider(fake_embed.ConceptBag())
    with clock.frozen(t0 + timedelta(days=30)):
        assert sem.index_pending(limit=2) == 2                 # 回填进行到一半
        hc = sem.health_check()
        assert hc["ok"] and hc["state"] == "backfilling" and hc["pending"] == 3, hc
    with clock.frozen(t0 + timedelta(days=30, minutes=20)):  # 之后 20 分钟没有任何进展
        hc = sem.health_check()
        assert hc["ok"] is False and any("最近一次成功写入向量" in x for x in hc["problems"]), hc
