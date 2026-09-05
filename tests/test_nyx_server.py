"""Phase 1 验收测试 — nyx_server 只读端点。
运行：python3 tests/test_nyx_server.py
用 TestClient（无需起真实端口）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    """用隔离的临时数据目录，避免污染真实 ~/.hermes/nexsandglass。"""
    tmp = tempfile.mkdtemp(prefix="nyx-server-test-")
    # 造一份 sandglass.txt
    with open(os.path.join(tmp, "sandglass.txt"), "w", encoding="utf-8") as f:
        f.write("2026-09-06 01:00:00 | user | 甲\n")
        f.write("2026-09-06 01:00:00 | user | 甲\n")  # 重复
        f.write("2026-09-06 02:00:00 | user | 乙\n")
    # personas
    os.makedirs(os.path.join(tmp, "persona"), exist_ok=True)
    with open(os.path.join(tmp, "persona", "persona.md"), "w", encoding="utf-8") as f:
        f.write("# 画像\n- 职业：工程师\n")
    # engram_store
    with open(os.path.join(tmp, "engram_store.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"ts":"2026-09-04 02:02:39","content":"未闭合问题","type":"contradiction","status":"conflict_candidate"}\n')
        f.write('{"ts":"2026-09-05 00:00:00","content":"稳定事实","type":"semantic","status":"active"}\n')
    # emotion_log
    with open(os.path.join(tmp, "emotion_log.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"ts":"2026-09-06 01:00:00","mood":"开心"}\n')

    monkeypatch.setenv("NEXSANDBASE_HOME", tmp)

    # 关键：重新加载模块（因为 NEXSANDBASE 在 import 时即求值）
    for mod in list(sys.modules):
        if mod.startswith("nexsandglass.nyx_server") or mod == "nexsandglass.nyx_server":
            del sys.modules[mod]
    from nexsandglass import nyx_server
    from fastapi.testclient import TestClient
    return TestClient(nyx_server.app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_today(client):
    r = client.get("/api/today")
    assert r.status_code == 200
    data = r.json()
    assert "summary_stats" in data
    # open_loops 应抓到 contradiction 那条
    assert any("未闭合问题" in l["content"] for l in data["open_loops"])
    # persona 摘录
    assert "工程师" in data["persona_excerpt"]


def test_memories_no_type(client):
    r = client.get("/api/memories", params={"limit": 10})
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_memories_by_type(client):
    r = client.get("/api/memories", params={"type": "semantic", "limit": 10})
    assert r.status_code == 200
    items = r.json()["items"]
    assert all(i.get("type") == "semantic" for i in items)


def test_persona(client):
    r = client.get("/api/persona")
    assert r.status_code == 200
    assert "工程师" in r.json()["raw_md"]


def test_engram(client):
    r = client.get("/api/engram", params={"limit": 10})
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_emotions(client):
    r = client.get("/api/emotions")
    assert r.status_code == 200
    assert len(r.json()["items"]) >= 1


# ── Phase 2 情感入口 ──────────────────────────────────────

def test_stamps_list(client):
    r = client.get("/api/stamps")
    assert r.status_code == 200
    stamps = r.json()["stamps"]
    assert len(stamps) == 12
    assert any(s["icon"] == "🔥" for s in stamps)


def test_write_diary(client):
    r = client.post("/api/diary", json={"text": "今天去海牙逛了逛"})
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "ok"
    assert "📖 [日记]" in data["text"]
    # 写后能读到
    mems = client.get("/api/memories", params={"limit": 100}).json()["items"]
    assert any("海牙" in m["text"] for m in mems)


def test_diary_empty(client):
    r = client.post("/api/diary", json={"text": "   "})
    assert r.status_code == 400


def test_diary_too_long(client):
    r = client.post("/api/diary", json={"text": "好" * 2100})
    assert r.status_code == 400


def test_write_stamp(client):
    r = client.post("/api/stamp", json={"title": "🔥", "text": "今天项目上线了"})
    assert r.status_code == 201
    data = r.json()
    assert data["recorded_mood"] == "热烈"
    assert "🔥" in data["icon"]
    # emotion_log 应新增一条
    emos = client.get("/api/emotions").json()["items"]
    assert any(e["mood"] == "热烈" for e in emos)


def test_stamp_by_name(client):
    r = client.post("/api/stamp", json={"title": "焦虑"})
    assert r.status_code == 201
    assert r.json()["icon"] == "😰"


def test_stamp_unknown(client):
    r = client.post("/api/stamp", json={"title": "🤖"})
    assert r.status_code == 400
