"""写入不随记忆变多而变慢（v7.18.1）

以前每写一条值得长期记住的消息，engram_store.jsonl 都被整份读进来、再整份写回去；取最近 50 条也要把整份读一遍。
BEAM 1000 万 token 的长对话写到 5000 条消息时，每轮写入 120 ms，其中八成多花在这里（写到 2 万条时每条约 100 ms）。
影子沙的实体行号串也是每写一条就整串拆开、排序、拼回去。
"""
import json
import os

import pytest

from nexsandglass.engram import bridge
from nexsandglass.features import shadow_sand


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "engram_store.jsonl")
    monkeypatch.setattr(bridge, "_STORE", path)
    return path


def _rows(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def _fill(path, n):
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({"ts": f"2026-01-01 00:00:{i % 60:02d}", "content": f"memory number {i}",
                                "type": "semantic", "action": "INSERT"}, ensure_ascii=False) + "\n")


def test_insert_appends_without_reading_the_store(store, monkeypatch):
    _fill(store, 2000)
    before = open(store, "rb").read()
    calls = {"loads": 0}
    real = json.loads

    def counting(*a, **k):
        calls["loads"] += 1
        return real(*a, **k)

    monkeypatch.setattr(bridge.json, "loads", counting)
    r = bridge.ingest_classified("the user switched to a standing desk", action="INSERT", mem_type="semantic")
    assert r["status"] == "insert"
    assert calls["loads"] == 0                                     # 不解析已有的行
    after = open(store, "rb").read()
    assert after.startswith(before) and after.count(b"\n") == before.count(b"\n") + 1   # 只在末尾多了一行
    assert _rows(store)[-1]["content"] == "the user switched to a standing desk"


def test_conflict_appends(store):
    _fill(store, 10)
    r = bridge.ingest_classified("the user does not live in Amsterdam", action="CONFLICT")
    assert r["status"] == "conflict"
    rows = _rows(store)
    assert len(rows) == 11 and rows[-1]["status"] == "conflict_candidate"


def test_recent_returns_the_same_rows_as_before(store, monkeypatch):
    _fill(store, 5000)                                             # 比一次读的 64 KB 大好几倍
    expect = [json.loads(l) for l in open(store, encoding="utf-8").readlines()[-50:]]
    calls = {"loads": 0}
    real = json.loads

    def counting(*a, **k):
        calls["loads"] += 1
        return real(*a, **k)

    monkeypatch.setattr(bridge.json, "loads", counting)
    assert bridge.recent(50) == expect
    assert calls["loads"] == 50


def test_recent_edge_cases(store):
    assert bridge.recent(5) == []                                  # 文件还不存在
    _fill(store, 3)
    assert [r["content"] for r in bridge.recent(10)] == ["memory number 0", "memory number 1", "memory number 2"]
    assert bridge.recent(0) == []
    with open(store, "a", encoding="utf-8") as f:                  # 最后一行没写完（没有换行）
        f.write('{"ts": "x", "content": "partial"')
    assert [r["content"] for r in bridge.recent(2)] == ["memory number 2"]


def test_append_after_a_line_without_newline(store):
    with open(store, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "a", "content": "first"}))       # 没有结尾换行
    bridge.ingest_classified("second memory", action="INSERT", mem_type="semantic")
    assert [r["content"] for r in _rows(store)] == ["first", "second memory"]   # 没粘到上一行后面


def test_reinforce_and_dedup_update_the_row_in_place(store):
    bridge.ingest_classified("the user loves pour-over coffee", action="INSERT", mem_type="emotional")
    _fill_extra = [bridge.ingest_classified(f"other memory {i}", action="INSERT") for i in range(3)]
    before = _rows(store)
    r = bridge.ingest_classified("the user loves pour-over coffee", action="REINFORCE")
    assert r["status"] == "reinforce"
    after = _rows(store)
    assert len(after) == len(before)                               # 不新插
    assert after[0]["access_count"] == 1 and after[0]["action"] == "REINFORCE"
    assert after[1:] == before[1:]                                 # 别的行不动
    r = bridge.ingest_classified("the user loves pour-over coffee", action="DEDUP")
    assert r["status"] == "dedup" and _rows(store)[0]["access_count"] == 2
    assert len(_fill_extra) == 3


def test_reinforce_picks_the_most_recent_identical_row(store):
    for _ in range(2):
        bridge.ingest_classified("same words", action="INSERT", mem_type="procedural")
    bridge.ingest_classified("same words", action="DEDUP")
    rows = _rows(store)
    assert [r.get("access_count") for r in rows] == [0, 1]


def test_long_text_cannot_match_and_is_inserted(store):
    long_text = "a long message " * 40                             # 存进去的只有前 300 字，不可能完全相同
    bridge.ingest_classified(long_text, action="INSERT", mem_type="emotional")
    r = bridge.ingest_classified(long_text, action="REINFORCE")
    assert r["status"] == "insert" and len(_rows(store)) == 2


def test_override_with_supersede_id_marks_the_old_row(store):
    r1 = bridge.ingest_classified("the user lives in Amsterdam", action="INSERT", mem_type="semantic")
    with open(store, "a", encoding="utf-8") as f:
        f.write("not json at all\n")                               # 解析不了的行原样保留
    r2 = bridge.ingest_classified("the user lives in Rotterdam", action="OVERRIDE", mem_type="semantic",
                                  supersede_id=r1["id"])
    assert r2["status"] == "insert"
    lines = open(store, encoding="utf-8").read().splitlines()
    assert lines[1] == "not json at all"
    old, new = json.loads(lines[0]), json.loads(lines[2])
    assert old["status"] == "historical" and old["superseded_by"]
    assert new["content"] == "the user lives in Rotterdam"


def test_entity_line_numbers_are_appended():
    assert shadow_sand._append_line_num("", 7) == "7"
    assert shadow_sand._append_line_num("3,5", 9) == "3,5,9"       # 常见情况：新行号最大，直接接上
    assert shadow_sand._append_line_num("3,9", 5) == "3,5,9"       # 不是追加：照旧去重排序
    assert shadow_sand._append_line_num("3,5", 5) == "3,5"
    assert shadow_sand._append_line_num(",3,", 4) == "3,4"         # 串里有空段也不出错


def test_shadow_index_keeps_entity_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXSANDBASE_HOME", str(tmp_path))
    shadow_sand.set_shadow_path(str(tmp_path / "shadow_sand.db"))
    monkeypatch.setattr(shadow_sand, "_conn", None)
    try:
        for ln, text in ((1, 'set "user_id" first'), (4, 'then "user_id" and "user_id" again'), (9, 'read "user_id"')):
            shadow_sand.shadow_index(text, line_num=ln, mem_id=f"m{ln}")
        db = shadow_sand._get_conn()
        lns, mids = db.execute("SELECT line_nums, mem_ids FROM entities WHERE name = 'user_id'").fetchone()
        assert lns == "1,4,9"
        assert mids.split(",") == ["m1", "m4", "m9"]
        assert db.execute("SELECT COUNT(*) FROM entities WHERE name = 'user_id'").fetchone()[0] == 1
    finally:
        if shadow_sand._conn is not None:
            shadow_sand._conn.close()
        monkeypatch.setattr(shadow_sand, "_conn", None)
