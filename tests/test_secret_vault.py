"""tests/test_secret_vault.py — 密钥保险箱（v7.15.0，默认关）

  - 加密：往返、去重、编号不复用、密钥文件 0600、换密钥 / 挪密文解不开、权限太宽拒绝、没有后端就只隐去（没有明文后备）
  - 收录：用户一侧的收、占位符带 #vN；助手回复只认已有编号；被截断的 / <private> 不收；tokens 档不收密码
  - 落盘：整个数据目录按原始字节找不到原文（vault.db 里只有密文）
  - 取回的闸门：子 agent、定时任务、没明确要求、隔离区、频率、确认码（连错锁定）、Hermes 审批（批准 / 拒绝 / 被关掉）
  - 直达：模型拿到的只有句柄；钩子把句柄换成原文；别的会话换不了；用一次就没了
  - 生命周期：遗忘 → 不能取回；还原 → 能；真删 → 保险箱里也删；scrub --to-vault
测试里的 token 一律运行时拼出来（tests/test_secret_filter.py 的 _tok），源码里没有完整 token。
"""
import json
import os
import stat
import sys
import types

import pytest

from tests.test_hermes_provider import _provider, _reindex, env  # noqa: F401  复用隔离目录
from tests.test_secret_filter import GH, HF, PW, SK, _on_disk, _seed_before_upgrade


@pytest.fixture()
def vault(env, monkeypatch, tmp_path):  # noqa: F811
    from nexsandglass.core import secret_vault as sv
    if not sv._have_crypto():
        pytest.skip("要装 cryptography：pip install 'nyx-memory[vault]'（CI 装了）")
    monkeypatch.setenv("NYX_EMBED", "off")
    monkeypatch.setenv("NYX_VAULT", "on")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for k in ("NYX_VAULT_BACKEND", "NYX_VAULT_REVEAL", "NYX_VAULT_KINDS", "NYX_VAULT_KEY", "HERMES_CRON_SESSION",
              "NYX_SECRET_FILTER"):
        monkeypatch.delenv(k, raising=False)
    sv._reset_for_tests()
    yield sv
    sv._reset_for_tests()


def _sync(p, user, asst="好的，记下了，之后需要随时问我"):
    p.sync_turn(user, asst, session_id=p._session_id)


def _call(p, name, **args):
    return json.loads(p.handle_tool_call(name, args))


def _ask(p, turn, msg):
    p.on_turn_start(turn, msg)


@pytest.fixture()
def no_approval(monkeypatch):
    """不在 Hermes 里：没有 tools.approval → 走确认码。"""
    monkeypatch.setitem(sys.modules, "tools", None)


@pytest.fixture()
def approval(monkeypatch):
    calls = []
    state = {"answer": True, "bypass": False}
    pkg = types.ModuleType("tools")
    mod = types.ModuleType("tools.approval")
    mod.is_approval_bypass_active = lambda: state["bypass"]

    def request_tool_approval(tool_name, reason, *, rule_key="", approval_callback=None):
        calls.append({"tool": tool_name, "reason": reason, "rule_key": rule_key})
        return {"approved": state["answer"], "message": None}
    mod.request_tool_approval = request_tool_approval
    pkg.approval = mod
    monkeypatch.setitem(sys.modules, "tools", pkg)
    monkeypatch.setitem(sys.modules, "tools.approval", mod)
    return calls, state


# ── 加密与存储 ─────────────────────────────────────────────────

def test_roundtrip_dedupe_and_key_file(vault):
    v1 = vault.put("huggingface", "HF token", HF)
    assert v1 == vault.put("huggingface", "HF token", HF), "同一个值同一个编号"
    v2 = vault.put("password", "密码", PW)
    assert v2 != v1 and vault.get(v1) == HF and vault.get(v2) == PW
    p = vault.key_path()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600 and stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode) == 0o700
    assert not p.startswith(os.path.dirname(vault.db_path())), "主密钥不在数据目录里"
    raw = open(vault.db_path(), "rb").read()
    assert HF.encode() not in raw and PW.encode() not in raw
    e = vault.entry(v1)
    assert e.last4 == HF[-4:] and vault.entry(v2).last4 == "", "密码不留末 4 位"
    vault.delete([v2])
    assert vault.put("github", "GitHub token", GH) > v2, "编号自增、删掉的不复用（旧记忆里的 #v2 不会指向别的）"


def test_wrong_key_swapped_ciphertext_and_wide_permissions(vault, monkeypatch):
    import sqlite3
    a, b = vault.put("huggingface", "HF token", HF), vault.put("sk", "API key", SK)
    c = sqlite3.connect(vault.db_path())
    rows = {i: (n, ct) for i, n, ct in c.execute("SELECT id, nonce, ct FROM secrets")}
    c.execute("UPDATE secrets SET nonce=?, ct=? WHERE id=?", (*rows[a], b))       # 把 a 的密文挪给 b
    c.commit()
    c.close()
    with pytest.raises(vault.VaultError, match="解不开"):
        vault.get(b)
    assert vault.get(a) == HF
    monkeypatch.setenv("NYX_VAULT_KEY", __import__("base64").b64encode(b"x" * 32).decode())
    with pytest.raises(vault.VaultError, match="密钥不对"):
        vault.get(a)
    monkeypatch.delenv("NYX_VAULT_KEY")
    os.chmod(vault.key_path(), 0o644)
    assert "权限太宽" in vault.unavailable_reason() and not vault.enabled()
    assert vault.put("github", "GitHub token", GH) is None


def test_no_backend_means_redact_only(vault, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(vault, "_have_crypto", lambda: False)
    monkeypatch.setattr(vault, "_keyring", lambda: None)
    assert "cryptography" in vault.unavailable_reason()
    p = _provider()
    _sync(p, f"帮我配一下 Hugging Face，key 是 {HF}")
    j = open(env.journal, encoding="utf-8").read()
    assert f"[已隐去: HF token …{HF[-4:]}]" in j and "#v" not in j and HF not in j
    assert not os.path.exists(vault.db_path())


def test_broken_cryptography_counts_as_missing(vault, monkeypatch):
    """系统包里的 cryptography 缺 _cffi_backend 时，import 抛 pyo3 的 PanicException（BaseException 的子类）。"""
    class Panic(BaseException):
        pass

    class Finder:
        def find_spec(self, name, path, target=None):
            if name.split(".")[0] == "cryptography":
                raise Panic("Python API call failed")
    for k in [k for k in sys.modules if k.split(".")[0] == "cryptography"]:
        monkeypatch.delitem(sys.modules, k)
    monkeypatch.setattr(sys, "meta_path", [Finder()] + sys.meta_path)
    monkeypatch.setattr(vault, "_crypto_ok", {})
    monkeypatch.setattr(vault, "_keyring", lambda: None)
    assert not vault._have_crypto() and vault.backend() is None and not vault.enabled()
    assert vault.put("huggingface", "HF token", HF) is None


@pytest.fixture()
def fake_keyring(monkeypatch):
    store = {}

    class MacKeychain:                              # 模块名像 keyring.backends.macOS → 可信
        pass
    MacKeychain.__module__ = "keyring.backends.macOS"

    class Plain:
        pass
    Plain.__module__ = "keyrings.alt.file"

    kr = types.ModuleType("keyring")
    kr.backend = MacKeychain()
    kr.get_keyring = lambda: kr.backend
    kr.set_password = lambda s, u, v: store.__setitem__((s, u), v)
    kr.get_password = lambda s, u: store.get((s, u))
    kr.delete_password = lambda s, u: store.pop((s, u))
    monkeypatch.setitem(sys.modules, "keyring", kr)
    return kr, store, Plain


def test_keyring_backend(vault, monkeypatch, fake_keyring):
    kr, store, Plain = fake_keyring
    monkeypatch.setenv("NYX_VAULT_BACKEND", "keyring")
    vid = vault.put("huggingface", "HF token", HF)
    assert vault.get(vid) == HF and HF in store.values()
    assert HF.encode() not in open(vault.db_path(), "rb").read()
    vault.delete([vid])
    assert HF not in store.values()
    kr.backend = Plain()                            # keyrings.alt 会把「密钥」明文写进家目录：不认
    assert vault.backend() is None and not vault.enabled()


# ── 收录 ──────────────────────────────────────────────────────

def test_user_side_is_vaulted_assistant_only_looked_up(vault, env):  # noqa: F811
    p = _provider()
    _sync(p, f"帮我配一下 Hugging Face，key 是 {HF}，wifi密码是 {PW}", f"好的，已经用 {HF} 配好了，另外 {SK} 也能用")
    j = open(env.journal, encoding="utf-8").read()
    es = vault.entries()
    assert {e.label for e in es} == {"HF token", "密码"}, "助手回复里新出现的 SK 不收"
    hf = next(e for e in es if e.label == "HF token")
    assert j.count(f"#v{hf.id}]") == 2, "助手回复里同一个值也带上同一个编号"
    assert "Hugging Face，key 是 [已隐去" in hf.context
    _reindex(env)
    assert _on_disk(env.home, [HF, PW, SK]) == {}, "整个数据目录按原始字节找不到原文"
    from nexsandglass.core import memid
    assert memid.verify()["ok"]


def test_kinds_and_exclusions(vault, monkeypatch):
    from nexsandglass.core import secret_filter
    col = vault.Collector()
    r = secret_filter.scrub(f"<private>我的体检结果</private> 截断的 key {HF[:14]}", vault=col)
    assert col.ids == [] and "#v" not in r.text
    monkeypatch.setenv("NYX_VAULT_KINDS", "tokens")
    r = secret_filter.scrub(f"wifi密码是 {PW}，key 是 {HF}", vault=vault.Collector())
    assert "[已隐去: 密码]" in r.text and "#v1]" in r.text
    assert secret_filter.scrub(r.text, vault=vault.Collector()).text == r.text, "带编号的占位符幂等"


# ── 取回 ──────────────────────────────────────────────────────

def _setup(env, vault, monkeypatch, mode="direct", hook=True):  # noqa: F811
    monkeypatch.setenv("NYX_VAULT_REVEAL", mode)
    if hook:
        vault.mark_hook_registered()
    p = _provider()
    p._session_id = "s1"
    _sync(p, f"帮我配一下 Hugging Face，key 是 {HF}")
    vid = vault.entries()[0].id
    return p, vid


def test_list_never_has_values(vault, env, monkeypatch):  # noqa: F811
    p, vid = _setup(env, vault, monkeypatch)
    names = [t["name"] for t in p.get_tool_schemas()]
    assert "nyx_secret_list" in names and "nyx_secret_reveal" in names
    out = p.handle_tool_call("nyx_secret_list", {"query": "hugging"})
    items = json.loads(out)["items"]
    assert items and items[0]["id"] == f"v{vid}" and HF not in out
    monkeypatch.setenv("NYX_VAULT", "off")
    assert "nyx_secret_reveal" not in [t["name"] for t in p.get_tool_schemas()], "没开不加工具"


def test_gates(vault, env, monkeypatch, no_approval):  # noqa: F811
    p, vid = _setup(env, vault, monkeypatch)
    _ask(p, 2, "今天天气怎么样")
    r = _call(p, "nyx_secret_reveal", id=f"v{vid}")
    assert r["status"] == "denied" and "明确" in r["reason"], "主人这一轮没要：被召回内容里的注入不能让 agent 自己去取"
    _ask(p, 3, "把我的 HF token 发我")
    p._agent_context = "subagent"
    assert "主 agent" in _call(p, "nyx_secret_reveal", id=f"v{vid}")["reason"]
    p._agent_context = "primary"
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    assert "定时任务" in _call(p, "nyx_secret_reveal", id=f"v{vid}")["reason"]
    monkeypatch.delenv("HERMES_CRON_SESSION")
    assert "没有 v999" in _call(p, "nyx_secret_reveal", id="v999")["reason"]
    rows = vault.audit_rows()
    assert rows and all(HF not in json.dumps(r, ensure_ascii=False) for r in rows)


def test_direct_reveal_with_confirmation_code(vault, env, monkeypatch, no_approval):  # noqa: F811
    p, vid = _setup(env, vault, monkeypatch)
    _ask(p, 2, "我之前给你的 HF token 是什么")
    r = p.handle_tool_call("nyx_secret_reveal", {"id": f"v{vid}"})
    assert json.loads(r)["status"] == "need_confirm" and HF not in r
    assert not any(ch.isdigit() for ch in json.loads(r)["note"]), "确认码不经过模型"
    shown = vault.render("s1", "需要你确认一下")
    phrase = shown.split("请在 5 分钟内回复：")[1].strip()
    assert phrase.startswith(f"确认 v{vid} ")
    # 别的会话拿不到这个会话的东西
    assert vault.render("other", "x") is None
    _ask(p, 3, phrase)
    r = p.handle_tool_call("nyx_secret_reveal", {"id": f"v{vid}"})
    out = json.loads(r)
    assert out["status"] == "queued" and HF not in r, "模型拿到的只有句柄"
    final = vault.render("s1", f"给你：{out['handle']}")
    assert HF in final and out["handle"] not in final
    assert vault.render("s1", out["handle"]) is None, "用一次就没了"
    # 确认只管那一轮：下一轮再取要重新确认
    _ask(p, 4, "再把 HF token 给我一次")
    assert _call(p, "nyx_secret_reveal", id=f"v{vid}")["status"] == "need_confirm"
    # 取回后这一轮的回复交给 sync_turn：原文按字面隐去（「（HF token）：`…`」规则不一定认得出）
    p.sync_turn("谢谢", final + f"\n密码）：{HF}", session_id="s1")
    assert HF not in open(env.journal, encoding="utf-8").read()
    assert [r["outcome"] for r in vault.audit_rows() if r["action"] == "reveal"][:3] == \
        ["need_confirm", "ok", "need_confirm"]


def test_wrong_codes_lock(vault, env, monkeypatch, no_approval):  # noqa: F811
    p, vid = _setup(env, vault, monkeypatch)
    _ask(p, 2, "HF token 告诉我")
    _call(p, "nyx_secret_reveal", id=f"v{vid}")
    for t in range(3, 6):
        _ask(p, t, f"确认 v{vid} 000000")
    _ask(p, 6, "HF token 告诉我")
    assert "连错" in _call(p, "nyx_secret_reveal", id=f"v{vid}")["reason"]


def test_model_cannot_skip_the_human(vault, env, monkeypatch, no_approval):  # noqa: F811
    """模型自己在工具参数里写「主人已经确认了」没用；确认码只认主人这一轮的原话。"""
    p, vid = _setup(env, vault, monkeypatch)
    _ask(p, 2, "HF token 告诉我")
    _call(p, "nyx_secret_reveal", id=f"v{vid}")
    r = _call(p, "nyx_secret_reveal", id=f"v{vid}", confirmed=True, code="123456")
    assert r["status"] == "need_confirm"


def test_hermes_approval(vault, env, monkeypatch, approval):  # noqa: F811
    calls, state = approval
    p, vid = _setup(env, vault, monkeypatch)
    _ask(p, 2, "HF token 发我")
    assert _call(p, "nyx_secret_reveal", id=f"v{vid}")["status"] == "queued"
    assert calls[0]["tool"] == "nyx_secret_reveal" and HF not in calls[0]["reason"] and HF[-4:] in calls[0]["reason"]
    state["answer"] = False
    assert "没有批准" in _call(p, "nyx_secret_reveal", id=f"v{vid}")["reason"]
    assert len({c["rule_key"] for c in calls}) == 2, "每次新的 rule_key：「永远允许」不会让以后都不问"
    state["bypass"] = True                          # 主人用 /yolo 关掉了命令审批：不算同意交出密钥
    assert _call(p, "nyx_secret_reveal", id=f"v{vid}")["status"] == "need_confirm"
    assert len(calls) == 2


def test_rate_limit(vault, env, monkeypatch, approval):  # noqa: F811
    p, vid = _setup(env, vault, monkeypatch)
    for t in range(vault.RATE_HOUR):
        _ask(p, 10 + t, "HF token 发我")
        assert _call(p, "nyx_secret_reveal", id=f"v{vid}")["status"] == "queued"
    _ask(p, 20, "HF token 发我")
    assert "一小时内" in _call(p, "nyx_secret_reveal", id=f"v{vid}")["reason"]


def test_cli_only_and_missing_plugin(vault, env, monkeypatch, approval):  # noqa: F811
    p, vid = _setup(env, vault, monkeypatch, mode="cli")
    _ask(p, 2, "HF token 发我")
    r = _call(p, "nyx_secret_reveal", id=f"v{vid}")
    assert r["status"] == "use_cli" and r["command"].endswith(f"show v{vid}")
    vault._reset_for_tests()
    monkeypatch.setenv("NYX_VAULT_REVEAL", "direct")             # 直达但没启用配套插件：不退到经模型
    r = _call(p, "nyx_secret_reveal", id=f"v{vid}")
    assert r["status"] == "use_cli" and "nyx-reveal" in r["note"]


def test_llm_mode_returns_value(vault, env, monkeypatch, approval):  # noqa: F811
    p, vid = _setup(env, vault, monkeypatch, mode="llm", hook=False)
    _ask(p, 2, "HF token 发我")
    r = _call(p, "nyx_secret_reveal", id=f"v{vid}")
    assert r["status"] == "value" and r["value"] == HF
    p.sync_turn("好", f"你的 token：{HF}", session_id="s1")
    assert HF not in open(env.journal, encoding="utf-8").read()


def test_plugin_registers_hook(vault):
    import nexsandglass_hermes_reveal as plug
    hooks = {}

    class Ctx:
        def register_hook(self, name, cb):
            hooks[name] = cb
    plug.register(Ctx())
    assert vault.hook_registered() and set(hooks) == {"transform_llm_output"}
    assert hooks["transform_llm_output"](response_text="hi", session_id="s", model="m", platform="") is None
    src = open(plug.__file__, encoding="utf-8").read()
    assert "register_memory_provider" not in src and "MemoryProvider" not in src, \
        "Hermes 看到这两个词会把插件当成记忆提供器、不加载钩子"


# ── 生命周期 ──────────────────────────────────────────────────

def test_forget_restore_purge(vault, env, monkeypatch, approval):  # noqa: F811
    from nexsandglass.core import erasure, memid, quarantine
    p, vid = _setup(env, vault, monkeypatch)
    mid = memid.get_conn().execute("SELECT mem_id FROM memories WHERE text LIKE ?", (f"%#v{vid}]%",)).fetchone()[0]
    assert erasure.forget({"mem_id": mid}, apply=True)["ok"]
    _ask(p, 2, "HF token 发我")
    assert "隔离区" in _call(p, "nyx_secret_reveal", id=f"v{vid}")["reason"]
    assert erasure.restore(mid)["ok"]
    assert _call(p, "nyx_secret_reveal", id=f"v{vid}")["status"] == "queued"
    assert erasure.forget({"mem_id": mid}, apply=True)["ok"]
    rep = quarantine.purge([mid], apply=True)
    assert rep["vault_deleted"] == [f"v{vid}"] and vault.entry(vid) is None
    # 当场真删（不经隔离区）
    _sync(p, f"GitHub token 是 {GH}")
    gid = vault.entries()[0].id
    mid2 = memid.get_conn().execute("SELECT mem_id FROM memories WHERE text LIKE ?", (f"%#v{gid}]%",)).fetchone()[0]
    rep = erasure.forget({"mem_id": mid2}, apply=True, mode="purge")
    assert rep["vault_deleted"] == [f"v{gid}"] and vault.entries() == []
    assert memid.health()["checks"]["vault"]["ok"]


def test_shared_secret_survives_forgetting_one_copy(vault, env):  # noqa: F811
    from nexsandglass.core import erasure, memid
    p = _provider()
    _sync(p, f"key 是 {HF}")
    _sync(p, f"再说一遍，key 是 {HF}")
    vid = vault.entries()[0].id
    mids = [r[0] for r in memid.get_conn().execute("SELECT mem_id FROM memories WHERE text LIKE ?", (f"%#v{vid}]%",))]
    assert len(mids) == 2
    erasure.forget({"mem_id": mids[0]}, apply=True, mode="purge")
    assert vault.entry(vid).state == "live", "另一条记忆还提到它"


def test_cli(vault, env, monkeypatch, capsys):  # noqa: F811
    from nexsandglass import doctor
    from nexsandglass import vault as cli
    p = _provider()
    _sync(p, f"帮我配一下 Hugging Face，key 是 {HF}")
    vid = vault.entries()[0].id
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert f"v{vid}" in out and HF not in out
    assert cli.main(["show", f"v{vid}"]) == 0
    assert capsys.readouterr().out.strip() == HF
    assert vault.audit_rows()[0]["path"] == "cli"
    assert cli.main(["status"]) == 0 and "开" in capsys.readouterr().out
    n = len(vault.entries())
    assert cli.main(["rekey"]) == 0
    capsys.readouterr()
    assert len(vault.entries()) == n and vault.get(vid) == HF and vault.put("huggingface", "HF token", HF) == vid
    doctor.main([])
    assert "vault" in capsys.readouterr().out
    assert cli.main(["delete", f"v{vid}", "--yes"]) == 0 and vault.entry(vid) is None


def test_scrub_to_vault(vault, env, monkeypatch):  # noqa: F811
    from nexsandglass.core import memid, secret_scrub
    _seed_before_upgrade(env, monkeypatch)
    import shutil
    shutil.rmtree(os.path.join(str(env.home), "backups"))
    dry = secret_scrub.run(apply=False, to_vault=True)
    assert dry["to_vault"] >= 3 and vault.entries() == []
    rep = secret_scrub.run(apply=True, to_vault=True)
    assert rep["clean"], rep["on_disk"]
    labels = {e.label for e in vault.entries()}
    assert {"HF token", "密码", "私钥"} <= labels and "GitHub token" not in labels, "隔离区里那条的日志已经抹掉了"
    assert memid.verify()["ok"], "日志和中枢拿到同一个编号"
    hf = next(e for e in vault.entries() if e.label == "HF token")
    assert vault.get(hf.id) == HF and hf.state == "live"
    monkeypatch.setenv("NYX_VAULT", "off")
    with pytest.raises(RuntimeError, match="保险箱用不了"):
        secret_scrub.run(apply=False, to_vault=True)


def test_residual_scan_ignores_vault_metadata(tmp_path):
    """保险箱的库每条明文存着类别、标签、末四位：「password密码4567」按规则会被认成密码。
    新版 SQLite（3.53）的页面布局下 scrub --apply 十有八九报「磁盘上还有」（v7.15.4 修）。
    保险箱自己的文件只查原文：原文真在里面照样报。"""
    from nexsandglass.core import secret_scrub, secret_vault
    meta = b"\x00\x01password\xe5\xaf\x86\xe7\xa0\x814567\x00ct-bytes\x00"
    vdb = tmp_path / secret_vault.DB_NAME
    other = tmp_path / "other.db"
    vdb.write_bytes(meta)
    other.write_bytes(meta)
    assert secret_scrub._is_vault_file(vdb.name) and secret_scrub._is_vault_file(vdb.name + "-wal")
    assert not secret_scrub._is_vault_file(other.name)
    assert secret_scrub._bytes_scan(str(other), "on") >= 1, "普通文件里这样的一段照样算"
    assert secret_scrub._bytes_scan(str(vdb), "on", rules=False) == 0
    leaked = "hunter2-Plaintext-77"
    vdb.write_bytes(meta + leaked.encode())
    assert secret_scrub._bytes_scan(str(vdb), "on", [leaked], rules=False) == 1, "原文在保险箱库里也要报"
