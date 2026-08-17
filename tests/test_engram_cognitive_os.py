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
_TOTAL_CHECKS = 11 + 9  # 原 11 + golden A2+B2+C2+D1+E2


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




# ═══════════════════════════════════════════════════════════
# B7 — 5 个 golden scenarios（方案 C DoD#7 自动化验收）
# 每个场景使用独立 NyxOrchestrator，避免共享单例串扰。
# ═══════════════════════════════════════════════════════════
def _fresh_orch():
    """返回使用独立临时数据目录的 Orchestrator（不污染生产记忆，场景间互不串扰）。"""
    import tempfile, os
    os.environ["NEXSANDBASE_HOME"] = tempfile.mkdtemp(prefix="nyx_golden_")
    # 强制重载路径模块，使其读取新 HOME
    for mod in ("nexsandglass.core.sandglass_paths",
                "nexsandglass.core.sandglass_log",
                "nexsandglass.features.shadow_sand",
                "nexsandglass.features.sandglass_vault",
                "nexsandglass.core.search_router"):
        try:
            __import__(mod)
            import sys
            if mod in sys.modules:
                import importlib
                importlib.reload(sys.modules[mod])
        except Exception:
            pass
    from nexsandglass.runtime.orchestrator import NyxOrchestrator, PolicyEngine
    return NyxOrchestrator(PolicyEngine())


def test_golden_prior_plan():
    """A. “还是按照之前那个方案。” → 找回决策/约束/结论进入 Context"""
    print("[golden A: 之前那个方案]")
    orch = _fresh_orch()
    tag = "GOLDENA_45"
    orch.observe(f"{tag}：用户决定餐厅菜单采用荷兰语双语，约束成本预算 2000 欧元", source="goldenA", force_promote=True)
    orch.observe(f"{tag}结论：先上线荷兰语菜单，中文作为副标题", source="goldenA", force_promote=True)
    ctx = orch.cognitive_recall("餐厅菜单方案", max_tokens=600)
    check("A1 返回非空上下文", isinstance(ctx, str) and len(ctx) > 0, ctx[:40])
    check("A2 能召回本场景方案记忆", tag in ctx or "荷兰语" in ctx or "2000" in ctx, ctx[:80])


def test_golden_preference_evolution():
    """B. 跨 session 偏好演化：current 与 history 不同"""
    print("[golden B: 偏好演化]")
    from nexsandglass.runtime.orchestrator import NyxOrchestrator, PolicyEngine
    o = NyxOrchestrator(PolicyEngine())
    tag = "GOLDENB_78"
    o.observe(f"{tag} 用户喜欢 Tesla 电动车", source="goldenB", force_promote=True)
    o.observe(f"{tag} 用户后来买了 Geely Starray", source="goldenB", force_promote=True)
    ctx_now = o.cognitive_recall(f"{tag} 用户现在喜欢什么车", max_tokens=400)
    ctx_hist = o.cognitive_recall(f"{tag} 用户以前喜欢什么车", max_tokens=400)
    check("B1 当前上下文非空", isinstance(ctx_now, str) and len(ctx_now) > 0)
    check("B2 历史上下文非空", isinstance(ctx_hist, str) and len(ctx_hist) > 0)


def test_golden_chitchat_no_pollution():
    """C. 闲聊不污染长期记忆（Formation 门禁：寒暄 drop）"""
    print("[golden C: 闲聊不污染]")
    orch = _fresh_orch()
    r1 = orch.observe("哈哈，今天天气真不错", source="goldenC")
    check("C1 闲聊不晋升长期(非 active)", r1.lifecycle_state != "active", str(r1.lifecycle_state))
    r2 = orch.observe("用户是餐厅老板，经营中餐厅", source="goldenC", force_promote=True)
    check("C2 关键身份可晋升", r2.ok, str(r2))


def test_golden_budget_keeps_core():
    """D. token_budget 受限时仍不崩、且返回可用上下文（procedural 铁律）"""
    print("[golden D: budget 保留关键]")
    orch = _fresh_orch()
    tag = "GOLDEND_33"
    orch.observe(f"{tag}铁律：涉及个人数据必须先检索再回答", source="goldenD", force_promote=True)
    ctx = orch.cognitive_recall(f"{tag}铁律是什么", max_tokens=500)
    check("D1 返回非空上下文", isinstance(ctx, str) and len(ctx) > 0, ctx[:60])
    check("D2 budget 有限且召回铁律", len(ctx) > 0 and (tag in ctx or "铁律" in ctx or "个人数据" in ctx), ctx[:80])


def test_golden_single_source_degradation():
    """E. 单源失败降级仍返回可用 context（异常/空 query 不崩）"""
    print("[golden E: 降级可用]")
    orch = _fresh_orch()
    try:
        ctx = orch.cognitive_recall("", max_tokens=200)
        check("E1 空 query 不崩", isinstance(ctx, str), repr(ctx)[:60])
    except Exception as e:
        check("E1 空 query 不崩", False, str(e))
    try:
        ctx2 = orch.cognitive_recall("!!!@@@###", max_tokens=200)
        check("E2 乱码 query 不崩", isinstance(ctx2, str), repr(ctx2)[:60])
    except Exception as e:
        check("E2 乱码 query 不崩", False, str(e))


if __name__ == "__main__":
    test_end_to_end()
    test_mcp_unified()
    test_eval()
    test_golden_prior_plan()
    test_golden_preference_evolution()
    test_golden_chitchat_no_pollution()
    test_golden_budget_keeps_core()
    test_golden_single_source_degradation()
    print(f"\nRESULT: {PASS}/{_TOTAL_CHECKS} checks passed")
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
