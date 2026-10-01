"""tests/test_hermes_provider.py — Hermes 官方 MemoryProvider 契约验收（v7.12）

没有装 Hermes 的环境里也要能证明两件事：
  1. 契约：在一个与 Hermes ``agent.memory_provider`` 同形的 ABC 下，Provider 能被实例化，
     每个钩子接受 MemoryManager 实际会传的参数，工具 schema 是官方的扁平格式，
     entry point / 目录插件两条发现路径都拿得到 Provider。
  2. 行为：多人会话里的来源绑定、非主 agent 只读、按本轮问题召回、遗忘只进隔离区可还原。
"""

import importlib.util
import inspect
import json
import os
import re
import sys
import types
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MP_PATH = os.path.join(_ROOT, "nexsandglass", "core", "memory_provider.py")


# ══════════════════════════════════════════════════════════
# 与 Hermes agent/memory_provider.py 同形的替身（抽象成员与钩子签名照官方）
# ══════════════════════════════════════════════════════════

def _fake_hermes_module():
    mod = types.ModuleType("agent.memory_provider")

    @dataclass(frozen=True)
    class RecallStatus:
        provider_label: str
        count: int
        glyph: str = "🧠"

    class MemoryProvider(ABC):
        pre_compress_checkpoint_api_version = 1

        @property
        @abstractmethod
        def name(self) -> str: ...

        @abstractmethod
        def is_available(self) -> bool: ...

        @abstractmethod
        def initialize(self, session_id: str, **kwargs) -> None: ...

        def unavailable_reason(self) -> str: return ""
        def system_prompt_block(self) -> str: return ""
        def prefetch(self, query: str, *, session_id: str = "") -> str: return ""
        def queue_prefetch(self, query: str, *, session_id: str = "") -> None: ...
        def recall_status(self): return None

        def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                      messages=None, turn_author=None) -> None: ...

        @abstractmethod
        def get_tool_schemas(self): ...

        def handle_tool_call(self, tool_name: str, args, **kwargs) -> str:
            raise NotImplementedError

        def shutdown(self) -> None: ...
        def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None: ...
        def identity_signature(self): return {}
        def on_session_end(self, messages) -> None: ...
        def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "",
                              reset: bool = False, rewound: bool = False, **kwargs) -> None: ...
        def on_pre_compress(self, messages) -> str: return ""
        def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None: ...
        def get_config_schema(self): return []
        def save_config(self, values, hermes_home: str) -> None: ...
        def on_memory_write(self, action: str, target: str, content: str, metadata=None) -> None: ...
        def backup_paths(self): return []

    def is_trivial_prompt(text):
        s = (text or "").strip()
        return not s or s.startswith("/") or bool(re.match(r"^(ok|thanks|hi|yes|no)[\s!.?]*$", s, re.I))

    mod.MemoryProvider, mod.RecallStatus, mod.is_trivial_prompt = MemoryProvider, RecallStatus, is_trivial_prompt
    return mod


@pytest.fixture()
def hermes(monkeypatch):
    """在替身 ABC 下重新加载 provider 模块（另起模块名，不影响其他测试里的类）。"""
    fake = _fake_hermes_module()
    pkg = types.ModuleType("agent")
    pkg.memory_provider = fake
    monkeypatch.setitem(sys.modules, "agent", pkg)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", fake)
    spec = importlib.util.spec_from_file_location("nyx_mp_under_hermes", _MP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return fake, mod


# ══════════════════════════════════════════════════════════
# 1. 契约
# ══════════════════════════════════════════════════════════

def test_instantiates_under_official_abc(hermes):
    fake, mod = hermes
    p = mod.NexSandglassProvider()
    assert isinstance(p, fake.MemoryProvider)
    assert p.name == "nexsandglass"
    assert p.is_available() is True
    assert p.unavailable_reason() == ""


_HOOK_KWARGS = {
    "prefetch": ("session_id",),
    "queue_prefetch": ("session_id",),
    "sync_turn": ("session_id", "messages", "turn_author"),
    "on_session_switch": ("parent_session_id", "reset", "rewound"),
    "on_delegation": ("child_session_id",),
}


@pytest.mark.parametrize("hook,kws", sorted(_HOOK_KWARGS.items()))
def test_hooks_accept_manager_kwargs(hermes, hook, kws):
    _, mod = hermes
    params = inspect.signature(getattr(mod.NexSandglassProvider, hook)).parameters
    for kw in kws:
        assert kw in params, f"{hook} 不接受 {kw}"
        assert params[kw].kind in (inspect.Parameter.KEYWORD_ONLY,
                                   inspect.Parameter.POSITIONAL_OR_KEYWORD)


def test_manager_style_calls_do_not_raise(hermes, monkeypatch):
    """按 MemoryManager 的调用方式逐个打一遍（非主上下文，不落盘）。"""
    _, mod = hermes
    p = mod.NexSandglassProvider()
    p._agent_context = "subagent"
    assert isinstance(p.prefetch("hi", session_id="s"), str)
    assert p.queue_prefetch("hi", session_id="s") is None
    p.sync_turn("u", "a", session_id="s", messages=[], turn_author={"id": "1", "name": "x", "is_bot": False})
    p.on_turn_start(1, "hello", author_id="1", author_name="x", author_is_bot=False)
    p.on_session_switch("s2", parent_session_id="s", reset=True, rewound=False, reason="new")
    p.on_delegation("task", "result", child_session_id="c")
    assert p.on_pre_compress([]) == ""
    assert isinstance(p.identity_signature(), dict)
    json.dumps(p.identity_signature())
    out = p.handle_tool_call("nyx_forget", {"contains": "任何东西", "confirm": True}, session_id="s")
    assert "只读" in json.loads(out)["error"]


def test_tool_schemas_are_flat_official_format(hermes):
    _, mod = hermes
    schemas = mod.NexSandglassProvider().get_tool_schemas()
    names = [s["name"] for s in schemas]
    assert len(names) == len(set(names))
    for s in schemas:
        assert set(s) == {"name", "description", "parameters"}, s
        assert "function" not in s
        assert s["parameters"]["type"] == "object"
        for req in s["parameters"].get("required", []):
            assert req in s["parameters"]["properties"]
    assert {"nyx_belief", "nyx_forget", "nyx_restore", "nyx_quarantine"} <= set(names)


def test_every_tool_returns_json(hermes, monkeypatch):
    _, mod = hermes
    p = mod.NexSandglassProvider()
    p._agent_context = "cron"          # 只读：写类工具必须拒绝而不是动数据
    minimal = {"sandglass_search": {"query": "x"}, "fact_store": {"action": "add", "content": "x"},
               "fact_feedback": {"line_num": 1, "helpful": True}, "nyx_restore": {"mem_id": "m_x"}}
    for s in p.get_tool_schemas():
        if s["name"] in ("sandglass_offset", "sandglass_echo", "sandglass_recent",
                         "sandglass_search", "nyx_belief", "nyx_quarantine"):
            continue   # 读类工具在行为测试里用隔离目录验证
        json.loads(p.handle_tool_call(s["name"], minimal.get(s["name"], {})))
    assert "error" in json.loads(p.handle_tool_call("no_such_tool", {}))


def test_entry_points_declared_and_loadable():
    txt = open(os.path.join(_ROOT, "pyproject.toml"), encoding="utf-8").read()
    block = txt.split('[project.entry-points."hermes_agent.memory_providers"]', 1)[1]
    block = block.split("\n[", 1)[0]
    eps = dict(re.findall(r'^(\w+)\s*=\s*"([^"]+)"', block, re.M))
    assert eps == {"nyx": "nexsandglass_hermes", "nexsandglass": "nexsandglass_hermes"}

    import nexsandglass_hermes as nh

    class Collector:                   # Hermes _ProviderCollector 的最小形态
        provider = None

        def register_memory_provider(self, provider):
            self.provider = provider

    c = Collector()
    nh.register(c)
    assert c.provider is not None and c.provider.name == "nexsandglass"


def test_plugin_dir_passes_hermes_discovery_heuristic():
    """目录插件：__init__.py 必须提到 register_memory_provider / MemoryProvider（Hermes 的免 import 探测）。"""
    d = os.path.join(_ROOT, "nexsandglass_hermes")
    src = open(os.path.join(d, "__init__.py"), encoding="utf-8").read()[:8192]
    assert "register_memory_provider" in src
    assert re.search(r"^name:\s*nyx\s*$", open(os.path.join(d, "plugin.yaml"), encoding="utf-8").read(), re.M)
    # 顶层只能用标准库：import nexsandglass 会把数据目录定死
    top = [l for l in src.splitlines() if re.match(r"^(from|import)\s", l)]
    assert not any("nexsandglass" in l and "nexsandglass_hermes" not in l for l in top), top


# ══════════════════════════════════════════════════════════
# 2. 配置 / 备份 / 身份
# ══════════════════════════════════════════════════════════

def test_config_roundtrip_backup_and_identity(tmp_path, monkeypatch):
    import nexsandglass_hermes as nh
    from nexsandglass.core.memory_provider import NexSandglassProvider
    hh = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(hh))
    monkeypatch.delenv("NYX_OWNER_IDS", raising=False)
    monkeypatch.delenv("NEXSANDBASE_HOME", raising=False)

    p = NexSandglassProvider()
    keys = {f["key"] for f in p.get_config_schema()}
    assert keys == {"data_dir", "owner_ids", "bots_are_external", "prefetch_tokens"}
    for f in p.get_config_schema():
        assert f["type"] in ("text", "integer", "number", "boolean")

    p.save_config({"owner_ids": "tg:42, alice", "prefetch_tokens": 300, "junk": 1}, str(hh))
    cfg = json.load(open(hh / "nyx.json", encoding="utf-8"))
    assert cfg == {"owner_ids": "tg:42, alice", "prefetch_tokens": 300}
    assert p.identity_signature() == {"nyx.owner_ids": ["alice", "tg:42"]}

    # 默认目录 ~/.neurobase 在 HERMES_HOME 外 → 交给 hermes backup
    assert p.backup_paths() == [nh.DEFAULT_DATA_DIR]
    # 目录配在 HERMES_HOME 里 → hermes 自己会备份，不重复声明
    nh.save_config({"data_dir": str(hh / "nyx-data")}, str(hh))
    assert nh.resolve_data_dir(str(hh)) == str(hh / "nyx-data")
    assert p.backup_paths() == []
    # 环境变量优先
    monkeypatch.setenv("NEXSANDBASE_HOME", str(tmp_path / "elsewhere"))
    assert p.backup_paths() == [str(tmp_path / "elsewhere")]


# ══════════════════════════════════════════════════════════
# 3. 行为（隔离目录）
# ══════════════════════════════════════════════════════════

@pytest.fixture()
def env(monkeypatch, tmp_path):
    home = tmp_path / "nb"
    home.mkdir()
    monkeypatch.setenv("NEXSANDBASE_HOME", str(home))
    monkeypatch.delenv("WTHREAD_LLM_EXTRACTION", raising=False)
    monkeypatch.delenv("NYX_UNDERSTAND_LLM", raising=False)
    monkeypatch.delenv("NYX_FORGET_RETENTION_DAYS", raising=False)

    from nexsandglass.core import memid, sandglass_sqlite, erasure, sandglass_log
    from nexsandglass.features import sandglass_vault, shadow_sand, weavethread
    from nexsandglass.engram import bridge

    sg = str(home / "sandglass.txt")
    monkeypatch.setattr(memid, "_SANDGLASS", sg)
    monkeypatch.setattr(memid, "_LOCK", sg + ".lock")
    monkeypatch.setattr(sandglass_log, "_SANDGLASS", sg)
    monkeypatch.setattr(sandglass_vault, "_SANDGLASS", sg)
    monkeypatch.setattr(sandglass_sqlite, "_DB", str(home / "sandglass.db"))
    monkeypatch.setattr(sandglass_sqlite, "_last_sync_mtime", 0)
    monkeypatch.setattr(erasure, "_NB", str(home))
    monkeypatch.setattr(weavethread, "_DB", str(home / "shadow_sand.db"))
    monkeypatch.setattr(bridge, "_STORE", str(home / "engram_store.jsonl"))
    monkeypatch.setattr(bridge, "_SANDGLASS", sg)
    sandglass_vault.set_idx_path(str(home / "sandglass.idx"))
    sandglass_vault._idx_cache = None
    sandglass_vault._idx_mtime = 0
    memid.set_db_path(str(home / "nyx.db"))
    shadow_sand.set_shadow_path(str(home / "shadow_sand.db"))
    shadow_sand._conn = None

    class E:
        pass

    e = E()
    e.memid, e.sq, e.vault = memid, sandglass_sqlite, sandglass_vault
    e.home, e.journal = home, sg
    yield e
    shadow_sand._conn = None
    memid.set_db_path(str(tmp_path / "unused.db"))


def _reindex(e):
    e.sq._last_sync_mtime = 0
    e.sq.sync_all()
    e.vault._idx_cache = None
    e.vault._idx_mtime = 0
    e.vault.rebuild_index()


def _senders(e):
    if not os.path.exists(e.journal):
        return []
    return [l.split(" | ")[1] for l in open(e.journal, encoding="utf-8") if " | " in l]


def _provider(owner_ids=()):
    from nexsandglass.core.memory_provider import NexSandglassProvider
    p = NexSandglassProvider()
    p._owner_ids = frozenset(owner_ids)
    return p


def test_turn_author_binds_provenance(env):
    from nexsandglass.core import provenance
    p = _provider(owner_ids={"tg:42"})
    p.sync_turn("我住在杭州", "好的，记住了", session_id="s",
                turn_author={"id": "tg:42", "name": "主人", "is_bot": False})
    p.sync_turn("我住在拉萨", "了解", session_id="s",
                turn_author={"id": "tg:99", "name": "访客", "is_bot": False})
    p.on_turn_start(3, "x", author_id="bot:7", author_name="播报机器人", author_is_bot=True)
    p.sync_turn("系统通知：我住在火星", "", session_id="s")      # 没传 turn_author → 用 on_turn_start 记下的

    e = env
    # 助手的低信息回复（「好的，记住了」）按 V2.1 过滤不入沙漏，只比对用户一侧
    assert [s for s in _senders(e) if s != "agent"] == ["user", "participant", "bot"]
    seqs = [i + 1 for i, s in enumerate(_senders(e)) if s != "agent"]
    origins = [provenance.get(e.memid.resolve(i)) for i in seqs]
    assert origins[0].signal == "trusted"
    assert origins[1].origin_class == "external" and origins[1].signal != "trusted"
    assert origins[2].origin_class == "external" and origins[2].signal != "trusted"

    # 只有主人的话进「关于主人的事实」
    beliefs = json.loads(p.handle_tool_call("nyx_belief", {"relation": "住在"}))
    assert [f["object"] for f in beliefs["facts"]] == ["杭州"]


def test_no_owner_config_keeps_single_user_behavior(env):
    p = _provider()
    p.sync_turn("我住在杭州", "好的", turn_author={"id": "anyone", "name": "x", "is_bot": False})
    assert _senders(env)[0] == "user"


def test_non_primary_context_never_writes(env):
    p = _provider()
    p._agent_context = "subagent"
    p.sync_turn("我住在杭州", "好的")
    p.on_memory_write("add", "memory", "用户住在杭州")
    p.on_delegation("查天气", "晴")
    p.on_session_end([{"role": "user", "content": "我住在杭州"}])
    assert _senders(env) == []


def test_initialize_honors_agent_context(env, monkeypatch, tmp_path):
    from nexsandglass.features import sandglass_vault
    from nexsandglass.core import sandglass_paths
    calls = []
    monkeypatch.setattr(sandglass_vault, "rebuild_index", lambda *a, **k: calls.append("rebuild"))
    monkeypatch.setattr(sandglass_paths, "validate", lambda: {"ok": True})
    hh = tmp_path / "hermes"
    p = _provider()
    p.initialize("s1", hermes_home=str(hh), platform="cli", agent_context="subagent")
    assert p._agent_context == "subagent" and calls == []
    q = _provider()
    q.initialize("s1", hermes_home=str(hh), platform="cli", agent_context="primary")
    assert calls == ["rebuild"] and q._can_write()


def test_prefetch_recalls_for_this_turn(env):
    p = _provider()
    p.sync_turn("我住在杭州，在一家做机器人的公司当工程师", "好的")
    p.sync_turn("我上个月搬到了上海", "搬家辛苦了")
    p.sync_turn("我对花生过敏，千万别推荐花生", "明白")
    _reindex(env)

    ctx = p.prefetch("我住哪", session_id="s")
    assert "Nyx 记忆" in ctx
    assert "上海" in ctx, ctx                    # 当前信念（v7.12：问句线索 → 知识图谱）
    assert re.search(r"- \[\d{4}-\d{2}-\d{2}\] ", ctx), ctx   # 每条带「哪天说的」
    st = p.recall_status()
    assert st is not None and st.count >= 1 and st.provider_label == "Nyx"

    ctx2 = p.prefetch("花生能吃吗", session_id="s")
    assert "花生过敏" in ctx2

    p.prefetch("ok", session_id="s")              # 无语义的一句 → 不召回，指示灯熄灭
    assert p.recall_status() is None


def test_belief_tool_answers_current_and_history(env):
    p = _provider()
    p.sync_turn("我住在杭州", "好的")
    p.sync_turn("我上个月搬到了上海", "好")
    cur = json.loads(p.handle_tool_call("nyx_belief", {"relation": "住在"}))
    assert [f["object"] for f in cur["facts"]] == ["上海"]
    hist = json.loads(p.handle_tool_call("nyx_belief", {"relation": "住在", "history": True}))
    assert {f["object"] for f in hist["facts"]} >= {"上海", "杭州"}
    assert hist["timeline"]


def test_forget_goes_to_quarantine_and_restores(env):
    p = _provider()
    p.sync_turn("我的备用邮箱是 secret-box@example.com", "记住了")
    _reindex(env)

    pv = json.loads(p.handle_tool_call("nyx_forget", {"contains": "secret-box"}))
    assert pv["status"] == "preview" and pv["would_forget"] == 1
    assert "secret-box" in open(env.journal, encoding="utf-8").read()     # 预览不动数据

    assert "error" in json.loads(p.handle_tool_call("nyx_forget", {"contains": "我", "confirm": True}))

    done = json.loads(p.handle_tool_call("nyx_forget", {"contains": "secret-box", "confirm": True,
                                                        "reason": "owner asked"}))
    assert done["status"] == "quarantined" and done["recoverable"] is True and done["purge_after"]
    assert "secret-box" not in open(env.journal, encoding="utf-8").read()
    _reindex(env)
    hits = json.loads(p.handle_tool_call("sandglass_search", {"query": "secret-box"}))
    assert not any("secret-box" in h["text"] for h in hits)

    q = json.loads(p.handle_tool_call("nyx_quarantine", {}))
    assert [i["mem_id"] for i in q["items"]] == done["mem_ids"]

    back = json.loads(p.handle_tool_call("nyx_restore", {"mem_id": done["mem_ids"][0]}))
    assert back["ok"], back
    assert "secret-box" in open(env.journal, encoding="utf-8").read()


def test_forget_scope_is_capped(env):
    p = _provider()
    for i in range(25):
        p.sync_turn(f"批量测试记录第{i}条，项目代号北极星", "")
    out = json.loads(p.handle_tool_call("nyx_forget", {"contains": "北极星", "confirm": True}))
    assert "上限" in out["error"]
    assert open(env.journal, encoding="utf-8").read().count("北极星") == 25


def test_session_end_does_not_relabel_synced_turns(env):
    p = _provider(owner_ids={"tg:42"})
    p.sync_turn("我住在拉萨", "了解", turn_author={"id": "tg:99", "name": "访客", "is_bot": False})
    before = _senders(env)
    p.on_session_end([{"role": "user", "content": "我住在拉萨"}, {"role": "assistant", "content": "了解"}])
    assert _senders(env) == before
    cur = json.loads(p.handle_tool_call("nyx_belief", {"relation": "住在"}))
    assert cur["facts"] == []


def test_pre_compress_always_returns_str(env):
    p = _provider()
    assert p.on_pre_compress([]) == ""
    assert isinstance(p.on_pre_compress([{"role": "user", "content": "我住哪"}]), str)


def test_shadow_results_carry_text_not_placeholders(env):
    """v7.11：影子沙召回的正文是字面量 "shadow#11"（拿行号当关键词去搜），还会被注入 prompt。"""
    from nexsandglass.runtime.orchestrator import _shadow_text
    p = _provider()
    p.sync_turn("我住在杭州，在一家做机器人的公司当工程师", "好的")
    assert _shadow_text(1) == "我住在杭州，在一家做机器人的公司当工程师"
    assert _shadow_text(999) == ""
    _reindex(env)
    from nexsandglass.runtime.orchestrator import get_orchestrator
    mc = get_orchestrator().recall("杭州", token_budget=600)
    assert not any(str(s).startswith("shadow#") for s in mc.strings)
    lines = [o.source_id for o in mc.objects if o.provenance != "wthread" and o.source_id]
    assert len(lines) == len(set(lines)), "同一条记录经两个源进来只留一份"
