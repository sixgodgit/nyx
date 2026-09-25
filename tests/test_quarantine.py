"""tests/test_quarantine.py — 遗忘隔离区验收：删得掉，也救得回

这一组测试要钉住的事故是 v7.4 那次（README 有案）：
一次 `forget` 按伪行号抹除，误抹了生产日志里 **345 行真实对话**，
而当时仓库里**没有任何一条路径能把它们拿回来**。

验收标准是三条互相拉扯的性质，必须同时成立：

  1. 隔离期内，用户读不到 —— 检索/召回/索引/影子副本里都摸不到（与老行为逐字节一致）
  2. 隔离期内，误删救得回 —— restore 逐字节还原日志正文，且不位移别人的行号
  3. 过期之后，真的没了 —— purge 不可逆，restore 也拿不回来

只做到 1 是老行为；只做到 2 是没真删；只做到 3 是把不可逆又装回去了。
"""

import json
from datetime import datetime, timedelta

import pytest

SECRET = "mother-maiden-name-Zhang-8871"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    home = tmp_path / "nb"
    home.mkdir()
    monkeypatch.setenv("NEXSANDBASE_HOME", str(home))
    monkeypatch.delenv("NYX_FORGET_RETENTION_DAYS", raising=False)

    from nexsandglass.core import (memid, quarantine, sandglass_sqlite, erasure,
                                  sandglass_log)
    from nexsandglass.features import sandglass_vault, shadow_sand

    sg = str(home / "sandglass.txt")
    monkeypatch.setattr(memid, "_SANDGLASS", sg)
    monkeypatch.setattr(memid, "_LOCK", sg + ".lock")
    monkeypatch.setattr(sandglass_log, "_SANDGLASS", sg)
    monkeypatch.setattr(sandglass_vault, "_SANDGLASS", sg)
    monkeypatch.setattr(sandglass_sqlite, "_DB", str(home / "sandglass.db"))
    monkeypatch.setattr(sandglass_sqlite, "_last_sync_mtime", 0)
    monkeypatch.setattr(erasure, "_NB", str(home))
    sandglass_vault.set_idx_path(str(home / "sandglass.idx"))
    sandglass_vault._idx_cache = None
    sandglass_vault._idx_mtime = 0
    memid.set_db_path(str(home / "nyx.db"))
    shadow_sand.set_shadow_path(str(home / "shadow_sand.db"))
    shadow_sand._conn = None

    class E:
        pass

    e = E()
    e.memid, e.erasure, e.quarantine = memid, erasure, quarantine
    e.sq, e.vault, e.shadow, e.log = sandglass_sqlite, sandglass_vault, shadow_sand, sandglass_log
    e.home, e.journal = home, sg
    yield e

    shadow_sand._conn = None
    memid.set_db_path(str(tmp_path / "unused.db"))


def _reindex(e):
    e.sq._last_sync_mtime = 0
    e.sq.sync_all()
    e.vault._idx_cache = None
    e.vault._idx_mtime = 0
    e.vault.rebuild_index()
    e.shadow.rebuild_entity_index()


def _seed(e, n=3):
    return [e.log.log_message(f"第{i}条记忆 Alpha Bravo{i}", sender="user", return_id=True)
            for i in range(1, n + 1)]


def _plant(e, before=3, after=2, text=None):
    """种一条要删的记忆，前后各若干条无关记忆，建好索引。返回它的 mem_id。"""
    _seed(e, before)
    mid = e.log.log_message(text or f"我的银行密码提示是 {SECRET}", sender="user",
                            return_id=True)
    _seed(e, after)
    _reindex(e)
    return mid


def _forget(e, **kw):
    return e.erasure.forget({"contains": SECRET}, apply=True, journal_path=e.journal, **kw)


# ══════════════════════════════════════════════════════════
# 1. 隔离期内：用户读不到（不能因为"可反悔"就少删了）
# ══════════════════════════════════════════════════════════

def test_forget_defaults_to_recoverable(env):
    e = env
    _plant(e)
    rep = _forget(e)
    assert rep["mode"] == "quarantine"
    assert rep["quarantined"] == 1, "默认没有进隔离区"
    assert rep["recoverable"] is True
    assert rep["purge_after"], "没有给出到期时间"
    assert rep["retention_days"] == 30


def test_quarantine_does_not_weaken_erasure(env):
    """隔离区不是"少删一点" —— 检索侧必须和老行为逐字节一致。"""
    e = env
    _plant(e)
    before = e.erasure.verify_erasure([SECRET], e.journal)
    assert before["clean"] is False, "前置条件不成立：删之前就搜不到"

    _forget(e)

    after = e.erasure.verify_erasure([SECRET], e.journal)
    assert after["clean"] is True, f"隔离模式下仍能检索到: {after['found_in']}"
    assert e.sq.count() == 5, "FTS 里应只剩 5 条无关记忆"

    _reindex(e)          # 重建也不能把内容捞回来
    assert e.erasure.verify_erasure([SECRET], e.journal)["clean"] is True


def test_verify_reports_quarantine_honestly(env):
    """隔离期内原文还在磁盘上 —— 这件事必须报出来，不能假装 fully_purged。"""
    e = env
    _plant(e)
    _forget(e)
    v = e.erasure.verify_erasure([SECRET], e.journal)
    assert v["clean"] is True, "检索侧应已摸不到"
    assert v["in_quarantine"], "隔离区里明明还有，却没报出来"
    assert v["fully_purged"] is False, "隔离期内不该声称已彻底擦除"


def test_dry_run_tells_whether_it_is_recoverable(env):
    """用户按确认之前，最该知道的就是"这一下还能不能反悔"。"""
    e = env
    _plant(e)
    rep = e.erasure.forget({"contains": SECRET}, apply=False, journal_path=e.journal)
    assert rep["found"] == 1 and rep["applied"] is False
    assert rep["recoverable"] is True and rep["retention_days"] == 30
    hard = e.erasure.forget({"contains": SECRET}, apply=False, journal_path=e.journal,
                            mode="purge")
    assert hard["recoverable"] is False


# ══════════════════════════════════════════════════════════
# 2. 隔离期内：误删救得回
# ══════════════════════════════════════════════════════════

def test_restore_brings_it_back_everywhere(env):
    e = env
    mid = _plant(e)
    _forget(e)

    rep = e.erasure.restore(mid)
    assert rep["ok"] is True, f"还原失败: {rep['problems']}"
    assert rep["journal"]["status"] == "restored"
    assert rep["hub"] == 1 and rep["tombstone_cleared"] == 1

    # 检索侧真的回来了 —— 不只是文件里有字
    back = e.erasure.verify_erasure([SECRET], e.journal)
    assert back["clean"] is False, "还原后反而搜不到，等于没还原"
    assert "journal" in back["found_in"], "日志正文没回来"
    assert "search" in back["found_in"], f"检索路径没回来: {list(back['found_in'])}"
    assert "fts" in back["found_in"], "FTS 没回来"

    row = e.memid.get(mid)
    assert row and SECRET in row["text"], "中枢正文没回来"
    assert e.memid.is_tombstoned(mid) is False, "墓碑没撤"
    assert e.memid.verify(e.journal)["ok"] is True, "还原破坏了中枢/日志一致性"


def test_restore_is_byte_exact(env):
    """还原不是"重新拼一条差不多的" —— 日志必须逐字节回到原样。

    从 (ts, sender, text) 重建日志行，靠的是"格式假设"，
    而格式假设正是 v7.4 栽过的地方。所以隔离区存的是原始行。
    """
    e = env
    mid = _plant(e)
    original = open(e.journal, "rb").read()

    _forget(e)
    assert open(e.journal, "rb").read() != original, "前置条件不成立：抹除没改文件"

    e.erasure.restore(mid)
    assert open(e.journal, "rb").read() == original, "还原后的日志与原文件不一致"


def test_restore_multiline_and_preserves_others(env):
    """多行消息也要逐字节还原，且不位移后面记忆的行号。"""
    e = env
    e.log.log_message("第一条", sender="user")
    mid = e.log.log_message(f"要删的多行\n续行A {SECRET}\n续行B", sender="user",
                            return_id=True)
    e.log.log_message("第三条", sender="user")
    _reindex(e)

    lines_before = len(open(e.journal, encoding="utf-8").read().splitlines())
    third_before = e.memid.get(e.memid.resolve(3))
    original = open(e.journal, "rb").read()

    _forget(e)
    assert len(open(e.journal, encoding="utf-8").read().splitlines()) == lines_before

    rep = e.erasure.restore(mid)
    assert rep["ok"] is True, rep["problems"]
    assert rep["journal"]["lines"] == 3, "三行的记忆只还原了部分"
    assert open(e.journal, "rb").read() == original
    third_after = e.memid.get(e.memid.resolve(3))
    assert third_after["line_start"] == third_before["line_start"], "第三条被位移"
    assert third_after["text"] == "第三条"


def test_restore_recovers_derived_rows(env):
    """派生行（影子沙 trust / 知识图谱三元组）也要回来，否则还原只是半个。"""
    e = env
    mid = _plant(e)
    row = e.memid.get(mid)
    line = row["line_start"]

    def trust_rows():
        with e.shadow._db_lock:
            return e.shadow._get_conn().execute(
                "SELECT COUNT(*) FROM trust WHERE line_num=?", (line,)).fetchone()[0]

    assert trust_rows() == 1, "前置条件不成立：影子沙里本来就没有这一行"
    _forget(e)
    assert trust_rows() == 0, "forget 没清掉影子沙"

    rep = e.erasure.restore(mid)
    assert rep["ok"] is True, rep["problems"]
    assert trust_rows() == 1, "还原没把影子沙那一行带回来"


def test_restore_twice_is_safe(env):
    """第二次还原必须是无害的 no-op，而不是把内容写第二份。"""
    e = env
    mid = _plant(e)
    _forget(e)
    assert e.erasure.restore(mid)["ok"] is True
    after_first = open(e.journal, "rb").read()

    again = e.erasure.restore(mid)
    assert again["ok"] is False and again["found"] is False
    assert open(e.journal, "rb").read() == after_first, "第二次还原改动了日志"


# ══════════════════════════════════════════════════════════
# 2b. 还原的安全闸门（v7.4 教训的另一半）
# ══════════════════════════════════════════════════════════

def test_restore_refuses_to_clobber_live_content(env):
    """目标行现在不是脱敏标记 → 拒绝写入。

    日志被截断 / 手工编辑 / 恢复了旧备份之后，line_start 指向的可能是
    **别人的活记忆**。宁可还原失败，也不能覆盖它 —— 那正是 v7.4 的错误方向。
    """
    e = env
    mid = _plant(e)
    _forget(e)

    row = e.memid.get(mid, allow_deleted=True)
    lines = open(e.journal, encoding="utf-8").read().splitlines()
    lines[row["line_start"] - 1] = "2026-09-25 12:00:00 | user | 别人的活记忆，不许覆盖"
    open(e.journal, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    guard = open(e.journal, "rb").read()

    rep = e.erasure.restore(mid)
    assert rep["ok"] is False
    assert rep["journal"]["status"] == "not_redacted"
    assert any("日志正文未还原" in p for p in rep["problems"])
    assert open(e.journal, "rb").read() == guard, "把活记忆覆盖掉了"
    assert e.memid.is_tombstoned(mid) is True, "还原失败却撤了墓碑"


def test_restore_refuses_when_not_deleted(env):
    """中枢里这条没有墓碑 → 它没被删，不该"还原"。"""
    e = env
    mid = _plant(e)
    _forget(e)
    conn = e.memid.get_conn()
    conn.execute("UPDATE memories SET deleted_at=NULL WHERE mem_id=?", (mid,))
    conn.commit()

    rep = e.erasure.restore(mid)
    assert rep["ok"] is False
    assert any("墓碑" in p for p in rep["problems"])


def test_restore_refuses_on_line_number_mismatch(env):
    """行号对不上就不许按行号写回 —— 伪行号是 v7.4 误抹 345 行的直接原因。"""
    e = env
    mid = _plant(e)
    _forget(e)
    conn = e.memid.get_conn()
    conn.execute("UPDATE memories SET line_start=line_start+1 WHERE mem_id=?", (mid,))
    conn.commit()

    rep = e.erasure.restore(mid)
    assert rep["ok"] is False
    assert any("行号不一致" in p for p in rep["problems"])


# ══════════════════════════════════════════════════════════
# 3. 过期之后：真的没了
# ══════════════════════════════════════════════════════════

def test_purge_makes_it_unrecoverable(env):
    e = env
    mid = _plant(e)
    _forget(e, retention_days=1)

    future = datetime.now() + timedelta(days=2)
    rep = e.quarantine.purge(apply=True, now=future)
    assert rep["purged"] == 1 and rep["ok"] is True
    assert rep["vacuumed"] is True, "没 VACUUM 就不能声称磁盘上没了"

    assert e.erasure.restore(mid)["found"] is False, "purge 之后还能还原"
    v = e.erasure.verify_erasure([SECRET], e.journal)
    assert v["clean"] is True and not v["in_quarantine"]
    assert v["fully_purged"] is True, "全都擦完了却不敢声明 fully_purged"


def test_purge_respects_retention_window(env):
    """没到期的不许被顺手清掉 —— 承诺 30 天就是 30 天。"""
    e = env
    mid = _plant(e)
    _forget(e)                      # 默认 30 天

    rep = e.quarantine.purge(apply=True)
    assert rep["selected"] == 0 and rep["purged"] == 0
    assert e.erasure.restore(mid)["ok"] is True, "没到期却已经救不回来了"


def test_purge_dry_run_changes_nothing(env):
    e = env
    mid = _plant(e)
    _forget(e, retention_days=1)
    future = datetime.now() + timedelta(days=2)

    rep = e.quarantine.purge(apply=False, now=future)
    assert rep["selected"] == 1 and rep["purged"] == 0
    assert rep["action"] == "dry_run"
    assert e.erasure.restore(mid)["ok"] is True, "预演把东西删了"


def test_purge_by_mem_id_ignores_window(env):
    """「这条现在就要彻底没有」—— 精确指定时不看到期时间。"""
    e = env
    mid = _plant(e)
    _forget(e)
    rep = e.quarantine.purge([mid], apply=True)
    assert rep["purged"] == 1
    assert e.erasure.restore(mid)["found"] is False


# ══════════════════════════════════════════════════════════
# 4. 老行为仍然可达（法务 / 他人隐私 / 误粘贴的凭据）
# ══════════════════════════════════════════════════════════

def test_purge_mode_is_irreversible_immediately(env):
    e = env
    mid = _plant(e)
    rep = _forget(e, mode="purge")
    assert rep["quarantined"] == 0 and rep["recoverable"] is False

    v = e.erasure.verify_erasure([SECRET], e.journal)
    assert v["clean"] is True and v["fully_purged"] is True
    assert e.erasure.restore(mid)["found"] is False


def test_retention_zero_disables_quarantine(env):
    e = env
    mid = _plant(e)
    rep = _forget(e, retention_days=0)
    assert rep["quarantined"] == 0 and rep["recoverable"] is False
    assert e.erasure.verify_erasure([SECRET], e.journal)["fully_purged"] is True


def test_env_overrides_retention(env, monkeypatch):
    e = env
    monkeypatch.setenv("NYX_FORGET_RETENTION_DAYS", "7")
    _plant(e)
    rep = _forget(e)
    assert rep["retention_days"] == 7
    q = e.quarantine.list_all()[0]
    days = (datetime.strptime(q["purge_after"], "%Y-%m-%d %H:%M:%S")
            - datetime.strptime(q["quarantined_at"], "%Y-%m-%d %H:%M:%S")).days
    assert days == 7


def test_invalid_mode_is_rejected(env):
    e = env
    _plant(e)
    with pytest.raises(ValueError):
        e.erasure.forget({"contains": SECRET}, apply=True, journal_path=e.journal,
                         mode="maybe")


# ══════════════════════════════════════════════════════════
# 5. 巡检：过期没清必须报红（否则"保留 30 天"等于"永久留着"）
# ══════════════════════════════════════════════════════════

def test_health_flags_overdue_quarantine(env):
    e = env
    _plant(e)
    _forget(e, retention_days=1)

    h = e.memid.health(e.journal)
    assert h["checks"]["pending_purge"]["ok"] is True
    assert h["checks"]["pending_purge"]["pending"] == 1

    # 时间过去了（把到期时间推到过去，等价于隔离期已满而没人清）
    conn = e.memid.get_conn()
    past = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE quarantine SET purge_after=?", (past,))
    conn.commit()

    h = e.memid.health(e.journal)
    assert h["checks"]["pending_purge"]["ok"] is False, "过期未清却报绿"
    assert h["checks"]["pending_purge"]["overdue"] == 1

    e.quarantine.purge(apply=True)
    h = e.memid.health(e.journal)
    assert h["checks"]["pending_purge"]["ok"] is True
    assert h["checks"]["pending_purge"]["pending"] == 0


def test_quarantine_list_does_not_leak_full_text(env):
    """清单是给人扫一眼的，不该把被忘记的正文整段摊回日志/终端。"""
    e = env
    _plant(e)
    _forget(e)
    items = e.quarantine.list_all()
    assert len(items) == 1
    assert "payload" not in items[0] and "text" not in items[0]
    assert len(items[0]["preview"]) <= 40


# ══════════════════════════════════════════════════════════
# 6. 走公开契约（B0 门面）的闭环
# ══════════════════════════════════════════════════════════

def test_facade_forget_then_restore(env):
    e = env
    _plant(e)
    from nexsandglass.runtime import facade

    rep = facade.forget({"contains": SECRET})
    assert rep["ok"] is True and rep["removed"] == 1
    assert rep["recoverable"] is True and rep["purge_after"]
    mid = rep["preview"][0]["mem_id"]
    assert e.erasure.verify_erasure([SECRET], e.journal)["clean"] is True

    back = facade.restore(mid)
    assert back["ok"] is True, back.get("problems")
    assert e.erasure.verify_erasure([SECRET], e.journal)["clean"] is False


def test_facade_purge_now_is_irreversible(env):
    e = env
    _plant(e)
    from nexsandglass.runtime import facade

    rep = facade.forget({"contains": SECRET, "purge_now": True})
    assert rep["ok"] is True and rep["recoverable"] is False
    mid = rep["preview"][0]["mem_id"]
    assert facade.restore(mid)["ok"] is False
    assert e.erasure.verify_erasure([SECRET], e.journal)["fully_purged"] is True


def test_facade_purge_forgotten_cron_entry(env):
    e = env
    _plant(e)
    from nexsandglass.runtime import facade

    facade.forget({"contains": SECRET, "retention_days": 1})
    conn = e.memid.get_conn()
    past = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE quarantine SET purge_after=?", (past,))
    conn.commit()

    dry = facade.purge_forgotten()
    assert dry["selected"] == 1 and dry["purged"] == 0
    wet = facade.purge_forgotten(apply=True)
    assert wet["ok"] is True and wet["purged"] == 1
    assert e.erasure.verify_erasure([SECRET], e.journal)["fully_purged"] is True


# ══════════════════════════════════════════════════════════
# 7. 隔离区本身的存储契约
# ══════════════════════════════════════════════════════════

def test_payload_holds_original_lines(env):
    """隔离区存的是"抹除前的原始行"，不是"重建所需的字段"。"""
    e = env
    mid = _plant(e)
    _forget(e)
    rec = e.quarantine.get(mid)
    pay = json.loads(rec["payload"])
    assert pay["version"] == 1
    assert pay["journal"] and SECRET in pay["journal"][0]
    assert pay["journal"][0].startswith(rec["ts"]), "存的不是完整的原始行"


def test_restore_does_not_clobber_another_restores_index(env):
    """回归：倒排索引的缓存键是日志物理行数，而脱敏/还原都不改行数 ——
    缓存于是永远"命中"，`_write_idx` 按旧索引整file重写，**把别的进程刚还原的
    token 抹掉**。实测形状：先用 CLI 还原一条，再在本进程还原其余 149 条，
    第一条的 posting 全没了。
    """
    e = env
    a = e.log.log_message(f"第一条要删的 {SECRET} alpha-uniq-aaa", sender="user",
                          return_id=True)
    b = e.log.log_message(f"第二条要删的 {SECRET} beta-uniq-bbb", sender="user",
                          return_id=True)
    _reindex(e)
    line_a = e.memid.get(a)["line_start"]
    _forget(e)

    assert e.erasure.restore(a)["ok"] is True
    tokens_a = {t for t, lines in e.vault._sync_index().items() if line_a in lines}
    assert tokens_a, "前置条件不成立：第一条还原后索引里本来就没有它"

    # 模拟另一个进程留下的过期缓存：行数没变，所以缓存会"命中"
    stale = {t: [l for l in lines if l != line_a]
             for t, lines in e.vault._sync_index().items()}
    e.vault._idx_cache, e.vault._idx_mtime = stale, e.vault._journal_lines()

    assert e.erasure.restore(b)["ok"] is True
    after = {t for t, lines in e.vault._sync_index().items() if line_a in lines}
    assert after, "第二次还原把第一条的倒排 posting 抹掉了"
