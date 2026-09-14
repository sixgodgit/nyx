"""tests/test_memid.py — ID 中枢（0 号改造）验收

覆盖四条铁律：
  A. 原子分配   —— seq 与 sandglass.txt 行号永远 1:1，不存在猜行号
  B. 确定性 ID  —— 同一条记忆算出来永远同一个 mem_id（迁移幂等）
  C. 只增不改   —— 删除不位移别人的主键
  D. 可校验     —— verify() 能发现不一致（行号漂移不再静默）
"""

import os
import sqlite3
import tempfile

import pytest


@pytest.fixture()
def hub(monkeypatch, tmp_path):
    """独立数据目录 + 干净的 ID 中枢单例。"""
    home = tmp_path / "nb"
    home.mkdir()
    monkeypatch.setenv("NEXSANDBASE_HOME", str(home))

    from nexsandglass.core import memid
    monkeypatch.setattr(memid, "_SANDGLASS", str(home / "sandglass.txt"))
    monkeypatch.setattr(memid, "_LOCK", str(home / "sandglass.txt.lock"))
    memid.set_db_path(str(home / "nyx.db"))
    yield memid, home
    memid.set_db_path(str(tmp_path / "unused.db"))


def _journal(home):
    p = home / "sandglass.txt"
    if not p.exists():
        return []
    return [l.rstrip("\n") for l in p.open(encoding="utf-8")]


# ── 铁律 B：确定性 ID ─────────────────────────────────────

def test_mem_id_is_deterministic(hub):
    memid, _ = hub
    a = memid.compute_mem_id("2026-08-27 10:00:00", "user", "我住在海牙")
    b = memid.compute_mem_id("2026-08-27 10:00:00", "user", "我住在海牙")
    assert a == b
    assert a.startswith("m_") and len(a) == 18
    # 任一字段变化 → ID 变化
    assert a != memid.compute_mem_id("2026-08-27 10:00:01", "user", "我住在海牙")
    assert a != memid.compute_mem_id("2026-08-27 10:00:00", "agent", "我住在海牙")
    assert a != memid.compute_mem_id("2026-08-27 10:00:00", "user", "我住在阿姆斯特丹")


def test_mem_id_collision_gets_suffix(hub):
    memid, _ = hub
    ts = "2026-08-27 10:00:00"
    first = memid.allocate("完全相同的一句话", sender="user", ts=ts)
    second = memid.allocate("完全相同的一句话", sender="user", ts=ts)
    assert first != second
    assert second.startswith(first) and second.endswith("-2")


# ── 铁律 A：原子分配 ─────────────────────────────────────

def test_seq_matches_journal_line(hub):
    memid, home = hub
    ids = [memid.allocate(f"第{i}条记忆", sender="user") for i in range(1, 6)]
    lines = _journal(home)
    assert len(lines) == 5
    for i, mid in enumerate(ids, 1):
        row = memid.get(mid)
        assert row["seq"] == i
        assert row["text"] in lines[i - 1]


def test_journal_failure_rolls_back_db(hub, monkeypatch):
    """文件写入失败必须回滚 DB —— 绝不留下孤儿 seq（否则下一条就错位）。"""
    memid, home = hub
    memid.allocate("正常的第一条", sender="user")
    before = memid.count()

    real_open = open

    def boom(path, *a, **k):
        if str(path).endswith("sandglass.txt") and "a" in (a[0] if a else k.get("mode", "r")):
            raise OSError("disk full")
        return real_open(path, *a, **k)

    # 独立 context —— 不能用 monkeypatch.undo()，那会连同 hub fixture 的补丁一起撤销
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("builtins.open", boom)
        with pytest.raises(OSError):
            memid.allocate("会失败的一条", sender="user")

    assert memid.count() == before, "文件失败后 DB 必须回滚"
    # 下一条仍然拿到正确的 seq，没有被吞掉的失败污染
    mid = memid.allocate("恢复后的一条", sender="user")
    row = memid.get(mid)
    assert row["seq"] == before + 1
    assert row["text"] in _journal(home)[row["seq"] - 1]


def test_no_drift_even_if_downstream_fails(hub):
    """下游（影子沙等）失败不再导致行号漂移 —— seq 由中枢分配，不靠 COUNT(*) 猜。"""
    memid, home = hub
    ids = []
    for i in range(1, 6):
        mid = memid.allocate(f"实体{i} 的记忆", sender="user")
        ids.append(mid)
        if i == 2:
            # 模拟下游索引抛异常被上层吞掉
            try:
                raise sqlite3.OperationalError("database is locked")
            except sqlite3.OperationalError:
                pass
    lines = _journal(home)
    for i, mid in enumerate(ids, 1):
        assert memid.get(mid)["text"] in lines[i - 1], f"第{i}条漂移了"


# ── 铁律 C：只增不改 ─────────────────────────────────────

def test_tombstone_does_not_shift_others(hub):
    memid, _ = hub
    ids = [memid.allocate(f"记忆{i}", sender="user") for i in range(1, 5)]
    after_ids = {seq: memid.resolve(seq) for seq in (1, 2, 3, 4)}

    assert memid.tombstone(ids[1], reason="user_forget") is True

    for seq in (1, 3, 4):
        assert memid.resolve(seq) == after_ids[seq], f"seq={seq} 的主键被位移了"
    assert memid.resolve(2) is None                      # 已删，读不到
    assert memid.get(ids[1]) is None
    assert memid.get(ids[1], allow_deleted=True)["text"] == ""   # 正文已抹除
    assert ids[1] in memid.tombstoned_ids()


def test_tombstone_unknown_id(hub):
    memid, _ = hub
    assert memid.tombstone("m_doesnotexist") is False


# ── 兼容层 ───────────────────────────────────────────────

def test_resolve_accepts_legacy_forms(hub):
    memid, _ = hub
    mid = memid.allocate("我的车是 Geely Starray", sender="user", ts="2026-08-27 12:00:00")
    assert memid.resolve(1) == mid
    assert memid.resolve("1") == mid
    assert memid.resolve("shadow:1") == mid
    assert memid.resolve("line:1") == mid
    assert memid.resolve(mid) == mid
    assert memid.resolve("engram:2026-08-27 12:00:00") == mid
    assert memid.resolve(9999) is None
    assert memid.resolve("garbage") is None
    assert memid.resolve(None) is None
    assert memid.resolve("") is None


def test_get_many(hub):
    memid, _ = hub
    ids = [memid.allocate(f"记忆{i}", sender="user") for i in range(1, 4)]
    got = memid.get_many(ids + ["m_nope"])
    assert set(got) == set(ids)
    assert got[ids[0]]["seq"] == 1


def test_iter_all_skips_deleted(hub):
    memid, _ = hub
    ids = [memid.allocate(f"记忆{i}", sender="user") for i in range(1, 4)]
    memid.tombstone(ids[0])
    seqs = [m["seq"] for m in memid.iter_all()]
    assert seqs == [2, 3]
    assert [m["seq"] for m in memid.iter_all(include_deleted=True)] == [1, 2, 3]


# ── 铁律 D：可校验 ───────────────────────────────────────

def test_verify_clean(hub):
    memid, home = hub
    for i in range(1, 4):
        memid.allocate(f"记忆{i}", sender="user")
    rep = memid.verify(str(home / "sandglass.txt"))
    assert rep["ok"] is True
    assert rep["memories"] == 3 and rep["journal_lines"] == 3
    assert rep["mismatches"] == [] and rep["orphan_seq"] == []


def test_verify_detects_tampered_journal(hub):
    """有人手改了 sandglass.txt → verify 必须报出来，而不是静默。"""
    memid, home = hub
    for i in range(1, 4):
        memid.allocate(f"记忆{i}", sender="user")
    p = home / "sandglass.txt"
    lines = _journal(home)
    lines[1] = lines[1].replace("记忆2", "被篡改的内容")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rep = memid.verify(str(p))
    assert rep["ok"] is False
    assert len(rep["mismatches"]) == 1
    assert rep["mismatches"][0]["seq"] == 2


def test_verify_detects_truncated_journal(hub):
    memid, home = hub
    for i in range(1, 4):
        memid.allocate(f"记忆{i}", sender="user")
    p = home / "sandglass.txt"
    p.write_text("\n".join(_journal(home)[:1]) + "\n", encoding="utf-8")
    rep = memid.verify(str(p))
    assert rep["ok"] is False
    assert rep["orphan_seq"] == [2, 3]


def test_deleted_rows_skip_journal_check(hub):
    """已 tombstone 的记忆不参与 journal 比对（正文已抹）。"""
    memid, home = hub
    ids = [memid.allocate(f"记忆{i}", sender="user") for i in range(1, 4)]
    memid.tombstone(ids[1])
    rep = memid.verify(str(home / "sandglass.txt"))
    assert rep["ok"] is True


# ── 多行消息：一条记忆 ≠ 一个物理行 ──────────────────────

def test_parse_multiline_records(hub):
    """log_message 写的 text 本身可以含换行 —— 一条记忆横跨多个物理行。"""
    memid, home = hub
    p = home / "sandglass.txt"
    p.write_text(
        "2026-08-27 10:00:00 | user | 第一条，单行\n"
        "2026-08-27 10:00:01 | user | 第二条开头\n"
        "    缩进的续行\n"
        "\n"
        "还有一行续行\n"
        "2026-08-27 10:00:02 | agent | 第三条，单行\n",
        encoding="utf-8")

    recs = memid.parse_journal_records(str(p))
    assert len(recs) == 3, "必须切成 3 条逻辑记忆，而不是 6 个物理行"
    assert recs[0]["text"] == "第一条，单行"
    assert recs[1]["text"] == "第二条开头\n    缩进的续行\n\n还有一行续行"
    assert recs[1]["line_start"] == 2 and recs[1]["line_end"] == 5
    assert recs[2]["seq"] == 3 and recs[2]["sender"] == "agent"

    st = memid.journal_stats(str(p))
    assert st == {"physical_lines": 6, "records": 3,
                  "continuation_lines": 3, "multiline_records": 1}


def test_multiline_not_truncated_on_migration_shape(hub):
    """回归：按物理行切分会把多行记忆截断成第一行 —— 必须保住全文。"""
    memid, home = hub
    body = "决策记录：\n1. 先做 ID 中枢\n2. 再修 forget\n3. 最后接向量"
    (home / "sandglass.txt").write_text(
        f"2026-08-27 10:00:00 | user | {body}\n", encoding="utf-8")
    recs = memid.parse_journal_records(str(home / "sandglass.txt"))
    assert len(recs) == 1
    assert recs[0]["text"] == body
    assert "最后接向量" in recs[0]["text"], "结尾内容被截断了"


def test_allocate_multiline_line_span(hub):
    memid, home = hub
    a = memid.allocate("单行", sender="user")
    b = memid.allocate("多行开头\n续行1\n续行2", sender="user")
    c = memid.allocate("再一条单行", sender="user")

    ra, rb, rc = memid.get(a), memid.get(b), memid.get(c)
    assert (ra["line_start"], ra["line_end"]) == (1, 1)
    assert (rb["line_start"], rb["line_end"]) == (2, 4)
    assert (rc["line_start"], rc["line_end"]) == (5, 5)

    lines = (home / "sandglass.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    assert lines[4].endswith("再一条单行")
    assert memid.verify(str(home / "sandglass.txt"))["ok"] is True


def test_verify_with_multiline_journal(hub):
    memid, home = hub
    memid.allocate("普通一条", sender="user")
    memid.allocate("带换行的一条\n第二行\n第三行", sender="user")
    rep = memid.verify(str(home / "sandglass.txt"))
    assert rep["ok"] is True
    assert rep["journal_records"] == 2
    assert rep["physical_lines"] if False else rep["journal_lines"] == 4
    assert rep["continuation_lines"] == 2
    assert rep["multiline_records"] == 1


def test_verify_detects_tamper_inside_multiline(hub):
    """篡改多行记忆的续行也必须被抓到。"""
    memid, home = hub
    memid.allocate("头一行\n原始续行", sender="user")
    p = home / "sandglass.txt"
    p.write_text(p.read_text(encoding="utf-8").replace("原始续行", "被改过的续行"),
                 encoding="utf-8")
    rep = memid.verify(str(p))
    assert rep["ok"] is False
    assert len(rep["mismatches"]) == 1


def test_get_exposes_line_span(hub):
    memid, _ = hub
    mid = memid.allocate("a\nb", sender="user")
    row = memid.get(mid)
    assert row["line_start"] == 1 and row["line_end"] == 2


def test_verify_rejects_empty_hub(hub):
    """空中枢必须报 not ok —— 否则"没迁移"和"迁移完美"无法区分。"""
    memid, home = hub
    (home / "sandglass.txt").write_text(
        "2026-08-27 10:00:00 | user | 日志里有内容\n"
        "2026-08-27 10:00:01 | user | 但中枢是空的\n", encoding="utf-8")
    rep = memid.verify(str(home / "sandglass.txt"))
    assert rep["journal_records"] == 2
    assert rep["memories"] == 0
    assert rep["hub_empty"] is True
    assert rep["ok"] is False, "空中枢报了 ok —— 这正是要防的假绿"
    assert rep["coverage"] == 0.0


def test_verify_reports_coverage(hub):
    """中枢落后于日志 = 有写路径在绕过中枢。**必须报错。**

    这条断言原本是 `ok is True`，注释写着「落后不是不一致」——
    在 Step 2 之前那是对的：当时 hermes 确实绕过中枢直写，落后是常态，
    标红就是永久红。Step 2 之后所有写入都该经 allocate()，契约变了，
    "落后"从常态变成了漏记忆的信号，而这条测试把旧契约锁死了。

    生产上因此出现：中枢 8852 / 日志 8854，verify() 报 ok=True，
    定时巡检在写路径正在漏记忆的时候一路全绿。
    """
    memid, home = hub
    memid.allocate("第一条", sender="user")
    memid.allocate("第二条", sender="user")
    # 模拟绕过中枢直接追加
    with open(home / "sandglass.txt", "a", encoding="utf-8") as f:
        f.write("2026-08-27 10:00:02 | user | 中枢不知道的新记忆\n")
    rep = memid.verify(str(home / "sandglass.txt"))
    assert rep["journal_records"] == 3
    assert rep["live_memories"] == 2
    assert abs(rep["coverage"] - 2 / 3) < 1e-9
    assert rep["hub_empty"] is False
    assert rep["hub_behind"] == 1
    assert rep["ok"] is False


def test_verify_rejects_seq_gaps(hub):
    """seq 断层 = 有记忆没进中枢，必须报 not ok。

    回归：迁移曾因内容哈希去重把 8846 条只写进 3900 条，
    gaps 被记录却不参与 ok 判定，于是照样报绿。
    """
    memid, home = hub
    for i in range(1, 5):
        memid.allocate(f"记忆{i}", sender="user")
    conn = memid.get_conn()
    # 制造断层：直接删掉 seq=2 的行（模拟迁移漏写，注意不是 tombstone）
    conn.execute("DELETE FROM memories WHERE seq=2")
    conn.commit()

    rep = memid.verify(str(home / "sandglass.txt"))
    assert rep["memories"] == 3 and rep["journal_records"] == 4
    assert rep["gaps"], "seq 断层没被记录"
    assert rep["ok"] is False, "seq 断层却报了 ok —— 正是要防的假绿"
