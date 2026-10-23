"""tests/test_secret_filter.py — 密钥、密码不进记忆（v7.14.0）

nyx 把对话原样写进日志。用户在聊天里贴一个 token，它就以明文留在日志、中枢、索引、备份里，
还会被召回、塞回提示词（多人会话里塞给别人看），语义检索走 API 时还会被发给嵌入服务。

这里验收三件事：
  1. 识别：已知格式的 token / 私钥 / 连接串 / 赋值 / 「密码是 …」都认得出；普通句子一个不误伤
  2. 入口：从 Hermes 每一个写入钩子、MCP、编排层进来的密钥，数据目录里（按原始字节找）一处都没有；
     发给嵌入后端的文本里也没有
  3. 存量：升级之前已经存下来的，`scrub --apply` 之后按原始字节扫整个数据目录也找不到，
     而中枢 / 日志 / 索引仍然一致、隔离区还原仍然有效

测试里的 token 在运行时拼出来，源码里不放完整的字面量（免得被当成真泄漏拦下）。
"""
import hashlib
import json
import os

import pytest

from tests.test_hermes_provider import _provider, _reindex, env  # noqa: F401  复用隔离目录

_ALNUM = "Ab3dE5gH7jK9mN1pQ3sT5vW7yZ9bC2dF4hJ6kL8xR0"


def _tok(prefix, n, salt=""):
    h = hashlib.sha256((prefix + salt).encode()).hexdigest()
    body = "".join(_ALNUM[int(h[i % 64], 16) + (i % 3) * 13] for i in range(n))
    return prefix + body


HF = _tok("hf_", 34)
GH = _tok("gh" + "p_", 36)
SK = _tok("sk-", 32, "deepseek")
PEM = "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAE\nQyNTUxOQAAACDx\n-----END OPENSSH " + "PRIVATE KEY-----"
PW = "Tr0ub4dor3x"

POSITIVE = {
    "HF token": f"帮我配一下，key 是 {HF} 谢谢",
    "GitHub token": f"token: {GH}",
    "GitHub fine-grained": "github_" + "pat_" + _tok("", 82, "pat"),
    "API key (sk-)": f"deepseek 的 key：{SK}",
    "Anthropic": "sk-ant-" + "api03-" + _tok("", 40, "ant"),
    "OpenRouter": "sk-or-v1-" + _tok("", 48, "or"),
    "PyPI": "pypi-" + "AgEIcHlwaS5vcmc" + _tok("", 50, "pypi"),
    "AWS": "AKIA" + "IOSFODNN7" + "EXAMPLE",
    "Google": "AIza" + _tok("", 35, "g"),
    "Slack": "xox" + "b-1234567890-" + _tok("", 24, "s"),
    "Telegram": "bot 1234567890:" + "AAH" + _tok("", 32, "tg"),
    "GitLab": "glpat-" + _tok("", 20, "gl"),
    "npm": "npm_" + _tok("", 36, "npm"),
    "Stripe": "sk_" + "live_" + _tok("", 24, "stripe"),
    "SendGrid": "SG." + _tok("", 22, "sg1") + "." + _tok("", 43, "sg2"),
    "Discord": "M" + _tok("", 23, "d1") + "." + _tok("", 6, "d2") + "." + _tok("", 27, "d3"),
    "Slack webhook": "https://hooks.slack.com/" + "services/T0" + _tok("", 8, "w1") + "/B0" + _tok("", 8, "w2") + "/" + _tok("", 24, "w3"),
    "JWT": "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0In0" + "." + _tok("", 24, "jwt"),
    "私钥": PEM,
    "连接串": "连这个 postgres://admin:S3cretPass99@db.example.com:5432/app",
    "环境变量": "export OPENAI_API_KEY=" + _tok("", 20, "env"),
    "小写键值": "api_key = '" + _tok("", 20, "kv") + "'",
    "Bearer": "Authorization: Bearer " + _tok("", 24, "bearer"),
    "中文密码": f"我的wifi密码是 {PW}，别忘了",
    "中文密码（数字）": "银行卡密码：123456",
    "中文密码（无分隔）": "wifi密码12345678",
    "英文密码": f"my password is {PW}.",
    "JSON 里的密码": '{"text": "wifi密码是abc12345"}',
    "私密标记": "这个 <private>身份证号 110101199003071234</private> 别记",
    "被截断的 token": "前面一大段……key 是 " + HF[:14],
}
NEGATIVE = [
    "密码是什么来着？我忘了", "the password is required", "password: required", "pwd: /home/user",
    "OLDPWD=/home/user/project", "会议 10:30 开始，地点 3 楼", "sk-learn 是个库", "pip install sk-video",
    "token 用完了，明天再充", "我在用 hf_transfer 加速下载", "结尾是普通词 hf_transfer", "hf_hub_download(repo_id='x')",
    "set OPENAI_API_KEY=${OPENAI_API_KEY}", "api_key = your_api_key_here_placeholder", "请重置密码", "密码 reset 一下",
    "版本 v7.13.9，PR #16", "用户的预算是 2000 欧", "ts=2026-09-13 03:00:00", "x = a == b and c != d",
    "https://example.com/path:with:colons@notcreds", "AKIAA is not a key", "邮箱 me@example.com 端口 8080:8080",
    "函数 gho_helper_function_name", "secret 是个英文单词", "我的 key 放在保险柜里",
]


def _secret_part(text):
    """正例里真正的秘密那一段（用来断言「隐去后找不到它」）。"""
    for marker in ("key 是 ", "token: ", "key：", "postgres://admin:", "密码是 ", "密码：", "密码", "password is ",
                   "密码是", "<private>", "Bearer ", "= '", "KEY="):
        if marker in text:
            rest = text.split(marker, 1)[1]
            return rest.split()[0].split("，")[0].split("@")[0].rstrip(".'\"}") if marker != "<private>" else "110101"
    return text.strip().split()[-1]


@pytest.mark.parametrize("name", list(POSITIVE))
def test_secrets_are_recognised(name):
    from nexsandglass.core import secret_filter
    t = POSITIVE[name]
    r = secret_filter.scrub(t, "on")
    assert r.changed, name
    part = _secret_part(t)
    assert part[:8] not in r.text or len(part) < 8, (name, r.text)
    assert "[REDACTED" not in r.text, "占位符不能长得像遗忘标记（整条记录会被索引跳过）"
    assert secret_filter.scrub(r.text, "on").text == r.text, "幂等"


@pytest.mark.parametrize("text", NEGATIVE)
def test_ordinary_text_is_untouched(text):
    from nexsandglass.core import secret_filter
    assert secret_filter.scrub(text, "on").text == text


def test_placeholder_keeps_last_four_for_tokens_only():
    from nexsandglass.core import secret_filter
    assert secret_filter.clean(f"key {HF}", "on") == f"key [已隐去: HF token …{HF[-4:]}]"
    assert secret_filter.clean(f"密码：{PW}", "on") == "密码：[已隐去: 密码]"
    assert "b3Bl" not in secret_filter.clean(PEM, "on")


def test_levels_and_line_count():
    from nexsandglass.core import secret_filter
    t = f"key {HF}，wifi密码是 {PW}"
    assert secret_filter.clean(t, "off") == t
    tok_only = secret_filter.clean(t, "tokens")
    assert HF not in tok_only and PW in tok_only, "tokens 档不动密码"
    assert HF not in secret_filter.clean(t, "on") and PW not in secret_filter.clean(t, "on")
    r = secret_filter.scrub("前文\n" + PEM + "\n后文", "on", keep_lines=True)
    assert r.text.count("\n") == ("前文\n" + PEM + "\n后文").count("\n"), "改写已有日志时行数不能变"


# ── 入口：从哪儿进来都不落盘 ──────────────────────────────────────

def _on_disk(home, needles):
    """按原始字节在整个数据目录里找（含库文件、WAL、空闲页）。"""
    hits = {}
    for root, _, files in os.walk(str(home)):
        for name in files:
            p = os.path.join(root, name)
            try:
                data = open(p, "rb").read()
            except OSError:
                continue
            low = data.lower()                      # 倒排索引里的词元是小写的
            for n in needles:
                if n.encode() in data or n.lower().encode() in low:
                    hits.setdefault(os.path.relpath(p, str(home)), []).append(n[:6])
    return hits


class _Spy:
    """记下发给嵌入后端的每一段文本。"""
    name = "fake:spy-v1"
    ready = True

    def __init__(self):
        from tests.fixtures import fake_embed
        self.inner, self.seen = fake_embed.ConceptBag(), []

    def encode(self, texts):
        self.seen.extend(texts)
        return self.inner.encode(texts)


@pytest.fixture()
def spy(env, monkeypatch):  # noqa: F811
    from nexsandglass.core import semantic
    for k in ("NYX_EMBED", "NYX_EMBED_PROVIDER", "NYX_EMBED_MODEL", "EMBEDDING_API_URL", "NYX_SECRET_FILTER"):
        monkeypatch.delenv(k, raising=False)
    s = _Spy()
    semantic.set_provider(s)
    yield s
    assert semantic.wait_idle()
    semantic.set_provider(None)


def test_every_write_hook_keeps_secrets_out(env, spy):  # noqa: F811
    from nexsandglass.core import erasure, memid, semantic
    from nexsandglass.runtime.orchestrator import get_orchestrator
    p = _provider()
    p.sync_turn(f"帮我配一下 Hugging Face，key 是 {HF}，wifi密码是 {PW}", f"好的，已经用 {HF} 配好了")
    p.sync_turn("这是我的部署私钥，记一下：\n" + PEM, "收到")
    p.on_memory_write("add", "user", f"GitHub token {GH}")
    p.on_delegation(f"用 {SK} 调接口", f"调用成功（key {SK}）")
    get_orchestrator().observe(f"我常用的 DeepSeek key 是 {SK}", source="mcp")     # MCP memory_observe 走的就是这里
    q = _provider()
    q.on_session_end([{"role": "user", "content": "很长的一段话" * 82 + f" key {HF}"}])   # 截断点正好切在 token 中间
    p.prefetch(f"我的 key {HF} 还能用吗")                                            # 查询也不发出去
    semantic.index_pending(limit=None)
    _reindex(env)

    needles = [HF, GH, SK, PW, "b3BlbnNzaC1rZXkt", HF[:14]]
    assert _on_disk(env.home, needles) == {}
    assert not [t for t in spy.seen if any(n in t for n in needles)], "发给嵌入后端的文本里有密钥"
    assert erasure.verify_erasure([HF, GH, SK, PW])["clean"]
    # 占位符留着末 4 位：agent 还能说出「你给过一个 HF token（…xxxx）」
    journal = open(env.journal, encoding="utf-8").read()
    assert f"[已隐去: HF token …{HF[-4:]}]" in journal and "[已隐去: 私钥]" in journal
    assert memid.verify()["ok"]


def test_filter_can_be_turned_off(env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("NYX_EMBED", "off")
    monkeypatch.setenv("NYX_SECRET_FILTER", "off")
    _provider().sync_turn(f"key 是 {HF}", "")
    assert HF in open(env.journal, encoding="utf-8").read()


# ── 存量：升级之前存下来的 ───────────────────────────────────────

def _seed_before_upgrade(env, monkeypatch):  # noqa: F811
    """模拟升级之前：过滤关着，密钥进了日志、中枢、隔离区、engram、信念、备份。"""
    from nexsandglass.core import erasure, forensics, memid
    from nexsandglass.engram.loops.temporal_fact import ensure_temporal_columns, record_fact
    from nexsandglass.features import weavethread
    import sqlite3
    monkeypatch.setenv("NYX_SECRET_FILTER", "off")
    p = _provider()
    p.sync_turn(f"帮我配一下，key 是 {HF}，wifi密码是 {PW}", "好的，配好了")
    p.sync_turn("部署私钥：\n" + PEM + "\n记一下", "收到")
    p.sync_turn(f"GitHub token 是 {GH}，这条以后可以忘掉", "好")
    p.sync_turn("我住在鹿特丹", "记住了")
    mid = memid.get_conn().execute("SELECT mem_id FROM memories WHERE text LIKE '%GitHub token%'").fetchone()[0]
    assert erasure.forget({"mem_id": mid}, apply=True, mode="quarantine")["ok"]         # 原文进了隔离区
    with open(os.path.join(str(env.home), "engram_store.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-01 10:00:00", "content": f"用户的 HF token 是 {HF}", "type": "fact"},
                           ensure_ascii=False) + "\n")
    ensure_temporal_columns(weavethread._DB)
    c = sqlite3.connect(weavethread._DB)
    record_fact(c, "用户", "常用", f"DeepSeek key {SK}", now="2026-10-01T10:00:00Z")
    c.commit()
    c.close()
    from nexsandglass.features import shadow_sand
    with shadow_sand._db_lock:                            # 实体抽取把引号里的密码单独存成了实体名：裸字符串，规则认不出
        db = shadow_sand._get_conn()
        shadow_sand._ensure_memid_cols(db)
        db.execute("INSERT INTO entities (name, line_nums, mem_ids) VALUES (?,?,?)", (PW, "1", ""))
        db.execute("INSERT OR REPLACE INTO fact_tags (line_num, category, tags) VALUES (?,?,?)", (1, "general", f"wifi,{PW}"))
        db.commit()
    _reindex(env)
    forensics._backup(str(env.home))                     # 一份旧的修复备份（里面的 .db nyx 不改，只报路径）
    monkeypatch.delenv("NYX_SECRET_FILTER")
    return mid


def test_existing_secrets_are_found_and_scrubbed(env, monkeypatch):  # noqa: F811
    from nexsandglass.core import erasure, forensics, memid, secret_scrub
    monkeypatch.setenv("NYX_EMBED", "off")
    mid = _seed_before_upgrade(env, monkeypatch)
    needles = [HF, GH, SK, PW, "b3BlbnNzaC1rZXkt"]
    assert _on_disk(env.home, needles), "前提：升级前确实存下来了"

    # doctor 能看到；只读检查不改任何东西、不输出原文
    h = memid.health()["checks"]["secrets"]
    assert not h["ok"] and h["found"] >= 3
    j0 = open(env.journal, "rb").read()
    dry = secret_scrub.run(apply=False)
    assert open(env.journal, "rb").read() == j0
    assert dry["found"] >= 6 and not dry["clean"]
    assert not any(n in json.dumps(dry, ensure_ascii=False) for n in needles), "报告里不能有原文"

    n_lines = len(j0.splitlines())
    rep = secret_scrub.run(apply=True)
    assert len(open(env.journal, "rb").read().splitlines()) == n_lines, "行数不变（行号是别处的主键）"
    left = _on_disk(env.home, needles)
    assert set(left) <= {k for k in left if k.startswith("backups" + os.sep) and k.endswith(".db")}, left
    assert set(rep["on_disk"]) == set(left), "复查报出来的就是还剩的那几个文件"
    assert rep["stores"]["shadow_sand.db:entities"]["found"] == 1, "裸密码当实体名：按字面找出来"

    # 隐去之后一切照常：中枢与日志一致、索引对得上、内容体检干净、隔离区还原有效
    assert memid.verify()["ok"] and forensics.diagnose()["clean"]
    c = memid.health()["checks"]
    assert c["fts_index"]["ok"] and c["erasure_integrity"]["ok"] and c["secrets"]["ok"]
    r = erasure.restore(mid)
    assert r.get("ok"), r
    restored = memid.get(mid)["text"]
    assert "GitHub token 是 [已隐去: GitHub token" in restored and GH not in restored

    # 删掉旧备份之后，整个数据目录一处都没有
    import shutil
    shutil.rmtree(os.path.join(str(env.home), "backups"))
    assert _on_disk(env.home, needles) == {}
    assert secret_scrub.run(apply=False)["clean"]


def test_scrub_cli(env, monkeypatch, capsys):  # noqa: F811
    from nexsandglass import doctor, scrub
    monkeypatch.setenv("NYX_EMBED", "off")
    _seed_before_upgrade(env, monkeypatch)
    import shutil
    shutil.rmtree(os.path.join(str(env.home), "backups"))
    assert scrub.main([]) == 1
    out = capsys.readouterr().out
    assert "只读" in out and "--apply" in out and HF not in out and PW not in out
    doctor.main([])
    assert "nexsandglass.scrub" in capsys.readouterr().out
    assert scrub.main(["--apply"]) == 0
    assert "一处都没有了" in capsys.readouterr().out
    assert scrub.main([]) == 0
