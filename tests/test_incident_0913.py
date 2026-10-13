"""tests/test_incident_0913.py — 2026-09-13 生产事故原样重演 + 修复验收（v7.13.9）

事故：测试把合成记忆（sender=golden / test / e2e / mcp）写进了生产中枢 —— 日志里没有；
真实记忆被覆盖；agent 把「用户喜欢喝拿铁」「预算 2000 欧」当成真记忆说了出来。
自检三项红：中枢对不上日志、全文索引条数对不上、墓碑「泄漏」。

这里把同样的现场造出来（每一类不一致各造几条），然后证明 core/forensics.repair：
  - 真实记忆一条不少，错位的挪回原位、被覆盖的认回原 id 和来源
  - 被遗忘的绝不复活
  - 合成记录和它们派生出来的「事实」（信念三元组、engram、实体、向量）全部撤下，被它们截断的真实信念恢复
  - 日志一个字节都不改；修之前有备份
  - 「泄漏」里有一条是假的：按 seq 去抹它，抹掉的会是一条真实记忆
"""
import hashlib
import json
import os
import sqlite3

import pytest

from tests.test_hermes_provider import _reindex, env  # noqa: F401  复用隔离目录

REAL = [
    ("user", "我在鹿特丹工作，做港口物流"),
    ("agent", "记住了：你在鹿特丹做港口物流"),
    ("user", "我平时喝茶，最爱龙井"),
    ("user", "下个月要去里斯本出差三天"),
    ("user", "我女儿叫小满，今年六岁"),
    ("user", "密码提示：这一条后来被我要求忘掉"),
    ("user", "周末打算去海牙看展"),
    ("agent", "好的，周末海牙看展"),
    ("user", "我对花生过敏"),
    ("user", "车是一辆二手的沃尔沃"),
    ("user", "每周三晚上练羽毛球"),
    ("agent", "记下了：周三羽毛球"),
]
FAKE = [("golden", "用户喜欢喝拿铁"), ("test", "用户的预算是 2000 欧"),
        ("e2e", "用户喜欢手冲咖啡"), ("mcp", "合成的 MCP 写入")]
FAKE_WORDS = ("拿铁", "2000 欧", "手冲咖啡", "合成的")


def _ts(i, day="2026-09-01"):
    return f"{day} 10:{i // 60:02d}:{i % 60:02d}"


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _hub(memid):
    return memid.get_conn().execute(
        "SELECT seq, mem_id, sender, text, deleted_at FROM memories ORDER BY seq").fetchall()


def _triples(env):  # noqa: F811
    from nexsandglass.features import weavethread
    c = sqlite3.connect(weavethread._DB)
    try:
        return c.execute("SELECT subject, relation, object, valid_until, closed_at, source_mem_id "
                         "FROM wthread_triples WHERE retracted_at IS NULL ORDER BY id").fetchall()
    finally:
        c.close()


def _fact(subject, relation, obj, mem_id, now, valid_from=None):
    from nexsandglass.engram.loops.temporal_fact import ensure_temporal_columns, record_fact
    from nexsandglass.features import weavethread
    ensure_temporal_columns(weavethread._DB)
    c = sqlite3.connect(weavethread._DB)
    try:
        record_fact(c, subject, relation, obj, now=now, valid_from=valid_from,
                    source_mem_id=mem_id, ongoing=True)
        c.commit()
    finally:
        c.close()


def _stage(env, tmp_path):  # noqa: F811
    """造出 09-13 的现场。返回 {真实 ids, 合成 ids, 各类 seq}。"""
    from nexsandglass.core import erasure, memid
    from nexsandglass.features import shadow_sand
    real = [memid.allocate(t, s, ts=_ts(i)) for i, (s, t) in enumerate(REAL[:6])]
    # 一条正常遗忘：日志抹掉，留 mem= 标记
    assert erasure.forget({"mem_id": real[1]}, apply=True, mode="purge")["ok"]

    # ① 测试进程把合成记忆写进了生产中枢，日志写到了别处（两个真相来源）——
    #    中枢里多出 seq 7..10，行号是在另一份日志上分配的，和本日志的第 1..4 行撞车
    other = str(tmp_path / "elsewhere" / "sandglass.txt")
    os.makedirs(os.path.dirname(other))
    fake = [memid.allocate(t, s, ts=_ts(i, "2026-09-13"), journal_path=other) for i, (s, t) in enumerate(FAKE)]

    # ② 之后的真实写入：日志 seq 7..12，中枢被挤到 11..16（错位）
    real += [memid.allocate(t, s, ts=_ts(i)) for i, (s, t) in enumerate(REAL[6:], start=6)]

    # ③ 合成记录的派生「事实」：一条真实信念先在，合成的那条把它截断了
    _fact("用户", "常用", "茶", real[2], "2026-09-01T10:00:02Z")         # 单值时序关系：新的会截断旧的
    _fact("用户", "常用", "拿铁", fake[0], "2026-09-13T03:00:00Z")
    _fact("用户", "住在", "鹿特丹", real[0], "2026-09-01T10:00:00Z")
    store = os.path.join(str(env.home), "engram_store.jsonl")
    with open(store, "a", encoding="utf-8") as f:
        for row in ({"ts": _ts(0, "2026-09-13"), "content": "用户喜欢喝拿铁", "type": "preference",
                     "source_mem_id": fake[0]},
                    {"ts": _ts(1, "2026-09-13"), "content": "用户的预算是 2000 欧", "type": "fact"},  # 旧格式：没有 source_mem_id
                    {"ts": _ts(8), "content": "用户对花生过敏", "type": "fact", "source_mem_id": real[8]}):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with shadow_sand._db_lock:
        db = shadow_sand._get_conn()
        shadow_sand._ensure_memid_cols(db)
        db.execute("INSERT INTO entities (name, line_nums, mem_ids) VALUES (?,?,?)", ("拿铁", "1", fake[0]))
        db.execute("INSERT INTO entities (name, line_nums, mem_ids) VALUES (?,?,?)",
                   ("鹿特丹", "1,2", f"{real[0]},{fake[2]}"))
        db.commit()

    conn = memid.get_conn()
    # ④ 测试「遗忘」了一条合成记录：墓碑 seq=10，日志第 10 条却是一条真实记忆 → 自检报泄漏（假的）
    memid.tombstone(fake[3], reason="test_cleanup")
    # ⑤ 真实记忆被覆盖：id 还是它的，正文换成了合成的
    conn.execute("UPDATE memories SET text=?, content_hash=? WHERE mem_id=?",
                 ("用户的预算是 2000 欧", memid.content_hash("用户的预算是 2000 欧"), real[3]))
    # ⑥ 真实记忆从中枢里丢了（日志里还在）
    conn.execute("DELETE FROM memories WHERE mem_id=?", (real[4],))
    conn.commit()
    # ⑦ 一条遗忘没做完：墓碑打了，日志正文还在（真泄漏）
    memid.tombstone(real[5], reason="user_forget")
    _reindex(env)
    return {"real": real, "fake": fake}


@pytest.fixture()
def scene(env, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("NYX_EMBED", "off")
    return _stage(env, tmp_path)


def test_incident_reproduces_the_red_doctor(env, scene):  # noqa: F811
    from nexsandglass.core import memid
    c = memid.health()["checks"]
    assert not c["hub_vs_journal"]["ok"]
    assert not c["fts_index"]["ok"]
    assert not c["erasure_integrity"]["ok"]
    assert sorted(c["erasure_integrity"]["leaked"]) == [6, 10]


def test_diagnose_tells_real_from_fake(env, scene):  # noqa: F811
    from nexsandglass.core import forensics, memid
    before = _hub(memid)
    d = forensics.diagnose()
    assert _hub(memid) == before, "体检不许写任何东西"
    assert not os.path.exists(os.path.join(str(env.home), "backups"))
    assert d["journal_records"] == 12 and d["hub_rows"] == 15
    assert d["misplaced"] == 6                     # 日志 7..12 在中枢 11..16
    assert d["overwritten"] == 1 and d["overwritten_seqs"] == ["4"]
    assert d["missing_from_hub"] == 1 and d["missing_seqs"] == ["5"]
    assert d["orphans"] == 3 and d["orphans_by_sender"] == {"golden": 1, "test": 1, "e2e": 1}
    assert d["unfinished_erasures"] == 1 and d["unfinished_erasure_seqs"] == ["6"]
    assert d["false_leaks"] == 1 and d["false_leak_seqs"] == ["10"]
    assert d["erased_ok"] == 1 and not d["clean"]
    blob = json.dumps(d, ensure_ascii=False)
    assert not any(w in blob for w in FAKE_WORDS) and "鹿特丹" not in blob, "体检报告不输出正文"


def test_false_leak_points_at_a_real_memory(env, scene):  # noqa: F811
    """自检报 seq 10「泄漏」。日志第 10 条是一条真实记忆 —— 照着 seq 去抹，抹掉的是它。"""
    from nexsandglass.core import memid
    rec = memid.parse_journal_records()[9]
    assert (rec["sender"], rec["text"]) == REAL[9]


def test_repair_restores_everything_real_and_nothing_fake(env, scene):  # noqa: F811
    from nexsandglass.core import forensics, memid, sandglass_sqlite
    real, fake = scene["real"], scene["fake"]
    j0 = _sha(env.journal)
    rep = forensics.repair(apply=True)

    # 日志一个字节没动；修之前有备份，备份里是修之前的中枢
    assert _sha(env.journal) == j0
    bak = rep["backup"]
    assert os.path.isfile(os.path.join(bak, "sandglass.txt")) and os.path.isfile(os.path.join(bak, "nyx.db"))
    with sqlite3.connect(os.path.join(bak, "nyx.db")) as b:
        assert b.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 15

    hub = _hub(memid)
    assert [h[0] for h in hub] == list(range(1, 13))
    by_seq = {h[0]: h for h in hub}
    for i, (s, t) in enumerate(REAL, start=1):
        if i in (2, 6):                            # 遗忘过的：保持已删除，绝不复活
            assert by_seq[i][4] and by_seq[i][3] == ""
            continue
        assert (by_seq[i][2], by_seq[i][3], by_seq[i][4]) == (s, t, None), i
        assert by_seq[i][1] == real[i - 1], f"seq {i} 的 mem_id 应保持原样（派生数据靠它挂着）"
    # 被覆盖、被弄丢的两条：写入时绑定的来源还在（不是「从日志恢复、未经证实」）
    for mid in (real[3], real[4]):
        row = memid.get_conn().execute("SELECT bound FROM provenance WHERE mem_id=?", (mid,)).fetchone()
        assert row and row[0] == 1
    for mid in fake:
        assert memid.get_conn().execute("SELECT 1 FROM provenance WHERE mem_id=?", (mid,)).fetchone() is None

    # 合成记录和它们派生的「事实」都没了
    live = " ".join(h[3] for h in hub)
    assert not any(w in live for w in FAKE_WORDS)
    tr = _triples(env)
    assert not any(t[2] == "拿铁" for t in tr)
    tea = [t for t in tr if t[2] == "茶"]
    assert tea and tea[0][3] is None and tea[0][4] is None, "被合成事实截断的真实信念要恢复成现任"
    assert any(t[2] == "鹿特丹" for t in tr)
    eng = open(os.path.join(str(env.home), "engram_store.jsonl"), encoding="utf-8").read()
    assert "拿铁" not in eng and "2000 欧" not in eng and "花生" in eng
    from nexsandglass.features import shadow_sand
    with shadow_sand._db_lock:
        ents = dict(shadow_sand._get_conn().execute("SELECT name, mem_ids FROM entities").fetchall())
    assert "拿铁" not in ents and ents["鹿特丹"] == real[0]
    d = rep["derived_removed"]
    assert d["triples"] == 1 and d["beliefs_reopened"] == 1 and d["engram"] == 2 and d["entities_removed"] == 1

    # 没做完的遗忘：中枢已删除、检索不到；日志正文还在，所以这一项如实保持红
    assert sandglass_sqlite.search("密码提示") == []
    h = rep["health_after"]
    assert h["hub_vs_journal"] and h["fts_index"] and h["inverted_index"]
    assert not h["erasure_integrity"]
    assert rep["after"]["hub_clean"] and rep["after"]["unfinished_erasures"] == 1

    # 再做完那一条：只抹那一行，日志其它行一个字不变
    lines0 = open(env.journal, encoding="utf-8").read().splitlines()
    rep2 = forensics.repair(apply=True, finish_erasures=True)
    lines1 = open(env.journal, encoding="utf-8").read().splitlines()
    changed = [i for i, (a, b) in enumerate(zip(lines0, lines1)) if a != b]
    assert len(lines0) == len(lines1) and changed == [5]
    assert "[REDACTED" in lines1[5] and f"mem={real[5]}" in lines1[5]
    assert rep2["after"]["clean"]
    assert all(memid.health()["checks"][k]["ok"] for k in
               ("hub_vs_journal", "fts_index", "erasure_integrity", "inverted_index"))
    from nexsandglass.core import erasure
    assert erasure.verify_erasure(["密码提示"])["clean"]


def test_repair_is_idempotent_on_a_clean_store(env, monkeypatch):  # noqa: F811
    from nexsandglass.core import forensics, memid
    monkeypatch.setenv("NYX_EMBED", "off")
    for i, (s, t) in enumerate(REAL[:4]):
        memid.allocate(t, s, ts=_ts(i))
    _reindex(env)
    before = _hub(memid)
    assert forensics.diagnose()["clean"]
    rep = forensics.repair(apply=True)
    assert _hub(memid) == before
    assert rep["after"]["clean"] and all(rep["derived_removed"][k] == 0 for k in ("triples", "engram"))


def test_forget_senders_after_alignment(env, scene):  # noqa: F811
    """合成记录也进了日志的（e2e 写进了真实日志）：对齐之后按发送者送进隔离区，只动那几行。"""
    from nexsandglass.core import forensics, memid
    memid.allocate("用户喜欢手冲咖啡", "e2e", ts=_ts(30))
    _reindex(env)
    assert forensics.diagnose()["suspect_senders_in_journal"] == {"e2e": 1}
    lines0 = open(env.journal, encoding="utf-8").read().splitlines()
    rep = forensics.repair(apply=True, finish_erasures=True, forget_senders=["e2e"])
    assert rep["forget_senders"]["removed"] == 1 and rep["forget_senders"]["recoverable"]
    lines1 = open(env.journal, encoding="utf-8").read().splitlines()
    changed = [i for i, (a, b) in enumerate(zip(lines0, lines1)) if a != b]
    assert changed == [5, 12]                       # 没做完的遗忘 + e2e 那一行
    assert rep["after"]["clean"] and rep["after"]["suspect_senders_in_journal"] == {}


def test_select_by_sender_narrows_never_widens(env, monkeypatch):  # noqa: F811
    from nexsandglass.core import erasure, memid
    monkeypatch.setenv("NYX_EMBED", "off")
    a = memid.allocate("用户喜欢喝拿铁", "golden", ts=_ts(1))
    b = memid.allocate("我喜欢喝拿铁吗？", "user", ts=_ts(2))
    c = memid.allocate("测试数据", "test", ts=_ts(3))
    assert erasure._select({"senders": ["golden", "test"]}) == [a, c]
    assert erasure._select({"sender": "golden"}) == [a]
    # 和别的条件一起用 = 收窄：「拿铁」两条里只留 golden 那条，主人自己说的不动
    assert erasure._select({"contains": "拿铁", "sender": "golden"}) == [a]
    assert erasure._select({"contains": "拿铁"}) == [a, b]
    assert erasure._select({"sender": "golden", "all": False}) == [a]


def test_cli_dry_run_and_doctor_hint(env, scene, capsys):  # noqa: F811
    from nexsandglass import doctor, repair
    from nexsandglass.core import memid
    before = _hub(memid)
    assert repair.main([]) == 1
    out = capsys.readouterr().out
    assert "只读" in out and "--apply" in out
    assert not any(w in out for w in FAKE_WORDS) and "鹿特丹" not in out, "体检不输出正文"
    assert _hub(memid) == before
    doctor.main([])
    assert "nexsandglass.repair" in capsys.readouterr().out
    assert repair.main(["--apply"]) == 1          # 还剩一条没做完的遗忘
    assert "--finish-erasures" in capsys.readouterr().out
    assert repair.main(["--apply", "--finish-erasures"]) in (0, 1)
    assert repair.main([]) == 0


def test_vectors_follow_the_repair(env, monkeypatch, tmp_path):  # noqa: F811
    """合成记录的向量撤掉；行号记错了的向量改回来（召回按行号回读日志）。"""
    from nexsandglass.core import forensics, memid, semantic
    from tests.fixtures import fake_embed
    for k in ("NYX_EMBED", "NYX_EMBED_PROVIDER", "NYX_EMBED_MODEL", "EMBEDDING_API_URL"):
        monkeypatch.delenv(k, raising=False)
    semantic.set_provider(fake_embed.ConceptBag())
    try:
        real = [memid.allocate(t, s, ts=_ts(i)) for i, (s, t) in enumerate(REAL[:3])]
        other = str(tmp_path / "elsewhere" / "sandglass.txt")
        os.makedirs(os.path.dirname(other))
        fake = memid.allocate("用户喜欢喝拿铁", "golden", ts=_ts(0, "2026-09-13"), journal_path=other)
        semantic.index_pending(limit=None)
        c = sqlite3.connect(semantic._db_path())
        c.execute("UPDATE embeddings SET line_start=99 WHERE mem_id=?", (real[0],))
        c.commit()
        assert {r[0] for r in c.execute("SELECT mem_id FROM embeddings")} == set(real) | {fake}
        c.close()
        _reindex(env)
        forensics.repair(apply=True)
        assert semantic.wait_idle()
        c = sqlite3.connect(semantic._db_path())
        rows = dict(c.execute("SELECT mem_id, line_start FROM embeddings").fetchall())
        c.close()
        assert fake not in rows
        assert rows[real[0]] == 1 and rows[real[1]] == 2 and rows[real[2]] == 3
    finally:
        assert semantic.wait_idle()
        semantic.set_provider(None)
