"""tests/test_step7_leaks.py — Step 7 验收：把删除的漏口堵上

Step 5 声称「七个子系统级联真删」，Step 6 让删除结果可判定。
这一步补的是那两步都没看见的三个洞：

1. sandglass.backup —— 删除的第二个现场。
   pulse._sync_shadow 按**行数**增量同步，而脱敏是原地改写、行数不变，
   于是影子副本永远停在脱敏之前。nightwatch 在主沙漏变短时会把它拷回去，
   那一下就是「已删除的记忆整个复活」。verify_erasure 原本压根不看它，
   于是会在正文仍躺在 backup 里的情况下报 clean=True。

2. 绕过 ID 中枢的直写 —— 三处：
   interfaces/plugin.py / core/sandglass.py / nyx_server._append_to_sandglass。
   Step 2 只改了 sandglass_log 这一条路。

3. 同一个钩子被注册两次 —— 仓库里有两份网关插件，各注册一次。
   每条消息因此写两份，每次重连再各加一次。
"""

import pytest


@pytest.fixture()
def env(monkeypatch, tmp_path):
    home = tmp_path / "nb"
    home.mkdir()
    monkeypatch.setenv("NEXSANDBASE_HOME", str(home))

    from nexsandglass.core import memid, sandglass_sqlite, erasure, sandglass_log
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
    sandglass_log._HOOKS_CLAIMED.clear()

    yield memid, erasure, sandglass_log, home

    shadow_sand._conn = None
    sandglass_log._HOOKS_CLAIMED.clear()
    memid.set_db_path(str(tmp_path / "unused.db"))


SECRET = "mother-maiden-name-Zhang-7714"


def _shadow_copy(home):
    """模拟 pulse/nightwatch 造出的影子副本（脱敏之前的全量拷贝）。"""
    src = home / "sandglass.txt"
    (home / "sandglass.backup").write_bytes(src.read_bytes())


# ── 洞 1：影子副本 ───────────────────────────────────────

def test_forget_also_redacts_the_backup(env):
    memid, erasure, log, home = env
    for i in range(3):
        log.log_message(f"普通记忆{i}", sender="user")
    log.log_message(f"要删的 {SECRET}", sender="user")
    _shadow_copy(home)
    assert SECRET in (home / "sandglass.backup").read_text(encoding="utf-8"), "前提：影子里有原文"

    r = erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))
    assert r["ok"] is True
    assert r["backup"]["status"] == "redacted" and r["backup"]["lines"] >= 1
    assert SECRET not in (home / "sandglass.backup").read_text(encoding="utf-8"), \
        "影子副本里还留着正文 —— nightwatch 能把它恢复回主沙漏"


def test_verify_erasure_looks_at_the_backup(env):
    """原来的 verify_erasure 不看 backup，于是正文还在也报 clean=True。"""
    memid, erasure, log, home = env
    log.log_message(f"要删的 {SECRET}", sender="user")
    _shadow_copy(home)
    # 只抹主日志，故意不动影子 —— 模拟修复前的行为
    import re
    p = home / "sandglass.txt"
    p.write_text(re.sub(re.escape(SECRET), "[REDACTED x]", p.read_text(encoding="utf-8")),
                 encoding="utf-8")

    v = erasure.verify_erasure([SECRET], str(p))
    assert v["clean"] is False, "影子里还有正文却报 clean —— 这正是要修的"
    assert "backup" in v["found_in"]


def test_backup_line_count_preserved(env):
    memid, erasure, log, home = env
    for i in range(4):
        log.log_message(f"记忆{i}", sender="user")
    log.log_message(f"要删的 {SECRET}", sender="user")
    _shadow_copy(home)
    before = len((home / "sandglass.backup").read_text(encoding="utf-8").splitlines())

    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))
    after = len((home / "sandglass.backup").read_text(encoding="utf-8").splitlines())
    assert before == after, "影子副本的物理行数也必须不变"


def test_out_of_sync_backup_is_reported_not_silently_skipped(env):
    """影子比主日志短 → 行号对不上，不能按行号盲改。必须报出来，不能假装删干净了。"""
    memid, erasure, log, home = env
    for i in range(5):
        log.log_message(f"记忆{i}", sender="user")
    log.log_message(f"要删的 {SECRET}", sender="user")
    lines = (home / "sandglass.txt").read_text(encoding="utf-8").splitlines()
    (home / "sandglass.backup").write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")

    r = erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))
    assert r["backup"]["status"] == "out_of_sync"
    assert r["ok"] is False and any("影子副本" in p for p in r["problems"])


def test_no_backup_file_is_fine(env):
    memid, erasure, log, home = env
    log.log_message(f"要删的 {SECRET}", sender="user")
    r = erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))
    assert r["backup"]["status"] == "absent"
    assert r["ok"] is True


# ── 洞 2：nightwatch 不得复活已删内容 ────────────────────

def test_nightwatch_refuses_to_resurrect(env):
    memid, erasure, log, home = env
    log.log_message(f"要删的 {SECRET}", sender="user")
    _shadow_copy(home)                      # 影子停在脱敏之前
    import re
    p = home / "sandglass.txt"
    mid = memid.resolve(1)
    memid.tombstone(mid, reason="user_forget")
    p.write_text(re.sub(re.escape(SECRET), "[REDACTED x]", p.read_text(encoding="utf-8")),
                 encoding="utf-8")

    from nexsandglass.features import nightwatch
    assert nightwatch._shadow_would_resurrect(str(home / "sandglass.backup")) is True


def test_nightwatch_allows_restore_when_shadow_is_clean(env):
    memid, erasure, log, home = env
    log.log_message(f"要删的 {SECRET}", sender="user")
    log.log_message("留着的记忆", sender="user")
    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))
    _shadow_copy(home)                      # 删完之后才拷 → 影子是干净的

    from nexsandglass.features import nightwatch
    assert nightwatch._shadow_would_resurrect(str(home / "sandglass.backup")) is False


def test_nightwatch_fails_closed(env):
    """**有墓碑**但读不了影子文件时，按最坏情况算 —— 宁可拒绝恢复。

    （没有墓碑时返回 False 是对的：没删过东西，就没什么可复活的。）
    """
    memid, erasure, log, home = env
    log.log_message(f"要删的 {SECRET}", sender="user")
    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))

    from nexsandglass.features import nightwatch
    assert nightwatch._shadow_would_resurrect(str(home / "根本不存在.backup")) is True


def test_nightwatch_no_tombstones_means_nothing_to_resurrect(env):
    memid, erasure, log, home = env
    log.log_message("一条普通记忆", sender="user")
    _shadow_copy(home)
    from nexsandglass.features import nightwatch
    assert nightwatch._shadow_would_resurrect(str(home / "sandglass.backup")) is False


# ── 洞 3：写路径与钩子注册 ───────────────────────────────

def test_no_module_appends_to_the_journal_directly(env):
    """全仓库不得再有 open(sandglass.txt, "a") —— 那是绕过 ID 中枢的后门。"""
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parent.parent / "nexsandglass"
    bad = []
    pat = re.compile(r"open\(\s*(_SANDGLASS|SANDGLASS_TXT)\s*,\s*[\"']a")
    for f in root.rglob("*.py"):
        if pat.search(f.read_text(encoding="utf-8", errors="replace")):
            bad.append(str(f.relative_to(root)))
    assert bad == [], f"这些模块绕过中枢直写日志: {bad}"


def test_plugin_write_goes_through_the_hub(env):
    memid, erasure, log, home = env

    class _Src:
        user_id = "tester"

    class _Ev:
        source = _Src()
        text = "通过插件写进来的一条消息"

    from nexsandglass.interfaces import plugin
    plugin._on_message(_Ev())

    v = memid.verify()
    assert v["memories"] == 1 and v["journal_records"] == 1 and v["ok"] is True, \
        "插件写完之后中枢没跟上 —— 说明它还在绕过 allocate()"


def test_hook_is_claimed_only_once_across_both_plugin_copies(env):
    """两份插件各注册一次 = 每条消息写两份。这是写入放大的形状。"""
    memid, erasure, log, home = env
    calls = []

    class _Ctx:
        def register_hook(self, name, fn):
            calls.append((name, fn))

    from nexsandglass.interfaces import plugin
    from nexsandglass.core import sandglass as core_plugin

    ctx = _Ctx()
    plugin.register(ctx)
    core_plugin.register(ctx)          # 第二份插件
    plugin.register(ctx)               # 网关重连，再来一次
    core_plugin.register(ctx)

    assert len(calls) == 1, f"pre_gateway_dispatch 被挂了 {len(calls)} 次"


def test_api_reader_skips_redacted(env):
    """HTTP 接口把 [REDACTED ...] 当记忆返回 = 对外公布用户删过什么。"""
    memid, erasure, log, home = env
    log.log_message("正常记忆 alpha", sender="user")
    log.log_message(f"要删的 {SECRET}", sender="user")
    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))

    text = (home / "sandglass.txt").read_text(encoding="utf-8")
    kept = [l for l in text.splitlines()
            if l.split(" | ", 2)[-1:] and not l.split(" | ", 2)[-1].lstrip().startswith("[REDACTED")]
    assert any("alpha" in l for l in kept)
    assert not any("REDACTED" in l for l in kept)


# ── 洞 4：中枢落后于日志，verify() 却报 ok ───────────────

def test_verify_catches_hub_behind_journal(env):
    """生产实测：中枢 8852 / 日志 8854，verify() 报 ok=True。

    因为它遍历的是**中枢已有的行**，每一行都能在日志里找到对应 ——
    零 mismatch、零 orphan、零 gap。没进中枢的那两条根本没机会被发现。
    只检查"已知的部分对不对"，永远发现不了"有东西没进来"。
    """
    memid, erasure, log, home = env
    for i in range(4):
        log.log_message(f"记忆{i}", sender="user")
    # 模拟绕过中枢的直写：日志多两行，中枢不知道
    p = home / "sandglass.txt"
    with open(p, "a", encoding="utf-8") as f:
        f.write("2026-09-13 04:00:00 | user | 绕过中枢写进来的一条\n")
        f.write("2026-09-13 04:00:01 | user | 又一条\n")

    v = memid.verify()
    assert v["memories"] == 4 and v["journal_records"] == 6
    assert v["hub_behind"] == 2
    assert v["ok"] is False, "中枢落后 2 条却报 ok —— 巡检会在写路径漏记忆时报全绿"


def test_health_shows_behind_and_goes_red(env):
    memid, erasure, log, home = env
    log.log_message("一条", sender="user")
    with open(home / "sandglass.txt", "a", encoding="utf-8") as f:
        f.write("2026-09-13 04:00:00 | user | 绕过中枢\n")

    c = memid.health()["checks"]["hub_vs_journal"]
    assert c["behind"] == 1 and c["ok"] is False


def test_verify_catches_hub_ahead_journal(env):
    """日志变短（被截断/恢复了旧备份）也必须报出来。"""
    memid, erasure, log, home = env
    for i in range(4):
        log.log_message(f"记忆{i}", sender="user")
    p = home / "sandglass.txt"
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")

    v = memid.verify()
    assert v["hub_ahead"] == 2 and v["ok"] is False


def test_repair_fixes_hub_behind(env):
    """Step 4 的自愈工具能补上尾部缺失的记录（不需要重跑 0 号迁移）。"""
    memid, erasure, log, home = env
    for i in range(3):
        log.log_message(f"记忆{i}", sender="user")
    with open(home / "sandglass.txt", "a", encoding="utf-8") as f:
        f.write("2026-09-13 04:00:00 | user | 绕过中枢写进来的一条\n")
        f.write("2026-09-13 04:00:01 | user | 又一条\n")
    assert memid.verify()["ok"] is False

    rep = memid.repair_from_journal(apply=True)
    assert [m["seq"] for m in rep["missing"]] == [4, 5]
    assert rep["repaired"] == 2
    assert memid.verify()["ok"] is True
    assert memid.get(memid.resolve(5))["text"] == "又一条"


def test_repair_keeps_tombstones_while_catching_up(env):
    """补齐尾部的同时，不能把已删除的记忆复活。"""
    memid, erasure, log, home = env
    log.log_message(f"要删的 {SECRET}", sender="user")
    log.log_message("留着的", sender="user")
    erasure.forget({"contains": SECRET}, apply=True, journal_path=str(home / "sandglass.txt"))
    with open(home / "sandglass.txt", "a", encoding="utf-8") as f:
        f.write("2026-09-13 04:00:00 | user | 新来的一条\n")

    rep = memid.repair_from_journal(apply=True)
    assert rep["repaired"] == 1
    assert memid.verify()["ok"] is True
    assert erasure.verify_erasure([SECRET], str(home / "sandglass.txt"))["clean"] is True
    assert memid.health()["checks"]["erasure_integrity"]["ok"] is True
