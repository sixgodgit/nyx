"""tests/test_erasure_disk.py — 真删之后，磁盘上也没了（v7.14.0）

以前的验收（erasure.verify_erasure）用 SQL 查每个子系统 —— 查不到删掉的行。可 SQLite 删行只是把页面
标成可复用：全文库 sandglass.db 自己存着整段正文，删行后正文还在空闲页里；FTS5 删掉的词元留在索引段里，
**VACUUM 都清不掉**（要先 optimize）。以前只有隔离区 purge 对 nyx.db 做了 VACUUM，
所以「彻底删除」之后，按原始字节读一下 sandglass.db 就能读出被删的内容。

这里按原始字节扫整个数据目录（库文件、WAL、空闲页都在内）。
"""
import os

import pytest

from tests.test_hermes_provider import _provider, _reindex, env  # noqa: F401  复用隔离目录

NEEDLES = ["zqx7781walrus", "紫色长颈鹿在火星上跳舞"]


def _trace(home):
    hits = []
    for root, _, files in os.walk(str(home)):
        for name in files:
            p = os.path.join(root, name)
            data = open(p, "rb").read()
            low = data.lower()
            if any(n.encode() in data or n.lower().encode() in low for n in NEEDLES):
                hits.append(os.path.relpath(p, str(home)))
    return sorted(hits)


@pytest.fixture()
def seeded(env, monkeypatch):  # noqa: F811
    from nexsandglass.core import memid, semantic
    from tests.fixtures import fake_embed
    for k in ("NYX_EMBED", "NYX_EMBED_PROVIDER", "NYX_EMBED_MODEL", "EMBEDDING_API_URL"):
        monkeypatch.delenv(k, raising=False)
    semantic.set_provider(fake_embed.ConceptBag())
    p = _provider()
    for i in range(30):                                   # 有足够多的别的记忆：被删的那条不是库里唯一一页
        p.sync_turn(f"第 {i} 天：今天读了一本书，去公司上班，晚上跑步 {i} 公里", "")
    p.sync_turn('我做了个梦："紫色长颈鹿在火星上跳舞"，暗号 zqx7781walrus', "这个梦很有意思")
    for i in range(30, 40):
        p.sync_turn(f"第 {i} 天：做了番茄炒蛋", "")
    semantic.index_pending(limit=None)
    _reindex(env)
    mid = memid.get_conn().execute("SELECT mem_id FROM memories WHERE text LIKE '%zqx7781walrus%'").fetchone()[0]
    assert _trace(env.home), "前提：数据目录里确实有"
    yield mid
    assert semantic.wait_idle()
    semantic.set_provider(None)


def test_purge_mode_leaves_nothing_on_disk(env, seeded):  # noqa: F811
    from nexsandglass.core import erasure
    r = erasure.forget({"mem_id": seeded}, apply=True, mode="purge")
    assert r["ok"], r
    assert all(v == "ok" for v in r["disk"].values()), r["disk"]
    assert erasure.verify_erasure(NEEDLES)["clean"]
    assert _trace(env.home) == []


def test_quarantine_then_purge_leaves_nothing_on_disk(env, seeded):  # noqa: F811
    from nexsandglass.core import erasure
    assert erasure.forget({"mem_id": seeded}, apply=True, mode="quarantine")["ok"]
    v = erasure.verify_erasure(NEEDLES)
    assert v["clean"], v["found_in"]                       # 隔离期内：检索不到，但原文还在磁盘上（为了能还原）
    p = erasure.purge(mem_ids=[seeded], apply=True)
    assert p["ok"] and p["vacuumed"], p
    assert _trace(env.home) == []


def test_disk_wipe_runs_outside_the_journal_lock(env, seeded, monkeypatch):  # noqa: F811
    """VACUUM 要几秒：持着日志锁做，别的进程（MCP、网关）追加日志会等锁超时、写不进去。"""
    from nexsandglass.core import erasure, memid
    held = []
    real = erasure.wipe_free_space

    def spy():
        held.append(bool(getattr(memid._jl, "held", {}).get(os.path.abspath(env.journal))))
        return real()
    monkeypatch.setattr(erasure, "wipe_free_space", spy)
    r = erasure.forget({"mem_id": seeded}, apply=True, mode="purge")
    assert r["ok"] and held == [False], held
    assert _trace(env.home) == []
