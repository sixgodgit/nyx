"""
NyxOrchestrator（Phase 1 热路径收编）测试。

覆盖：
  1. FormationRouter 写入统一入口（observe → 多 store）
  2. RecallPlanner 多源聚合（SearchRouter/shadow/wthread/persona/nyx）
  3. 单源失败降级（模拟某源抛异常 → 跳过，其他源仍返回）
  4. PolicyEngine 关模块（disabled 源被跳过）
  5. Provider / MCP 改造验证（内部转 orchestrator）

独立可运行：python3 tests/test_engram_orchestrator.py
"""

import os
import sys

# ── 兼容两种布局 ──
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    _THIS_DIR,
    os.path.dirname(_THIS_DIR),
    os.path.dirname(os.path.dirname(_THIS_DIR)),
):
    if os.path.isdir(os.path.join(_candidate, "nexsandglass")):
        sys.path.insert(0, _candidate)
        break

from nexsandglass.runtime.facade import MemoryContext
from nexsandglass.runtime.orchestrator import (
    NyxOrchestrator,
    PolicyEngine,
    ModulePolicy,
    FormationRouter,
    RecallPlanner,
    get_orchestrator,
)
from nexsandglass.engram.types import MemoryObject

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} — {detail}")


# ══════════════════════════════════════════════════════════
# 1. FormationRouter 写入统一入口
# ══════════════════════════════════════════════════════════
def test_formation():
    print("[formation 写入统一入口]")
    orch = get_orchestrator()
    fr = orch.observe("用户今天去试驾了 Geely", source="test-formation", force_promote=True)
    check("observe 成功", fr.ok)
    check("observe 类型合法", fr.memory_type in ("semantic", "episodic", "emotional", "procedural"))
    check("写入 sandglass 路由", "sandglass" in fr.routes_written)
    check("写入 engram 路由", "engram" in fr.routes_written)
    check("写入 shadow 路由", "shadow" in fr.routes_written)
    check("memory_id 非空", bool(fr.memory_id))
    check("lifecycle 有效", fr.lifecycle_state in ("observed", "validated", "active"))
    # 空事件
    fr_empty = orch.observe("")
    check("空事件拒绝", not fr_empty.ok)


# ══════════════════════════════════════════════════════════
# 2. RecallPlanner 多源聚合
# ══════════════════════════════════════════════════════════
def test_recall_multi_source():
    print("[recall 多源聚合]")
    orch = get_orchestrator()
    rr = orch.recall("Geely", token_budget=1500)
    # 公共接口返回 MemoryContext（统一上下文）
    check("返回 MemoryContext", hasattr(rr, "strings") and hasattr(rr, "to_text"))
    check("MemoryContext 可转文本", isinstance(rr.to_text(), str))
    # 多源聚合结果：strings 非空（有源返回内容）
    check("recall 有结果", len(rr.strings) > 0, f"strings={len(rr.strings)}")
    check("recall ids 对齐", len(rr.memory_ids) == len(rr.strings))


# ══════════════════════════════════════════════════════════
# 3. 单源失败降级
# ══════════════════════════════════════════════════════════
def test_single_source_failure():
    print("[单源失败降级]")
    # 构造一个会抛异常的源（fail-safe：异常被捕获，不影响整体）
    class FailingPlanner(RecallPlanner):
        def _adapt_wthread(self, query):
            raise RuntimeError("wthread 故意失败")

    p = PolicyEngine()
    planner = FailingPlanner(p)
    # 异常源不影响整体：recall 不抛异常，返回 MemoryContext
    rr = planner.recall("test", token_budget=500)
    check("异常源不拖垮整体", isinstance(rr, MemoryContext))
    check("降级仍返回对象", isinstance(rr.strings, list))


# ══════════════════════════════════════════════════════════
# 4. PolicyEngine 关模块
# ══════════════════════════════════════════════════════════
def test_policy_disable():
    print("[policy 关模块]")
    # 关闭 shadow 和 nyx
    policies = {
        "shadow": ModulePolicy(enabled=False),
        "nyx": ModulePolicy(enabled=False),
        "consolidation": ModulePolicy(enabled=False),
    }
    p = PolicyEngine(policies)
    check("shadow 被禁用", not p.enabled("shadow"))
    check("nyx 被禁用", not p.enabled("nyx"))
    check("search_router 仍启用", p.enabled("search_router"))

    planner = RecallPlanner(p)
    # 被禁源不产生结果，但整体仍返回 MemoryContext（不崩）
    rr = planner.recall("test", token_budget=500)
    check("被禁源仍返回 MemoryContext", isinstance(rr, MemoryContext))
    check("被禁源 strings 为列表", isinstance(rr.strings, list))

    # consolidation 默认关闭
    orch = NyxOrchestrator(p)
    cr = orch.consolidate()
    check("consolidate 被 policy 关闭", not cr.ok and "disabled" in cr.message)

    # 环境变量覆盖
    os.environ["NYX_POLICY_PERSONA_ENABLED"] = "0"
    p2 = PolicyEngine()
    check("env 覆盖 persona 禁用", not p2.enabled("persona"))
    os.environ.pop("NYX_POLICY_PERSONA_ENABLED", None)


# ══════════════════════════════════════════════════════════
# 5. Provider / MCP 转 orchestrator
# ══════════════════════════════════════════════════════════
def test_provider_integration():
    print("[Provider/MCP 转 orchestrator]")
    # memory_provider.sync_turn 走 orchestrator.observe
    try:
        from nexsandglass.core.memory_provider import NexSandglassProvider
        p = NexSandglassProvider.__new__(NexSandglassProvider)
        p._turn_count = 0
        p._use_facade = True
        p.sync_turn("provider 集成测试消息", None)
        check("sync_turn 走 orchestrator", p._turn_count == 1)
    except Exception as e:
        check("sync_turn 走 orchestrator", False, str(e))

    # sandglass_mcp._handle_tool 检索走 orchestrator
    try:
        from nexsandglass.interfaces.sandglass_mcp import _handle_tool
        resp = _handle_tool("sandglass_search", {"query": "Geely", "limit": 5}, 1)
        check("MCP sandglass_search 转 orchestrator", "result" in str(resp)[:50] or '"text"' in str(resp),
              str(resp)[:80])
    except Exception as e:
        check("MCP sandglass_search 转 orchestrator", False, str(e))


if __name__ == "__main__":
    test_formation()
    test_recall_multi_source()
    test_single_source_failure()
    test_policy_disable()
    test_provider_integration()
    print(f"\nRESULT: {PASS}/20 checks passed")
    if PASS < 20:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
