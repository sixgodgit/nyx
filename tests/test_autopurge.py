"""隔离区到期自动清理（v7.22.0）"""
import json
from datetime import datetime, timedelta

import pytest

from tests.test_hermes_provider import env, _reindex, _provider  # noqa: F401  复用隔离目录

from nexsandglass.core import autopurge, lookup


@pytest.fixture()
def marker(tmp_path, monkeypatch):
    p = tmp_path / "auto_purge.json"
    monkeypatch.setattr(autopurge, "_path", lambda: str(p))
    return p


def _forget(p, text, days_ago):
    """忘掉一条，并把它在隔离区里的「到期日」往前挪（模拟过了 days_ago 天）。"""
    from nexsandglass.core import quarantine
    mid = lookup.containing(text)[0]["mem_id"]
    out = json.loads(p.handle_tool_call("nyx_forget", {"mem_id": mid, "confirm": True}))
    assert out["status"] == "quarantined"
    due = (datetime.now() + timedelta(days=30 - days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    c = quarantine._conn()
    c.execute("UPDATE quarantine SET purge_after = ? WHERE mem_id = ?", (due, mid))
    c.commit()
    return mid


def test_erases_only_what_is_past_retention_once_a_day(env, marker):  # noqa: F811
    p = _provider()
    p.sync_turn("旧地址是解放路 12 号，已经不住了。", "好的。", session_id="s")
    p.sync_turn("上周那个约会的事先别记。", "好的。", session_id="s")
    old = _forget(p, "解放路", days_ago=40)                 # 过了 30 天保留期
    new = _forget(p, "约会", days_ago=3)                    # 还在保留期内
    r = autopurge.run_if_due()
    assert r["ok"] and r["purged"] == 1
    assert lookup.get(old)["status"] == "erased"
    assert lookup.get(new)["status"] == "quarantined"          # 没到期的一条都不动
    assert json.loads(marker.read_text())["purged"] == 1
    assert autopurge.run_if_due() is None                    # 一天之内不再跑
    later = datetime.now() + timedelta(hours=25)
    assert autopurge.run_if_due(now=later)["purged"] == 0


def test_setting_and_warm_up(env, marker, monkeypatch):  # noqa: F811
    assert not autopurge.enabled()                           # 测试会话里默认关（conftest）
    monkeypatch.setenv("NYX_AUTO_PURGE", "on")
    assert autopurge.enabled()
    calls = []
    monkeypatch.setattr(autopurge, "run_if_due", lambda **kw: calls.append(kw) or {})
    p = _provider()
    p._warm(auto_purge=True)
    p._warm(auto_purge=False)
    assert len(calls) == 1


def test_doctor_reports_it(env, marker):  # noqa: F811
    from nexsandglass.core import memid
    autopurge.run_if_due(force=True)
    chk = memid.health()["checks"]["pending_purge"]
    assert chk["auto_purge"] is False and chk["last_auto_purge"]
