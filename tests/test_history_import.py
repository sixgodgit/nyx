"""hermes nyx import（v7.20.0）：把以前在 Hermes 里的对话导进 Nyx

会话库按 Hermes 的 state.db 结构造（sessions / messages 里导入用得到的列；真实 Hermes 生成的库在
tests/integration 之外手工核对过）。
"""
import json
import sqlite3
from datetime import datetime

import pytest

from tests.test_hermes_provider import env, _reindex, _provider  # noqa: F401  复用隔离目录

from nexsandglass import history_import as hi

_SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, chat_type TEXT, display_name TEXT,
    parent_session_id TEXT, started_at REAL NOT NULL, ended_at REAL, title TEXT);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT, tool_calls TEXT, timestamp REAL NOT NULL, observed INTEGER DEFAULT 0,
    _compressed_summary INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, api_content TEXT,
    display_kind TEXT);
"""


def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp()


@pytest.fixture()
def hermes_db(tmp_path):
    path = tmp_path / "state.db"
    c = sqlite3.connect(path)
    c.executescript(_SCHEMA)

    def session(sid, source, start, **kw):
        c.execute("INSERT INTO sessions(id, source, user_id, chat_type, display_name, parent_session_id, started_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)", (sid, source, kw.get("user_id"), kw.get("chat_type"),
                                                  kw.get("display_name"), kw.get("parent"), ts(start)))

    def msg(sid, role, content, when, **kw):
        c.execute("INSERT INTO messages(session_id, role, content, tool_calls, timestamp, observed, _compressed_summary, "
                  "active, api_content, display_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (sid, role, content, kw.get("tool_calls"), ts(when), int(kw.get("observed", 0)),
                   int(kw.get("summary", 0)), int(kw.get("active", 1)), kw.get("api_content"), kw.get("kind")))

    session("cli-1", "cli", "2026-01-10 09:00")
    msg("cli-1", "user", "我住在杭州，在一家做游戏的公司上班。", "2026-01-10 09:00",
        api_content="我住在杭州。\n<memory-context>\n- 当时注入的旧记忆\n</memory-context>")
    msg("cli-1", "assistant", "了解，游戏行业挺有意思的。", "2026-01-10 09:01")
    msg("cli-1", "user", "我对花生过敏，吃一点就会起疹子。", "2026-01-10 09:05")
    msg("cli-1", "assistant", None, "2026-01-10 09:05", tool_calls='[{"id": "c1"}]')
    msg("cli-1", "tool", '{"ok": true}', "2026-01-10 09:05")
    msg("cli-1", "assistant", "记下了，以后推荐吃的会避开那类坚果。", "2026-01-10 09:06")
    msg("cli-1", "user", "Continue from where you stopped.", "2026-01-10 09:07", kind="auto_continue")
    msg("cli-1", "user", "[Summary] The user lives in Hangzhou.", "2026-01-10 09:08", summary=1)
    msg("cli-1", "user", "这句被回退掉了", "2026-01-10 09:09", active=0)
    msg("cli-1", "user", json.dumps([{"type": "text", "text": "看看这张图里的猫"}, {"type": "image_url", "image_url": {}}],
                                    ensure_ascii=False), "2026-01-11 20:00")
    msg("cli-1", "assistant", "是一只橘猫，看起来很精神。", "2026-01-11 20:01")
    session("dm-2", "telegram", "2026-02-03 21:00", chat_type="dm", user_id="42", display_name="owner")
    msg("dm-2", "user", "以后回复我都用中文，而且不要用 emoji。", "2026-02-03 21:00")
    msg("dm-2", "assistant", "明白，之后都用中文、不用 emoji。", "2026-02-03 21:01")
    session("grp-3", "telegram", "2026-02-05 12:00", chat_type="group", user_id="77")
    msg("grp-3", "user", "我住在北京，周末一起吃饭吗", "2026-02-05 12:00")
    session("sub-4", "subagent", "2026-01-10 09:03", parent="cli-1")
    msg("sub-4", "user", "Research peanut-free snacks and report back.", "2026-01-10 09:03")
    session("cron-5", "cron", "2026-02-06 07:00")
    msg("cron-5", "user", "Daily briefing: summarise the news.", "2026-02-06 07:00")
    c.commit()
    c.close()
    return str(path)


def _journal(e):
    return open(e.journal, encoding="utf-8").read() if e.journal and __import__("os").path.exists(e.journal) else ""


def test_lists_only_your_own_conversations(hermes_db):
    db = hi.HermesDB(hermes_db)
    sessions = {s["id"]: s for s in db.sessions()}
    ok = [sid for sid, s in sessions.items() if hi.importable(s) is None]
    assert sorted(ok) == ["cli-1", "dm-2"]                          # 群聊、子 agent、定时任务不导
    assert sessions["cli-1"]["messages"] == 6                      # 不算工具、续写、摘要、回退
    assert hi.importable(sessions["dm-2"], {"99"})                 # 配了主人，别人的私聊不导
    assert hi.importable(sessions["dm-2"], {"42"}) is None
    assert hi.importable(sessions["dm-2"], {"telegram:42"}) is None


def test_only_the_original_words(hermes_db):
    texts = [m["text"] for m in hi.HermesDB(hermes_db).messages("cli-1")]
    assert texts[0] == "我住在杭州，在一家做游戏的公司上班。"           # content，不是带注入的 api_content
    assert "看看这张图里的猫" in texts                                   # 多模态只取文字
    assert not any("Continue from" in x or "[Summary]" in x or "回退" in x or '"ok"' in x for x in texts)


def test_import_keeps_time_and_skips_what_nyx_has(env, hermes_db):  # noqa: F811
    from nexsandglass.core import clock
    p = _provider()
    clock.set_global(datetime(2026, 2, 3, 21, 0))
    p.sync_turn("以后回复我都用中文，而且不要用 emoji。", "明白，之后都用中文、不用 emoji。", session_id="live")
    clock.set_global(None)

    assert hi.main(["--db", hermes_db, "--all", "--yes"]) == 0
    j = _journal(env)
    assert "2026-01-10 09:00:00 | user | 我住在杭州" in j and "2026-01-11 20:00:00 | user | 看看这张图里的猫" in j
    assert j.count("不要用 emoji") == 1                                # 已经有的那句不再写一遍
    for gone in ("当时注入的旧记忆", "Continue from", "[Summary]", "回退", "北京", "peanut", "Daily briefing"):
        assert gone not in j, gone
    before = j
    assert hi.main(["--db", hermes_db, "--all", "--yes"]) == 0      # 再跑：没有新的
    assert _journal(env) == before
    _reindex(env)
    block = p.system_prompt_block()
    assert "花生过敏" in block and "不要用 emoji" in block


def test_older_imported_facts_do_not_override_newer_ones(env, hermes_db):  # noqa: F811
    from nexsandglass.core import clock
    p = _provider()
    clock.set_global(datetime(2026, 10, 20, 10, 0))
    p.sync_turn("我上周已经搬到上海了，现在在上海上班。", "好的。", session_id="live")
    clock.set_global(None)
    assert hi.main(["--db", hermes_db, "--all", "--yes"]) == 0       # 1 月的「我住在杭州」后写进来
    cur = json.loads(p.handle_tool_call("nyx_belief", {"relation": "lives_in"}))["facts"]
    assert [f["object"] for f in cur] == ["上海"]
    hist = json.loads(p.handle_tool_call("nyx_belief", {"relation": "lives_in", "history": True}))["facts"]
    hz = [f for f in hist if f["object"] == "杭州"]
    assert hz and hz[0]["valid_until"]                               # 杭州补成了历史
    block = p.system_prompt_block()
    assert block.index("搬到上海") < block.index("住在杭州")           # 画像按说话时间，不按写入先后


def test_several_people_in_private_chats_needs_owner_ids(tmp_path):
    path = tmp_path / "state.db"
    c = sqlite3.connect(path)
    c.executescript(_SCHEMA)
    for sid, uid in (("dm-a", "1"), ("dm-b", "2")):
        c.execute("INSERT INTO sessions(id, source, user_id, chat_type, display_name, started_at) VALUES (?, 'telegram', ?, "
                  "'dm', ?, ?)", (sid, uid, f"user{uid}", ts("2026-03-01 10:00")))
        c.execute("INSERT INTO messages(session_id, role, content, timestamp) VALUES (?, 'user', '我对花生过敏', ?)",
                  (sid, ts("2026-03-01 10:00")))
    c.commit()
    c.close()
    assert hi.main(["--db", str(path), "--all", "--yes"]) == 1       # 分不清哪个是你：不全导


def test_pick_by_number_range_and_id_prefix():
    rows = [{"n": i, "id": f"sess-{i}"} for i in range(1, 9)]
    assert [r["n"] for r in hi._pick("1 3 5-7", rows)] == [1, 3, 5, 6, 7]
    assert [r["n"] for r in hi._pick("sess-8", rows)] == [8]
    assert hi._pick("n", rows) == [] and len(hi._pick("a", rows)) == 8


def test_not_a_hermes_database(tmp_path):
    path = tmp_path / "other.db"
    sqlite3.connect(path).execute("CREATE TABLE t (x)").connection.close()
    assert hi.main(["--db", str(path), "--list"]) == 2
