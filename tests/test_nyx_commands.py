"""/nyx 会话内命令与暂停记录（v7.20.0）"""
import json
from types import SimpleNamespace

import pytest

from tests.test_hermes_provider import env, _reindex, _provider  # noqa: F401  复用隔离目录

import nexsandglass_hermes_commands as cmds
from nexsandglass.core import recording


@pytest.fixture(autouse=True)
def _never_leave_paused():
    # 暂停状态是数据目录里的一个文件（整个测试会话共用）：哪个测试中途失败，也不能让后面的测试都不记录
    yield
    recording.resume()


def _src(chat_type="dm", user_id="42", user_name="owner", is_bot=False, platform="telegram"):
    return SimpleNamespace(chat_type=chat_type, user_id=user_id, user_name=user_name, is_bot=is_bot,
                           platform=SimpleNamespace(value=platform))


def _journal(e):
    import os
    return open(e.journal, encoding="utf-8").read() if os.path.exists(e.journal) else ""


def test_pause_stops_recording_and_resume_restarts(env):  # noqa: F811
    p = _provider()
    p.sync_turn("我对花生过敏。", "记下了。", session_id="s")
    assert "paused" in cmds.handle("pause", source=None).lower()
    assert "paused" in cmds.handle("status", source=None).lower()
    p.sync_turn("这句不该被记住：我的私事", "好的。", session_id="s")
    p.on_memory_write("add", "user", "mirrored note")
    assert "私事" not in _journal(env) and "mirrored note" not in _journal(env)
    assert "recording paused" in p.prefetch("花生")                       # agent 知道现在没在记
    cmds.handle("resume", source=None)
    assert recording.paused() is None
    p.sync_turn("恢复之后这句会被记住。", "好的。", session_id="s")
    assert "恢复之后" in _journal(env) and "recording paused" not in p.prefetch("花生")


def test_explicit_requests_still_work_while_paused(env):  # noqa: F811
    p = _provider()
    p.sync_turn("我住在杭州。", "好的。", session_id="s")
    recording.pause()
    try:
        out = json.loads(p.handle_tool_call("nyx_forget", {"contains": "住在杭州", "confirm": True}))
        assert out["status"] == "quarantined"                             # 明确要求的遗忘照常
    finally:
        recording.resume()


def test_forget_last_quarantines_this_chats_last_turn(env):  # noqa: F811
    p = _provider()
    p.sync_turn("第一轮：我喜欢手冲咖啡。", "好的，记住了你喜欢手冲。", session_id="chat-1")
    p.sync_turn("第二轮：我对花生过敏。", "记下了，以后会避开那类坚果。", session_id="chat-1")
    out = cmds.handle("forget-last", source=None, session_id="chat-1")
    assert "2" in out and "第二轮：我对花生过敏" in out and "手冲" not in out   # 回显忘掉的那句，方便核对
    from nexsandglass.core import memid
    live = [r[0] for r in memid.get_conn().execute("SELECT text FROM memories WHERE deleted_at IS NULL")]
    assert any("手冲咖啡" in x for x in live) and not any("花生过敏" in x for x in live)
    assert "花生" not in p.system_prompt_block()                         # 画像跟着变


def test_who_can_do_what(monkeypatch):
    assert cmds.allowed("pause", None) is None                            # 本机
    assert cmds.allowed("pause", _src()) is None                          # 私聊，没配主人
    assert cmds.allowed("pause", _src(chat_type="group"))                 # 群里谁都能打：不行
    assert cmds.allowed("status", _src(chat_type="group")) is None        # 看状态可以
    assert cmds.allowed("forget-last", _src(is_bot=True))                 # 机器人不行
    monkeypatch.setattr(cmds, "_owners", lambda: {"telegram:42"})
    assert cmds.allowed("pause", _src(chat_type="group")) is None         # 配了主人：群里主人可以
    assert cmds.allowed("pause", _src(user_id="99", user_name="guest"))   # 别人不行
    local = _src(user_id=None, user_name=None, platform="local")          # 本机会话经共享网关转来
    assert cmds.allowed("forget-last", local) is None and cmds.allowed("pause", None) is None


def test_status_and_help(env):  # noqa: F811
    p = _provider()
    p.sync_turn("以后回复我都用中文。", "好的。", session_id="s")
    st = cmds.handle("status", source=None)
    assert "Nyx" in st and ("memories" in st or "条记忆" in st)
    assert "/nyx pause" in cmds.handle("", source=None)
    assert "/nyx pause" in cmds.handle("whatever", source=None)


def test_register_adds_the_slash_command():
    seen = {}

    class Ctx:
        def register_command(self, name, handler, description="", args_hint=""):
            seen.update(name=name, handler=handler, hint=args_hint)

    cmds.register(Ctx())
    assert seen["name"] == "nyx" and seen["handler"] is cmds.handle and "pause" in seen["hint"]
