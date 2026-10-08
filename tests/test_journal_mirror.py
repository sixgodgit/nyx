"""tests/test_journal_mirror.py — 日志内存镜像（v7.13.2）

召回里有两件事和历史长度成正比：每次查询数两遍日志行数、无索引命中时逐行扫描整份日志。
镜像把它们变成增量的。这里钉住三件事：结果与直接读文件一致、增量与重读的边界对、
**遗忘之后镜像里不能留着旧正文**（兜底扫描绕过所有索引，是最后一个漏点）。
"""
import json
import os

from nexsandglass.core import journal_mirror as jm
from tests.test_hermes_provider import env, _provider, _reindex  # noqa: F401


def _lines(p):
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def _write(p, text, mode="a"):
    with open(p, mode, encoding="utf-8") as f:
        f.write(text)


def test_count_and_records_match_file(tmp_path):
    p = str(tmp_path / "sandglass.txt")
    _write(p, "2026-01-01 00:00:00 | user | 第一条 Alpha\n续行没有头\n2026-01-01 00:01:00 | agent | 第二条\n", "w")
    assert jm.line_count(p) == _lines(p) == 3
    assert [(r[0], r[2], r[3]) for r in jm.records(p)] == [(1, "第一条 Alpha", "第一条 alpha"), (3, "第二条", "第二条")]

    _write(p, "2026-01-02 00:00:00 | user | 追加的 Beta\n")                 # 追加：增量读
    assert jm.line_count(p) == _lines(p) == 4
    assert jm.records(p)[-1][:3] == (4, "2026-01-02 00:00:00", "追加的 Beta")

    _write(p, "2026-01-02 00:01:00 | user | 半行")                          # 没写完的半行
    assert jm.line_count(p) == _lines(p) == 5
    assert jm.records(p)[-1][2] == "追加的 Beta"                            # 半行先不收
    _write(p, "写完了\n")
    assert jm.records(p)[-1][2] == "半行写完了" and jm.line_count(p) == _lines(p) == 5


def test_replaced_or_truncated_file_is_reread(tmp_path):
    p = str(tmp_path / "sandglass.txt")
    _write(p, "".join(f"2026-01-01 00:00:{i:02d} | user | 原文{i}\n" for i in range(10)), "w")
    assert len(jm.records(p)) == 10
    tmp = p + ".tmp"                                                       # os.replace：遗忘 / 还原的写法
    _write(tmp, "".join(f"2026-01-01 00:00:{i:02d} | user | 改写{i}\n" for i in range(10)), "w")
    os.replace(tmp, p)
    assert {r[2] for r in jm.records(p)} == {f"改写{i}" for i in range(10)}
    _write(p, "2026-01-01 00:00:00 | user | 只剩一条\n", "w")              # 截短
    assert [r[2] for r in jm.records(p)] == ["只剩一条"] and jm.line_count(p) == 1


def test_in_place_edit_with_same_size_tail_mismatch_rereads(tmp_path):
    """同一个文件、变长了、但旧末尾被改过 → 不能按增量拼，必须重读。"""
    p = str(tmp_path / "sandglass.txt")
    _write(p, "2026-01-01 00:00:00 | user | 甲乙丙\n", "w")
    jm.records(p)
    with open(p, "r+b") as f:                                              # 原地改掉最后几个字节
        data = f.read().replace("甲乙丙".encode(), "丁戊己".encode())
        f.seek(0); f.write(data + "2026-01-02 00:00:00 | user | 新的\n".encode())
    assert [r[2] for r in jm.records(p)] == ["丁戊己", "新的"]


def test_size_cap_falls_back_to_file_scan(tmp_path, monkeypatch):
    p = str(tmp_path / "sandglass.txt")
    _write(p, "".join(f"2026-01-01 00:00:00 | user | 第{i}条 记录 needle-{i}\n" for i in range(200)), "w")
    monkeypatch.setenv("NYX_JOURNAL_MIRROR_MB", "0.001")
    jm.invalidate(p)
    assert jm.records(p) is None and jm.line_count(p) == 200
    from nexsandglass.core.search_router import MmapFallback
    hits = MmapFallback(p).search("needle-123", 5)
    assert hits and "needle-123" in hits[0][2]


def test_forgotten_text_never_served_from_mirror(env):  # noqa: F811
    """先让镜像把记录读进内存，再遗忘：兜底扫描必须立刻看不到它。"""
    from nexsandglass.core.search_router import MmapFallback
    p = _provider()
    p.sync_turn("我的护照号码是 E99887766", "")
    p.sync_turn("今天天气不错", "")
    assert any("E99887766" in h[2] for h in MmapFallback().search("E99887766", 5))   # 镜像已建
    out = json.loads(p.handle_tool_call("nyx_forget", {"contains": "E99887766", "confirm": True}))
    assert out["status"] == "quarantined"
    assert not any("E99887766" in h[2] for h in MmapFallback().search("E99887766", 5))
    assert not any("E99887766" in r[2] for r in jm.records(env.journal))
    json.loads(p.handle_tool_call("nyx_restore", {"mem_id": out["mem_ids"][0]}))
    assert any("E99887766" in h[2] for h in MmapFallback().search("E99887766", 5))   # 还原后回来


def test_fallback_results_identical_to_file_scan(env, monkeypatch):  # noqa: F811
    from nexsandglass.core.search_router import MmapFallback
    p = _provider()
    for i in range(60):
        p.sync_turn(f"第{i}次和老周讨论预算表，金额 {i * 100} 欧", "")
    for q in ("预算", "老周", "金额 3", "不存在的词", "欧"):
        mem = MmapFallback().search(q, 10)
        monkeypatch.setenv("NYX_JOURNAL_MIRROR_MB", "0")
        jm.invalidate()
        disk = MmapFallback().search(q, 10)
        monkeypatch.delenv("NYX_JOURNAL_MIRROR_MB")
        jm.invalidate()
        assert mem == disk, q


def test_new_message_extends_index_in_memory_without_rereading_file(env, monkeypatch):  # noqa: F811
    """v7.13.2 实测：15 万条时，每写一句话，下一次查询都把 11MB 的 sandglass.idx 整份重读（1.6s）
    再整份重写（0.6s）—— 真实对话里每轮查询 1.3–2.0 秒。现在新记录直接补进内存。"""
    import builtins
    from nexsandglass.core import sandglass_log
    from nexsandglass.features import sandglass_vault as v
    p = _provider()
    for i in range(20):
        p.sync_turn(f"第{i}条 背景记录 老周", "")
    _reindex(env)
    v._sync_index()
    opened = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open",
                        lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    monkeypatch.setattr(v, "PERSIST_EVERY_LINES", 10 ** 9)
    monkeypatch.setattr(v, "PERSIST_EVERY_SECONDS", 10 ** 9)
    sandglass_log.log_message("新写入的一句：护照放在书房抽屉 zz-unique-token", "user")
    idx = v._sync_index()
    assert "zz-unique-token" in idx or any(t in idx for t in ("uniq", "uniqu", "护照"))
    assert not [f for f in opened if os.path.abspath(f) == os.path.abspath(v._IDX)], \
        "有新行时又去整份读 / 写 idx 文件了"


def test_deferred_index_persistence_round_trips(env, monkeypatch):  # noqa: F811
    """攒着没落盘的增量：flush 后冷启动读回来的索引与内存里的一致；不 flush 也只是从文件覆盖的行起重补。"""
    from nexsandglass.features import sandglass_vault as v
    p = _provider()
    for i in range(10):
        p.sync_turn(f"第{i}条 记录 甲乙", "")
    _reindex(env)
    v._sync_index()
    monkeypatch.setattr(v, "PERSIST_EVERY_LINES", 10 ** 9)
    monkeypatch.setattr(v, "PERSIST_EVERY_SECONDS", 10 ** 9)
    for i in range(10):
        p.sync_turn(f"后来的第{i}条 丙丁 mark{i}x", "")
    live = {k: sorted(set(x)) for k, x in v._sync_index().items()}
    v._idx_cache, v._idx_mtime = None, 0                     # 没 flush 就"重启"
    assert {k: sorted(set(x)) for k, x in v._sync_index().items()} == live
    v.flush_index()
    v._idx_cache, v._idx_mtime = None, 0                     # flush 之后再"重启"
    assert {k: sorted(set(x)) for k, x in v._sync_index().items()} == live
    hdr = [l for l in open(v._IDX, encoding="utf-8") if l.startswith("# covered_lines:")]
    assert int(hdr[0].split(":")[1]) == v._journal_lines()


def test_background_persist_never_overwrites_a_purge(env, monkeypatch):  # noqa: F811
    """后台落盘写的是旧快照；如果期间发生了遗忘，那份快照不能盖掉刚抹掉 posting 的文件。"""
    import threading
    import time
    from nexsandglass.core import memid
    from nexsandglass.features import sandglass_vault as v
    p = _provider()
    p.sync_turn("要被遗忘的 secretword 记录", "")
    for i in range(5):
        p.sync_turn(f"普通记录 {i}", "")
    _reindex(env)
    v._sync_index()
    line = memid.get_conn().execute("SELECT line_start FROM memories WHERE text LIKE '%secretword%'").fetchone()[0]
    gate = threading.Event()
    real_body = v._write_idx_body

    def slow_body(f, idx, covered, lens=None):
        if lens is not None:
            gate.wait(5)                                      # 后台落盘卡在写的中途
        return real_body(f, idx, covered, lens)

    monkeypatch.setattr(v, "_write_idx_body", slow_body)
    with v._idx_lock:
        v._persist_async()                                   # 拿到遗忘之前的快照
    out = json.loads(p.handle_tool_call("nyx_forget", {"contains": "secretword", "confirm": True}))
    assert out["status"] == "quarantined"
    gate.set()
    for _ in range(250):
        if not v._persisting:
            break
        time.sleep(0.02)
    assert not v._persisting
    still = [l.split(":")[0] for l in open(v._IDX, encoding="utf-8")
             if not l.startswith("#") and ":" in l and str(line) in l.strip().split(":", 1)[1].split(",")]
    assert not still, f"后台落盘用旧快照把遗忘之前的索引写回去了：{still}"
