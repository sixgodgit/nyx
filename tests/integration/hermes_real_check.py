"""对真实的 Hermes Agent 源码跑一遍 nyx 插件（不是替身）。

用法：HERMES_AGENT_SRC=/path/to/hermes-agent python3 tests/integration/hermes_real_check.py
经过的全是 Hermes 自己的代码：plugins.memory 的 entry point 加载器与用户目录插件发现、
agent.memory_manager.MemoryManager 的 initialize_all / build_system_prompt / prefetch_all /
describe_recall / sync_all（后台串行线程）/ flush_pending / 工具注册与分发 / on_session_end / shutdown_all。
"""
import importlib.metadata as md
import json
import os
import shutil
import sys
import tempfile

HERMES = os.environ["HERMES_AGENT_SRC"]
NYX = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [NYX, HERMES]

tmp = tempfile.mkdtemp(prefix="nyx-hermes-")
hh = os.path.join(tmp, "hermes_home")
nb = os.path.join(tmp, "nyx-data")
os.makedirs(hh)
os.environ["HERMES_HOME"] = hh
os.environ.pop("NEXSANDBASE_HOME", None)
for k in ("NYX_UNDERSTAND_LLM", "NYX_OWNER_IDS", "NYX_NOW"):
    os.environ.pop(k, None)
# 数据目录与主人身份都写在 Hermes profile 的 nyx.json 里（hermes memory setup 会写的那个文件）
json.dump({"data_dir": nb, "owner_ids": "tg:42"}, open(os.path.join(hh, "nyx.json"), "w"))

report = {}
try:
    import subprocess
    report["hermes_commit"] = subprocess.run(["git", "-C", HERMES, "rev-parse", "HEAD"], capture_output=True,
                                             text=True).stdout.strip() or "unknown"
except Exception:
    report["hermes_commit"] = "unknown"
from agent.memory_provider import MemoryProvider          # noqa: E402  真 ABC
import plugins.memory as pm                               # noqa: E402  真加载器

ep = md.EntryPoint(name="nyx", value="nexsandglass_hermes", group=pm.ENTRY_POINTS_GROUP)
prov = pm._load_provider_from_entry_point(ep, register_skills=False)
assert isinstance(prov, MemoryProvider), type(prov)
from nexsandglass.core import sandglass_paths             # noqa: E402
assert os.path.abspath(sandglass_paths._NB) == nb, (sandglass_paths._NB, nb)
report["entry_point"] = "ok (data_dir from nyx.json applied before import)"

# 目录插件：拷到 $HERMES_HOME/plugins/nyx/，走 Hermes 的免 import 探测 + 包加载
shutil.copytree(os.path.join(NYX, "nexsandglass_hermes"), os.path.join(hh, "plugins", "nyx"))
assert "nyx" in pm.list_memory_provider_names()
p2 = pm.load_memory_provider("nyx")
assert isinstance(p2, MemoryProvider) and p2.name == "nexsandglass"
report["user_plugin_dir"] = "ok"

from agent.memory_manager import MemoryManager            # noqa: E402
mm = MemoryManager()
mm.add_provider(prov)
mm.initialize_all("sess-1", platform="cli", agent_context="primary")
report["system_prompt_chars"] = len(mm.build_system_prompt())
assert report["system_prompt_chars"] > 0

owner = {"id": "tg:42", "name": "主人", "is_bot": False}
guest = {"id": "tg:99", "name": "访客", "is_bot": False}
mm.on_turn_start(1, "我住在杭州", author_id="tg:42", author_name="主人", author_is_bot=False)
mm.sync_all("我住在杭州，在一家做机器人的公司当工程师", "好的，记下了你在杭州做工程师",
            session_id="sess-1", messages=[], turn_author=owner)
mm.sync_all("我上个月搬到了上海", "搬家辛苦了，上海的通勤怎么样", session_id="sess-1", turn_author=owner)
mm.sync_all("我住在拉萨", "了解", session_id="sess-1", turn_author=guest)
mm.sync_all("我对花生过敏，千万别推荐花生", "明白，以后推荐食物都会避开花生", session_id="sess-1",
            turn_author=owner)
assert mm.flush_pending(timeout=60), "后台写入没有在 60 秒内落盘"

from nexsandglass.core import sandglass_sqlite             # noqa: E402
from nexsandglass.features import sandglass_vault          # noqa: E402
sandglass_sqlite._last_sync_mtime = 0
sandglass_sqlite.sync_all()
sandglass_vault._idx_cache = None
sandglass_vault.rebuild_index()

ctx = mm.prefetch_all("我现在住哪", session_id="sess-1")
assert "上海" in ctx, ctx
report["prefetch"] = ctx
report["recall_indicator"] = mm.describe_recall()
assert "Nyx" in report["recall_indicator"]
assert "花生" in mm.prefetch_all("晚饭吃花生米可以吗", session_id="sess-1")

names = {s["name"] for s in mm.get_all_tool_schemas()}
assert {"nyx_belief", "nyx_forget", "nyx_restore", "nyx_quarantine", "sandglass_search"} <= names
report["tools"] = sorted(names)

belief = json.loads(mm.handle_tool_call("nyx_belief", {"relation": "住在"}))
assert [f["object"] for f in belief["facts"]] == ["上海"], belief      # 访客说的拉萨没有进来
report["belief_now"] = belief["facts"]

journal = open(os.path.join(nb, "sandglass.txt"), encoding="utf-8").read()
assert " | participant | 我住在拉萨" in journal
report["guest_bound_as"] = "participant"

pv = json.loads(mm.handle_tool_call("nyx_forget", {"contains": "花生"}))
assert pv["status"] == "preview" and pv["would_forget"] >= 1
done = json.loads(mm.handle_tool_call("nyx_forget", {"contains": "花生", "confirm": True}))
assert done["status"] == "quarantined" and done["recoverable"]
for mid in done["mem_ids"]:
    assert json.loads(mm.handle_tool_call("nyx_restore", {"mem_id": mid}))["ok"]
report["forget_restore"] = f"{len(done['mem_ids'])} quarantined and restored"

mm.on_session_end([{"role": "user", "content": "我住在拉萨"}])
mm.shutdown_all()
assert " | user | 我住在拉萨" not in open(os.path.join(nb, "sandglass.txt"), encoding="utf-8").read()
print(json.dumps(report, ensure_ascii=False, indent=1))
print("HERMES REAL CHECK: PASS")
