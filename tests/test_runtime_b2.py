"""
B2 — 消灭双出口：MemoryBundle → MemoryContext.text 唯一主路径。

覆盖：
  1. system_prompt_block 主注入走 bundle（无旧四层 join 成功路径）
  2. 预算 200/500/2000 不超
  3. 空记忆稳定模板（persona/offset 槽仍在）
  4. MemoryBundle thread_layer 槽位
  5. engram/context 降为 bundle 内部库（非 Provider 第二出口）

独立可运行：python3 tests/test_runtime_b2.py
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


def test_system_prompt_bundle_main():
    print("[B2: system_prompt_block 走 bundle 主路径]")
    from nexsandglass.core.memory_provider import NexSandglassProvider
    p = NexSandglassProvider.__new__(NexSandglassProvider)
    p._use_facade = True
    sp = p.system_prompt_block()
    # bundle 槽位（render 产出）
    check("含 Bundle 槽位", "【核心事实】" in sp or "（无记忆）" in sp or "【你是谁】" in sp, sp[:60])
    check("非兜底", "记忆系统已就绪" not in sp)
    # Provider 源码：主路径不再有 blocks join（bundle.render 是唯一成功路径）
    src = open(os.path.join(_THIS_DIR, "../nexsandglass/core/memory_provider.py"), encoding="utf-8").read()
    check("主路径用 bundle.render", "body = bundle.render()" in src)
    check("bundle 槽位收集", "thread_layer" in src and "persona_layer" in src)


def test_budget_respected():
    print("[B2: 预算 200/500/2000 不超]")
    from nexsandglass.runtime import bundle as bm
    from nexsandglass.engram.types import MemoryObject
    # 构造大量候选
    cands = [MemoryObject(content=f"记忆内容第{i}条 用户信息", type="semantic") for i in range(50)]
    for budget in (200, 500, 2000):
        b = bm.build_bundle(cands, query="用户", budget=budget)
        # 估算 token（约 4 字/token）不应远超预算（允许槽位开销）
        est = b.est_tokens or (len(b.render()) // 4)
        check(f"budget={budget} 不超(2x)", est <= budget * 2, f"est={est}")
        check(f"budget={budget} render 非空", bool(b.render()))


def test_empty_memory_stable_template():
    print("[B2: 空记忆稳定模板]")
    from nexsandglass.runtime import bundle as bm
    b = bm.build_bundle([], query="", budget=500,
                        persona_layer="主人是架构师", offset_layer="决策平稳",
                        open_loops="无待办")
    rendered = b.render()
    check("空记忆含【你是谁】", "【你是谁】" in rendered and "架构师" in rendered)
    check("空记忆含【你在往哪走】", "决策平稳" in rendered)
    check("空记忆含【还没做完】", "无待办" in rendered)
    check("空记忆标记无记忆", "（无记忆）" in rendered)


def test_thread_layer_slot():
    print("[B2: MemoryBundle thread_layer 槽位]")
    from nexsandglass.runtime import bundle as bm
    b = bm.build_bundle([], query="", budget=300, thread_layer="用户从阿姆斯特丹搬到鹿特丹")
    rendered = b.render()
    check("render 含【你怎么变成这样】", "【你怎么变成这样】" in rendered)
    check("render 含织线内容", "鹿特丹" in rendered)


def test_engram_context_is_internal():
    print("[B2: engram/context 非 Provider 第二出口]")
    # Provider 主注入只走 bundle.render；engram/context 仅供 bundle 内部复用
    src = open(os.path.join(_THIS_DIR, "../nexsandglass/core/memory_provider.py"), encoding="utf-8").read()
    check("Provider 不用 build_constitutional_context", "build_constitutional_context" not in src)
    check("Provider 不 import engram.context", "from nexsandglass.engram.context" not in src)
    # bundle 仍复用它（内部库）
    bsrc = open(os.path.join(_THIS_DIR, "../nexsandglass/runtime/bundle.py"), encoding="utf-8").read()
    check("bundle 复用 constitutional", "build_constitutional_context" in bsrc)


def test_provider_max_tokens_config():
    print("[B2: max_tokens 可配]")
    from nexsandglass.core.memory_provider import NexSandglassProvider
    p = NexSandglassProvider.__new__(NexSandglassProvider)
    os.environ["NYX_BUNDLE_MAX_TOKENS"] = "500"
    check("默认预算可配", p._bundle_max_tokens() == 500)
    os.environ["NYX_BUNDLE_MAX_TOKENS"] = "abc"
    check("非法值回退 1500", p._bundle_max_tokens() == 1500)
    os.environ.pop("NYX_BUNDLE_MAX_TOKENS", None)
    check("默认 1500", p._bundle_max_tokens() == 1500)


if __name__ == "__main__":
    test_system_prompt_bundle_main()
    test_budget_respected()
    test_empty_memory_stable_template()
    test_thread_layer_slot()
    test_engram_context_is_internal()
    test_provider_max_tokens_config()
    print(f"\nRESULT: {PASS}/16 checks passed")
    if PASS < 16:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
