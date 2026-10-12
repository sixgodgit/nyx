"""tests/test_locomo_harness.py — LoCoMo 检索评测框架 + v7.13.5 召回聚合的回归

LoCoMo 真实数据是 CC BY-NC 4.0，不进仓库；这里用我写的同格式小样本（两个人、三个会话）：
  - 框架本身：日期解析、证据编号规范化（数据里真有「D:11:26」「D8:6; D9:17」这种写法）、
    BM25 参照、指标定义（对抗题与无证据题不计）
  - 端到端：子进程、独立目录、走 provider.sync_turn 写入，召回映射回轮次编号
  - v7.13.5 的回归：影子沙（实体名命中、与问句其余部分无关）不能排在相关度排序前面。
    以前问「Ava 什么时候去的陶艺课」，前几名是「Hey Ava!」这种打招呼的句子
小样本是我写的，不是基准；数字只用来防回归，不用来报分。
"""

import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "benchmarks"))

import locomo_eval as L  # noqa: E402


def test_parse_locomo_dates():
    assert L.parse_date("1:56 pm on 8 May, 2023").isoformat() == "2023-05-08T13:56:00"
    assert L.parse_date("12:05 am on 1 January, 2024").isoformat() == "2024-01-01T00:05:00"
    assert L.parse_date("12:30 pm on 15 Sep, 2023").isoformat() == "2023-09-15T12:30:00"
    with pytest.raises(ValueError):
        L.parse_date("yesterday")


def test_evidence_ids_are_normalized():
    assert L.evidence(["D1:3"]) == ["D1:3"]
    assert L.evidence(["D8:6; D9:17"]) == ["D8:6", "D9:17"]
    assert L.evidence(["D:11:26"]) == ["D11:26"]
    assert L.evidence(["D9:1 D4:4 D4:6", "D9:1"]) == ["D9:1", "D4:4", "D4:6"]
    assert L.evidence(["D"]) == [] and L.evidence(None) == []


def test_turn_text_keeps_speaker_and_caption():
    t = {"speaker": "Ava", "text": " made a bowl ", "blip_caption": "a blue bowl"}
    assert L.turn_text(t) == "Ava: made a bowl [shared a photo: a blue bowl]"


def test_sessions_sorted_numerically():
    conv = {"session_10": [], "session_10_date_time": "1:00 pm on 2 May, 2023",
            "session_2": [], "session_2_date_time": "1:00 pm on 1 May, 2023", "speaker_a": "A"}
    assert [n for n, _, _ in L.sessions(conv)] == [2, 10]


def test_bm25_ranks_rare_terms_first():
    docs = ["Ava: hey Ben", "Ava: I went to a pottery class", "Ben: hey Ava, pottery is fun", "Ben: marathon"]
    assert L.BM25(docs).rank("When did Ava go to the pottery class?")[0] == 1


def test_score_definitions():
    res = [{"sample_id": "x", "n_turns": 3, "ingest_s": 0, "questions": [
        {"category": 4, "evidence": ["D1:1"], "ranked": ["D1:1", "D1:2"], "bm25": ["D1:2"], "unmapped": 0, "recall_ms": 1},
        {"category": 1, "evidence": ["D1:1", "D2:1"], "ranked": ["D2:1", "D9:9", "D1:1"], "bm25": [], "unmapped": 0,
         "recall_ms": 2},
        {"category": 5, "evidence": ["D1:1"], "ranked": [], "bm25": [], "unmapped": 0, "recall_ms": 1},
        {"category": 4, "evidence": [], "ranked": [], "bm25": [], "unmapped": 0, "recall_ms": 1},
    ]}]
    s = L.score(res)
    assert s["_meta"]["questions_scored"] == 2
    assert s["_meta"]["skipped"] == {"adversarial": 1, "no evidence": 1}
    o = s["_overall"]["nyx"]
    assert o["recall@1"] == 0.75 and o["hit@1"] == 1.0 and o["all@1"] == 0.5
    assert o["all@5"] == 1.0
    assert s["1 multi-hop"]["nyx"]["recall@1"] == 0.5 and s["1 multi-hop"]["bm25"]["hit@5"] == 0.0


@pytest.fixture(scope="module")
def mini_run(tmp_path_factory):
    dst = tmp_path_factory.mktemp("locomo") / "mini.json"
    env = {k: v for k, v in os.environ.items() if k not in ("NYX_UNDERSTAND_LLM", "NYX_NOW")}
    r = subprocess.run([sys.executable, os.path.join(_ROOT, "benchmarks", "locomo_eval.py"),
                        os.path.join(_ROOT, "tests", "fixtures", "locomo_mini.json"),
                        "--no-semantic", "--workers", "1", "--json", str(dst)],
                       capture_output=True, text=True, timeout=600, env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.load(open(dst, encoding="utf-8"))


def test_harness_end_to_end(mini_run):
    meta = mini_run["scores"]["_meta"]
    assert meta["errors"] == 0 and meta["questions_scored"] == 4 and meta["turns"] == 13
    assert meta["skipped"] == {"adversarial": 1, "no evidence": 1}
    assert meta["unmapped_objects"] == 0, "召回对象必须都能映射回轮次，否则指标是假的"
    conv = mini_run["results"][0]
    assert conv["n_memories"] >= conv["n_turns"]
    for q in conv["questions"]:
        assert len(q["ranked"]) == len(set(q["ranked"]))


def test_relevance_outranks_entity_mentions(mini_run):
    """v7.13.5：影子沙按「问句里的词是某个实体名的一部分」取行、按信任分排，与问句其余部分无关，
    以前在聚合里排第一 —— 问 Ava 的事，前几名是「Hey Ava!」「Hey Ben!」。LoCoMo 上 recall@5 0.046。"""
    qs = mini_run["results"][0]["questions"]
    top1 = [(q["ranked"] or [None])[0] for q in qs]
    assert top1[0] == "D1:3", qs[0]["ranked"][:5]            # 什么时候去的陶艺课
    assert top1[1] == "D2:2", qs[1]["ranked"][:5]            # 猫叫什么
    assert top1[3] == "D3:2", qs[3]["ranked"][:5]            # 半马跑了多久
    assert set(qs[2]["ranked"][:3]) >= {"D1:3", "D3:3"}, qs[2]["ranked"][:5]   # 多跳：两句都在前 3
