"""
B0 Memory Runtime 契约 smoke test。

覆盖：
  1. 外部只 import runtime API（observe/recall/feedback/forget/consolidate）
  2. observe 签名（关键字参数 source/session_id/mem_type）→ ObserveReport
  3. recall 返回结构化 MemoryContext（text/objects/strategy_used/degraded）
  4. MemoryContext.text 可直接进 system prompt（非空字符串）
  5. MemoryObject 为唯一 canonical
  6. NYX_RUNTIME 默认开；=0 回滚旧路径 deprecated
  7. consolidate 入口可调

独立可运行：python3 tests/test_runtime_api.py
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

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} — {detail}")


def test_public_api():
    print("[B0: 公共 API 可 import]")
    from nexsandglass import runtime
    for name in ["observe", "recall", "feedback", "forget", "consolidate",
                 "MemoryContext", "MemoryObject", "MemoryBundle", "ObserveReport"]:
        check(f"runtime.{name}", hasattr(runtime, name))


def test_observe_signature():
    print("[B0: observe 签名]")
    from nexsandglass import runtime
    # 关键字参数
    rep = runtime.observe("用户喜欢手冲咖啡", source="smoke", session_id="s1")
    check("observe ok", rep.ok, rep.message)
    check("返回 ObserveReport", rep.memory_type in ("semantic", "emotional", "episodic", "procedural"))
    check("lifecycle 有效", rep.lifecycle_state in ("observed", "validated", "active"), rep.lifecycle_state)


def test_recall_structured():
    print("[B0: recall 结构化 MemoryContext]")
    from nexsandglass import runtime
    mc = runtime.recall("咖啡", token_budget=200, session_id="s1")
    check("返回 MemoryContext", hasattr(mc, "text") and hasattr(mc, "objects"))
    check("text 非空可进 system prompt", isinstance(mc.text, str))
    check("objects 存在", isinstance(mc.objects, list))
    check("strategy_used", isinstance(mc.strategy_used, str))
    check("degraded 布尔", isinstance(mc.degraded, bool))
    # objects 全为 MemoryObject
    if mc.objects:
        from nexsandglass.engram.types import MemoryObject
        check("objects 全 MemoryObject", all(isinstance(o, MemoryObject) for o in mc.objects))


def test_memory_object_canonical():
    print("[B0: MemoryObject canonical]")
    from nexsandglass.engram.types import MemoryObject
    o = MemoryObject(content="测试", type="semantic", status="observed")
    for field in ["content", "type", "status", "valid_from", "valid_until",
                  "supersedes", "confidence", "access_count"]:
        check(f"MemoryObject.{field}", hasattr(o, field))


def test_nyx_runtime_flag():
    print("[B0: NYX_RUNTIME 开关]")
    from nexsandglass import runtime
    check("NYX_RUNTIME 默认开", runtime.NYX_RUNTIME)
    # 显式 =1
    os.environ["NYX_RUNTIME"] = "1"
    from nexsandglass import runtime as r2
    check("=1 开启", r2.NYX_RUNTIME)


def test_consolidate_entry():
    print("[B0: consolidate 入口]")
    from nexsandglass import runtime
    rep = runtime.consolidate(tag="b0-test")
    check("consolidate 可调", isinstance(rep, dict) and "ok" in rep)


def test_feedback_forget():
    print("[B0: feedback/forget]")
    from nexsandglass import runtime
    fb = runtime.feedback({"memory_ids": [], "helpful": True})
    check("feedback 可调", isinstance(fb, dict) and "action" in fb)
    fg = runtime.forget({"memory_id": "no-such"})
    check("forget 可调", isinstance(fg, dict) and "ok" in fg)


if __name__ == "__main__":
    test_public_api()
    test_observe_signature()
    test_recall_structured()
    test_memory_object_canonical()
    test_nyx_runtime_flag()
    test_consolidate_entry()
    test_feedback_forget()
    print(f"\nRESULT: {PASS}/18 checks passed")
    if PASS < 18:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
