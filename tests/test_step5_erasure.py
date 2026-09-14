"""tests/test_step5_erasure.py — Step 5 验收：forget 真正删干净

第一条测试是生产上那次失败的精确复现：

    observe("我的银行密码提示是 ...")   → ok
    forget({"all": True})              → {'ok': True, 'removed': 0}
    recall("银行密码提示")              → "我的银行密码提示是 ..."

`facade.forget` 只遍历 engram_store.jsonl，而召回读的是 sandglass.txt。
用户点了"忘记"，助理还记得，而且还会说出来。

删除的验收标准只有一个：**在每一个子系统里都找不到那段内容。**
调用返回 ok 不算数 —— 上次就是返回了 ok 而内容原封不动。
"""

import pytest

SECRET = "mother-maiden-name-Zhang-8871"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    home = tmp_path / "nb"
    home.mkdir()
    monkeypatch.setenv("NEXSANDBASE_HOME", str(home))

    from nexsandglass.core import memid, sandglass_sqlite, erasure, sandglass_log
    from nexsandglass.features import sandglass_vault, shadow_sand
    import nexsandglass.core.sandglass_paths as paths

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

    yield memid, erasure, sandglass_sqlite, sandglass_vault, shadow_sand, home, sandglass_log

    shadow_sand._conn = None
    memid.set_db_path(str(tmp_path / "unused.db"))


def _reindex(sq, vault, shadow):
    sq._last_sync_mtime = 0
    sq.sync_all()
    vault._idx_cache = None
    vault._idx_mtime = 0
    vault.rebuild_index()
    shadow.rebuild_entity_index()


def _seed(log, n=6):
    ids = []
    for i in range(1, n + 1):
        ids.append(log.log_message(f"第{i}条记忆 Alpha Bravo{i}", sender="user", return_id=True))
    return ids


# ── 生产复现 ─────────────────────────────────────────────

def test_forget_actually_erases_everywhere(env):
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 3)
    log.log_message(f"我的银行密码提示是 {SECRET}", sender="user")
    _seed(log, 2)
    _reindex(sq, vault, shadow)

    # 删之前：确实能搜到
    before = erasure.verify_erasure([SECRET], str(home / "sandglass.txt"))
    assert before["clean"] is False, "前置条件不成立：删之前就搜不到"
    assert "journal" in before["found_in"] and "search" in before["found_in"]

    rep = erasure.forget({"contains": SECRET}, reason="user_forget", apply=True,
                         journal_path=str(home / "sandglass.txt"))
    assert rep["found"] == 1 and rep["hub"] == 1

    after = erasure.verify_erasure([SECRET], str(home / "sandglass.txt"))
    assert after["clean"] is True, f"仍然能找到: {after['found_in']}"


def test_verify_erasure_can_actually_fail(env):
    """负向：内容还在时必须报 clean=False。否则这个验收指标毫无意义。"""
    memid, erasure, sq, vault, shadow, home, log = env
    log.log_message(f"敏感内容 {SECRET}", sender="user")
    _reindex(sq, vault, shadow)
    r = erasure.verify_erasure([SECRET], str(home / "sandglass.txt"))
    assert r["clean"] is False
    assert "journal" in r["found_in"]


# ── 不位移别人 ───────────────────────────────────────────

def test_erasure_preserves_line_numbers(env):
    """抹除必须保持物理行数不变 —— 否则后面所有记忆的 line_start 全部位移。"""
    memid, erasure, sq, vault, shadow, home, log = env
    log.log_message("第一条", sender="user")
    log.log_message(f"要删的多行\n续行A {SECRET}\n续行B", sender="user")
    log.log_message("第三条", sender="user")
    p = home / "sandglass.txt"
    before_lines = len(p.read_text(encoding="utf-8").splitlines())
    third_before = memid.get(memid.resolve(3))

    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(p))

    assert len(p.read_text(encoding="utf-8").splitlines()) == before_lines, "行数变了"
    third_after = memid.get(memid.resolve(3))
    assert third_after["line_start"] == third_before["line_start"], "第三条的行号被位移了"
    assert third_after["text"] == "第三条"


def test_erasure_preserves_seq_numbering(env):
    memid, erasure, sq, vault, shadow, home, log = env
    ids = _seed(log, 5)
    erasure.forget({"seq": 3}, apply=True, journal_path=str(home / "sandglass.txt"))

    recs = memid.parse_journal_records(str(home / "sandglass.txt"))
    assert [r["seq"] for r in recs] == [1, 2, 3, 4, 5], "seq 编号被打乱"
    assert memid.resolve(5) == ids[4], "seq=5 指向的记忆变了"
    assert memid.is_redacted(recs[2]), "第 3 条没被标记为已抹除"


def test_verify_still_ok_after_erasure(env):
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 5)
    erasure.forget({"seq": 2}, apply=True, journal_path=str(home / "sandglass.txt"))
    rep = memid.verify(str(home / "sandglass.txt"))
    assert rep["ok"] is True, f"抹除后一致性被破坏: {rep['mismatches']}"


def test_repair_does_not_resurrect(env):
    """自愈不能把已删的内容写回中枢。"""
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 4)
    log.log_message(f"要删的 {SECRET}", sender="user")
    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))

    r = memid.repair_from_journal(str(home / "sandglass.txt"), apply=True)
    assert r["repaired"] == 0
    after = erasure.verify_erasure([SECRET], str(home / "sandglass.txt"))
    assert after["clean"] is True, f"自愈把内容捞回来了: {after['found_in']}"


# ── 重建不复活 ───────────────────────────────────────────

def test_rebuild_does_not_resurrect(env):
    """全量重建索引之后，删掉的内容仍然必须找不到。"""
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 3)
    log.log_message(f"要删的 {SECRET}", sender="user")
    _seed(log, 2)
    _reindex(sq, vault, shadow)
    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))

    _reindex(sq, vault, shadow)          # ← 全量重建
    after = erasure.verify_erasure([SECRET], str(home / "sandglass.txt"))
    assert after["clean"] is True, f"重建把内容捞回来了: {after['found_in']}"


def test_redacted_records_are_not_indexed(env):
    """墓碑标记本身也不该进索引 —— 否则搜 REDACTED 就能列出用户删过什么。"""
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 3)
    log.log_message(f"要删的 {SECRET}", sender="user")
    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))
    _reindex(sq, vault, shadow)

    assert sq.count() == 3, f"FTS 里还有已抹除的记录（{sq.count()} 条，应为 3）"
    from nexsandglass.core.search_router import SearchRouter
    hits = SearchRouter().search("REDACTED", limit=10)
    assert not any("REDACTED" in (h[2] or "") for h in hits), "搜 REDACTED 能列出删除记录"


# ── 预演不改任何东西 ─────────────────────────────────────

def test_dry_run_changes_nothing(env):
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 3)
    log.log_message(f"要删的 {SECRET}", sender="user")
    p = home / "sandglass.txt"
    before = p.read_text(encoding="utf-8")

    rep = erasure.forget({"contains": SECRET}, apply=False, journal_path=str(p))
    assert rep["found"] == 1 and rep["applied"] is False
    assert rep["preview"] and SECRET in rep["preview"][0]["text"]
    assert rep["hub"] == 0 and rep["journal_lines"] == 0
    assert p.read_text(encoding="utf-8") == before
    assert memid.count() == 4


# ── 崩溃重放 ─────────────────────────────────────────────

def test_find_duplicate_runs(env):
    """只认连续重复。跨时段偶然写了同样一句话是正常的，同秒连写 N 次才是事故。"""
    memid, erasure, sq, vault, shadow, home, log = env
    memid.allocate("正常一条", sender="user", ts="2026-09-06 10:00:00")
    for _ in range(6):
        memid.allocate("重启", sender="user", ts="2026-09-06 10:00:01")
    memid.allocate("正常两条", sender="user", ts="2026-09-06 10:00:02")
    memid.allocate("重启", sender="user", ts="2026-09-06 11:00:00")     # 隔了一段，不算

    runs = erasure.find_duplicate_runs(min_run=3)
    assert len(runs) == 1
    r = runs[0]
    assert r["count"] == 6 and r["text_preview"] == "重启"
    assert len(r["drop"]) == 5 and r["keep"] not in r["drop"]


def test_erasing_a_run_keeps_the_first(env):
    memid, erasure, sq, vault, shadow, home, log = env
    memid.allocate("前面", sender="user", ts="2026-09-06 10:00:00")
    for _ in range(5):
        memid.allocate("重启", sender="user", ts="2026-09-06 10:00:01")
    memid.allocate("后面", sender="user", ts="2026-09-06 10:00:02")
    _reindex(sq, vault, shadow)

    run = erasure.find_duplicate_runs(min_run=3)[0]
    rep = erasure.forget({"mem_ids": run["drop"]}, reason="crash_replay",
                         apply=True, journal_path=str(home / "sandglass.txt"))
    assert rep["hub"] == 4

    recs = memid.parse_journal_records(str(home / "sandglass.txt"))
    live = [r for r in recs if not memid.is_redacted(r)]
    assert [r["text"] for r in live] == ["前面", "重启", "后面"], \
        f"保留的不是第一条: {[r['text'] for r in live]}"
    assert memid.verify(str(home / "sandglass.txt"))["ok"] is True


def test_tombstone_records_the_reason(env):
    memid, erasure, sq, vault, shadow, home, log = env
    mid = log.log_message(f"要删的 {SECRET}", sender="user", return_id=True)
    erasure.forget({"mem_id": mid}, reason="crash_replay_2026-09-06", apply=True,
                   journal_path=str(home / "sandglass.txt"))
    conn = memid.get_conn()
    row = conn.execute("SELECT reason FROM tombstones WHERE mem_id=?", (mid,)).fetchone()
    assert row and row[0] == "crash_replay_2026-09-06"
    line = (home / "sandglass.txt").read_text(encoding="utf-8").splitlines()[0]
    assert "reason=crash_replay_2026-09-06" in line, "日志墓碑没记原因"


# ── 对外 API ─────────────────────────────────────────────

def test_facade_forget_actually_erases(env):
    """facade.forget 是对外 API（MCP 的 memory_forget 走它）。它必须真删。"""
    memid, erasure, sq, vault, shadow, home, log = env
    from nexsandglass.runtime import facade
    _seed(log, 3)
    log.log_message(f"我的银行密码提示是 {SECRET}", sender="user")
    _reindex(sq, vault, shadow)

    r = facade.forget({"contains": SECRET})
    assert r["ok"] is True and r["removed"] == 1
    assert erasure.verify_erasure([SECRET], str(home / "sandglass.txt"))["clean"] is True


def test_facade_forget_dry_run(env):
    memid, erasure, sq, vault, shadow, home, log = env
    from nexsandglass.runtime import facade
    log.log_message(f"要删的 {SECRET}", sender="user")
    p = home / "sandglass.txt"
    before = p.read_text(encoding="utf-8")

    r = facade.forget({"contains": SECRET, "dry_run": True})
    assert r["ok"] is True and r["action"] == "dry_run" and r["removed"] == 0
    assert r["found"] == 1 and SECRET in r["preview"][0]["text"]
    assert p.read_text(encoding="utf-8") == before


# ── 删完之后，巡检必须还是绿的 ────────────────────────

def test_health_stays_green_after_forget(env):
    """正常删除不是故障。

    删完 FTS 里就是会少一条 —— 那是对的。如果巡检把它当成"索引落后"，
    用户每 forget 一次就永久红一项；红久了就没人看，真出事也报不出来。
    """
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 5)
    log.log_message(f"要删的 {SECRET}", sender="user")
    _reindex(sq, vault, shadow)
    assert memid.health()["ok"] is True, "删之前就不绿，后面不用比了"

    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))

    h = memid.health()
    assert h["ok"] is True, h["checks"]
    c = h["checks"]
    assert c["fts_index"]["ok"] is True
    assert c["fts_index"]["indexed"] == c["fts_index"]["expected"] == 5
    assert c["fts_index"]["records"] == 6, "日志记录数不变 —— 抹除是原地改写，不删行"
    assert c["hub_vs_journal"]["coverage"] == 1.0, "墓碑也是被覆盖了的，不该拉低 coverage"
    assert c["hub_vs_journal"]["live"] == 5 and c["hub_vs_journal"]["tombstoned"] == 1


def test_health_catches_tombstone_whose_text_survived(env):
    """打了墓碑、正文却还躺在日志里 —— 这就是"以为删了其实没删"。

    它必须自己成为一项，不能被别的指标盖住。
    """
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 3)
    mid = log.log_message(f"要删的 {SECRET}", sender="user", return_id=True)
    _reindex(sq, vault, shadow)

    memid.tombstone(mid, reason="user_forget")      # 只打墓碑，不动日志正文
    sq._last_sync_mtime = 0; sq.sync_all()          # 让 FTS 跟上，排除干扰项

    h = memid.health()
    assert h["checks"]["erasure_integrity"]["ok"] is False
    assert h["checks"]["erasure_integrity"]["leaked_count"] == 1
    assert h["ok"] is False
    assert SECRET in (home / "sandglass.txt").read_text(encoding="utf-8"), \
        "前提没成立：正文本该还在"


def test_health_erasure_integrity_green_after_real_forget(env):
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 3)
    log.log_message(f"要删的 {SECRET}", sender="user")
    _reindex(sq, vault, shadow)
    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))

    c = memid.health()["checks"]["erasure_integrity"]
    assert c["ok"] is True and c["tombstones"] == 1
    assert c["leaked"] == [] and c["seq_not_in_journal"] == 0


def test_health_erasure_integrity_on_clean_hub(env):
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 3)
    _reindex(sq, vault, shadow)
    c = memid.health()["checks"]["erasure_integrity"]
    assert c["ok"] is True and c["tombstones"] == 0


# ── Step 6：成功和失败必须可区分 ─────────────────────

def test_forget_reports_ok_and_removed(env):
    """erasure.forget 原先不返回 ok / removed —— 调用方 r.get('ok') 永远是 None。

    一个专职「证明内容真的没了」的模块，返回值里连"成没成"都没有。
    """
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 3)
    log.log_message(f"要删的 {SECRET}", sender="user")
    _reindex(sq, vault, shadow)

    r = erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))
    assert r["ok"] is True
    assert r["removed"] == 1 and r["found"] == 1
    assert r["action"] == "erased" and r["problems"] == []


def test_forget_dry_run_is_ok_but_removed_zero(env):
    memid, erasure, sq, vault, shadow, home, log = env
    log.log_message(f"要删的 {SECRET}", sender="user")
    r = erasure.forget({"contains": SECRET}, journal_path=str(home / "sandglass.txt"))
    assert r["ok"] is True and r["removed"] == 0
    assert r["action"] == "dry_run" and r["found"] == 1


def test_forget_not_ok_when_journal_untouched(env, monkeypatch):
    """日志正文没抹掉，却照样打了墓碑 —— 这是"以为删了其实没删"，必须报 not ok。"""
    memid, erasure, sq, vault, shadow, home, log = env
    log.log_message(f"要删的 {SECRET}", sender="user")
    monkeypatch.setattr(erasure, "_redact_journal", lambda *a, **k: 0)

    r = erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))
    assert r["ok"] is False
    assert r["action"] == "partial"
    assert any("日志抹除" in p for p in r["problems"])


def test_forget_ok_even_when_indexes_report_zero(env, monkeypatch):
    """索引返回 0 是合法的 —— 那条记录可能本来就没进过索引，不该判成失败。"""
    memid, erasure, sq, vault, shadow, home, log = env
    log.log_message(f"要删的 {SECRET}", sender="user")
    monkeypatch.setattr(erasure, "_purge_fts", lambda *a, **k: 0)
    monkeypatch.setattr(erasure, "_purge_idx", lambda *a, **k: 0)
    monkeypatch.setattr(erasure, "_purge_engram", lambda *a, **k: 0)

    r = erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))
    assert r["ok"] is True and r["removed"] == 1


def test_forget_nothing_matched_is_ok_not_failure(env):
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 2)
    r = erasure.forget({"contains": "根本不存在的内容"}, apply=True,
                       journal_path=str(home / "sandglass.txt"))
    assert r["ok"] is True and r["removed"] == 0 and r["found"] == 0
    assert r["action"] == "nothing_matched"


# ── 写入放大：复发检测 ───────────────────────────────

def test_health_catches_write_amplification(env):
    """一条消息被连写 N 份。生产上它从 3 倍爬到 56 倍，爬了七天没人发现。"""
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 3)
    ts = memid._now() if hasattr(memid, "_now") else None
    for _ in range(6):                       # 同一秒、同一内容，连写 6 份
        memid.allocate("重启", sender="user", ts="2026-09-12 10:00:00")
    _reindex(sq, vault, shadow)

    c = memid.health(window_days=3650)["checks"]["write_amplification"]
    assert c["ok"] is False
    assert c["max_run"] == 6
    assert c["factor"] > 1


def test_health_write_amplification_green_on_normal_traffic(env):
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 6)
    _reindex(sq, vault, shadow)
    c = memid.health(window_days=3650)["checks"]["write_amplification"]
    assert c["ok"] is True and c["max_run"] <= 2


def test_health_write_amplification_tolerates_a_single_double(env):
    """阈值不是 1：偶发写重一次是噪声，连续爬升才是泄漏。"""
    memid, erasure, sq, vault, shadow, home, log = env
    _seed(log, 4)
    for _ in range(2):
        memid.allocate("偶然重了一次", sender="user", ts="2026-09-12 10:00:00")
    c = memid.health(window_days=3650)["checks"]["write_amplification"]
    assert c["ok"] is True and c["max_run"] == 2


def test_health_write_amplification_no_data_is_not_green_by_accident(env):
    """窗口内没有记录时，必须标 no_data —— 不能让"没数据"看起来像"很健康"。"""
    memid, erasure, sq, vault, shadow, home, log = env
    for _ in range(3):
        memid.allocate("很久以前的记忆", sender="user", ts="2020-01-01 00:00:00")
    c = memid.health(window_days=3)["checks"]["write_amplification"]
    assert c["no_data"] is True and c["records"] == 0
