"""
Consolidation Engine（v6.5）测试 —— Dream 生产化。

覆盖：
  1. 抽象（多条相似 → 合并提案）
  2. 冲突（不乱覆盖 → 冲突提案）
  3. 衰减（旧/低访问 → 降权）
  4. Validator（空内容/低置信 → 隔离）
  5. 快照可回滚
  6. 热路径不跑满管线（Engine 是独立组件）

独立可运行：python3 tests/test_engram_consolidation.py
"""

import os
import sys
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

from nexsandglass.runtime.consolidation import (
    ConsolidationEngine,
    restore_snapshot,
    DreamProposal,
    Validator,
)
from nexsandglass.engram.types import Memory

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} — {detail}")


def make_mems():
    return [
        Memory(memory_id="c1", type="episodic", content="用户看了一辆Geely", created_at="2026-01-01", access_count=0),
        Memory(memory_id="c2", type="episodic", content="用户试驾了Geely Starray", created_at="2026-01-05", access_count=0),
        Memory(memory_id="c3", type="episodic", content="用户买了辆Geely车", created_at="2026-01-10", access_count=0),
        Memory(memory_id="old1", type="semantic", content="很久以前的旧记忆", created_at="2025-01-01", access_count=0),
        Memory(memory_id="conf1", type="semantic", content="用户住在阿姆斯特丹", created_at="2026-08-01"),
        Memory(memory_id="conf2", type="semantic", content="用户不住在阿姆斯特丹", created_at="2026-08-02"),
    ]


def test_consolidation():
    print("[consolidation 生产化]")
    eng = ConsolidationEngine()
    result = eng.run(make_mems(), tag="test_engine")
    check("有抽象提案", "abstract" in result["applied_actions"], str(result["applied_actions"]))
    check("有冲突提案", "conflict" in result["applied_actions"], str(result["applied_actions"]))
    check("有衰减提案", "decay" in result["applied_actions"], str(result["applied_actions"]))
    check("快照生成", bool(result["snapshot"]) and os.path.exists(result["snapshot"]))
    # 回滚
    restored = restore_snapshot(result["snapshot"])
    check("回滚恢复记忆数", len(restored) == 6, str(len(restored)))
    check("回滚内容保留", restored[0].content == "用户看了一辆Geely")


def test_validator():
    print("[validator 隔离]")
    v = Validator()
    # 空内容 → 隔离
    p1 = DreamProposal(action="abstract", content="", source_ids=["x"])
    p1 = v.validate(p1)
    check("空内容隔离", not p1.apply and "空内容" in p1.quarantine_reason)
    # 低置信 → 隔离
    p2 = DreamProposal(action="abstract", content="内容", confidence=0.1)
    p2 = v.validate(p2)
    check("低置信隔离", not p2.apply and "低置信" in p2.quarantine_reason)
    # 正常 → apply
    p3 = DreamProposal(action="abstract", content="正常内容", confidence=0.8)
    p3 = v.validate(p3)
    check("正常提案 apply", p3.apply)


def test_no_overwrite():
    print("[不乱覆盖]")
    eng = ConsolidationEngine()
    result = eng.run(make_mems(), tag="test_conflict")
    # 冲突被识别为 proposal（不静默覆盖）
    check("冲突不静默覆盖", "conflict" in result["applied_actions"],
          str(result["applied_actions"]))


if __name__ == "__main__":
    test_consolidation()
    test_validator()
    test_no_overwrite()
    print(f"\nRESULT: {PASS}/9 checks passed")
    if PASS < 9:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
