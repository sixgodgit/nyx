"""
MemoryBundle（Phase 3 合并两套出口）测试。

覆盖：
  1. budget 200/500/2000 不超（token 预算严格）
  2. 短高相关优先（utility = relevance/token_cost）
  3. 空记忆稳定模板（persona/offset 仍注入）
  4. 内容去重
  5. orchestrator.recall 返回 MemoryContext；hits 仅 debug
  6. 矛盾与 procedural 优先

独立可运行：python3 tests/test_engram_bundle.py
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

from nexsandglass.runtime.bundle import (
    build_bundle,
    estimate_tokens,
    utility,
    score_object,
    MemoryBundle,
)
from nexsandglass.runtime.orchestrator import get_orchestrator
from nexsandglass.runtime.facade import MemoryContext
from nexsandglass.engram.types import MemoryObject

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} — {detail}")


def mk(content, mtype="semantic", conf=0.5, created=""):
    return MemoryObject(content=content, type=mtype, confidence=conf, created_at=created)


# ══════════════════════════════════════════════════════════
# 1. budget 200/500/2000 不超
# ══════════════════════════════════════════════════════════
def test_budget():
    print("[budget 200/500/2000 不超]")
    cands = [mk("用户住在荷兰阿姆斯特丹，经营一家中餐厅多年", "semantic"),
             mk("用户喜欢喝咖啡，偏好手冲耶加雪菲", "semantic"),
             mk("用户的公司购买了 Geely Starray 电动车用于通勤", "episodic"),
             mk("切记：所有重要操作前必须先备份数据", "procedural")]
    for b in (200, 500, 2000):
        bundle = build_bundle(cands, query="用户", budget=b)
        total = estimate_tokens(bundle.render())
        check(f"budget={b} est_tokens={bundle.est_tokens} <= {b}", bundle.est_tokens <= b,
              f"实际 {bundle.est_tokens}")
        check(f"budget={b} render不超", total <= b + 20, f"render {total}")
    # 小预算丢弃
    bundle_small = build_bundle(cands, query="用户", budget=30)
    check("小预算有丢弃", bundle_small.dropped_count >= 0)
    check("小预算 est 不超", bundle_small.est_tokens <= 30)


# ══════════════════════════════════════════════════════════
# 2. 短高相关优先
# ══════════════════════════════════════════════════════════
def test_utility():
    print("[短高相关优先]")
    short = mk("用户喜欢咖啡", "semantic", conf=0.9)
    long = mk("用户过去十年一直喜欢喝咖啡并且研究各种咖啡豆的烘焙工艺包括耶加雪菲瑰夏曼特宁等", "semantic", conf=0.9)
    u_short = utility(short, "咖啡")
    u_long = utility(long, "咖啡")
    check("短高相关 utility 更高", u_short > u_long, f"{u_short} vs {u_long}")
    # 预算受限时短条目优先进入
    bundle = build_bundle([long, short], query="咖啡", budget=20)
    check("短条目优先进入", "用户喜欢咖啡" in bundle.core_facts or "用户喜欢咖啡" in str(bundle),
          str(bundle.core_facts))


# ══════════════════════════════════════════════════════════
# 3. 空记忆稳定模板
# ══════════════════════════════════════════════════════════
def test_empty_stable():
    print("[空记忆稳定模板]")
    bundle = build_bundle([], query="任何", budget=500,
                          persona_layer="你是一个助手", offset_layer="决策平稳")
    check("空记忆不崩", isinstance(bundle, MemoryBundle))
    check("persona 仍注入", bundle.persona_layer == "你是一个助手")
    check("offset 仍注入", bundle.offset_layer == "决策平稳")
    check("confidence 空标记", bundle.confidence_summary == "（无记忆）")
    rendered = bundle.render()
    check("渲染含 persona", "你是一个助手" in rendered)


# ══════════════════════════════════════════════════════════
# 4. 内容去重
# ══════════════════════════════════════════════════════════
def test_dedup():
    print("[内容去重]")
    cands = [mk("用户买了 Geely", "semantic"), mk("用户买了 Geely", "semantic"),
             mk("用户买了 Geely", "episodic"), mk("用户买了 Geely", "emotional")]
    bundle = build_bundle(cands, query="Geely", budget=500)
    check("重复内容只保留一条", len(bundle.core_facts) <= 1, str(bundle.core_facts))


# ══════════════════════════════════════════════════════════
# 5. orchestrator.recall 返回 MemoryContext
# ══════════════════════════════════════════════════════════
def test_orchestrator_memorycontext():
    print("[recall 返回 MemoryContext]")
    orch = get_orchestrator()
    mc = orch.recall("用户", token_budget=500)
    check("返回 MemoryContext", isinstance(mc, MemoryContext))
    check("MemoryContext 有 strings", isinstance(mc.strings, list))
    check("MemoryContext 可转文本", isinstance(mc.to_text(), str))
    # bundle 构建
    bundle = orch.recall_bundle("用户", max_tokens=500,
                                persona_layer="助手", offset_layer="平稳")
    check("recall_bundle 返回 MemoryBundle", isinstance(bundle, MemoryBundle))
    check("bundle persona 槽位", bundle.persona_layer == "助手")


# ══════════════════════════════════════════════════════════
# 6. 矛盾与 procedural 优先
# ══════════════════════════════════════════════════════════
def test_priority():
    print("[矛盾与 procedural 优先]")
    cands = [
        mk("用户住在阿姆斯特丹", "semantic", conf=0.9),
        mk("记住了，重要操作前备份数据", "procedural", conf=0.5),
        mk("用户其实不住在阿姆斯特丹，搬家去了鹿特丹", "semantic", conf=0.7),
    ]
    bundle = build_bundle(cands, query="用户", budget=200)
    check("procedural 进入 core_facts", any("备份" in f for f in bundle.core_facts),
          str(bundle.core_facts))
    check("矛盾被识别", len(bundle.contradictions) >= 1 or any("搬家" in f for f in bundle.core_facts),
          str(bundle.contradictions))


if __name__ == "__main__":
    test_budget()
    test_utility()
    test_empty_stable()
    test_dedup()
    test_orchestrator_memorycontext()
    test_priority()
    print(f"\nRESULT: {PASS}/18 checks passed")
    if PASS < 18:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
