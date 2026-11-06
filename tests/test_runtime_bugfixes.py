"""
Nyx runtime 脚手架 bug 修复测试（7 阻断 bug）。

覆盖：
  1. test_system_prompt_block_uses_bundle_not_fallback（mock orch）
  2. test_cognitive_recall_history_chain
  3. test_mcp_lists_memory_tools
  4. test_adapt_nyx_reads_phantoms
  5. test_sync_turn_single_raw_log
  6. test_feedback_reinforces_persisted_memory

独立可运行：python3 tests/test_runtime_bugfixes.py
"""

import os
import sys
import types
from unittest.mock import patch, MagicMock

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


# ══════════════════════════════════════════════════════════
# Bug1: system_prompt_block 用 Bundle 而非 fallback
# ══════════════════════════════════════════════════════════
def test_system_prompt_block_uses_bundle():
    """v7.19.0 起常驻块不再走「空问题召回 → Bundle」：那次召回实际是日志里最早的十来条（混着助手的客套话），
    还会把含「不要 / 别」的句子标成 [Contradictions]。现在常驻块由画像（core/profile.py）和几个有内容才出现的层组成。"""
    print("[Bug1: system_prompt_block 不走空问题召回]")
    from nexsandglass.core.memory_provider import NexSandglassProvider

    p = NexSandglassProvider.__new__(NexSandglassProvider)
    p._use_facade = True
    sp = p.system_prompt_block()
    assert "Nyx memory is ready" not in sp, sp[:120]          # 不是整体失败后的兜底
    assert "Nyx memory:" in sp and "sandglass_search" in sp, sp[:200]
    for gone in ("[Core facts]", "[Contradictions]", "[Confidence]", "[Time]"):
        assert gone not in sp, sp

    fake_orch = MagicMock()
    with patch("nexsandglass.runtime.orchestrator.get_orchestrator", return_value=fake_orch), \
            patch("nexsandglass.l3.l3_tasks.task_pending", return_value=[{"task": "交周报"}]):
        sp2 = p.system_prompt_block()
    assert not fake_orch.recall_bundle.called and not fake_orch.recall.called
    assert "[Unfinished]" in sp2 and "交周报" in sp2, sp2


# ══════════════════════════════════════════════════════════
# Bug2: cognitive_recall history 链
# ══════════════════════════════════════════════════════════
def test_cognitive_recall_history_chain():
    print("[Bug2: cognitive_recall history 链]")
    from nexsandglass.runtime.orchestrator import get_orchestrator
    from nexsandglass.runtime.intent import parse_intent

    # intent.history 应为 True（上次买的...）
    it = parse_intent("上次买的吉利怎么样")
    check("intent.history=True", it.history)
    check("intent.history_context 可调用", callable(__import__("nexsandglass.runtime.intent", fromlist=["x"]).history_context))
    # cognitive_recall 不抛异常（修复前 intent.history_context 属性不存在 → 会崩）
    orch = get_orchestrator()
    ctx = orch.cognitive_recall("上次买的吉利怎么样", max_tokens=200)
    check("cognitive_recall 不崩", isinstance(ctx, str))


# ══════════════════════════════════════════════════════════
# Bug3: MCP tools/list 声明 memory_*
# ══════════════════════════════════════════════════════════
def test_mcp_lists_memory_tools():
    print("[Bug3: MCP tools/list 声明 memory_*]")
    from nexsandglass.interfaces.sandglass_mcp import _handle_tool
    # 通过 handler 模拟 tools/list（实际 tools/list 在 main 循环里，用 patch 检查 tools 数组）
    # 直接检查 sandglass_mcp 源码中 tools/list 包含 memory_*
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(_ROOT, 'nexsandglass/interfaces/sandglass_mcp.py'), encoding='utf-8').read()
    for tool in ["memory_observe", "memory_recall", "memory_feedback", "memory_forget"]:
        # handler 里有
        check(f"handler 有 {tool}", f'"{tool}"' in src or f"'{tool}'" in src)
    # tools/list 声明（在 _tool(...) 调用里）
    list_part = src[src.find('tools = ['):src.find('print(_rpc_response(tid')]
    for tool in ["memory_observe", "memory_recall", "memory_feedback", "memory_forget"]:
        check(f"tools/list 声明 {tool}", f'"{tool}"' in list_part)


# ══════════════════════════════════════════════════════════
# Bug4: _adapt_nyx 读 phantoms
# ══════════════════════════════════════════════════════════
def test_adapt_nyx_reads_phantoms():
    print("[Bug4: _adapt_nyx 读 phantoms]")
    from nexsandglass.runtime.orchestrator import RecallPlanner

    # mock nyx 模块（对齐真实返回结构）
    fake = types.ModuleType("nyx")
    fake.nyx_sense = lambda t: {"familiar_ratio": 0.6, "known_tokens": ["Geely"],
                                "unknown_tokens": [], "total": 1}
    fake.nyx_hunt = lambda q, limit=5: {"hunted": True, "conviction": 0.5,
                                        "phantoms": [{"token": "Geely", "whisper": "用户谈过Geely", "sightings": 3}]}
    with patch.dict(sys.modules, {"nexsandglass.interfaces.nyx": fake}):
        rp = RecallPlanner()
        objs = rp._adapt_nyx("Geely")
    check("返回对象数>0", len(objs) > 0)
    check("含 phantoms whisper", any("谈过Geely" in o.content for o in objs))
    check("含熟悉度", any("熟悉度 0.6" in o.content for o in objs))


# ══════════════════════════════════════════════════════════
# Bug5: sync_turn 单次 raw log
# ══════════════════════════════════════════════════════════
def test_sync_turn_single_raw_log():
    print("[Bug5: sync_turn 单次 raw log]")
    os.environ.setdefault("NEXSANDBASE_HOME",
                      os.path.join(os.path.expanduser("~"), ".nyx"))
    from nexsandglass.core.sandglass_log import _SANDGLASS
    from nexsandglass.core.memory_provider import NexSandglassProvider

    start = sum(1 for _ in open(_SANDGLASS, encoding="utf-8")) if os.path.exists(_SANDGLASS) else 0
    p = NexSandglassProvider.__new__(NexSandglassProvider)
    p._turn_count = 0
    p._use_facade = True
    # 两条高信息量消息（都会 promote + 过 info filter）
    p.sync_turn("用户是餐厅老板，经营中餐厅十年", "用户推荐了新的厨师候选人")
    end = sum(1 for _ in open(_SANDGLASS, encoding="utf-8"))
    check("raw log 增量=2（无双写）", end - start == 2, f"{end-start}")
    check("turn_count 递增", p._turn_count == 1)


# ══════════════════════════════════════════════════════════
# Bug6: feedback 强化持久化记忆
# ══════════════════════════════════════════════════════════
def test_feedback_reinforces_persisted_memory():
    print("[Bug6: feedback 持久化权重]")
    import tempfile, json
    os.environ.setdefault("NEXSANDBASE_HOME",
                      os.path.join(os.path.expanduser("~"), ".nyx"))
    from nexsandglass.engram import bridge
    from nexsandglass.runtime.facade import feedback

    # 用临时 store 避免污染
    tmp = tempfile.mkdtemp(prefix="nyx-fb-")
    store = os.path.join(tmp, "engram_store.jsonl")
    old_store = bridge._STORE
    bridge._STORE = store
    try:
        # 写入一条测试记忆（ts 固定）
        row = {"ts": "2026-08-12T00:00:00Z", "content": "用户喜欢咖啡", "type": "semantic"}
        with open(store, "w", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

        # feedback reinforce
        r = feedback({"memory_ids": ["engram:2026-08-12T00:00:00Z"], "helpful": True})
        check("feedback ok", r["ok"])
        check("action=reinforce", r["action"] == "reinforce", r["action"])
        check("持久化变更>0", r["detail"].get("persisted_changes", 0) > 0)
        # 读回验证 decay_weight 提升
        with open(store, encoding="utf-8") as f:
            updated = json.loads(f.readline())
        check("decay_weight 提升", updated.get("decay_weight", 0) >= 1.0, str(updated.get("decay_weight")))

        # feedback weaken
        r2 = feedback({"memory_ids": ["engram:2026-08-12T00:00:00Z"], "helpful": False})
        check("action=weaken", r2["action"] == "weaken", r2["action"])
        with open(store, encoding="utf-8") as f:
            updated2 = json.loads(f.readline())
        check("weaken 降低 weight", updated2.get("decay_weight", 1.0) < 1.0,
              str(updated2.get("decay_weight")))
    finally:
        bridge._STORE = old_store
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_system_prompt_block_uses_bundle()
    test_cognitive_recall_history_chain()
    test_mcp_lists_memory_tools()
    test_adapt_nyx_reads_phantoms()
    test_sync_turn_single_raw_log()
    test_feedback_reinforces_persisted_memory()
    print(f"\nRESULT: {PASS}/16 checks passed")
    if PASS < 16:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
