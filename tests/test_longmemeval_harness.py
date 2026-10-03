"""tests/test_longmemeval_harness.py — LongMemEval 评测框架 + v7.12 英文检索修复

真实数据（HuggingFace）在这个环境里拿不到；这里用同格式的小样本验证：
  - 框架本身：日期解析、轮次配对、官方 recall_any / recall_all 定义（abstention 不计）
  - 端到端：每题独立子进程、独立目录，走 provider.sync_turn 写入，召回映射回会话
  - 英文检索修复的回归：虚词 / 跨词碎片不再淹没排序
小样本是我写的，不是基准；数字只用来防回归，不用来报分。
"""

import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "benchmarks"))

import longmemeval_eval as L  # noqa: E402


def test_parse_longmemeval_dates():
    assert L.parse_date("2023/05/20 (Sat) 02:21").isoformat() == "2023-05-20T02:21:00"
    assert L.parse_date("2023-05-20").isoformat() == "2023-05-20T00:00:00"
    with pytest.raises(ValueError):
        L.parse_date("yesterday")


def test_turn_pairing_keeps_has_answer_and_odd_turns():
    turns = [{"role": "user", "content": "a", "has_answer": True}, {"role": "assistant", "content": "b"},
             {"role": "user", "content": "c"}, {"role": "user", "content": "d"},
             {"role": "assistant", "content": "e", "has_answer": True}, {"role": "assistant", "content": "f"}]
    assert L._pairs(turns) == [("a", "b", True, False), ("c", "", False, False),
                               ("d", "e", False, True), ("", "f", False, False)]


def test_score_follows_official_definitions():
    rs = [
        {"question_id": "1", "question_type": "t", "abstention": False, "answer_session_ids": ["a", "b"],
         "ranked_sessions": ["a", "x", "b"], "answer_turn_hits": [True], "n_answer_turns": 1, "recall_ms": 1},
        {"question_id": "2", "question_type": "t", "abstention": False, "answer_session_ids": ["c"],
         "ranked_sessions": ["x", "y"], "answer_turn_hits": [False], "n_answer_turns": 1, "recall_ms": 3},
        {"question_id": "3_abs", "question_type": "t", "abstention": True, "answer_session_ids": [],
         "ranked_sessions": [], "answer_turn_hits": [], "n_answer_turns": 0, "recall_ms": 2},
    ]
    s = L.score(rs)
    row = s["t"]
    assert row["n"] == 2 and s["_meta"]["abstention_skipped"] == 1
    assert row["recall_any@1"] == 0.5 and row["recall_all@1"] == 0.0
    assert row["recall_all@3"] == 0.5 and row["turn_hit@10"] == 0.5


@pytest.fixture(scope="module")
def mini_runs(tmp_path_factory):
    out = {}
    for name in ("longmemeval_mini", "longmemeval_mini_zh"):
        dst = tmp_path_factory.mktemp("lme") / f"{name}.json"
        env = {k: v for k, v in os.environ.items() if k not in ("NYX_UNDERSTAND_LLM", "NYX_NOW")}
        r = subprocess.run([sys.executable, os.path.join(_ROOT, "benchmarks", "longmemeval_eval.py"),
                            os.path.join(_ROOT, "tests", "fixtures", f"{name}.json"),
                            "--workers", "2", "--json", str(dst)],
                           capture_output=True, text=True, timeout=600, env=env)
        assert r.returncode == 0, r.stderr[-2000:]
        out[name] = json.load(open(dst, encoding="utf-8"))
    return out


def test_harness_end_to_end(mini_runs):
    for name, d in mini_runs.items():
        meta = d["scores"]["_meta"]
        assert meta["errors"] == 0 and meta["abstention_skipped"] == 1, name
        assert meta["unmapped_objects"] == 0, "召回对象必须都能映射回会话，否则指标是假的"
        for r in d["results"]:
            assert len(r["ranked_sessions"]) == len(set(r["ranked_sessions"])) <= r["n_sessions"]
            assert r["n_memories"] >= r["n_sessions"]          # 每个会话至少用户那句落了沙


def test_english_ranking_regression(mini_runs):
    """v7.11：问「我的狗叫什么」排第一的是讲书的会话；问食谱温度排第一的也是它。"""
    top1 = {r["question_id"]: (r["ranked_sessions"] or [None])[0]
            for r in mini_runs["longmemeval_mini"]["results"]}
    assert top1["q_dog"] == "s_dog"
    assert top1["q_recipe"] == "s_recipe"
    assert top1["q_books"] == "s_book"


def test_chinese_ranking_unchanged(mini_runs):
    top1 = {r["question_id"]: (r["ranked_sessions"] or [None])[0]
            for r in mini_runs["longmemeval_mini_zh"]["results"]}
    assert top1["zq_allergy"] == "z_allergy"
    assert top1["zq_food"] == "z_food"
    assert top1["zq_job"] == "z_job2"


def test_query_tokens_english_and_chinese():
    from nexsandglass.features.sandglass_vault import _query_tokens
    en = _query_tokens("What is the name of my dog?")
    assert en == {"name", "dog"}, en                       # 虚词与跨词碎片（th/is/he…）都没了
    assert {"company", "comp", "pany"} <= _query_tokens("Which company do I work for?")
    assert _query_tokens("what is it") == {"what", "is", "it"}   # 全是虚词时退回原词
    zh = _query_tokens("我上个月搬到了哪里")
    assert zh == {"我上", "上个", "个月", "月搬", "搬到", "到了", "了哪", "哪里"}
    assert _query_tokens("猫") == {"猫"}


def test_harness_semantic_switch(tmp_path):
    """语义这一路在评测框架里真的开得起来、关得掉（概念词袋只证明管道，不是质量）。"""
    out = {}
    for mode, extra in (("on", {"NYX_EMBED_PROVIDER": "tests.fixtures.fake_embed:provider"}),
                        ("off", {})):
        env = {k: v for k, v in os.environ.items()
               if k not in ("NYX_UNDERSTAND_LLM", "NYX_NOW", "NYX_EMBED", "NYX_EMBED_PROVIDER")}
        env.update(extra, PYTHONPATH=_ROOT)
        dst = tmp_path / f"{mode}.json"
        cmd = [sys.executable, os.path.join(_ROOT, "benchmarks", "longmemeval_eval.py"),
               os.path.join(_ROOT, "tests", "fixtures", "longmemeval_mini_zh.json"),
               "--workers", "2", "--json", str(dst)] + (["--no-semantic"] if mode == "off" else [])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        assert r.returncode == 0, r.stderr[-2000:]
        out[mode] = json.load(open(dst, encoding="utf-8"))
    assert out["on"]["scores"]["_meta"]["semantic_backend"] == ["fake:concept-bag-v1"]
    assert out["off"]["scores"]["_meta"]["semantic_backend"] == ["off"]
    assert all(r["semantic"]["pending"] == 0 and r["semantic"]["indexed"] > 0 for r in out["on"]["results"])
    top1 = lambda d, q: next(r["ranked_sessions"][:1] for r in d["results"] if r["question_id"] == q)
    assert top1(out["off"], "zq_cat") != ["z_cat"]          # 「猫」≠「橘猫」，词法召不回
    assert top1(out["on"], "zq_cat") == ["z_cat"]
