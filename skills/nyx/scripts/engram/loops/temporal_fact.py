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

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# 关系类型：地点/状态类关系需要时效性处理
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


def _insert_fact(
    db_path: str,
    subject: str,
    relation: str,
    obj: str,
    source_line: int,
    source: str,
    now_str: str,
) -> None:
    """写入新事实（带 valid_from）。"""
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(
            """
            INSERT INTO wthread_triples (subject, relation, object, source_line, source, valid_from, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (subject, relation, obj, source_line, source, now_str, now_str),
        )
        conn.commit()
    finally:
        conn.close()


def ensure_temporal_columns(db_path: str) -> None:
    """确保 wthread_triples 表有 valid_from / valid_until 列（兼容旧表）。"""
    conn = sqlite3.connect(db_path, timeout=10)
    for col, typ in [("valid_from", "TEXT"), ("valid_until", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE wthread_triples ADD COLUMN {col} {typ}")
        except Exception:
            pass  # 列已存在
    conn.commit()
    conn.close()


import logging
logger = logging.getLogger(__name__)
