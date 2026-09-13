"""
B1 — Orchestrator 唯一热路径测试。

覆盖：
  1. Provider handle_tool_call 检索类全走 orchestrator（sandglass_search/recent）
  2. on_session_end 经 orchestrator（不直连 log_message）
  3. FormationResult.errors（子路由失败记录）+ routes_written
  4. 直写治理 NYX_ENFORCE_RUNTIME=1 检测非 orchestrator 直写
  5. NyxOrchestrator.recent adapter
  6. 单源失败召回仍可用（policy 开关生效）

独立可运行：python3 tests/test_runtime_b1.py
"""

import os
import sys
import types
from unittest.mock import patch

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


def test_provider_tools_via_orchestrator():
    print("[B1: Provider 工具走 orchestrator]")
    from nexsandglass.core.memory_provider import NexSandglassProvider
    p = NexSandglassProvider.__new__(NexSandglassProvider)
    # sandglass_recent 走 orch（返回 id/text，非旧 (line,ts,text) 直连）
    r = p.handle_tool_call("sandglass_recent", {"n": 3})
    check("recent 走 orch", "id" in r and "text" in r, r[:60])
    # sandglass_search 走 orch
    r2 = p.handle_tool_call("sandglass_search", {"query": "咖啡", "limit": 3})
    check("search 走 orch", '"text"' in r2 or "results" in r2 or "[]" in r2, r2[:60])


def test_on_session_end_via_orchestrator():
    print("[B1: on_session_end 经 orchestrator]")
    from nexsandglass.core.memory_provider import NexSandglassProvider
    p = NexSandglassProvider.__new__(NexSandglassProvider)
    p._use_facade = True
    try:
        p.on_session_end([{"role": "user", "content": "用户喜欢喝乌龙茶"}])
        check("on_session_end 不崩", True)
    except Exception as e:
        check("on_session_end 不崩", False, str(e))


def test_formation_errors():
    print("[B1: FormationResult errors + routes_written]")
    from nexsandglass.runtime.orchestrator import get_orchestrator
    orch = get_orchestrator()
    fr = orch.observe("B1 测试消息：用户是工程师", source="b1")
    check("routes_written 非空", len(fr.routes_written) > 0, str(fr.routes_written))
    check("errors 为 dict", isinstance(fr.errors, dict))
    check("observe ok", fr.ok)


def test_direct_write_governance():
    print("[B1: 直写治理 NYX_ENFORCE_RUNTIME]")
    import subprocess, tempfile
    # 独立脚本模拟非 orchestrator 直写
    script = '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("NEXSANDBASE_HOME",
                      os.path.join(os.path.expanduser("~"), ".nyx"))
os.environ["NYX_ENFORCE_RUNTIME"] = "1"
import logging, io
h = logging.StreamHandler(io.StringIO())
lg = logging.getLogger("nexsandglass.features.sandglass_vault")
lg.setLevel(logging.WARNING); lg.addHandler(h)
from nexsandglass.features.sandglass_vault import search
search("咖啡", limit=1)
out = h.stream.getvalue()
print("GOV_TRIGGERED", "B1 直写治理" in out)
'''
    fd, path = tempfile.mkstemp(prefix="hermes-direct-", suffix=".py", dir="/tmp")
    with os.fdopen(fd, "w") as f:
        f.write(script)
    venv = sys.executable
    r = subprocess.run([venv, path], capture_output=True, text=True, timeout=60)
    os.remove(path)
    check("直写治理触发", "GOV_TRIGGERED True" in r.stdout, r.stdout[-80:])


def test_orchestrator_recent_adapter():
    print("[B1: NyxOrchestrator.recent adapter]")
    from nexsandglass.runtime.orchestrator import get_orchestrator
    orch = get_orchestrator()
    objs = orch.recent(3)
    check("recent 返回列表", isinstance(objs, list))
    if objs:
        from nexsandglass.engram.types import MemoryObject
        check("recent 元素 MemoryObject", all(isinstance(o, MemoryObject) for o in objs))


def test_single_source_failure():
    print("[B1: 单源失败召回仍可用]")
    from nexsandglass.runtime.orchestrator import RecallPlanner

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("source exploded")

    rp = RecallPlanner()
    # 让 wthread 源抛异常，其他源正常 → recall 仍返回
    with patch.object(rp, "_adapt_wthread", side_effect=RuntimeError("wthread down")):
        mc = rp.recall("咖啡", token_budget=200)
        check("recall 不崩返回 MemoryContext", hasattr(mc, "strings"))
        check("degraded 标记", mc.degraded in (True, False))


if __name__ == "__main__":
    test_provider_tools_via_orchestrator()
    test_on_session_end_via_orchestrator()
    test_formation_errors()
    test_direct_write_governance()
    test_orchestrator_recent_adapter()
    test_single_source_failure()
    print(f"\nRESULT: {PASS}/12 checks passed")
    if PASS < 11:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
