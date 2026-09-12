"""tests/test_step2_writepath.py — Step 2 验收：写路径接入 ID 中枢

核心断言只有一句：**行号不再需要被猜，所以下游失败不再传染。**

历史上 log_message 把 line_num=0 传给 shadow_index，后者用 COUNT(*)+1 估算；
一次索引异常被吞掉，计数就永久错位一格，之后每条记忆的 provenance 全错。
生产实测：8846 条记忆的 entity→line 映射自洽率 **1.1%**。

这里把那个指标搬进测试 —— 注入失败后仍须 100% 自洽。
"""

import sqlite3

import pytest


@pytest.fixture()
def env(monkeypatch, tmp_path):
    home = tmp_path / "nb"
    home.mkdir()
    monkeypatch.setenv("NEXSANDBASE_HOME", str(home))

    from nexsandglass.core import memid, sandglass_log
    from nexsandglass.features import shadow_sand

    sg = str(home / "sandglass.txt")
    monkeypatch.setattr(memid, "_SANDGLASS", sg)
    monkeypatch.setattr(memid, "_LOCK", sg + ".lock")
    monkeypatch.setattr(sandglass_log, "_SANDGLASS", sg)
    memid.set_db_path(str(home / "nyx.db"))
    shadow_sand.set_shadow_path(str(home / "shadow_sand.db"))
    shadow_sand._conn = None

    yield sandglass_log, memid, shadow_sand, home

    shadow_sand._conn = None
    memid.set_db_path(str(tmp_path / "unused.db"))


def _entities(home):
    db = sqlite3.connect(str(home / "shadow_sand.db"))
    return db.execute("SELECT name, line_nums, mem_ids FROM entities").fetchall()


def _self_consistency(memid, home):
    """复算 provenance 自洽率：实体说自己在第 N 行 —— 那第 N 行到底有没有它。"""
    recs = memid.parse_journal_records(str(home / "sandglass.txt"))
    line2text = {}
    for r in recs:
        for n in range(r["line_start"], r["line_end"] + 1):
            line2text[n] = r["text"]
    ok = bad = 0
    for name, lns, _ in _entities(home):
        for x in (lns or "").split(","):
            if not x.strip().isdigit():
                continue
            t = line2text.get(int(x))
            if t is not None and name.lower() in t.lower():
                ok += 1
            else:
                bad += 1
    return ok, bad


# ── 基本契约 ─────────────────────────────────────────────

def test_log_message_creates_hub_row(env):
    log, memid, _, home = env
    assert log.log_message("Geely Starray 是我的车", sender="user") is True
    rep = memid.verify(str(home / "sandglass.txt"))
    assert rep["memories"] == 1 and rep["journal_records"] == 1 and rep["ok"] is True


def test_return_id(env):
    log, memid, _, _ = env
    mid = log.log_message("测试一条", sender="user", return_id=True)
    assert mid.startswith("m_")
    assert memid.get(mid)["text"] == "测试一条"


def test_low_value_agent_reply_writes_nothing(env):
    log, memid, _, home = env
    assert log.log_message("好的", sender="agent") is False
    assert memid.count() == 0
    assert not (home / "sandglass.txt").exists() or \
           (home / "sandglass.txt").read_text(encoding="utf-8") == ""


def test_shadow_gets_real_line_not_a_guess(env):
    """多行消息之后，下一条的行号必须是真实行号，而不是记忆条数。"""
    log, memid, _, home = env
    log.log_message("第一条", sender="user")
    log.log_message("多行开头\n续行1\n续行2", sender="user")      # 占 2-4 行
    mid = log.log_message("Amsterdam Oracle 是欧洲服务器", sender="user", return_id=True)

    row = memid.get(mid)
    assert row["line_start"] == 5, "第三条应从第 5 行开始（前两条占了 1 + 3 行）"

    ents = dict((n, l) for n, l, _ in _entities(home))
    assert ents["Amsterdam Oracle"] == "5", \
        f"影子沙记的是 {ents['Amsterdam Oracle']}，若为 3 说明又在按记忆条数猜"


# ── 核心：下游失败不再传染 ───────────────────────────────

def test_downstream_failure_does_not_drift(env, monkeypatch):
    log, memid, shadow_sand, home = env

    real = shadow_sand.shadow_index
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] in (2, 5):                       # 第 2、5 次写入时索引挂掉
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **k)

    monkeypatch.setattr(shadow_sand, "shadow_index", flaky)

    names = ["Geely Starray", "Amsterdam Oracle", "Nginx Proxy",
             "Thalamus Gateway", "Xian Delicious", "Odido Fiber"]
    for nm in names:
        assert log.log_message(f"{nm} 是一个实体", sender="user") is True

    # 记忆一条没丢
    rep = memid.verify(str(home / "sandglass.txt"))
    assert rep["memories"] == len(names) and rep["ok"] is True

    # 索引失败的两条没进影子沙，但**活下来的每一条都指向正确的行**
    ok, bad = _self_consistency(memid, home)
    assert bad == 0, f"仍有 {bad} 条 provenance 指错 —— 漂移没根除"
    assert ok == len(names) - 2, f"预期 {len(names)-2} 条自洽，实得 {ok}"


def test_provenance_self_consistency_is_total(env, monkeypatch):
    """生产上这个指标是 1.1%。注入失败后仍须 100%。"""
    log, memid, shadow_sand, home = env

    real = shadow_sand.shadow_index
    n = {"i": 0}

    def flaky(*a, **k):
        n["i"] += 1
        if n["i"] % 7 == 0:                            # 每 7 条挂一次
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **k)

    monkeypatch.setattr(shadow_sand, "shadow_index", flaky)

    # 注意：shadow_sand 的实体正则只读 group1-3，裸中文（group4）永远提不出来，
    # 所以这里用英文双词专名 —— 测的是行号是否漂移，不是抽取能力。
    w1 = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"]
    w2 = ["One", "Two", "Three", "Four", "Five"]
    names = [f"{a} {b}" for a in w1 for b in w2]        # 40 个唯一双词专名
    for i, nm in enumerate(names, 1):
        body = f"{nm} 是一条记忆"
        if i % 5 == 0:                                 # 掺入多行消息
            body += "\n附注第一行\n附注第二行"
        log.log_message(body, sender="user")

    ok, bad = _self_consistency(memid, home)
    assert ok > 0
    assert bad == 0, f"自洽率 {ok/(ok+bad):.1%}，应为 100%"
    assert memid.verify(str(home / "sandglass.txt"))["ok"] is True


# ── 拒绝裸写 ─────────────────────────────────────────────

def test_lock_timeout_writes_nothing(env, monkeypatch):
    """锁超时旧实现会"强制裸写"，写出一条中枢不知道的记忆。现在必须整条放弃。"""
    log, memid, _, home = env
    log.log_message("正常的一条", sender="user")
    before_mem = memid.count()
    before_lines = len((home / "sandglass.txt").read_text(encoding="utf-8").splitlines())

    from nexsandglass.core import memid as m

    class Boom:
        def __enter__(self):
            raise TimeoutError("获取沙漏锁超时")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(m, "_FileLock", lambda *a, **k: Boom())
    assert log.log_message("应该写不进去的一条", sender="user") is False

    assert memid.count() == before_mem
    assert len((home / "sandglass.txt").read_text(encoding="utf-8").splitlines()) == before_lines


def test_multiline_roundtrip(env):
    log, memid, _, home = env
    body = "决策记录：\n1. 先做 ID 中枢\n2. 再修 forget\n3. 最后接向量"
    mid = log.log_message(body, sender="user", return_id=True)
    row = memid.get(mid)
    assert row["text"] == body
    assert (row["line_start"], row["line_end"]) == (1, 4)
    assert memid.verify(str(home / "sandglass.txt"))["ok"] is True


def test_knowledge_graph_gets_provenance(env):
    """历史上 99 条三元组里 93 条 source_line=0，图谱无法回溯来源。"""
    log, memid, _, home = env
    mid = log.log_message("我买了 Geely Starray 这辆车", sender="user", return_id=True)
    db = sqlite3.connect(str(home / "shadow_sand.db"))
    try:
        rows = db.execute(
            "SELECT source_line, source_mem_id FROM wthread_triples").fetchall()
    except sqlite3.OperationalError:
        pytest.skip("本次未抽出三元组")
    if not rows:
        pytest.skip("本次未抽出三元组")
    for source_line, source_mem_id in rows:
        assert source_line != 0, "source_line 仍是 0 —— 来源丢失"
        assert source_mem_id == mid


def test_allocate_when_hub_lags_journal(env):
    """中枢落后于日志时，新记录的行号必须接在**日志实际末尾**之后。

    回归：生产上 Step 2 上线前中枢 8846 / 日志 8847（hermes 绕过中枢写过一条）。
    旧实现用 DB 的 MAX(line_end) 推算追加位置，会算出一个已被占用的行号。
    """
    log, memid, _, home = env
    log.log_message("第一条", sender="user")
    log.log_message("第二条", sender="user")

    # 模拟外部直写：绕过中枢，直接往日志追加两条（含一条多行）
    with open(home / "sandglass.txt", "a", encoding="utf-8") as f:
        f.write("2026-09-11 10:00:00 | user | 中枢不知道的一条\n")
        f.write("2026-09-11 10:00:01 | user | 中枢不知道的多行\n  续行\n")

    rep = memid.verify(str(home / "sandglass.txt"))
    assert rep["memories"] == 2 and rep["journal_records"] == 4, "前置条件：中枢落后 2 条"

    mid = log.log_message("Alpha Bravo 新写入的一条", sender="user", return_id=True)
    row = memid.get(mid)
    lines = (home / "sandglass.txt").read_text(encoding="utf-8").splitlines()

    assert row["line_start"] == 6, f"应落在第 6 行，实得 {row['line_start']}"
    assert row["text"] in lines[row["line_start"] - 1], "DB 记的行号与文件实际位置对不上"
    assert len(lines) == 6


def test_line_count_cache_survives_external_append(env):
    """外部追加后缓存必须失效（以文件大小为键）。"""
    log, memid, _, home = env
    log.log_message("第一条", sender="user")
    with open(home / "sandglass.txt", "a", encoding="utf-8") as f:
        f.write("2026-09-11 10:00:00 | user | 外部追加\n")
    mid = log.log_message("Charlie Delta 之后写的", sender="user", return_id=True)
    row = memid.get(mid)
    lines = (home / "sandglass.txt").read_text(encoding="utf-8").splitlines()
    assert row["line_start"] == 3
    assert row["text"] in lines[2]
