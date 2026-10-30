"""一个 Hermes 进程、三个 profile（v7.17.0；Hermes 插件目录评审指出的问题）。

    HERMES_AGENT_SRC=/path/to/hermes-agent python3 tests/integration/hermes_multiprofile_check.py

  A：l3_llm: on        B：同一个数据目录，没开        C：另一个数据目录
全用 Hermes 自己的代码：profile 覆盖（set_hermes_home_override）、插件加载器（hermes-plugin/ 拷进各 profile）、
resolve_memory_provider、MemoryManager。通过的条件：
  - B 的调用一次都不往外发（v7.16.2 及以前：A 的 l3_llm 写进了 os.environ，B 也发）
  - C 报不可用、Hermes 把原因告诉用户（以前：悄悄写进 A 的数据目录）
  - os.environ 与 sys.path 都没被改（以前：NEXSANDBASE_HOME、NYX_L3_LLM，以及 ~/.neurobase/scripts）
"""
import json, os, shutil, sys, tempfile, urllib.request
HERMES = os.environ["HERMES_AGENT_SRC"]
NYX = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [NYX, HERMES]
os.environ.pop("NEXSANDBASE_HOME", None)
for k in [k for k in os.environ if k.startswith("NYX_")]:
    os.environ.pop(k)
os.environ["OPENROUTER_API_KEY"] = "or-test-" + "0" * 24          # 主模型走 OpenRouter 的常见配置
root = tempfile.mkdtemp(prefix="nyx-mp-")
data, data2 = os.path.join(root, "nyx-data"), os.path.join(root, "nyx-data-2")
homes = {}
for name, cfg in (("A", {"data_dir": data, "l3_llm": "on"}), ("B", {"data_dir": data}), ("C", {"data_dir": data2})):
    h = os.path.join(root, name)
    shutil.copytree(os.path.join(NYX, "hermes-plugin"), os.path.join(h, "plugins", "nyx"))
    os.makedirs(h, exist_ok=True)
    open(os.path.join(h, "config.yaml"), "w").write("memory:\n  provider: nyx\n")
    json.dump(cfg, open(os.path.join(h, "nyx.json"), "w"))
    homes[name] = h
os.environ["HERMES_HOME"] = homes["A"]

calls = []
def fake_urlopen(req, timeout=None):
    calls.append(getattr(req, "full_url", str(req)))
    class R:
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "[]"}}]}).encode()
    return R()
urllib.request.urlopen = fake_urlopen

from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from agent.agent_init_memory_provider import resolve_memory_provider
from agent.memory_manager import MemoryManager

env_before, path_before = dict(os.environ), list(sys.path)
report, managers = {}, {}
said = []
for name in ("A", "B", "C"):
    tok = set_hermes_home_override(homes[name])
    try:
        p = resolve_memory_provider("nyx", say=said.append)
        report[f"{name}_provider"] = None if p is None else p.name
        if p is not None:
            mm = MemoryManager()
            mm.add_provider(p)
            mm.initialize_all(f"s-{name}", platform="cli", agent_context="primary", hermes_home=homes[name])
            managers[name] = mm
    finally:
        reset_hermes_home_override(tok)

for name in ("A", "B"):
    mm = managers[name]
    tok = set_hermes_home_override(homes[name])
    try:
        n0 = len(calls)
        mm.sync_all("我决定以后都自己搭服务器，省钱，不再买云主机", "好的，记下了", session_id=f"s-{name}")
        mm.sync_all("I decided to stop buying cloud servers and host everything myself", "Noted", session_id=f"s-{name}")
        assert mm.flush_pending(timeout=60)
        mm.build_system_prompt()
        mm.prefetch_all("what did I decide about servers")
        report[f"{name}_outbound_calls"] = len(calls) - n0
    finally:
        reset_hermes_home_override(tok)

report["notice_for_C"] = said[-1][:240] if said else None
report["environ_unchanged"] = dict(os.environ) == env_before
report["environ_diff"] = sorted(set(os.environ.items()) ^ set(env_before.items()))
report["sys_path_unchanged"] = sys.path == path_before
from nexsandglass.core import sandglass_paths
report["data_dir_in_use"] = sandglass_paths._NB == data
print(json.dumps(report, ensure_ascii=False, indent=1))
ok = (report["A_provider"] == report["B_provider"] == "nyx" and report["C_provider"] is None
      and report["A_outbound_calls"] > 0 and report["B_outbound_calls"] == 0
      and report["environ_unchanged"] and report["sys_path_unchanged"] and report["data_dir_in_use"])
print("MULTI-PROFILE CHECK:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
