"""tests/test_l3_llm_optin.py — L3 分析调用外部大模型要主人自己打开（v7.15.2）

以前只要环境里有 DEEPSEEK_API_KEY / OPENROUTER_API_KEY，画像 / 偏移率 / 幽灵决策就会调用 DeepSeek / OpenRouter。
在 Hermes 里这几乎总是成立（主模型多半走 OpenRouter）—— 每次搭系统提示，search_filter("") 都把画像和最近 15 条
决策记录发了出去。现在要 NYX_L3_LLM=1（nyx.json 的 l3_llm: on）才发；key 在 Hermes 里按当前 profile 取。
"""
import json
import sys
import types
import urllib.request

import pytest

from tests.test_hermes_provider import _provider, env  # noqa: F401  复用隔离目录


FAKE = "or-test-" + "0" * 24                        # 不像任何真 token：过滤器不会动它，便于断言


class _Net:
    def __init__(self):
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append({"url": req.full_url, "auth": req.headers.get("Authorization"),
                           "body": req.data.decode("utf-8") if req.data else ""})

        class R:
            def read(self_inner):
                return json.dumps({"choices": [{"message": {"content": "[]"}}]}).encode()
        return R()


@pytest.fixture()
def net(monkeypatch):
    n = _Net()
    monkeypatch.setattr(urllib.request, "urlopen", n)
    monkeypatch.delenv("NYX_L3_LLM", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE)       # Hermes 用户的常见配置：主模型走 OpenRouter
    return n


def test_key_in_env_is_not_enough(env, net):  # noqa: F811
    from nexsandglass.features import sandglass_think as st
    from nexsandglass.l3 import offset_signals as os_
    p = _provider()
    p.sync_turn("我决定以后都自己搭服务器，省钱", "好的")
    p.system_prompt_block()                             # 以前在这里把画像和近期决策发给 OpenRouter
    st.search_filter("上次那个服务器")
    assert net.calls == [], "没打开 l3_llm 就不该有任何对外请求"
    assert st._llm("s", "u") == "" and not os_.llm_on()


def test_opt_in_uses_the_key(env, net, monkeypatch):  # noqa: F811
    from nexsandglass.features import sandglass_think as st
    monkeypatch.setenv("NYX_L3_LLM", "1")
    assert st._llm("system", "user") == "[]"
    assert net.calls[0]["url"].startswith("https://openrouter.ai/") and net.calls[0]["auth"] == f"Bearer {FAKE}"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test-" + "1" * 24)
    st._llm("system", "user")
    assert net.calls[1]["url"].startswith("https://api.deepseek.com/")


def test_nyx_json_switch(env, net, tmp_path, monkeypatch):  # noqa: F811
    """nyx.json 的 l3_llm: on 在这个 profile 的调用里生效，不写进进程环境（v7.17.0）。"""
    import os
    import nexsandglass_hermes as nh
    hh = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(hh))
    nh.save_config({"l3_llm": "on"}, str(hh))
    p = _provider()
    p._hermes_home = str(hh)
    p._load_settings()
    from nexsandglass.l3 import offset_signals as os_
    with p._settings_scope():
        assert os_.llm_on()
    assert not os_.llm_on() and "NYX_L3_LLM" not in os.environ, "出了这个 profile 的调用就不算数"


def test_one_profile_opting_in_does_not_turn_it_on_for_another(env, net, tmp_path, monkeypatch):  # noqa: F811
    """Hermes 评审的例子：同一个进程服务两个 profile，A 打开 l3_llm，B 没打开 —— B 的调用不往外发（v7.17.0）。

    以前 _load_settings 把 A 的 nyx.json 复制进 os.environ，B 之后的每次调用都当成打开了。
    """
    import os
    import nexsandglass_hermes as nh
    from nexsandglass.features import sandglass_think as st
    from nexsandglass.l3 import offset_signals as os_
    home_a, home_b = tmp_path / "a", tmp_path / "b"
    nh.save_config({"l3_llm": "on", "lang": "zh"}, str(home_a))
    nh.save_config({}, str(home_b))
    def snapshot():
        return {k: v for k, v in os.environ.items() if k != "HERMES_HOME"}     # 这个测试自己在切 profile
    before = snapshot()
    a, b = _provider(), _provider()
    monkeypatch.setenv("HERMES_HOME", str(home_a))
    a.initialize("sa", hermes_home=str(home_a), platform="cli", agent_context="primary")
    monkeypatch.setenv("HERMES_HOME", str(home_b))
    b.initialize("sb", hermes_home=str(home_b), platform="cli", agent_context="primary")
    assert snapshot() == before, "initialize 不改进程环境"

    seen = {}
    monkeypatch.setattr(st, "_llm", lambda *a_, **k: seen.setdefault("calls", []).append(os_.llm_on()) or "[]")
    for p in (a, b):
        with p._settings_scope():
            seen.setdefault("on", []).append(os_.llm_on())
    assert seen["on"] == [True, False]

    # B 的真实入口：搭系统提示、同步一轮 —— 一次外发都没有，也没有把开关看成打开
    b.sync_turn("我决定以后都自己搭服务器，省钱", "好的")
    b.system_prompt_block()
    assert net.calls == [] and True not in seen.get("calls", [])
    # 语言也按 profile：A 是中文，B 是英文（conftest 设了 NYX_LANG=en，这里去掉它，让 nyx.json 说了算）
    monkeypatch.delenv("NYX_LANG")
    from nexsandglass.core.lang import is_zh
    with a._settings_scope():
        assert is_zh()
    with b._settings_scope():
        assert not is_zh()


def test_hermes_secret_scope_wins(monkeypatch):
    """在 Hermes 里按 profile 取 key（agent.secret_scope.get_secret），不读进程环境里别人的；Hermes 拒绝时按没有处理。"""
    from nexsandglass.core import credentials
    monkeypatch.setenv("OPENROUTER_API_KEY", "process-env-key")
    mod = types.ModuleType("agent.secret_scope")
    state = {"raise": False}

    def get_secret(name, default=None):
        if state["raise"]:
            raise RuntimeError("UnscopedSecretError")
        return {"OPENROUTER_API_KEY": "profile-key"}.get(name, default)
    mod.get_secret = get_secret
    pkg = types.ModuleType("agent")
    pkg.secret_scope = mod
    monkeypatch.setitem(sys.modules, "agent", pkg)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", mod)
    assert credentials.get("OPENROUTER_API_KEY") == "profile-key"
    assert credentials.get("NOT_SET", "d") == "d"
    state["raise"] = True
    assert credentials.get("OPENROUTER_API_KEY") == "", "Hermes 拒绝（多路复用时没有作用域）：不退回去读进程环境"


def test_no_credential_files(monkeypatch, tmp_path):
    """multi_analysis 以前会自己去翻 keys.json 和 ~/.nyx/.env —— 插件不该读凭据文件。"""
    from nexsandglass.features import multi_analysis as ma
    envf = tmp_path / ".env"
    envf.write_text("SOME_KEY=from-file\n")
    monkeypatch.setenv("NYX_ENV_FILE", str(envf))
    monkeypatch.delenv("SOME_KEY", raising=False)
    assert ma._resolve_key("SOME_KEY") == ""
    monkeypatch.setenv("SOME_KEY", "from-env")
    assert ma._resolve_key("SOME_KEY") == "from-env"
