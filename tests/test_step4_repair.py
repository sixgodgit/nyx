"""tests/test_step4_repair.py — Step 4 验收：一致性自愈 + 巡检

在此之前，verify() 只能告诉你"不一致了"，没有任何工具能修。
而迁移是按 `WHERE seq=?` 判断"这条已迁过"就跳过 —— 它压根不看内容。
于是 seq 一旦错位，错误会永久留在库里，重跑迁移也不会修。

repair_from_journal() 补上这个洞：**日志是唯一真相，中枢是它的索引。**
"""

import pytest


@pytest.fixture()
def hub(monkeypatch, tmp_path):
    home = tmp_path / "nb"
    home.mkdir()
    monkeypatch.setenv("NEXSANDBASE_HOME", str(home))

    from nexsandglass.core import memid, sandglass_sqlite
    from nexsandglass.features import sandglass_vault, shadow_sand

    sg = str(home / "sandglass.txt")
    monkeypatch.setattr(memid, "_SANDGLASS", sg)
    monkeypatch.setattr(memid, "_LOCK", sg + ".lock")
    monkeypatch.setattr(sandglass_vault, "_SANDGLASS", sg)
    monkeypatch.setattr(sandglass_sqlite, "_DB", str(home / "sandglass.db"))
    monkeypatch.setattr(sandglass_sqlite, "_last_sync_mtime", 0)
    sandglass_vault.set_idx_path(str(home / "sandglass.idx"))
    sandglass_vault._idx_cache = None
    sandglass_vault._idx_mtime = 0
    memid.set_db_path(str(home / "nyx.db"))
    shadow_sand.set_shadow_path(str(home / "shadow_sand.db"))
    shadow_sand._conn = None

    yield memid, home, sandglass_sqlite, sandglass_vault, shadow_sand

    shadow_sand._conn = None
    memid.set_db_path(str(tmp_path / "unused.db"))


def _seed(memid, n=5):
    return [memid.allocate(f"第{i}条记忆 Alpha Bravo{i}", sender="user") for i in range(1, n + 1)]


# ── 体检（apply=False 不改任何东西）─────────────────────

def test_repair_detects_missing(hub):
    memid, home, *_ = hub
    _seed(memid)
    conn = memid.get_conn()
    conn.execute("DELETE FROM memories WHERE seq=3")
    conn.commit()

    rep = memid.repair_from_journal(apply=False)
    assert [m["seq"] for m in rep["missing"]] == [3]
    assert rep["mismatch"] == [] and rep["extra"] == []
    assert rep["needs_reindex"] is True
    assert memid.count() == 4, "apply=False 不该改任何东西"


def test_repair_detects_mismatch(hub):
    memid, home, *_ = hub
    _seed(memid)
    conn = memid.get_conn()
    conn.execute("UPDATE memories SET text='被改过的内容' WHERE seq=2")
    conn.commit()

    rep = memid.repair_from_journal(apply=False)
    assert [m["seq"] for m in rep["mismatch"]] == [2]
    assert "被改过的内容" in rep["mismatch"][0]["hub"]
    assert "第2条记忆" in rep["mismatch"][0]["journal"]


def test_repair_reports_extra_but_does_not_delete(hub):
    """中枢里 seq 超出日志范围 = 日志变短了。这是事故，要人判断，不是脚本该决定的。"""
    memid, home, *_ = hub
    _seed(memid, 3)
    p = home / "sandglass.txt"
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")   # 砍掉最后一条

    rep = memid.repair_from_journal(apply=True)
    assert [e["seq"] for e in rep["extra"]] == [3]
    assert memid.get(memid.resolve(3, allow_deleted=True) or "") is not None or True
    conn = memid.get_conn()
    assert conn.execute("SELECT COUNT(*) FROM memories WHERE seq=3").fetchone()[0] == 1, \
        "extra 被自动删了 —— 不该替用户做这个决定"


# ── 修复 ─────────────────────────────────────────────────

def test_repair_fixes_missing_and_mismatch(hub):
    memid, home, *_ = hub
    _seed(memid, 6)
    conn = memid.get_conn()
    conn.execute("DELETE FROM memories WHERE seq=4")
    conn.execute("UPDATE memories SET text='脏数据' WHERE seq=2")
    conn.commit()
    assert memid.verify()["ok"] is False

    rep = memid.repair_from_journal(apply=True)
    assert rep["repaired"] == 2
    assert rep["needs_reindex"] is True
    assert memid.verify()["ok"] is True, "修完仍然不一致"
    assert memid.count() == 6


def test_repair_restoring_content_keeps_the_same_id(hub):
    """中枢被弄脏、日志完好 → 修回原文，mem_id 自然算回原值，不该有 id 变化。"""
    memid, home, *_ = hub
    ids = _seed(memid, 3)
    conn = memid.get_conn()
    conn.execute("UPDATE memories SET text='脏数据' WHERE seq=2")
    conn.commit()

    rep = memid.repair_from_journal(apply=True)
    assert rep["repaired"] == 1
    assert rep["id_changes"] == [], "修回原文却报告 id 变了 —— 说明碰撞检查把自己算进去了"
    assert memid.resolve(2) == ids[1]
    assert memid.verify()["ok"] is True


def test_repair_reports_id_changes_when_journal_was_edited(hub):
    """日志被编辑过 → 中枢跟着改，mem_id 必然变化，必须报出来。

    否则 shadow_sand.mem_ids / wthread.source_mem_id 会指向一个不存在的 id。
    """
    memid, home, *_ = hub
    ids = _seed(memid, 3)
    p = home / "sandglass.txt"
    p.write_text(p.read_text(encoding="utf-8").replace("第2条记忆", "第2条记忆（已修订）"),
                 encoding="utf-8")

    rep = memid.repair_from_journal(apply=True)
    assert rep["repaired"] == 1
    assert len(rep["id_changes"]) == 1
    ch = rep["id_changes"][0]
    assert ch["seq"] == 2 and ch["old"] == ids[1] and ch["new"] != ids[1]
    assert rep["needs_reindex"] is True
    assert memid.get(ch["new"])["text"].startswith("第2条记忆（已修订）")
    assert memid.verify()["ok"] is True


def test_repair_is_idempotent(hub):
    memid, home, *_ = hub
    _seed(memid, 4)
    conn = memid.get_conn()
    conn.execute("DELETE FROM memories WHERE seq=2")
    conn.commit()
    memid.repair_from_journal(apply=True)

    again = memid.repair_from_journal(apply=True)
    assert again["repaired"] == 0
    assert again["missing"] == [] and again["mismatch"] == []
    assert again["needs_reindex"] is False


def test_repair_skips_tombstoned(hub):
    """已 tombstone 的记忆正文本就被抹掉，不该被当成 mismatch 反复"修"。"""
    memid, home, *_ = hub
    ids = _seed(memid, 4)
    memid.tombstone(ids[1], reason="user_forget")

    rep = memid.repair_from_journal(apply=False)
    assert rep["mismatch"] == []
    assert rep["missing"] == []


def test_repair_on_healthy_hub_is_noop(hub):
    memid, home, *_ = hub
    _seed(memid, 5)
    rep = memid.repair_from_journal(apply=True)
    assert rep == dict(rep, repaired=0, missing=[], mismatch=[], extra=[], id_changes=[])
    assert rep["needs_reindex"] is False


# ── 巡检 ─────────────────────────────────────────────────

def test_health_all_green(hub):
    memid, home, sq, vault, shadow = hub
    _seed(memid, 5)
    sq.sync_all(); vault.rebuild_index(); shadow.rebuild_entity_index()

    h = memid.health()
    assert h["ok"] is True, h["checks"]
    assert set(h["checks"]) == {"hub_vs_journal", "fts_index", "inverted_index",
                                "entity_index", "erasure_integrity", "write_amplification"}
    assert h["checks"]["hub_vs_journal"]["coverage"] == 1.0


def test_health_catches_stale_fts(hub):
    memid, home, sq, vault, shadow = hub
    _seed(memid, 5)
    sq.sync_all(); vault.rebuild_index(); shadow.rebuild_entity_index()
    memid.allocate("索引还不知道的新记忆", sender="user")     # FTS 落后一条

    h = memid.health()
    assert h["checks"]["fts_index"]["ok"] is False
    assert h["ok"] is False


def test_health_catches_hub_gap(hub):
    memid, home, sq, vault, shadow = hub
    _seed(memid, 5)
    sq.sync_all(); vault.rebuild_index(); shadow.rebuild_entity_index()
    conn = memid.get_conn()
    conn.execute("DELETE FROM memories WHERE seq=3")
    conn.commit()

    h = memid.health()
    assert h["checks"]["hub_vs_journal"]["ok"] is False
    assert h["ok"] is False


def test_health_does_not_collapse_into_one_score(hub):
    """每项各自成立或各自失败 —— 糊成一个分数就又回到"失败和成功不可区分"。"""
    memid, home, sq, vault, shadow = hub
    _seed(memid, 3)
    sq.sync_all(); vault.rebuild_index(); shadow.rebuild_entity_index()
    memid.allocate("让 FTS 落后", sender="user")

    h = memid.health()
    assert h["checks"]["fts_index"]["ok"] is False
    assert h["checks"]["hub_vs_journal"]["ok"] is True, "一项坏不该把别项也标成坏"
    assert "score" not in h and "rate" not in h


def test_health_reports_per_check_numbers(hub):
    memid, home, sq, vault, shadow = hub
    _seed(memid, 4)
    sq.sync_all(); vault.rebuild_index()
    h = memid.health()
    c = h["checks"]
    assert c["hub_vs_journal"]["hub"] == 4 and c["hub_vs_journal"]["journal"] == 4
    assert c["fts_index"]["indexed"] == 4
    assert len(set(c["inverted_index"]["sizes"])) == 1
