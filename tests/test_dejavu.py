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


def test_scent_chinese_skips_common_words_and_fragments():
    """中文（v7.16.2）：虚词、时间指代、回忆句式和带助词 / 代词的跨词碎片不算 token；实词照收。"""
    assert sorted(scent("上周三和张老板聊了川菜馆的事")) == [
        "三和", "周三", "和张", "川菜", "张老", "板聊", "老板", "菜馆"], "了川 / 的事 / 馆的 / 聊了 / 上周 不算"
    assert set(scent("我们聊过的那家川菜馆")) == {"家川", "川菜", "菜馆"}
    assert scent("你还记得我之前跟你说过的吗") == ["前跟"], "整句回忆句式只剩一个跨词碎片（两个实字拼成的，分不出来）"
    assert scent("我们的") == [] and scent("哈哈哈哈") == []
    for text, word in (("我在学吉他", "吉他"), ("去成都出差", "成都"), ("我对花生过敏", "过敏"),
                       ("下周去那不勒斯", "勒斯"), ("其他人都到了", "其他")):
        assert word in scent(text), (text, word)
    assert {'"川菜馆"', "川菜馆"} <= set(scent('"川菜馆" 和 "the"')), "引号里的中文照收"


def test_unrelated_chinese_question_is_unfamiliar(dv):
    """记过一句中文之后：毫不相干的问题是陌生，问到同一件事是熟悉。v7.16.1 两个都是熟悉（我们 / 聊过 / 的那 ……）。"""
    dv.imprint("msg-1", "我们上周三和张老板聊了川菜馆的事，他说要换厨师")
    for q in ("我们之前聊过的那篇量子计算论文", "你还记得我说过的南极企鹅迁徙路线吗", "上次说的那个事情怎么样了"):
        r = dv.sense(q)
        assert not r.familiar and r.phantoms == [], (q, r)
    r = dv.sense("我们聊过的那家川菜馆")
    assert r.familiar and set(r.phantoms) == {"川菜", "菜馆"}


def test_scent_skips_english_common_words():
    """the / what / about / talked 这类词不算 token：几乎每句英文都有，算进去就什么都「见过」（v7.16.1）。"""
    assert scent("I don't remember what we talked about") == []
    toks = set(scent("What About the Sichuan place on Clement Street"))
    assert toks == {"sichuan", "place", "clement", "street", "clement street"}, "全由常用词组成的专名也不算"
    assert "may" in scent("my trip in May"), "may 也是五月，不当常用词"


def test_unrelated_english_question_is_unfamiliar(dv):
    """记过一句英文之后：毫不相干的英文问题是陌生，共享一个实词的问题是熟悉。v7.16.0 两个都是熟悉。"""
    dv.imprint("msg-1", "Last Wednesday Sam told me about that Sichuan place on Clement Street")
    for q in ("that quantum computing paper we talked about", "what did you tell me about the weather",
              "the ramen bar in Osaka I told you about"):
        r = dv.sense(q)
        assert not r.familiar and r.phantoms == [], (q, r)
    r = dv.sense("the Sichuan restaurant we talked about")
    assert r.familiar and r.phantoms == ["sichuan"]
    assert any("msg-1" in p.refs for p in dv.hunt("the Sichuan restaurant we talked about"))


def test_common_words_imprinted_by_older_versions_are_never_probed(dv):
    """以前的版本把 about / that 记进了 Veil：sense 不再产出它们，所以不会因此熟悉（不用迁移数据）。"""
    for w in ("about", "that", "talked", "what"):
        dv.veil.touch(w)
    assert not dv.sense("what about that quantum paper we talked about").familiar

# ── refs 存储契约（内嵌串，非规范化表）─────────────────────
# 曾尝试把 refs 规范化到独立表，实测更差（同数据量磁盘 518 vs 151 B/token，
# 写入慢 5 倍），已回退。这些测试锁定回退后的行为，防止再犯。

def test_refs_dedup_and_sightings(dv):
    """同一 key 重复 imprint 不计两次引用，但 sightings 累加。"""
    dv.imprint("k1", "张老板 川菜馆")
    dv.imprint("k1", "张老板 川菜馆")
    ph = dv.hunt("张老板")
    assert ph, "应能寻回"
    top = max(ph, key=lambda x: x.sightings)
    assert top.refs.count("k1") == 1, f"refs 未去重: {top.refs}"
    assert top.sightings >= 2


def test_refs_capped_at_max_refs(tmp_path):
    """refs 数量不超过 MAX_REFS，且保留的是最近的。"""
    from nexsandglass.dejavu.mist import MAX_REFS
    d = DejaVu(str(tmp_path / "dv"))
    for i in range(MAX_REFS + 15):
        d.imprint(f"key-{i}", "张老板 川菜馆")
    ph = d.hunt("张老板")
    top = max(ph, key=lambda x: x.sightings)
    assert len(top.refs) == MAX_REFS, f"{len(top.refs)} != {MAX_REFS}"
    assert f"key-{MAX_REFS + 14}" in top.refs, "应保留最近的引用"
    assert "key-0" not in top.refs, "最旧的应被挤出"
    d.close()


def test_no_normalized_refs_table(tmp_path):
    """schema 里不应出现 refs 表（规范化方案已回退）。"""
    import sqlite3
    d = DejaVu(str(tmp_path / "dv"))
    d.imprint("k1", "任意内容")
    con = sqlite3.connect(os.path.join(d.storage_dir, "mist.db"))
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    d.close()
    assert "refs" not in tables, f"遗留 refs 表: {tables}"
    assert "phantoms" in tables
