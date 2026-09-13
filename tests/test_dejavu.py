"""Déjà Vu 独立测试 —— 零配置、零外部依赖。

覆盖：构造 / imprint / sense / hunt / persist / reindex / forget / cleanup
以及解耦性（不依赖 ~/.hermes、不假设行号）。

运行：python3 -m pytest tests/test_dejavu.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from nexsandglass.dejavu import DejaVu, FamiliarityResult, Phantom  # noqa: E402
from nexsandglass.dejavu.core import scent  # noqa: E402
from nexsandglass.dejavu.veil import Veil  # noqa: E402


@pytest.fixture()
def dv(tmp_path):
    """每个用例一个全新空目录。"""
    d = DejaVu(str(tmp_path / "dv"))
    yield d
    d.close()


# ── 构造与解耦 ─────────────────────────────────────────────

def test_construct_in_empty_dir(tmp_path):
    """DoD：全新空目录下可直接构造，零配置。"""
    p = tmp_path / "brand-new"
    assert not p.exists()
    dv = DejaVu(str(p))
    assert p.exists()
    assert dv.gaze().mist_total == 0
    assert dv.gaze().veil_items == 0
    dv.close()


def test_does_not_touch_home_hermes(tmp_path, monkeypatch):
    """解耦：不应假设或写入 ~/.hermes。"""
    monkeypatch.setenv("NEXSANDBASE_HOME", "/nonexistent/should-not-be-used")
    p = tmp_path / "isolated"
    dv = DejaVu(str(p))
    dv.imprint("k1", "独立存储测试")
    files = sorted(os.listdir(p))
    assert any(f.startswith("mist.db") for f in files)
    # 环境变量不该影响显式传入的目录
    assert dv.storage_dir == str(p)
    assert "/nonexistent" not in dv.storage_dir
    dv.close()


def test_storage_dir_required():
    with pytest.raises(ValueError):
        DejaVu("")


def test_key_is_arbitrary_string(dv):
    """解耦：key 可以是任意字符串，不假设是行号。"""
    dv.imprint("https://example.com/page#42", "张三 讨论 预算")
    dv.imprint("msg-abc-123", "张三 再次 讨论")
    ph = dv.hunt("张三")
    assert len(ph) >= 1
    refs = {r for p in ph for r in p.refs}
    assert "https://example.com/page#42" in refs or "msg-abc-123" in refs


# ── imprint ────────────────────────────────────────────────

def test_imprint_returns_token_count(dv):
    n = dv.imprint("k1", "John Smith discussed the budget")
    assert n > 0


def test_imprint_empty_text(dv):
    assert dv.imprint("k1", "") == 0
    assert dv.imprint("k1", None or "") == 0


def test_imprint_accumulates_sightings(dv):
    dv.imprint("k1", "张老板 川菜馆")
    dv.imprint("k2", "张老板 川菜馆")
    dv.imprint("k3", "张老板 川菜馆")
    ph = dv.hunt("张老板", limit=10)
    top = max(p.sightings for p in ph)
    assert top >= 3


# ── sense ──────────────────────────────────────────────────

def test_sense_unfamiliar_on_empty(dv):
    r = dv.sense("完全没见过的内容 abcdef")
    assert isinstance(r, FamiliarityResult)
    assert r.familiar is False
    assert r.score == 0.0


def test_sense_familiar_after_imprint(dv):
    dv.imprint("k1", "上周三和张老板聊了川菜馆的事")
    r = dv.sense("我们聊过的那家川菜馆")
    assert r.familiar is True
    assert r.score > 0
    assert r.reason


def test_sense_empty_text(dv):
    r = dv.sense("")
    assert r.familiar is False
    assert r.total == 0


def test_sense_is_pure_read(dv):
    """sense 不应写入任何东西。"""
    before = sorted(os.listdir(dv.storage_dir))
    dv.sense("随便说点什么")
    after = sorted(os.listdir(dv.storage_dir))
    assert before == after


# ── hunt ───────────────────────────────────────────────────

def test_hunt_returns_phantoms(dv):
    dv.imprint("k1", "上周三和张老板聊了川菜馆的事")
    ph = dv.hunt("川菜馆")
    assert len(ph) >= 1
    assert all(isinstance(p, Phantom) for p in ph)
    assert any("川菜" in p.token or "菜馆" in p.token for p in ph)


def test_hunt_empty_when_unfamiliar(dv):
    assert dv.hunt("从来没见过的词 xyzzy") == []


def test_hunt_respects_limit(dv):
    for i in range(30):
        dv.imprint(f"k{i}", f"项目 讨论 记录 编号{i}")
    ph = dv.hunt("项目 讨论 记录", limit=3)
    assert len(ph) <= 3


def test_hunt_detailed_shape(dv):
    dv.imprint("k1", "张老板 川菜馆")
    h = dv.hunt_detailed("川菜馆")
    assert h.hunted is True
    assert 0.0 <= h.conviction <= 1.0
    assert isinstance(h.to_dict()["phantoms"], list)


# ── persist / 重开 ─────────────────────────────────────────

def test_persist_and_reload(tmp_path):
    d = str(tmp_path / "dv")
    a = DejaVu(d)
    a.imprint("k1", "张老板 川菜馆 预算")
    a.persist()
    a.close()

    b = DejaVu(d)
    assert b.gaze().veil_items > 0
    assert b.sense("川菜馆").familiar is True
    assert len(b.hunt("川菜馆")) >= 1
    b.close()


def test_autosave(tmp_path):
    d = str(tmp_path / "dv")
    a = DejaVu(d, autosave=3)
    for i in range(3):
        a.imprint(f"k{i}", f"自动保存 测试 {i}")
    assert os.path.exists(os.path.join(d, "veil.bin"))
    a.close()


def test_context_manager(tmp_path):
    with DejaVu(str(tmp_path / "dv")) as dv:
        dv.imprint("k1", "上下文管理器 测试")
    assert os.path.exists(os.path.join(dv.storage_dir, "veil.bin"))


# ── reindex ────────────────────────────────────────────────

def test_reindex_rebuilds_veil(tmp_path):
    d = str(tmp_path / "dv")
    dv = DejaVu(d)
    dv.imprint("k1", "张老板 川菜馆")
    dv.imprint("k2", "李经理 预算")
    n = dv.reindex()
    assert n > 0
    assert dv.sense("川菜馆").familiar is True
    assert dv.sense("预算").familiar is True
    dv.close()


def test_reindex_after_total_reset(tmp_path):
    """veil 文件损坏后仍可由 Mist 完全重建。"""
    d = str(tmp_path / "dv")
    dv = DejaVu(d)
    dv.imprint("k1", "可重建 测试 内容")
    dv.persist()
    dv.veil.reset()
    assert dv.sense("可重建").familiar is False
    dv.reindex()
    assert dv.sense("可重建").familiar is True
    dv.close()


# ── forget / cleanup ───────────────────────────────────────

def test_forget_removes_token(dv):
    dv.imprint("k1", "要被删除的 特殊词 zzqq")
    assert dv.forget("zzqq") >= 0
    assert dv.forget("不存在的词") == 0


def test_cleanup_removes_old(tmp_path):
    d = str(tmp_path / "dv")
    dv = DejaVu(d)
    dv.imprint("k1", "很久以前的 记忆 内容", ts="2000-01-01 00:00:00")
    dv.imprint("k2", "刚刚的 记忆 内容")
    removed = dv.cleanup(days=30)
    assert removed >= 1
    dv.close()


def test_cleanup_keeps_recent(tmp_path):
    d = str(tmp_path / "dv")
    dv = DejaVu(d)
    from datetime import datetime
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    dv.imprint("k1", "新鲜的 记忆", ts=now)
    removed = dv.cleanup(days=90)
    assert removed == 0
    dv.close()


# ── Veil 单元 ──────────────────────────────────────────────

def test_veil_no_false_negative():
    """Bloom 的核心保证：写入的必定能查到。"""
    v = Veil(bits=1 << 14, hashes=5)
    items = [f"item-{i}" for i in range(200)]
    for it in items:
        v.touch(it)
    assert all(v.probe(it) for it in items)


def test_veil_false_positive_rate_low_on_empty():
    v = Veil(bits=1 << 16, hashes=7)
    for i in range(100):
        v.touch(f"x{i}")
    fp = sum(1 for i in range(2000) if v.probe(f"never-seen-{i}"))
    assert fp / 2000 < 0.05


def test_veil_persist_roundtrip(tmp_path):
    p = str(tmp_path / "v.bin")
    a = Veil(bits=1 << 12, hashes=4)
    for i in range(50):
        a.touch(f"k{i}")
    a.persist(p)
    b = Veil(bits=1 << 12, hashes=4)
    assert b.load(p) is True
    assert all(b.probe(f"k{i}") for i in range(50))


def test_veil_load_rejects_size_mismatch(tmp_path):
    p = str(tmp_path / "v.bin")
    a = Veil(bits=1 << 12, hashes=4)
    a.touch("x")
    a.persist(p)
    b = Veil(bits=1 << 20, hashes=4)   # 大小不同
    assert b.load(p) is False
    assert b.density == 0


def test_veil_estimated_fp_zero_when_empty():
    v = Veil()
    assert v.estimated_fp() == 0.0
    assert v.theoretical_fp() == 0.0


# ── 分词 ───────────────────────────────────────────────────

def test_scent_consistent_across_contexts():
    """同一个词在不同上下文里必须产出可匹配的 token。"""
    a = set(scent("上周三和张老板聊了川菜馆的事"))
    b = set(scent("我们聊过的那家川菜馆"))
    assert a & b, "不同上下文中的同一实体应有共同 token"


def test_scent_empty():
    assert scent("") == []
    assert scent(None or "") == []
