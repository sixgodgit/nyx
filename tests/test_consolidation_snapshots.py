"""tests/test_consolidation_snapshots.py — 巩固快照跟着数据目录走；遗忘和 scrub 都管得到它

runtime/consolidation.py 每次跑之前，把这批记忆的全文存成快照（consolidation_snapshots/*.json，给 restore_snapshot 回滚）。
以前有两个问题：
  1. 快照目录自己算：没设 NEXSANDBASE_HOME 时落到 ~/.nyx，而真正的默认数据目录是 ~/.neurobase。
     MCP 服务、直接用 Python 时就是这样 —— 不在文档里，也不在备份里
  2. 遗忘不管快照：mode="purge"、隔离区到期 purge 之后，原文还在快照里，回滚一次就回来了；
     scrub 也只在快照碰巧落在数据目录里时才扫得到

巩固默认关（orchestrator 的 policy），只有 NYX_USE_FACADE=1 走 facade.consolidate 才跑得到，所以一直没人碰上。
"""
import os
import subprocess
import sys

import pytest

from nexsandglass.engram.types import Memory
from tests.test_erasure_disk import NEEDLES, _trace, seeded  # noqa: F401  复用种好的数据目录
from tests.test_hermes_provider import _provider, env  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def snapdir(env, monkeypatch):  # noqa: F811
    from nexsandglass.runtime import consolidation
    d = str(env.home / "consolidation_snapshots")
    monkeypatch.setattr(consolidation, "_SNAPSHOT_DIR", d)
    return d


@pytest.fixture()
def snap(env, seeded, snapdir):  # noqa: F811
    """真走一次 facade.consolidate：快照里有被删的那条，也有别的记忆。"""
    from nexsandglass.runtime import facade
    r = facade.consolidate(tag="nightly")
    assert r["ok"] and r["action"] == "consolidate", r
    path = r["detail"]["snapshot"]
    assert path == os.path.join(snapdir, "nightly.json")
    assert os.path.join("consolidation_snapshots", "nightly.json") in _trace(env.home), "前提：快照里确实有"
    return path


# ── 1. 目录 ────────────────────────────────────────────────────

def test_snapshot_dir_is_inside_the_data_dir():
    from nexsandglass.core import sandglass_paths
    from nexsandglass.runtime import consolidation
    assert consolidation._SNAPSHOT_DIR == os.path.join(sandglass_paths._NB, "consolidation_snapshots")


def test_default_data_dir_when_env_is_unset(tmp_path):
    """没设 NEXSANDBASE_HOME（MCP 服务、直接用 Python）：快照在 ~/.neurobase 下，和别的数据在一起，不在 ~/.nyx。"""
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "NEXSANDBASE_HOME"}
    env.update(HOME=str(home), PYTHONPATH=ROOT)
    code = ("from nexsandglass.runtime.consolidation import _snapshot\n"
            "from nexsandglass.engram.types import Memory\n"
            "print(_snapshot([Memory(memory_id='m1', type='semantic', content='x')], 't'))")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-1000:]
    assert r.stdout.strip().splitlines()[-1] == str(home / ".neurobase" / "consolidation_snapshots" / "t.json")
    assert not (home / ".nyx" / "consolidation_snapshots").exists()


# ── 2. 遗忘 ────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["purge", "quarantine"])
def test_forget_takes_the_copy_out_of_snapshots(env, seeded, snap, mode):  # noqa: F811
    from nexsandglass.core import erasure
    from nexsandglass.runtime.consolidation import restore_snapshot
    n_before = len(restore_snapshot(snap))
    r = erasure.forget({"mem_id": seeded}, apply=True, mode=mode)
    assert r["ok"], r
    # 只删那一条：种子是同一两秒里写进来的，按 engram:<ts> 认会把别的记忆一起删掉
    assert r["snapshots"] == {"files": 1, "entries": 1, "left": []}, r["snapshots"]
    v = erasure.verify_erasure(NEEDLES)
    assert v["clean"], v["found_in"]
    left = restore_snapshot(snap)                         # 别的记忆的回滚点还在
    assert len(left) == n_before - 1
    assert not any(n in m.content for m in left for n in NEEDLES)
    if mode == "quarantine":                              # 隔离期内原文只在隔离区里（可还原），到期 purge 后磁盘上没了
        assert v["in_quarantine"]
        assert erasure.purge(mem_ids=[seeded], apply=True)["ok"]
    assert _trace(env.home) == []


def test_purge_catches_copies_that_appeared_after_forget(env, seeded, snapdir):  # noqa: F811
    """forget 之后才出现的快照（修复之前就忘掉的、从旧备份拷回来的）：verify_erasure 报出来，隔离区到期 purge 时删掉。
    这一条的 memory_id 对不上，只能按正文认出来。"""
    from nexsandglass.core import erasure, memid
    from nexsandglass.runtime import consolidation
    text = memid.get(seeded)["text"]
    assert erasure.forget({"mem_id": seeded}, apply=True)["ok"]
    path = consolidation._snapshot([
        Memory(memory_id="engram:2026-01-01 00:00:00", type="episodic", content=text[:300]),
        Memory(memory_id="engram:2026-01-02 00:00:00", type="semantic", content="周末去爬山"),
    ], "stale")

    v = erasure.verify_erasure(NEEDLES)
    assert not v["clean"] and "stale.json" in v["found_in"]["snapshots"], v["found_in"]

    p = erasure.purge(mem_ids=[seeded], apply=True)
    assert p["ok"] and p["snapshots"]["entries"] == 1, p
    assert [m.content for m in consolidation.restore_snapshot(path)] == ["周末去爬山"]
    assert _trace(env.home) == []


def test_snapshot_left_with_only_forgotten_entries_is_deleted(env, seeded, snapdir):  # noqa: F811
    from nexsandglass.core import erasure, memid
    from nexsandglass.runtime import consolidation
    path = consolidation._snapshot([Memory(memory_id="x", type="episodic", content=memid.get(seeded)["text"],
                                           source_id=seeded)], "only_this")
    r = erasure.forget({"mem_id": seeded}, apply=True, mode="purge")
    assert r["ok"] and r["snapshots"] == {"files": 1, "entries": 1, "left": []}, r["snapshots"]
    assert not os.path.exists(path)


def test_unreadable_snapshot_is_reported_not_ignored(env, seeded, snapdir):  # noqa: F811
    """快照文件坏了、读不懂：不替它改，但里面还有被删的正文，就不能报「删干净了」。"""
    from nexsandglass.core import erasure, memid
    text = memid.get(seeded)["text"]
    os.makedirs(snapdir, exist_ok=True)
    with open(os.path.join(snapdir, "broken.json"), "w", encoding="utf-8") as f:
        f.write('[{"memory_id": "m", "content": "' + text)    # 写到一半断了
    r = erasure.forget({"mem_id": seeded}, apply=True, mode="purge")
    assert not r["ok"] and r["action"] == "partial", r
    assert any("broken.json" in p for p in r["problems"]), r["problems"]
    assert "snapshots" in erasure.verify_erasure(NEEDLES)["found_in"]


def test_snapshot_that_cannot_be_rewritten_does_not_abort_forget(env, seeded, snap, monkeypatch):  # noqa: F811
    """快照改不动（权限、磁盘满）：走到这一步时日志已经抹了，不能抛出去让墓碑打不上；报 partial。"""
    from nexsandglass.core import erasure, fsutil
    real = fsutil.atomic_write

    def flaky(path, *a, **k):
        if "consolidation_snapshots" in str(path):
            raise PermissionError("只读")
        return real(path, *a, **k)
    monkeypatch.setattr(fsutil, "atomic_write", flaky)
    r = erasure.forget({"mem_id": seeded}, apply=True, mode="purge")
    assert r["hub"] == 1 and r["journal_lines"] >= 1, r      # 级联走完了
    assert not r["ok"] and r["action"] == "partial"
    assert any("nightly.json" in p and "改不了" in p for p in r["problems"]), r["problems"]


# ── 3. scrub ──────────────────────────────────────────────────

def test_scrub_covers_snapshots(env, snapdir, monkeypatch):  # noqa: F811
    """升级之前存下来的密钥也可能在快照里：scrub 扫得到、按字段隐去，隐去之后照样能回滚。"""
    from nexsandglass.core import secret_scrub
    from nexsandglass.runtime import consolidation
    from tests.test_secret_filter import HF, PW
    monkeypatch.setenv("NYX_EMBED", "off")
    _provider().sync_turn("我住在鹿特丹", "记住了")
    path = consolidation._snapshot([
        Memory(memory_id="engram:2026-10-01 10:00:00", type="semantic", content=f"帮我配一下，key 是 {HF}，wifi密码是 {PW}"),
        Memory(memory_id="engram:2026-10-01 10:00:01", type="semantic", content="我住在鹿特丹"),
    ], "before_upgrade")
    rel = os.path.relpath(path, str(env.home))

    dry = secret_scrub.run(apply=False)
    assert dry["stores"][rel]["found"] >= 2 and rel in dry["on_disk"] and not dry["clean"], dry["stores"]
    assert HF in open(path, encoding="utf-8").read(), "只读检查不改文件"

    rep = secret_scrub.run(apply=True)
    assert rep["clean"], rep["on_disk"]
    raw = open(path, encoding="utf-8").read()
    assert HF not in raw and PW not in raw
    mems = consolidation.restore_snapshot(path)
    assert "[已隐去: HF token" in mems[0].content and mems[1].content == "我住在鹿特丹"
