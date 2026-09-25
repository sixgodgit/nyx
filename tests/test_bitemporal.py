"""tests/test_bitemporal.py — 双时态事实验收（v7.7）

两条时间轴必须分开：
    有效时间  这件事在世界里何时为真     valid_from / valid_until / valid_basis
    记录时间  系统何时知道这件事          recorded_at / closed_at / retracted_at

验收的是三类问题，单时态系统一类都答不了：
    「你现在住哪」                   get_current
    「2024 年 6 月你住哪」           as_of（有效时间）
    「我 3 月的时候以为你住哪」       known_at / get_current(known_at=)（记录时间）

外加一条生产回归：v7.6 以前 wthread_store 绕过时序逻辑，
「最终用特斯拉」「后来改用吉利」之后 get_current 同时返回两者、as_of 什么都查不到。
"""

import os
import sqlite3
from datetime import datetime

import pytest

from nexsandglass.engram.loops.temporal_fact import (
    as_of, belief_timeline, ensure_temporal_columns, evolution_chain, get_current,
    history_of, known_at, normalize_ts, repair_open_conflicts, resolve_temporal_conflict,
)

T_MAR = datetime(2026, 3, 1, 10, 0, 0)     # 记录时刻
T_JUN = datetime(2026, 6, 1, 10, 0, 0)
T_SEP = datetime(2026, 9, 1, 10, 0, 0)


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "shadow_sand.db")


def _put(db, obj, now, rel="住在", subj="user", valid_from=None):
    return resolve_temporal_conflict(db, subj, rel, obj, now=now, valid_from=valid_from)


def _objs(rows):
    return sorted(r["object"] for r in rows)


def _count(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT COUNT(*) FROM wthread_triples").fetchone()[0]
    finally:
        c.close()


# ══════════════════════════════════════════════════════════
# 1. 两条轴分开记录
# ══════════════════════════════════════════════════════════

def test_default_is_assumed_not_stated(db):
    """没陈述生效时间 → 只能假设 = 记录时刻，而且必须标明是假设的。"""
    r = _put(db, "海牙", T_MAR)
    assert r.valid_basis == "assumed"
    assert r.valid_from == r.recorded_at == "2026-03-01T10:00:00Z"


def test_stated_valid_time_differs_from_record_time(db):
    """「我去年就搬到伦敦了」：今天得知，去年为真。单时态系统会记成「今天起住伦敦」。"""
    r = _put(db, "伦敦", T_SEP, valid_from="2025-04")
    assert r.valid_basis == "stated"
    assert r.valid_from == "2025-04-01T00:00:00Z"
    assert r.recorded_at == "2026-09-01T10:00:00Z"

    assert _objs(as_of(db, "2025-06-01")) == ["伦敦"], "陈述的生效时间没被尊重"
    # 对照：只假设的事实，在得知之前的有效时刻查不到
    _put(db, "猫", T_SEP, rel="喜欢")
    assert "猫" not in _objs(as_of(db, "2025-06-01"))


# ══════════════════════════════════════════════════════════
# 2. 标准冲突：搬家
# ══════════════════════════════════════════════════════════

def test_move_closes_old_in_place(db):
    _put(db, "海牙", T_MAR)
    r = _put(db, "阿姆斯特丹", T_JUN)
    assert r.expired and r.conflicts[0]["old_object"] == "海牙"
    assert _objs(get_current(db, "user", "住在")) == ["阿姆斯特丹"]
    assert _count(db) == 2, "标准情形应就地截断，不该复制行"

    old = [h for h in history_of(db, "user", "住在") if h["object"] == "海牙"][0]
    assert old["valid_until"] == "2026-06-01T10:00:00Z"
    assert old["closed_at"] == "2026-06-01T10:00:00Z", "没记下「何时得知它结束」"


def test_belief_at_earlier_record_time(db):
    """「我 4 月的时候以为你住哪」—— 那时还不知道你会搬。"""
    _put(db, "海牙", T_MAR)
    _put(db, "阿姆斯特丹", T_JUN)
    april = "2026-04-15"
    assert _objs(get_current(db, "user", "住在", known_at=april)) == ["海牙"]
    assert _objs(get_current(db, "user", "住在")) == ["阿姆斯特丹"]
    then = known_at(db, april, "user", "住在")
    assert len(then) == 1 and then[0]["valid_until"] is None, \
        "4 月的信念里海牙应是开放区间（那时还不知道结束）"
    assert _objs(known_at(db, "2026-02-01", "user")) == [], "得知之前不该有任何信念"


# ══════════════════════════════════════════════════════════
# 3. 乱序到达 / 更正 / 精化
# ══════════════════════════════════════════════════════════

def test_late_arriving_history_does_not_dethrone_current(db):
    """先知道「2024 起住阿姆」，后来才听说「2022 年住海牙」→ 海牙是历史，不是现任。"""
    _put(db, "阿姆斯特丹", T_MAR, valid_from="2024")
    r = _put(db, "海牙", T_JUN, valid_from="2022")
    assert r.late_arrival is True and r.valid_until == "2024-01-01T00:00:00Z"
    assert _objs(get_current(db, "user", "住在")) == ["阿姆斯特丹"], "旧消息把现任顶掉了"
    assert _objs(as_of(db, "2023-01-01")) == ["海牙"]
    assert _objs(as_of(db, "2025-01-01")) == ["阿姆斯特丹"]


def test_correction_splits_bounded_interval_and_keeps_old_belief(db):
    """海牙 [2020,2023)、阿姆 [2023,∞)；后来得知「2021 就搬去鹿特丹了」。

    现在的信念：海牙 [2020,2021) → 鹿特丹 [2021,2023) → 阿姆。
    更正之前的信念：海牙一直到 2023 —— 这个必须还查得到。
    """
    _put(db, "海牙", T_MAR, valid_from="2020")
    _put(db, "阿姆斯特丹", T_MAR, valid_from="2023")
    r = _put(db, "鹿特丹", T_SEP, valid_from="2021")
    assert r.retracted, "改写有终点的区间应走版本化（撤回+新版本）"

    assert _objs(as_of(db, "2020-06-01")) == ["海牙"]
    assert _objs(as_of(db, "2022-06-01")) == ["鹿特丹"]
    assert _objs(as_of(db, "2024-06-01")) == ["阿姆斯特丹"]
    assert _objs(get_current(db, "user", "住在")) == ["阿姆斯特丹"]

    before = as_of(db, "2022-06-01", known_at="2026-06-01")
    assert _objs(before) == ["海牙"], "更正之前「以为 2022 住海牙」这条信念丢了"


def test_stated_start_refines_assumed_row(db):
    """原来只知道「9 月得知住阿姆」，后来说清楚「2024 年就搬去了」→ 精化，不是新事实。"""
    _put(db, "阿姆斯特丹", T_MAR)
    r = _put(db, "阿姆斯特丹", T_SEP, valid_from="2024")
    assert r.refined and r.inserted

    cur = get_current(db, "user", "住在")
    assert len(cur) == 1 and cur[0]["valid_basis"] == "stated"
    assert cur[0]["valid_from"] == "2024-01-01T00:00:00Z"
    then = get_current(db, "user", "住在", known_at="2026-06-01")
    assert len(then) == 1 and then[0]["valid_basis"] == "assumed", "精化之前的信念丢了"


def test_same_object_dedup(db):
    _put(db, "海牙", T_MAR)
    r = _put(db, "海牙", T_JUN)
    assert r.inserted is False and _count(db) == 1


def test_switch_back_is_a_new_episode(db):
    """特斯拉 → 吉利 → 特斯拉：第三次不是重复，是新的一段。"""
    _put(db, "特斯拉", T_MAR, rel="使用")
    _put(db, "吉利", T_JUN, rel="使用")
    r = _put(db, "特斯拉", T_SEP, rel="使用")
    assert r.inserted is True
    assert _objs(get_current(db, "user", "使用")) == ["特斯拉"]
    assert len(history_of(db, "user", "使用")) == 3


# ══════════════════════════════════════════════════════════
# 4. 看法怎么变的 / 注入文本的诚实性
# ══════════════════════════════════════════════════════════

def test_belief_timeline_orders_by_record_time(db):
    _put(db, "海牙", T_MAR)
    _put(db, "阿姆斯特丹", T_JUN)
    ev = [(e["event"], e["object"]) for e in belief_timeline(db, "user", "住在")]
    assert ev == [("learned", "海牙"), ("learned", "阿姆斯特丹"), ("closed", "海牙")]


def test_evolution_chain_does_not_pass_assumed_off_as_stated(db):
    _put(db, "海牙", T_MAR)
    _put(db, "阿姆斯特丹", T_JUN, valid_from="2026-05")
    chain = evolution_chain(db, "user", "住在")
    assert "阿姆斯特丹（自 2026-05-01 起）" in chain
    assert "海牙（得知于 2026-03-01，起始时间未陈述）" in chain, chain


# ══════════════════════════════════════════════════════════
# 5. 旧库迁移
# ══════════════════════════════════════════════════════════

def _legacy_db(path):
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE wthread_triples (
        id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL,
        relation TEXT NOT NULL, object TEXT NOT NULL, source_line INTEGER,
        confidence REAL DEFAULT 0.5, source TEXT DEFAULT 'regex',
        valid_from TEXT, valid_until TEXT, created_at TEXT DEFAULT (datetime('now')))""")
    # v7.6 wthread_store 的形状：无 valid_from，created_at 为 ISO Z
    c.execute("INSERT INTO wthread_triples (subject,relation,object,created_at) "
              "VALUES ('user','使用','特斯拉','2026-03-01T10:00:00Z')")
    c.execute("INSERT INTO wthread_triples (subject,relation,object,created_at) "
              "VALUES ('user','使用','吉利','2026-06-01T10:00:00Z')")
    # SQLite datetime('now') 的形状：空格分隔、无 Z
    c.execute("INSERT INTO wthread_triples (subject,relation,object,created_at) "
              "VALUES ('user','喜欢','猫','2026-02-01 08:00:00')")
    # 旧 resolve_temporal_conflict 截断过的行
    c.execute("INSERT INTO wthread_triples (subject,relation,object,valid_from,valid_until,"
              "created_at) VALUES ('user','住在','海牙','2026-01-01T00:00:00Z',"
              "'2026-04-01T00:00:00Z','2026-01-01T00:00:00Z')")
    c.commit()
    c.close()


def test_legacy_rows_are_backfilled_honestly(db):
    _legacy_db(db)
    ensure_temporal_columns(db)
    ensure_temporal_columns(db)                      # 幂等
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = {r["object"]: dict(r) for r in c.execute("SELECT * FROM wthread_triples")}
    c.close()
    assert all(r["valid_basis"] == "assumed" for r in rows.values()), \
        "旧行的生效时间从来不是陈述的，不能标 stated"
    assert rows["猫"]["recorded_at"] == "2026-02-01T08:00:00Z", "空格格式没规范化"
    assert rows["特斯拉"]["valid_from"] == "2026-03-01T10:00:00Z", "缺 valid_from 的旧行没回填"
    assert rows["海牙"]["closed_at"] == "2026-04-01T00:00:00Z"
    assert "猫" in _objs(as_of(db, "2026-09-01")), "回填后 as_of 仍查不到旧行"


def test_repair_open_conflicts_is_dry_run_then_preserves_old_belief(db):
    """旧 wthread_store 留下的「多个现任」：预演不动；修复后仍能看到修复前的信念。"""
    _legacy_db(db)
    assert _objs(get_current(db, "user", "使用")) == ["吉利", "特斯拉"]

    dry = repair_open_conflicts(db)
    assert dry["applied"] is False and dry["groups"][0]["keep"] == "吉利"
    assert _objs(get_current(db, "user", "使用")) == ["吉利", "特斯拉"], "预演改了数据"

    rep = repair_open_conflicts(db, apply=True, now=T_SEP)
    assert rep["closed"] == 1
    assert _objs(get_current(db, "user", "使用")) == ["吉利"]
    assert _objs(get_current(db, "user", "使用", known_at="2026-07-01")) == ["吉利", "特斯拉"], \
        "修复之前的信念应仍可重建（只截断不删除）"


# ══════════════════════════════════════════════════════════
# 6. 时间规范化
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("raw,want", [
    ("2025", "2025-01-01T00:00:00Z"),
    ("2025-06", "2025-06-01T00:00:00Z"),
    ("2025-06-15", "2025-06-15T00:00:00Z"),
    ("2025-06-15 08:30:00", "2025-06-15T08:30:00Z"),
    ("2025-06-15T08:30:00Z", "2025-06-15T08:30:00Z"),
    ("2025-06-15T10:30:00+02:00", "2025-06-15T08:30:00Z"),
    (datetime(2025, 6, 15, 8, 30), "2025-06-15T08:30:00Z"),
])
def test_normalize_ts(raw, want):
    assert normalize_ts(raw) == want


def test_normalize_ts_refuses_to_guess():
    with pytest.raises(ValueError):
        normalize_ts("去年夏天")


# ══════════════════════════════════════════════════════════
# 7. 生产写入口回归（真正的 observe 路径）
# ══════════════════════════════════════════════════════════

@pytest.fixture()
def wt(monkeypatch, tmp_path):
    from nexsandglass.features import weavethread
    path = str(tmp_path / "shadow_sand.db")
    monkeypatch.setattr(weavethread, "_DB", path)
    monkeypatch.delenv("WTHREAD_LLM_EXTRACTION", raising=False)
    return weavethread, path


def test_wthread_store_resolves_conflicts(wt):
    """v7.6 以前：get_current 同时返回特斯拉和吉利，as_of 返回 0 行。"""
    weavethread, path = wt
    assert weavethread.wthread_store("我们最终用特斯拉", 1, mem_id="m_a") == 1
    assert weavethread.wthread_store("后来改用吉利", 2, mem_id="m_b") == 1
    assert _objs(get_current(path, "user", "使用")) == ["吉利"], "生产路径仍然出现多个现任"
    assert as_of(path, "2099-01-01"), "生产路径写入的行 as_of 查不到"
    row = get_current(path, "user", "使用")[0]
    assert row["source_mem_id"] == "m_b", "来源 mem_id 丢了（擦除级联依赖它）"


def test_wthread_store_still_dedups_repeats(wt):
    weavethread, path = wt
    assert weavethread.wthread_store("我偏好 Python", 1) == 1
    assert weavethread.wthread_store("我偏好 Python", 2) == 0, "同一句话重复抽取不该重复写"


def test_wthread_add_accepts_stated_time(wt):
    weavethread, path = wt
    weavethread.wthread_add("user", "住在", "海牙", valid_from="2023")
    cur = get_current(path, "user", "住在")
    assert cur[0]["valid_basis"] == "stated" and cur[0]["valid_from"].startswith("2023")


def test_eval_temporal_runs_and_rejects_multiple_currents(wt):
    """README 列出的 temporal accuracy 指标以前一调就 ModuleNotFoundError。"""
    weavethread, path = wt
    from nexsandglass.runtime.eval import eval_temporal
    weavethread.wthread_store("我们最终用特斯拉", 1)
    weavethread.wthread_store("后来改用吉利", 2)
    assert eval_temporal([("user", "使用", "吉利")], db_path=path) == 1.0

    c = sqlite3.connect(path)
    c.execute("UPDATE wthread_triples SET valid_until=NULL, closed_at=NULL")
    c.commit()
    c.close()
    assert eval_temporal([("user", "使用", "吉利")], db_path=path) == 0.0, \
        "两个现任却判对 —— 被排序巧合掩盖了"
