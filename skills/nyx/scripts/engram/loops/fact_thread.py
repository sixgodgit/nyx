"""
engram/loops/fact_thread.py — 闭环 1：Thread ↔ Fact Store

事实变化自动更新知识图谱；图谱关系反向验证事实（冲突检测）。

方向 A（事实 → 图谱）：
  fact_to_thread() — 新事实入库后调用，从事实文本中提取实体三元组，
  写入织线图谱；同一 (subject, relation) 已存在但 object 不同 → 冲突报告。

方向 B（图谱 → 事实）：
  thread_validate_fact() — 新事实入库前调用，用图谱中已知关系校验：
  - 图谱已有相同 (subject, relation, object) → 事实可信（confirm）
  - 图谱有相同 (subject, relation) 但 object 不同 → 冲突（conflict）
  - 图谱无此关系 → 未知（unknown，正常新增）

设计：纯函数 + 注入存储适配器，便于测试与对接现有 weavethread / shadow_sand。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class FactThreadReport:
    """闭环 1 报告"""

    triples_extracted: list[tuple[str, str, str]] = field(default_factory=list)
    triples_stored: list[tuple[str, str, str]] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    validations: list[dict] = field(default_factory=list)


# ── 实体/关系提取（规则式，轻量无 LLM 依赖）────────────────────

# 常见事实模式：主语 + 谓语 + 宾语
_PATTERNS = [
    # X 的 Y 是 Z
    re.compile(r"([\u4e00-\u9fffA-Za-z0-9_.@+-]{2,30})的([\u4e00-\u9fffA-Za-z0-9]{2,12})是([^\n，。；;]{2,60})"),
    # X 住在/在/使用 Z
    re.compile(r"([\u4e00-\u9fffA-Za-z0-9_.@+-]{2,30})(?:住在|位于|使用|用的是|常用|主要用)([^\n，。；;]{2,60})"),
    # X 是 Y 的 Z（公司/角色关系）
    re.compile(r"([\u4e00-\u9fffA-Za-z0-9_.@+-]{2,40})是([\u4e00-\u9fffA-Za-z0-9]{2,20})(?:的|的成员|的一员)([\u4e00-\u9fffA-Za-z0-9_.@+-]{2,40})"),
    # 用户<属性>是<值>（无"的"结构）：用户邮箱是 X / 用户地址是 Y
    re.compile(r"([\u4e00-\u9fffA-Za-z0-9_.@+-]{2,20})(邮箱|电话|手机|地址|住址|生日|名字|姓名|邮编|车牌|公司|职位|网站|域名|账户|账号)(?:是|为|：|:)([^\n，。；;]{2,60})"),
    # 主语 是 宾语（简单判断句）
    re.compile(r"^([\u4e00-\u9fffA-Za-z0-9_.@+-]{2,30})是([^\n，。；;]{2,60})$"),
]

# 实体词表辅助：用户代词归一
_ENTITY_ALIASES = {
    "我": "user",
    "本人": "user",
    "用户": "user",
    "我们": "user",
    "我的": "user",
}


def _normalize_entity(e: str) -> str:
    e = e.strip().strip("，。；;、")
    return _ENTITY_ALIASES.get(e, e)


def extract_triples(text: str) -> list[tuple[str, str, str]]:
    """从事实文本中提取 (subject, relation, object) 三元组（规则式）。"""
    triples: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for pat in _PATTERNS:
        for m in pat.finditer(text):
            groups = m.groups()
            if len(groups) == 3:
                subj, rel, obj = groups
                # 模式1: X 的 Y 是 Z → (X, Y, Z)
                rel = rel.strip()
                if rel and rel not in ("的", "是"):
                    t = (_normalize_entity(subj), rel, _normalize_entity(obj))
                else:
                    continue
            elif len(groups) == 2:
                # 模式2: X 住在 Z → (X, "住在", Z)
                # 模式4: 用户邮箱是 X → (用户, "邮箱", X)
                subj, obj = groups
                rel = "住在"
                t = (_normalize_entity(subj), rel, _normalize_entity(obj))
            else:
                continue
            if all(t) and t not in seen:
                seen.add(t)
                triples.append(t)
    return triples


# ── 方向 A：事实 → 图谱 ────────────────────────────────────────

def fact_to_thread(
    fact_text: str,
    thread_store: Callable[[str, str, str], None],
    thread_query: Callable[[Optional[str], Optional[str], int], list],
    source: str = "fact_store",
) -> FactThreadReport:
    """
    新事实入库后调用：提取三元组写入图谱，检测冲突。

    Args:
        fact_text: 事实内容（如 "用户邮箱是 enfys@hvh.expert"）
        thread_store: 写入图谱的回调 (subject, relation, object) -> None
        thread_query: 查询图谱的回调 (entity, relation, limit) -> list[dict]
        source: 来源标记

    Returns:
        FactThreadReport: 提取/存储/冲突
    """
    report = FactThreadReport()
    triples = extract_triples(fact_text)
    report.triples_extracted = triples

    for subj, rel, obj in triples:
        # 查重 + 冲突检测
        existing = thread_query(subj, rel, 10)
        conflict = False
        for row in existing:
            existing_obj = _row_object(row)
            if existing_obj and existing_obj != obj:
                report.conflicts.append(
                    {
                        "subject": subj,
                        "relation": rel,
                        "existing_object": existing_obj,
                        "new_object": obj,
                        "source": source,
                    }
                )
                conflict = True
                break
        if not conflict:
            thread_store(subj, rel, obj)
            report.triples_stored.append((subj, rel, obj))

    return report


def _row_object(row) -> Optional[str]:
    """兼容 dict / 对象两种查询返回。"""
    if isinstance(row, dict):
        return row.get("object") or row.get("obj") or row.get("object_name")
    return getattr(row, "object", None) or getattr(row, "obj", None)


# ── 方向 B：图谱 → 事实验证 ────────────────────────────────────

def thread_validate_fact(
    fact_text: str,
    thread_query: Callable[[Optional[str], Optional[str], int], list],
) -> FactThreadReport:
    """
    新事实入库前调用：用图谱已知关系校验。

    返回 report.validations，每项含 verdict: confirm / conflict / unknown。
    """
    report = FactThreadReport()
    triples = extract_triples(fact_text)
    report.triples_extracted = triples

    for subj, rel, obj in triples:
        existing = thread_query(subj, rel, 10)
        if not existing:
            report.validations.append(
                {"subject": subj, "relation": rel, "object": obj, "verdict": "unknown"}
            )
            continue
        matched = any(
            _row_object(row) == obj for row in existing
        )
        if matched:
            report.validations.append(
                {"subject": subj, "relation": rel, "object": obj, "verdict": "confirm"}
            )
        else:
            report.validations.append(
                {
                    "subject": subj,
                    "relation": rel,
                    "object": obj,
                    "verdict": "conflict",
                    "existing_objects": [
                        _row_object(row) for row in existing if _row_object(row)
                    ],
                }
            )

    return report
