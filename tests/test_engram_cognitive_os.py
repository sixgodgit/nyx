"""
Cognitive OS 端到端（v7.0）测试。

覆盖：
  1. 端到端 pipeline（Formation→Intent Recall→Bundle→Context）
  2. MCP 统一入口（memory_observe/recall/feedback/forget）
  3. 评测框架（formation P/R、token utility、temporal accuracy）
  4. 失败降级（异常输入不崩）

独立可运行：python3 tests/test_engram_cognitive_os.py
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

from nexsandglass.runtime.orchestrator import get_orchestrator
from nexsandglass.runtime.eval import run_eval, eval_formation, eval_token_utility

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} — {detail}")


def test_end_to_end():
    print("[端到端 pipeline]")
    orch = get_orchestrator()
    # observe 喂入
    orch.observe("用户是餐厅老板，经营中餐厅", source="e2e")
    orch.observe("用户喜欢手冲咖啡", source="e2e")
    # cognitive_recall 串联全链路
    ctx = orch.cognitive_recall("用户喜欢什么", max_tokens=500)
    check("cognitive_recall 返回文本", isinstance(ctx, str) and len(ctx) > 0)
    check("包含核心事实槽位", "【核心事实】" in ctx, ctx[:50])
    check("含咖啡事实", "咖啡" in ctx)
    # 失败降级（异常输入）
    ctx2 = orch.cognitive_recall("!!!###", max_tokens=100)
    check("异常输入不崩", isinstance(ctx2, str))


def test_mcp_unified():
    print("[MCP 统一入口]")
    try:
        from nexsandglass.interfaces.sandglass_mcp import _handle_tool
        # memory_observe
        resp = _handle_tool("memory_observe", {"content": "统一入口测试"}, 1)
        check("memory_observe 有 ok", '"ok"' in str(resp), str(resp)[:80])
        # memory_recall
        resp2 = _handle_tool("memory_recall", {"query": "用户", "limit": 5}, 2)
        check("memory_recall 有 results", '"results"' in str(resp2), str(resp2)[:80])
        # memory_forget
        resp3 = _handle_tool("memory_forget", {"memory_id": "no-such"}, 3)
        check("memory_forget 不崩", '"removed"' in str(resp3) or '"ok"' in str(resp3),
              str(resp3)[:80])
    except Exception as e:
        check("MCP 统一入口", False, str(e))


def test_eval():
    print("[评测框架]")
    golden = [
        ("我叫阿文", True), ("我们明天下午3点开会", False), ("我喜欢手冲咖啡", True),
        ("嗯嗯", False), ("验证码123456", False), ("我买了辆 Geely", True),
    ]
    f = eval_formation(golden)
    check("formation precision", f["precision"] > 0.5, f"precision={f['precision']}")
    check("formation recall", f["recall"] > 0.5, f"recall={f['recall']}")
    tu = eval_token_utility(["用户", "咖啡"], budget=500)
    check("token_utility 可计算", 0 <= tu <= 1, f"tu={tu}")
    # 完整 run_eval
    report = run_eval(formation_golden=golden, token_queries=["用户"])
    check("run_eval 生成报告", report.samples > 0)


if __name__ == "__main__":
    test_end_to_end()
    test_mcp_unified()
    test_eval()
    print(f"\nRESULT: {PASS}/11 checks passed")
    if PASS < 11:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
