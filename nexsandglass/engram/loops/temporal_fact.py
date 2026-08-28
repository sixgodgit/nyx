"""
engram/loops/temporal_fact.py — 时序事实冲突处理（Task 5）

在 thread_validate_fact 基础上，给三元组加上 valid_from / valid_until 字段。
当新事实与旧事实冲突时（如地点/状态类关系），不拒绝写入，而是：
1. 把旧三元组标记失效（写入 valid_until）
2. 新三元组正常写入
3. 保留完整历史轨迹

这是 Zep/Graphiti 处理"用户从纽约搬到伦敦"的核心思路。

设计：
- valid_from / valid_until 可为 NULL（表示仍然有效）
- 检索时默认只返回当前有效的（valid_until IS NULL）
- 历史查询可选 INCLUDE expired=true 返回全部
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# 关系类型：地点/状态类关系需要时效性处理
# 语义约定：只有本集合内的 relation 才做「冲突→旧值失效+写新值」的时序覆盖；
# 集合外的 relation（如"座驾"、"喜欢"）走非时序直插分支，不查冲突、直接写。
# 集成者如需对某 relation 做时序覆盖，须先加入此集合，否则仅直插。
TEMPORAL_RELATIONS = frozenset({
    "住在", "位于", "使用", "常用", "主要用",
    "邮箱", "电话", "地址", "住址",
    "公司", "职位",
})


@dataclass
class TemporalFactReport:
    """时序冲突处理报告"""

    expired: list[str] = field(default_factory=list)   # 被标记失效的三元组 id
    inserted: bool = False
    conflicts: list[dict] = field(default_factory=list)


def resolve_temporal_conflict(
    db_path: str,
    subject: str,
    relation: str,
    new_object: str,
    source_line: int = 0,
    source: str = "regex",
    now: Optional[datetime] = None,
) -> TemporalFactReport:
    """
    处理时序事实冲突。

    - 非时效关系（如"喜欢"、"害怕"）：直接写入，不检查冲突
    - 时效关系（如"住在"、"邮箱"）：检查是否有相同 (subject, relation) 但不同 object
      的活跃三元组 → 标记失效 → 写入新事实

    返回报告（expired ids + inserted flag）。
    """
    report = TemporalFactReport()
    now = now or datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    expired_ids = []

    # 自足建表：消除对 weavethread 写入口的隐藏依赖（全新空库可直接用）
    _ensure_table(db_path)
    conn = sqlite3.connect(db_path, timeout=10)

    # 非时效关系：直接写入
    if relation not in TEMPORAL_RELATIONS:
        try:
            conn.execute(
                """
                INSERT INTO wthread_triples (subject, relation, object, source_line, source, valid_from, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (subject, relation, new_object, source_line, source, now_str, now_str),
            )
            conn.commit()
            report.inserted = True
        except Exception as e:
            logger.warning("[resolve_temporal_conflict] 写入失败: %s", e)
        finally:
            conn.close()
        return report

    # 时效关系：检查冲突
    try:
        # 找活跃的冲突三元组
        rows = conn.execute(
            """
            SELECT id, object FROM wthread_triples
            WHERE subject = ? AND relation = ? AND valid_until IS NULL
            """,
            (subject, relation),
        ).fetchall()

        expired_ids = []
        for row_id, old_object in rows:
            if old_object != new_object:
                # 冲突：标记旧事实失效
                conn.execute(
                    "UPDATE wthread_triples SET valid_until = ? WHERE id = ?",
                    (now_str, row_id),
                )
                expired_ids.append(str(row_id))
                report.conflicts.append({
                    "expired_id": row_id,
                    "old_object": old_object,
                    "new_object": new_object,
                    "subject": subject,
                    "relation": relation,
                })
            # 如果 object 相同 → 不重复写入（去重）

        # 写入新事实
        if expired_ids or not rows:
            conn.execute(
                """
                INSERT INTO wthread_triples (subject, relation, object, source_line, source, valid_from, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (subject, relation, new_object, source_line, source, now_str, now_str),
            )
            report.inserted = True

        conn.commit()
    except Exception as e:
        logger.warning("[resolve_temporal_conflict] 失败: %s", e)
    finally:
        conn.close()

    report.expired = expired_ids
    return report


def _ensure_table(db_path: str) -> None:
    """确保 wthread_triples 表存在（temporal_fact 自足，不依赖外部建表）。

    表 DDL 原本只存在于 `weavethread._ensure_table()`，且仅在 weavethread
    的写入口被调用。第三方若直接使用本模块的 temporal API 而未先触发
    weavethread 初始化（全新空库 / 按 README 直接集成），`wthread_triples`
    不存在 → `no such table`，写路径被吞 → 数据静默丢失，读路径直接崩溃。

    这里在模块内复制建表 + 索引 + 时序列迁移，使 temporal_fact 独立可用。
    """
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wthread_triples (
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wthread_subject ON wthread_triples(subject)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wthread_relation ON wthread_triples(relation)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wthread_object ON wthread_triples(object)")
        conn.commit()
    finally:
        conn.close()


def ensure_temporal_columns(db_path: str) -> None:
    """确保 wthread_triples 表有 valid_from / valid_until 列（兼容旧表）。

    先 `_ensure_table` 建表（若无），再 ALTER 补列——老表可能缺时序列。
    """
    _ensure_table(db_path)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        for col, typ in [("valid_from", "TEXT"), ("valid_until", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE wthread_triples ADD COLUMN {col} {typ}")
            except Exception:
                pass  # 列已存在
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════
# 查询 API（v6.0 融合补充）：get_current / as_of / history_of / evolution_chain
# ══════════════════════════════════════════════════════════

def _connect(db_path: str):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def get_current(db_path: str, subject: str = None, predicate: str = None) -> list:
    """当前有效事实（只取 valid_until IS NULL 的 active）。"""
    ensure_temporal_columns(db_path)
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM wthread_triples WHERE valid_until IS NULL"
        params: list = []
        if subject:
            sql += " AND (subject=? OR object=?)"
            params += [subject, subject]
        if predicate:
            sql += " AND relation=?"
            params.append(predicate)
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def as_of(db_path: str, timestamp: str) -> list:
    """某时刻的有效事实快照（valid_from <= t 且 [valid_until IS NULL 或 >= t]）。"""
    ensure_temporal_columns(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM wthread_triples WHERE "
            "valid_from <= ? AND (valid_until IS NULL OR valid_until >= ?) "
            "ORDER BY id DESC",
            (timestamp, timestamp),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def history_of(db_path: str, subject: str, predicate: str = None) -> list:
    """演变链：按时间倒序（最新在前）。"""
    ensure_temporal_columns(db_path)
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM wthread_triples WHERE subject=?"
        params = [subject]
        if predicate:
            sql += " AND relation=?"
            params.append(predicate)
        sql += " ORDER BY valid_from DESC, id DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def evolution_chain(db_path: str, subject: str, predicate: str = None, limit: int = 8) -> str:
    """压缩的演变链文本（供 history intent 注入）。"""
    hist = history_of(db_path, subject, predicate)
    if not hist:
        return f"{subject}: 无历史记录"
    parts = []
    for h in hist[:limit]:
        status = "当前" if h.get("valid_until") is None else ("曾" + (h.get("valid_until") or "")[:10])
        parts.append(f"[{status}] {h['subject']} {h['relation']} {h['object']}")
    return "\n".join(parts)
