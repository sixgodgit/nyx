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
    import nexsandglass_hermes as nh
    hh = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(hh))
    nh.save_config({"l3_llm": "on"}, str(hh))
    p = _provider()
    p._hermes_home = str(hh)
    p._load_settings()
    from nexsandglass.l3 import offset_signals as os_
    assert os_.llm_on()


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
