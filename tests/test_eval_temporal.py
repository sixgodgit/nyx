"""
测试：评测框架（Task 4）+ 时序事实冲突处理（Task 5）
"""

import sys
import os
import tempfile

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    _THIS_DIR,
    os.path.dirname(_THIS_DIR),
    os.path.dirname(os.path.dirname(_THIS_DIR)),
):
    if os.path.isdir(os.path.join(_candidate, "nexsandglass")):
        sys.path.insert(0, _candidate)
        break

# ── Task 4: 评测框架 ──────────────────────────────────────────
from tests.eval.test_set import get_eval_set, get_eval_stats
from tests.eval.run_eval import run_eval


def test_eval_set_exists():
    test_set = get_eval_set()
    assert len(test_set) >= 10, "测试集应至少 10 条"
    # 每条都有必要字段
    for item in test_set:
        assert "query" in item
        assert "expected" in item
        assert "category" in item


def test_eval_stats():
    stats = get_eval_stats()
    assert stats["total"] == len(get_eval_set())
    assert "fact_exact" in stats["categories"]
    assert "fact_semantic" in stats["categories"]
    assert "cross_session" in stats["categories"]


def test_run_eval_returns_report():
    report = run_eval()
    assert "lexical_recall" in report
    assert "decay_verification" in report
    dv = report["decay_verification"]
    assert dv["decayed"] is True  # 30 天 episodic 应衰减
    assert dv["decayed_weight"] < dv["original_weight"]


def test_run_eval_writes_report():
    tmp = tempfile.mktemp(suffix=".md")
    try:
        run_eval(output_path=tmp)
        assert os.path.exists(tmp)
        with open(tmp, "r", encoding="utf-8") as f:
            content = f.read()
        assert "评测报告" in content
        assert "衰减曲线验证" in content
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── Task 5: 时序事实冲突处理 ──────────────────────────────────
import sqlite3
from nexsandglass.engram.loops.temporal_fact import (
    resolve_temporal_conflict,
    ensure_temporal_columns,
    TEMPORAL_RELATIONS,
)


def _make_db():
    """创建带 wthread_triples 表的临时数据库。"""
    tmp = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(tmp)
    conn.execute("""
        CREATE TABLE wthread_triples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            relation TEXT NOT NULL,
            object TEXT NOT NULL,
            source_line INTEGER,
            confidence REAL DEFAULT 0.5,
            source TEXT DEFAULT 'regex',
            valid_from TEXT,
            valid_until TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
    return tmp


def test_temporal_relations_defined():
    assert "住在" in TEMPORAL_RELATIONS
    assert "邮箱" in TEMPORAL_RELATIONS
    assert "喜欢" not in TEMPORAL_RELATIONS  # 非时效关系


def test_resolve_conflict_expires_old():
    db = _make_db()
    try:
        # 第一次：写入 "住在 海牙"
        r1 = resolve_temporal_conflict(db, "user", "住在", "海牙")
        assert r1.inserted is True
        assert r1.expired == []

        # 第二次：写入 "住在 阿姆斯特丹"（冲突）
        r2 = resolve_temporal_conflict(db, "user", "住在", "阿姆斯特丹")
        assert r2.inserted is True
        assert len(r2.expired) == 1, "旧事实应被标记失效"
        assert len(r2.conflicts) == 1
        assert r2.conflicts[0]["old_object"] == "海牙"
        assert r2.conflicts[0]["new_object"] == "阿姆斯特丹"

        # 验证：旧事实 valid_until 非空，新事实 valid_until 为空
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT object, valid_from, valid_until FROM wthread_triples ORDER BY id"
        ).fetchall()
        conn.close()
        assert len(rows) == 2, "新旧事实都应保留（历史轨迹）"
        old, new = rows[0], rows[1]
        assert old[0] == "海牙" and old[2] is not None, "旧事实应失效"
        assert new[0] == "阿姆斯特丹" and new[2] is None, "新事实应有效"
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_resolve_same_object_dedup():
    db = _make_db()
    try:
        r1 = resolve_temporal_conflict(db, "user", "邮箱", "a@b.com")
        assert r1.inserted is True
        # 相同 object 重复写入 → 不新增
        r2 = resolve_temporal_conflict(db, "user", "邮箱", "a@b.com")
        assert r2.inserted is False, "相同事实不应重复写入"
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM wthread_triples").fetchone()[0]
        conn.close()
        assert count == 1
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_non_temporal_relation_no_expiry():
    db = _make_db()
    try:
        r1 = resolve_temporal_conflict(db, "user", "喜欢", "车")
        assert r1.inserted is True
        r2 = resolve_temporal_conflict(db, "user", "喜欢", "车")
        assert r2.inserted is True, "非时效关系不检查冲突（允许多条）"
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM wthread_triples").fetchone()[0]
        conn.close()
        assert count == 2
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_ensure_temporal_columns_idempotent():
    db = _make_db()
    try:
        ensure_temporal_columns(db)  # 已存在 → 不报错
        ensure_temporal_columns(db)  # 幂等
        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(wthread_triples)").fetchall()]
        conn.close()
        assert "valid_from" in cols
        assert "valid_until" in cols
    finally:
        if os.path.exists(db):
            os.remove(db)


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"✅ {fn.__name__}")
            passed += 1
        except Exception:
            print(f"❌ {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
