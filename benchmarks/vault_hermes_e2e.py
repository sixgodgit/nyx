#!/usr/bin/env python3
"""benchmarks/vault_hermes_e2e.py — 密钥保险箱在**真实 Hermes** 里的端到端验收（v7.15.0，发布前手动跑）

单测用的是 Hermes 的替身；这里装着真的 hermes-agent，用真的 AIAgent、真的插件加载、真的审批、真的会话库，
模型换成本地假服务（只在 127.0.0.1 上），记下它收到的每一个请求体。CI 不装 hermes-agent，所以不在 CI 里跑。

    python3 -m venv /tmp/hv && /tmp/hv/bin/pip install "hermes-agent==0.19.0" -e ".[vault]"
    /tmp/hv/bin/python benchmarks/vault_hermes_e2e.py

流程：
  0. 之前某次对话里主人贴过一个 HF token（用 nyx 直接写进同一个数据目录）
  1. 新会话：「把我之前给你的 HF token 发我」→ 模型调 nyx_secret_reveal → Hermes 终端审批（模拟主人按 once）
     → 模型只拿到句柄 → nyx-reveal 钩子在回复发出前换成原文
  2. approvals.mode: off（主人关掉了命令审批）→「再发我一次」→ nyx 改走确认码（钩子追加在回复末尾）
     → 主人回复确认短语 → 原文
验收：发给模型的请求里从没出现过原文；确认码在主人打出来之前没发给过模型；审批提示里没有原文；
      Hermes 的会话库存的是句柄（正向对照）、按原始字节找不到原文；nyx 数据目录按原始字节找不到原文。

v7.15.0 实测（hermes-agent 0.19.0）：发给模型 7 个请求，含原文 0 个；会话库 9 行，带句柄 4 行，带原文 0 行。
注意：Hermes 0.19.0 只按目录找记忆插件，所以这里先把 nexsandglass_hermes 拷进 $HERMES_HOME/plugins/nyx/。
"""
import hashlib
import http.server
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT = os.dup(1)                     # Hermes 的 quiet_mode 会接管 sys.stdout / stderr：结果直接写原来的 fd


def say(*a):
    os.write(_OUT, (" ".join(str(x) for x in a) + "\n").encode("utf-8"))


TMP = tempfile.mkdtemp(prefix="nyx-vault-e2e-")
HH, NB = os.path.join(TMP, "hermes"), os.path.join(TMP, "nb")
os.environ.update(HERMES_HOME=HH, NEXSANDBASE_HOME=NB, XDG_CONFIG_HOME=os.path.join(TMP, "config"),
                  NYX_VAULT="on", NYX_VAULT_REVEAL="direct", NYX_EMBED="off", HERMES_INTERACTIVE="1")
os.makedirs(HH)
with open(os.path.join(HH, "config.yaml"), "w") as f:
    f.write("memory:\n  provider: nyx\nplugins:\n  enabled:\n    - nyx-reveal\n")
shutil.copytree(os.path.join(REPO, "nexsandglass_hermes"), os.path.join(HH, "plugins", "nyx"),
                ignore=shutil.ignore_patterns("__pycache__"))

# 运行时拼出来的 token：源码里没有完整 token
_A = "Ab3dE5gH7jK9mN1pQ3sT5vW7yZ9bC2dF4hJ6kL8xR0"
_h = hashlib.sha256(b"hf_e2e").hexdigest()
HF = "hf_" + "".join(_A[int(_h[i % 64], 16) + (i % 3) * 13] for i in range(34))

SEEN = []


def _reply(body):
    """假模型：要密钥就调 nyx_secret_reveal(v1)；拿到工具结果后照着回。"""
    msgs = body.get("messages") or []
    tools = {t.get("function", {}).get("name") for t in body.get("tools") or []}
    last = msgs[-1] if msgs else {}
    if last.get("role") == "tool":
        r = json.loads(last.get("content") or "{}")
        if r.get("status") == "queued":
            return {"content": f"给你：{r['handle']}"}
        if r.get("status") == "need_confirm":
            return {"content": "需要你确认一下：照着这条回复末尾那句话回复我就行。"}
        return {"content": "取不到：" + json.dumps(r, ensure_ascii=False)}
    user = next((m["content"] if isinstance(m["content"], str) else json.dumps(m["content"], ensure_ascii=False)
                 for m in reversed(msgs) if m.get("role") == "user"), "")
    if "nyx_secret_reveal" in tools and ("token" in user.lower() or "确认 v" in user):
        return {"tool_calls": [{"id": f"call_{len(SEEN)}", "type": "function",
                                "function": {"name": "nyx_secret_reveal", "arguments": json.dumps({"id": "v1"})}}]}
    return {"content": "好的。"}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, data, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(json.dumps({"data": [{"id": "fake-model", "object": "model"}]}).encode())

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        SEEN.append(raw)
        body = json.loads(raw or "{}")
        msg = _reply(body)
        fin = "tool_calls" if msg.get("tool_calls") else "stop"
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if body.get("stream"):
            delta = {"role": "assistant", **({"content": msg["content"]} if msg.get("content") else {})}
            if msg.get("tool_calls"):
                delta["tool_calls"] = [{**tc, "index": i} for i, tc in enumerate(msg["tool_calls"])]
            chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": fin}], "usage": usage}]
            data = "".join(f"data: {json.dumps({'id': 'x', 'object': 'chat.completion.chunk', 'model': 'fake-model', **c})}\n\n"
                           for c in chunks) + "data: [DONE]\n\n"
            self._send(data.encode(), "text/event-stream")
            return
        self._send(json.dumps({"id": "x", "object": "chat.completion", "model": "fake-model", "usage": usage,
                               "choices": [{"index": 0, "message": {"role": "assistant", **msg},
                                            "finish_reason": fin}]}).encode())


def scan(root):
    hits = []
    for d, _, fs in os.walk(root):
        for fn in fs:
            try:
                if HF.encode() in open(os.path.join(d, fn), "rb").read():
                    hits.append(os.path.relpath(os.path.join(d, fn), TMP))
            except OSError:
                pass
    return hits


def main() -> int:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"

    from nexsandglass.core import secret_vault
    from nexsandglass.core.memory_provider import NexSandglassProvider
    p0 = NexSandglassProvider()
    p0.initialize("earlier", hermes_home=HH, platform="cli")
    p0.sync_turn(f"帮我配一下 Hugging Face，key 是 {HF}", "好的，配好了，之后需要随时问我", session_id="earlier")
    assert [e.id for e in secret_vault.entries()] == [1]

    from hermes_state import SessionDB
    from run_agent import AIAgent
    from tools import approval, terminal_tool
    prompts = []

    def human(command, description, **kw):        # 模拟主人在终端里按 once
        prompts.append(description)
        return "once"

    agent = AIAgent(base_url=base, api_key="fake-key", model="fake-model", quiet_mode=True, skip_context_files=True,
                    max_iterations=6, enabled_toolsets=["memory"], session_db=SessionDB())
    assert agent._memory_manager is not None, "Hermes 没加载 nyx 记忆插件"
    assert secret_vault.hook_registered(), "Hermes 没加载 nyx-reveal（hermes plugins enable nyx-reveal）"
    terminal_tool.set_approval_callback(human)

    r1 = agent.run_conversation("把我之前给你的 HF token 发我")
    assert HF in r1["final_response"], r1["final_response"]
    assert prompts and all(HF not in p for p in prompts), prompts
    say("1. 审批：", prompts[0], "→ 回复里拿到原文")

    with open(os.path.join(HH, "config.yaml"), "a") as f:     # 主人关掉了命令审批
        f.write("approvals:\n  mode: \"off\"\n")
    assert approval.is_approval_bypass_active()
    n = len(SEEN)
    r2 = agent.run_conversation("再发我一次 HF token", conversation_history=r1["messages"])
    m = re.search(r"确认 v1 (\d{6})", r2["final_response"])
    assert m and len(prompts) == 1, r2["final_response"]
    assert not any(m.group(1) in s for s in SEEN[n:]), "确认码在主人打出来之前就发给了模型"
    r3 = agent.run_conversation(f"确认 v1 {m.group(1)}", conversation_history=r2["messages"])
    assert HF in r3["final_response"], r3["final_response"]
    say("2. 审批被关掉 → 确认码（模型没见过）→ 回复里拿到原文")

    time.sleep(2)                                              # Hermes 在后台线程上 sync_turn
    leaks = [i for i, s in enumerate(SEEN) if HF in s]
    rows = [r[0] for r in sqlite3.connect(os.path.join(HH, "state.db")).execute("SELECT content FROM messages")
            if r[0]]
    with_handle, with_token = sum("⟦nyx:v1⟧" in r for r in rows), sum(HF in r for r in rows)
    say(f"3. 发给模型 {len(SEEN)} 个请求，含原文 {len(leaks)} 个")
    say(f"4. Hermes 会话库 {len(rows)} 行：带句柄 {with_handle}，带原文 {with_token}")
    hh, nb = scan(HH), scan(NB)
    say(f"5. 按原始字节：Hermes 目录 {hh or '没有'}；nyx 数据目录 {nb or '没有'}")
    say("6. 审计：", [(r["action"], r["outcome"]) for r in reversed(secret_vault.audit_rows())])
    ok = not leaks and with_handle > 0 and not with_token and not hh and not nb
    say("结论：" + ("通过" if ok else "不通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except BaseException as e:
        import traceback
        say("".join(traceback.format_exception(type(e), e, e.__traceback__)))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    os._exit(code)
