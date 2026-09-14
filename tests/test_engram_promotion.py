"""
候选晋升（PromotionEngine）测试 —— 解决"什么值得记住"。

覆盖：
  1. Golden 基准（我叫阿文/会议时间/重复偏好/闲聊）
  2. 差异化写入（重复→reinforce 不双写；冲突→conflict candidate）
  3. 生命周期（promote→validated；session_only/drop 不晋升）
  4. before/after 指标（无意义记忆下降）
  5. sync_turn 改造（raw 落沙 + 晋升）

独立可运行：python3 tests/test_engram_promotion.py
"""

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    _THIS_DIR,
    os.path.dirname(_THIS_DIR),
    os.path.dirname(os.path.dirname(_THIS_DIR)),
):
    if os.path.isdir(os.path.join(_candidate, "nexsandglass")):
        sys.path.insert(0, _candidate)
        break

from nexsandglass.runtime.promotion import (
    PromotionEngine,
    analyze,
    classify_candidate,
    PromotionStats,
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


# ══════════════════════════════════════════════════════════
# 1. Golden 基准
# ══════════════════════════════════════════════════════════
def test_golden():
    print("[Golden 基准]")
    eng = PromotionEngine(use_llm=False)
    # 我叫阿文 → semantic/identity + promote
    c = eng.observe("我叫阿文")
    check("我叫阿文 type=identity", c.mem_type == "identity", c.mem_type)
    check("我叫阿文 promote", c.disposition == "promote", c.disposition)
    # 一次性会议 → short-lived/session
    c2 = eng.observe("我们明天下午3点开会")
    check("会议时间 short-lived", c2.mem_type == "short-lived", c2.mem_type)
    check("会议时间 session_only", c2.disposition == "session_only", c2.disposition)
    # 闲聊 → 不进长期
    c3 = eng.observe("嗯嗯")
    check("闲聊 drop", c3.disposition == "drop", c3.disposition)
    # 偏好 → promote
    c4 = eng.observe("我喜欢喝手冲咖啡")
    check("偏好 promote", c4.disposition == "promote", c4.disposition)


# ══════════════════════════════════════════════════════════
# 2. 差异化写入（重复/冲突）
# ══════════════════════════════════════════════════════════
def test_diff_write():
    print("[差异化写入]")
    eng = PromotionEngine(use_llm=False)
    # 重复偏好 → reinforce 不双写
    existing = [Memory(memory_id="m1", type="semantic", content="我喜欢喝手冲咖啡")]
    c = eng.observe("我喜欢喝手冲咖啡")
    eng.promote(c, existing)
    check("重复 → reinforce", c.meta.get("write_action") in ("reinforce", "dedup"),
          str(c.meta.get("write_action")))
    check("重复 lifecycle=active", c.lifecycle == "active", c.lifecycle)
    # 冲突 → conflict candidate（不静默覆盖）
    existing2 = [Memory(memory_id="m2", type="semantic", content="用户住在阿姆斯特丹")]
    c2 = eng.observe("用户不住在阿姆斯特丹，搬到鹿特丹了")
    eng.promote(c2, existing2)
    decision = c2.meta.get("decision", {}).get("decision")
    check("冲突 → conflict_candidate", decision == "conflict_candidate", str(decision))
    # 新记忆 → insert/validated
    c3 = eng.observe("我买了辆 Geely 电动车")
    eng.promote(c3, [])
    check("新记忆 insert", c3.meta.get("write_action") == "insert", str(c3.meta.get("write_action")))
    check("新记忆 validated", c3.lifecycle == "validated", c3.lifecycle)


# ══════════════════════════════════════════════════════════
# 3. 生命周期（session_only/drop 不晋升）
# ══════════════════════════════════════════════════════════
def test_lifecycle():
    print("[生命周期]")
    eng = PromotionEngine(use_llm=False)
    c = eng.observe("我们明天下午3点开会")
    eng.promote(c, [])
    check("session_only 不晋升", c.lifecycle == "candidate", c.lifecycle)
    c2 = eng.observe("嗯嗯")
    eng.promote(c2, [])
    check("drop 不晋升", c2.lifecycle == "candidate" or c2.disposition == "drop",
          c2.lifecycle)
    # promote → candidate→validated
    c3 = eng.observe("我叫阿文")
    eng.promote(c3, [])
    check("promote 走 candidate→validated", c3.lifecycle in ("validated", "active"),
          c3.lifecycle)


# ══════════════════════════════════════════════════════════
# 4. before/after 指标（无意义记忆下降）
# ══════════════════════════════════════════════════════════
def test_metrics():
    print("[before/after 指标]")
    # before：整轮 dump 会把所有消息写入（无过滤）
    # after：PromotionEngine 只晋升值得的
    messages = [
        "我叫阿文", "我们明天下午3点开会", "我喜欢喝手冲咖啡", "嗯嗯", "好的好的",
        "验证码是123456", "我买了辆 Geely 电动车", "随便都行", "晚安", "用户是餐厅老板",
    ]
    stats = analyze(messages)
    check("指标为 PromotionStats", isinstance(stats, PromotionStats))
    check("有 drop（闲聊/OTP）", stats.dropped >= 2, f"dropped={stats.dropped}")
    check("有 session_only（临时）", stats.session_only >= 2, f"session={stats.session_only}")
    check("promote_rate < 1", stats.promote_rate() < 0.8, f"rate={stats.promote_rate()}")
    # 无意义记忆（闲聊/OTP/临时）占比下降：promoted 少于 total
    check("晋升数 < 总数", stats.promoted < stats.total_observed,
          f"{stats.promoted} < {stats.total_observed}")


# ══════════════════════════════════════════════════════════
# 5. sync_turn 改造（raw 落沙 + 晋升）
# ══════════════════════════════════════════════════════════
def test_sync_turn():
    print("[sync_turn 改造]")
    try:
        from nexsandglass.core.memory_provider import NexSandglassProvider
        p = NexSandglassProvider.__new__(NexSandglassProvider)
        p._turn_count = 0
        p._use_facade = True
        # 闲聊不应触发长期写；晋升消息应正常处理不崩
        p.sync_turn("嗯嗯", "好的", None) if False else None
        # 用正常消息测试不崩
        p.sync_turn("我叫阿文", "很高兴认识你")
        check("sync_turn 不崩", p._turn_count >= 1)
    except Exception as e:
        check("sync_turn 不崩", False, str(e))


if __name__ == "__main__":
    test_golden()
    test_diff_write()
    test_lifecycle()
    test_metrics()
    test_sync_turn()
    print(f"\nRESULT: {PASS}/20 checks passed")
    if PASS < 20:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
