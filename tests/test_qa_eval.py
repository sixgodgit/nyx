"""tests/test_qa_eval.py — 端到端问答评测框架（v7.15.0）

验证的是**框架代码**，不产出任何分数：本地起一个假的 OpenAI 兼容服务（只在 127.0.0.1 上），
作答 = 把注入的记忆原样念出来，裁判 = 标准答案是不是出现在回答里。用仓库里的小样本把三步完整走一遍：
  - 喂给作答模型的正是 Hermes 默认设置下 prefetch 注入的那段文字（带日期）；none 组什么都不给
  - 裁判提示词与 LongMemEval 官方 evaluate_qa.py 逐字一致；_abs 题用拒答模板
  - 断点续跑：第二次运行一次模型都不调
  - 结果文件里没有 key；LoCoMo 结果里不存题目与标准答案原文
真实分数要用真实模型跑：见 benchmarks/qa_eval.py。
"""
import http.server
import json
import os
import re
import sys
import threading

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "benchmarks"))

import qa_eval as Q  # noqa: E402

FAKE_KEY = "fake-key-for-tests-0000"


class _Fake(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Fake.seen.append({"auth": self.headers.get("Authorization"), "body": body})
        msg = body["messages"][-1]["content"]
        if body["max_tokens"] == 10:                       # 裁判
            if "unanswerable" in msg:
                resp = msg.split("Model Response:")[1]
                out = "yes" if "don't know" in resp.lower() else "no"
            else:
                gold = re.search(r"(?:Correct Answer|Rubric): (.*?)\n\nModel Response: ", msg, re.S).group(1)
                resp = msg.split("Model Response:")[1]
                out = "yes" if gold.strip().lower() in resp.lower() else "no"
        else:                                              # 作答：念出注入的记忆
            mem = [l for l in msg.splitlines() if l.startswith("- ")]
            out = " ".join(mem) if mem else "I don't know."
        data = json.dumps({"choices": [{"message": {"content": out}}],
                           "usage": {"prompt_tokens": len(msg) // 4, "completion_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def fake_llm(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for k in ("NYX_JUDGE_API_URL", "NYX_JUDGE_API_KEY", "NYX_JUDGE_MODEL", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NYX_EVAL_API_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions")
    monkeypatch.setenv("NYX_EVAL_API_KEY", FAKE_KEY)
    monkeypatch.setenv("NYX_EVAL_MODEL", "fake-model")
    monkeypatch.setenv("NYX_EMBED", "off")
    _Fake.seen = []
    yield _Fake
    srv.shutdown()


def test_judge_prompts_are_the_official_ones():
    # 逐字核对几处官方原文（LongMemEval src/evaluation/evaluate_qa.py）
    assert Q._J_TEMPORAL.count("do not penalize off-by-one errors for the number of days") == 1
    assert "the response should be considered as correct as long as the updated answer is the required answer" \
        in Q._J_UPDATE
    assert "The model does not need to reflect all the points in the rubric" in Q._J_PREF
    assert Q.judge_prompt("longmemeval", "multi-session", "q", "a", "r", True).startswith(
        "I will give you an unanswerable question")
    assert Q.judge_prompt("locomo", "temporal", "q", "a", "r", False) == Q._J_TEMPORAL.format("q", "a", "r")
    assert Q.judge_prompt("locomo", "multi-hop", "q", "a", "r", False) == Q._J_DEFAULT.format("q", "a", "r")
    with pytest.raises(NotImplementedError):
        Q.judge_prompt("longmemeval", "made-up-type", "q", "a", "r", False)
    assert Q.judged_correct(" Yes.") and not Q.judged_correct("No")


def test_longmemeval_end_to_end(fake_llm, tmp_path):
    out, cache = tmp_path / "out.json", tmp_path / "cache.jsonl"
    argv = ["longmemeval", os.path.join(_ROOT, "tests", "fixtures", "longmemeval_mini.json"), "--workers", "2",
            "--conditions", "nyx,none", "--cache", str(cache), "--out", str(out), "--contexts",
            str(tmp_path / "ctx.jsonl")]
    assert Q.main(argv) == 0
    rep = json.load(open(out, encoding="utf-8"))
    nyx, none = rep["scores"]["nyx"], rep["scores"]["none"]
    assert nyx["_answerable"]["acc"] > none["_answerable"]["acc"], rep["scores"]
    assert rep["meta"]["answer_model"] == rep["meta"]["judge_model"] == "fake-model"
    # 作答模型看到的就是 Hermes 默认 prefetch 注入的那段（带日期的「## Nyx 记忆」块）；none 组什么都没有
    answers = [s["body"]["messages"][-1]["content"] for s in fake_llm.seen if s["body"]["max_tokens"] == 512]
    assert any("## Nyx 记忆" in a and "- [" in a for a in answers)
    assert any("(no memories recalled)" in a for a in answers)
    assert all(s["auth"] == "Bearer " + FAKE_KEY for s in fake_llm.seen)
    # key 不进任何文件
    for p in (out, cache, tmp_path / "ctx.jsonl"):
        assert FAKE_KEY not in open(p, encoding="utf-8").read()
    # 断点续跑：再跑一次，一次模型都不调
    n = len(fake_llm.seen)
    assert Q.main(argv) == 0
    assert len(fake_llm.seen) == n
    assert json.load(open(out, encoding="utf-8"))["meta"]["llm_calls"] == 0


def test_locomo_end_to_end_keeps_dataset_text_out_of_results(fake_llm, tmp_path):
    out = tmp_path / "out.json"
    data = os.path.join(_ROOT, "tests", "fixtures", "locomo_mini.json")
    assert Q.main(["locomo", data, "--workers", "1", "--conditions", "nyx,none", "--cache",
                   str(tmp_path / "c.jsonl"), "--out", str(out)]) == 0
    rep = json.load(open(out, encoding="utf-8"))
    assert rep["scores"]["nyx"]["_overall"]["acc"] >= rep["scores"]["none"]["_overall"]["acc"]
    sample = json.load(open(data, encoding="utf-8"))[0]
    blob = open(out, encoding="utf-8").read()
    for q in sample["qa"]:
        assert q["question"] not in blob, "LoCoMo 是 CC BY-NC 数据：结果里不存题目原文"
    assert all("hypothesis" not in it for it in rep["items"])


def test_missing_model_config_fails_loudly(monkeypatch):
    for k in ("NYX_EVAL_API_URL", "NYX_EVAL_API_KEY", "NYX_EVAL_MODEL", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit, match="没有配置"):
        Q.endpoint("eval")
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    ep = Q.endpoint("judge")
    assert ep["url"] == "https://api.deepseek.com/chat/completions" and ep["model"] == "deepseek-chat"
