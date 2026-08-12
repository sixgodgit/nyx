"""
Cognitive Memory OS 地基测试 — Canonical schema / lifecycle / bridge / facade

覆盖：
  1. MemoryObject schema（兼容现有 Memory dataclass 双向转换）
  2. lifecycle 状态机（合法迁移推进、非法迁移拒绝、terminal 停滞）
  3. bridge 双向映射（Sandglass 行 / engram 旧行 / shadow / Memory 往返）
  4. runtime.facade 稳定门面（observe / recall / feedback / forget 路由）

独立可运行：python3 tests/test_engram_cognitive.py
"""

import json
import os
import sys
import tempfile

# ── 兼容两种布局（与现有测试约定一致）──
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    _THIS_DIR,
    os.path.dirname(_THIS_DIR),
    os.path.dirname(os.path.dirname(_THIS_DIR)),
):
    if os.path.isdir(os.path.join(_candidate, "nexsandglass")):
        sys.path.insert(0, _candidate)
        break

from nexsandglass.engram.types import (
    Memory,
    MemoryObject,
    SPO,
    LifecycleState,
    validate_transition,
    next_lifecycle,
    OBJECT_MEMORY_TYPES,
)
from nexsandglass.engram import bridge
from nexsandglass.runtime import facade

PASS = 0


def check(name, cond, detail=""):
    global PASS
    status = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    print(f"  {status} {name}" + (f" — {detail}" if detail and not cond else ""))


# ══════════════════════════════════════════════════════════
# 1. MemoryObject schema
# ══════════════════════════════════════════════════════════
def test_schema():
    print("[schema]")
    # 全字段最小编码
    obj = MemoryObject(content="荷兰用户开餐厅", type="semantic")
    check("最小编码默认值", obj.status == "observed" and obj.importance == 0.5 and obj.content != "")
    # 类型全集
    check("类型全集含预留", {"preference", "relational", "temporal", "meta"} <= OBJECT_MEMORY_TYPES)
    # 结构化 SPO
    obj2 = MemoryObject(content="用户 经营 餐厅", type="relational", structured=SPO("用户", "经营", "餐厅"))
    check("结构化 SPO", obj2.structured.subject == "用户" and obj2.structured.predicate == "经营")
    # 兼容 Memory: from_memory / to_memory
    mem = Memory(memory_id="semantic-x", type="semantic", content="原始", tags=["a", "b"])
    up = MemoryObject.from_memory(mem)
    check("from_memory 保留字段", up.content == "原始" and up.tags == ["a", "b"] and up.type == "semantic")
    down = up.to_memory()
    check("to_memory 降级兼容", down.content == "原始" and down.memory_id == "semantic-x")
    # 无 id 生成 fallback
    obj3 = MemoryObject(content="无id内容", type="episodic")
    check("fallback id 生成", obj3._fallback_id().startswith("episodic-"))
    # touches 计访问
    obj3.touches()
    check("touches 计数", obj3.access_count == 1 and obj3.last_accessed != "")


# ══════════════════════════════════════════════════════════
# 2. lifecycle 状态机
# ══════════════════════════════════════════════════════════
def test_lifecycle():
    print("[lifecycle]")
    # 合法前向迁移
    check("observed->candidate 合法", validate_transition("observed", "candidate"))
    check("validated->active 合法", validate_transition("validated", "active"))
    check("active->reinforced 合法", validate_transition("active", "reinforced"))
    check("aging->archived 合法", validate_transition("aging", "archived"))
    check("archived->forgotten 合法", validate_transition("archived", "forgotten"))
    # 非法迁移
    check("observed->active 非法", not validate_transition("observed", "active"))
    check("observed->forgotten 非法", not validate_transition("observed", "forgotten"))
    check("回退 active->observed 非法", not validate_transition("active", "observed"))
    check("未知状态非法", not validate_transition("nonexistent", "active"))
    # terminal 停滞
    check("forgotten terminal", next_lifecycle("forgotten") == "forgotten")
    # next_lifecycle
    check("next(observed)=candidate", next_lifecycle("observed") == "candidate")
    check("next(active) in {reinforced,consolidated,aging}", next_lifecycle("active") in {"reinforced", "consolidated", "aging"})
    # MemoryObject.advance 方法
    obj = MemoryObject(content="x")
    check("advance_lifecycle observed->candidate", obj.advance_lifecycle() and obj.status == "candidate")
    check("非法 advance_to(active) from candidate 拒绝", not obj.advance_to("active") and obj.status == "candidate")
    check("合法 advance_to(validated)", obj.advance_to("validated") and obj.status == "validated")
    # 完整链到 forgotten
    chain = MemoryObject(content="y")
    for _ in range(10):
        if not chain.advance_lifecycle():
            break
    check("推进到 forgotten 停滞", chain.status == "forgotten")


# ══════════════════════════════════════════════════════════
# 3. bridge 双向映射（旧数据可读）
# ══════════════════════════════════════════════════════════
def test_bridge_roundtrip():
    print("[bridge]")
    # Sandglass 行 -> object -> 行
    line = "2026-08-09 10:00:00 | hermes | 帮我部署 nginx"
    obj = bridge.sandglass_line_to_object(line)
    check("sandglass 行解析", obj.content == "帮我部署 nginx" and obj.provenance == "sandglass")
    check("sandglass 行分类 procedural", obj.type == "procedural")
    back = bridge.object_to_sandglass_line(obj)
    check("sandglass 往返可写回", "帮我部署 nginx" in back and " | " in back)
    # 非标准行 -> fallback
    bad = bridge.sandglass_line_to_object("not a valid line")
    check("非标准行 fallback", bad.content == "not a valid line")
    # engram 旧行 -> object -> 行（旧数据可读）
    old = {"ts": "2026-08-07 01:42:00", "content": "买了辆 Geely", "type": "semantic"}
    obj2 = bridge.engram_row_to_object(old)
    check("engram 旧行可读", obj2.content == "买了辆 Geely" and obj2.provenance == "engram_store")
    row2 = bridge.object_to_engram_row(obj2)
    check("engram 行回写兼容", row2 == {"ts": old["ts"], "content": "买了辆 Geely", "type": "semantic"})
    # shadow -> object -> shadow
    obj3 = bridge.shadow_row_to_object(7, "荷兰用户", 0.8)
    check("shadow 映射", obj3.source_id == "shadow:7" and abs(obj3.confidence - 0.8) < 1e-6)
    row3 = bridge.object_to_shadow_row(obj3, 7, 0.8)
    check("shadow 行回写", row3["line_num"] == 7 and row3["content"] == "荷兰用户")
    # Memory <-> object
    mem = Memory(memory_id="m1", type="emotional", content="今天焦虑")
    obj4 = bridge.memory_to_object(mem)
    mem4 = bridge.object_to_memory(obj4)
    check("Memory<->object 往返", mem4.content == "今天焦虑" and mem4.type == "emotional")
    # 分类
    check("分类 procedural", bridge.classify_memory_type("如何部署") == "procedural")
    check("分类 emotional", bridge.classify_memory_type("今天真开心") == "emotional")


# ══════════════════════════════════════════════════════════
# 4. runtime.facade 稳定门面
# ══════════════════════════════════════════════════════════
def test_facade():
    print("[facade]")
    # 用临时路径避免污染生产 engram_store
    tmpdir = tempfile.mkdtemp(prefix="nyx-facade-test-")
    bridge._STORE = os.path.join(tmpdir, "engram_store.jsonl")

    # observe
    r = facade.observe("测试观察事件：用户是餐厅老板，经营十年", source="test", force_promote=True)
    check("observe ok", r.ok and r.lifecycle_state in ("observed", "validated", "active"))
    check("observe 类型", r.memory_type in ("semantic", "episodic", "emotional", "procedural"))
    check("observe 落盘", os.path.exists(bridge._STORE))

    # recall（SearchRouter 检索现有沙漏，不依赖本测试写入）
    mc = facade.recall("Geely", token_budget=800)
    check("recall 返回 MemoryContext", hasattr(mc, "strings") and hasattr(mc, "token_budget"))
    check("recall 可转文本", isinstance(mc.to_text(), str))

    # feedback
    fb = facade.feedback({"memory_ids": ["x"], "helpful": True})
    check("feedback 路由", fb["ok"] and fb["action"] in ("reinforce", "noop"))

    # forget
    fg = facade.forget({"memory_id": "no-such-id"})
    check("forget 无匹配不误删", fg["ok"] and fg["removed"] == 0)

    # 清理临时
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    test_schema()
    test_lifecycle()
    test_bridge_roundtrip()
    test_facade()
    print(f"\nRESULT: {PASS}/24 checks passed")
    if PASS < 24:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
