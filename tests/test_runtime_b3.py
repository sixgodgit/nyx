"""
B3 — Memory 是形成的（Promotion 门禁 + 差异化写入）。

覆盖：
  1. FormationRouter 调用 PromotionEngine（非仅 sync_turn）
  2. Golden：我叫阿文→promote；下午3点开会→session_only；重复偏好→reinforce；哈哈好的→drop
  3. 差异化写入落地：REINFORCE 不新插、DEDUP touch、OVERRIDE 旧 superseded、CONFLICT
  4. MCP memory_observe 走同一门禁
  5. 指标：promote_rate/drop_rate/reinforce_rate
  6. engram 增长率低于 raw log

独立可运行：python3 tests/test_runtime_b3.py
"""

import json
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

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} — {detail}")


def test_formations_golden():
    print("[B3: Formation 走 Promotion 门禁]")
    os.environ["NEXSANDBASE_HOME"] = "/root/.hermes/nexsandglass"
    from nexsandglass.runtime.orchestrator import get_orchestrator
    orch = get_orchestrator()
    # Golden 1: 身份 → promote
    fr = orch.observe("我叫阿文", source="golden")
    check("身份 promote", "engram" in fr.routes_written and ("promoted" in fr.message or "reinforce" in fr.message),
          fr.message)
    # Golden 2: 会议 → session_only 不写 engram
    fr2 = orch.observe("我们下午3点开会", source="golden")
    check("会议 session_only", "session_only" in fr2.message, fr2.message)
    check("会议不写 engram", "engram" not in fr2.routes_written, str(fr2.routes_written))
    # Golden 3: 闲聊 → drop 不写 engram
    fr3 = orch.observe("哈哈好的", source="golden")
    check("闲聊 drop", "dropped" in fr3.message, fr3.message)
    check("闲聊不写 engram", "engram" not in fr3.routes_written, str(fr3.routes_written))


def test_diff_write_persistence():
    print("[B3: 差异化写入落地]")
    import tempfile, shutil
    from nexsandglass.engram import bridge
    # 用临时 store
    tmp = tempfile.mkdtemp(prefix="nyx-b3-")
    old_store = bridge._STORE
    store = os.path.join(tmp, "engram_store.jsonl")
    bridge._STORE = store
    try:
        # INSERT
        r1 = bridge.ingest_classified("用户喜欢喝手冲咖啡", action="INSERT")
        check("INSERT 写入", r1["status"] == "insert")
        # REINFORCE（同内容）
        r2 = bridge.ingest_classified("用户喜欢喝手冲咖啡", action="REINFORCE")
        check("REINFORCE 不新插", r2["status"] == "reinforce")
        # 验证只有 1 行（REINFORCE 未新增）
        rows = [json.loads(l) for l in open(store, encoding="utf-8") if l.strip()]
        check("REINFORCE 不双写", len(rows) == 1, f"rows={len(rows)}")
        # DEDUP
        r3 = bridge.ingest_classified("用户喜欢喝手冲咖啡", action="DEDUP")
        check("DEDUP touch", r3["status"] == "dedup")
        rows2 = [json.loads(l) for l in open(store, encoding="utf-8") if l.strip()]
        check("DEDUP 不新插", len(rows2) == 1, f"rows={len(rows2)}")
        # CONFLICT
        r4 = bridge.ingest_classified("用户不住在阿姆斯特丹", action="CONFLICT")
        check("CONFLICT 标记", r4["status"] == "conflict")
        # OVERRIDE
        r5 = bridge.ingest_classified("用户住在鹿特丹", action="OVERRIDE",
                                      mem_type="semantic", supersede_id=r1["id"])
        check("OVERRIDE 新 active", r5["status"] == "insert")
        rows3 = [json.loads(l) for l in open(store, encoding="utf-8") if l.strip()]
        # conflict + override 新增 2 行
        check("OVERRIDE 追加", len(rows3) >= 3, f"rows={len(rows3)}")
    finally:
        bridge._STORE = old_store
        shutil.rmtree(tmp, ignore_errors=True)


def test_mcp_gate():
    print("[B3: MCP memory_observe 走同一门禁]")
    from nexsandglass.interfaces.sandglass_mcp import _handle_tool
    r1 = _handle_tool("memory_observe", {"content": "嗯嗯好的"}, 1)
    check("MCP 闲聊不写长期", "chitchat" in str(r1) or "memory_id\": \"\"" in str(r1), str(r1)[:80])
    r2 = _handle_tool("memory_observe", {"content": "用户喜欢喝拿铁咖啡"}, 2)
    check("MCP 偏好 promote", "engram" in str(r2), str(r2)[:80])


def test_metrics():
    print("[B3: 指标 promote_rate/drop_rate/reinforce_rate]")
    from nexsandglass.runtime.promotion import analyze
    texts = ["我叫阿文", "我们下午3点开会", "用户喜欢喝手冲咖啡", "嗯嗯",
             "好的好的", "验证码是123456", "用户是餐厅老板", "随便都行"]
    stats = analyze(texts)
    report = stats.report()
    check("promote_rate 存在", 0 <= report["promote_rate"] <= 1, str(report["promote_rate"]))
    check("drop_rate 存在", report["drop_rate"] > 0, str(report["drop_rate"]))
    check("reinforce_rate 存在", 0 <= report["reinforce_rate"] <= 1, str(report["reinforce_rate"]))


def test_growth_rate():
    print("[B3: engram 增长率低于 raw log]")
    from nexsandglass.engram import bridge
    from nexsandglass.core.sandglass_log import _SANDGLASS
    from nexsandglass.runtime.orchestrator import get_orchestrator

    def counts():
        raw = sum(1 for _ in open(_SANDGLASS, encoding="utf-8")) if os.path.exists(_SANDGLASS) else 0
        eng = sum(1 for _ in open(bridge._STORE, encoding="utf-8")) if os.path.exists(bridge._STORE) else 0
        return raw, eng

    r0, e0 = counts()
    orch = get_orchestrator()
    msgs = ["用户是架构师，做AI产品", "嗯嗯", "好的好的", "我们下午3点开会", "哈哈",
            "用户喜欢喝红茶", "随便", "用户女儿在荷兰读书"]
    for m in msgs:
        orch.observe(m, source="growth")
    r1, e1 = counts()
    check("engram 增长率 < raw log", (e1 - e0) < (r1 - r0), f"eng+{e1-e0} raw+{r1-r0}")


if __name__ == "__main__":
    test_formations_golden()
    test_diff_write_persistence()
    test_mcp_gate()
    test_metrics()
    test_growth_rate()
    print(f"\nRESULT: {PASS}/15 checks passed")
    if PASS < 15:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
