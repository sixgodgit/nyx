"""hermes nyx search / show / forget / restore / edit（v7.20.0）和共用的 core/lookup.py"""
import json
from argparse import Namespace
from datetime import datetime

import pytest

from tests.test_hermes_provider import env, _reindex, _provider  # noqa: F401  复用隔离目录

from nexsandglass import memory_cli as cli
from nexsandglass.core import lookup


def _say(p, when, user, reply="好的。"):
    from nexsandglass.core import clock
    clock.set_global(datetime.strptime(when, "%Y-%m-%d %H:%M"))
    try:
        p.sync_turn(user, reply, session_id="s")
    finally:
        clock.set_global(None)


@pytest.fixture()
def story(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-01-10 09:00", "我住在杭州，在一家做游戏的公司上班。", "了解，游戏行业挺有意思的。")
    _say(p, "2026-03-05 20:00", "我三月份在考虑搬家，想搬到上海去。", "搬家要提前规划好时间。")
    _say(p, "2026-03-20 21:00", "我对花生过敏，吃一点就会起疹子。", "记下了，以后推荐吃的会避开那类坚果。")
    _say(p, "2026-10-20 10:00", "我已经搬到上海了，新家离公司很近。", "恭喜，通勤轻松多了。")
    _reindex(env)
    return p


def _run(capsys, *argv):
    rc = cli.main(list(argv))
    out = capsys.readouterr()
    return rc, out.out + out.err


def _id_of(text):
    hits = lookup.containing(text)
    assert hits, text
    return hits[0]["mem_id"]


def test_date_bounds():
    assert lookup.bound("2026-03") == "2026-03-01 00:00:00"
    assert lookup.bound("2026-03", end=True) == "2026-03-31 23:59:59"
    assert lookup.bound("2024-02", end=True) == "2024-02-29 23:59:59"
    assert lookup.bound("2026", end=True) == "2026-12-31 23:59:59"
    assert lookup.bound("2026-3-5") == "2026-03-05 00:00:00" and lookup.bound("") is None
    for bad in ("2026-13", "2026-02-30", "last week", "03/05/2026"):
        with pytest.raises(lookup.BadDate):
            lookup.bound(bad)


def test_search_shows_date_and_who(story, capsys):
    rc, out = _run(capsys, "search", "花生")
    assert rc == 0 and "2026-03-20 21:00" in out and "you" in out and "花生过敏" in out
    items = lookup.search("上海")
    said = [i for i in items if i.get("mem_id")]
    assert {i["date"][:7] for i in said} >= {"2026-03", "2026-10"}
    assert all(i["who"] in ("user", "assistant") and i["trust"] for i in said)


def test_search_within_dates(story, capsys):
    march = lookup.search("上海", since="2026-03", until="2026-03")
    assert march and all(i["ts"].startswith("2026-03") for i in march)
    assert any("考虑搬家" in i["text"] for i in march)
    assert not lookup.search("上海", since="2026-11-01")                     # 那之后没说过
    rc, out = _run(capsys, "search", "--since", "2026-10-01")                # 不写词：这段时间说的
    assert rc == 0 and "新家离公司很近" in out and "花生" not in out
    rc, out = _run(capsys, "search", "上海", "--since", "March")
    assert rc == 2 and "not a date" in out
    rc, out = _run(capsys, "search")
    assert rc == 2


def test_range_search_finds_old_memories_recall_ranks_low(env):  # noqa: F811
    """日期范围内的那句即使排不进整体召回的前几条，也要找得到。"""
    p = _provider()
    for d in range(1, 26):
        _say(p, f"2026-05-{d:02d} 09:00", f"今天的周报写完了，第 {d} 周的项目进度正常，周报已经发给经理。")
    _say(p, "2026-02-14 20:00", "二月中旬那次周报里我提过想换个项目组。")
    _reindex(env)
    feb = lookup.search("周报", limit=5, since="2026-02-01", until="2026-02-28")
    assert [i["ts"][:10] for i in feb] == ["2026-02-14"]


def test_show_full_text_and_neighbours(story, capsys):
    mid = _id_of("考虑搬家")
    rc, out = _run(capsys, "show", mid, "--around", "1")
    assert rc == 0 and "我三月份在考虑搬家，想搬到上海去。" in out and "2026-03-05 20:00:00" in out
    assert "before:" in out and "after:" in out and "搬家要提前规划" in out
    rc, out = _run(capsys, "show", mid, "--json")
    data = json.loads(out)
    assert data["memory"]["who"] == "user" and data["memory"]["status"] == "live"
    assert data["after"][0]["who"] == "assistant"
    rc, out = _run(capsys, "show", "m_doesnotexist0000")
    assert rc == 1


def test_forget_restore_round_trip(story, capsys):
    mid = _id_of("花生过敏")
    rc, out = _run(capsys, "forget", mid)                                     # 不是终端、没加 --yes：不忘
    assert rc == 1 and "--yes" in out and lookup.get(mid)["status"] == "live"
    rc, out = _run(capsys, "forget", mid, "--yes")
    assert rc == 0 and "quarantine" in out
    assert lookup.get(mid)["status"] == "quarantined" and lookup.get(mid)["text"].startswith("我对花生过敏")
    assert "花生" not in story.system_prompt_block()
    assert not any(i.get("mem_id") == mid for i in lookup.search("花生"))
    rc, out = _run(capsys, "restore")
    assert mid in out and "我对花生过敏" in out
    rc, out = _run(capsys, "show", mid)
    assert "quarantine" in out and "restore" in out
    rc, out = _run(capsys, "restore", mid)
    assert rc == 0 and "restored" in out and lookup.get(mid)["status"] == "live"
    assert any(i.get("mem_id") == mid for i in lookup.search("花生"))
    assert "花生" in story.system_prompt_block()


def test_forget_contains_and_erase_now(story, capsys):
    rc, out = _run(capsys, "forget", "--contains", "上海", "--yes")
    assert rc == 0 and not lookup.containing("上海")
    rc, out = _run(capsys, "forget", "--contains", "x")
    assert rc == 2
    mid = _id_of("花生过敏")
    rc, out = _run(capsys, "forget", mid, "--now", "--yes")
    assert rc == 0 and "Erased 1" in out
    assert lookup.get(mid)["status"] == "erased" and not lookup.get(mid)["text"]
    rc, out = _run(capsys, "restore", mid)
    assert rc == 1                                                            # 擦掉的救不回来


def test_edit_replaces_with_users_words_at_the_original_time(story, capsys):
    old = _id_of("我住在杭州")
    rc, out = _run(capsys, "edit", old, "我住在苏州，在一家做游戏的公司上班。")
    assert rc == 0 and "2026-01-10" in out
    new = _id_of("我住在苏州")
    it = lookup.get(new)
    assert it["who"] == "user" and it["ts"].startswith("2026-01-10 09:00")
    assert lookup.get(old)["status"] == "quarantined"
    hist = json.loads(story.handle_tool_call("nyx_belief", {"relation": "lives_in", "history": True}))["facts"]
    assert "杭州" not in [f["object"] for f in hist]                          # 旧说法的事实跟着走了
    cur = json.loads(story.handle_tool_call("nyx_belief", {"relation": "lives_in"}))["facts"]
    assert [f["object"] for f in cur] == ["上海"]                             # 10 月说的还是现在的
    rc, out = _run(capsys, "edit", new, "我住在苏州，在一家做游戏的公司上班。")
    assert rc == 0 and "Nothing changed" in out


def test_hermes_nyx_routes_the_subcommands(story, capsys, monkeypatch):
    import nexsandglass_hermes as nh
    from nexsandglass_hermes import cli as hcli
    monkeypatch.setattr(nh, "apply_data_dir", lambda: "")
    monkeypatch.setattr(nh, "load_config", lambda: {})
    rc = hcli.nyx_command(Namespace(nyx_command="search", nyx_args=["花生"]))
    out = capsys.readouterr().out
    assert rc == 0 and "花生过敏" in out and "hermes nyx show" in out
    from nexsandglass.core import hints
    monkeypatch.setattr(hints, "via_hermes", lambda: False)
    assert hints.cmd("restore", "m_1") == "python3 -m nexsandglass.memory_cli restore m_1"
    assert hints.cmd("import") == "python3 -m nexsandglass.history_import"
