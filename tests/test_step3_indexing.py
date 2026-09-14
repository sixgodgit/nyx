"""tests/test_step3_indexing.py — Step 3 验收：索引改为记录粒度

核心问题：所有索引都假设「一个物理行 = 一条记忆」。
多行消息的续行 `_parse_line` 解析不出时间戳 → `if not ts: continue` → **整个跳过**。
生产实测：59462 个物理行里 50611 行（85%）是续行，从未进过任何索引；
关键词只出现在续行里时，FTS5 / 倒排 / TF-IDF / mmap 四路全部 0 命中。

而多行记忆恰恰是长的、有价值的那些（部署方案、决策记录、粘贴的文档）。
"已记录。"这种一行短句索引得好好的，真正该被召回的内容是瞎的。
"""

import os

import pytest


@pytest.fixture()
def idx(monkeypatch, tmp_path):
    home = tmp_path / "nb"
    home.mkdir()
    monkeypatch.setenv("NEXSANDBASE_HOME", str(home))

    from nexsandglass.core import memid, sandglass_sqlite
    from nexsandglass.features import sandglass_vault, shadow_sand

    sg = str(home / "sandglass.txt")
    monkeypatch.setattr(memid, "_SANDGLASS", sg)
    monkeypatch.setattr(memid, "_LOCK", sg + ".lock")
    monkeypatch.setattr(sandglass_vault, "_SANDGLASS", sg)
    monkeypatch.setattr(sandglass_sqlite, "_DB", str(home / "sandglass.db"))
    monkeypatch.setattr(sandglass_sqlite, "_last_sync_mtime", 0)
    sandglass_vault.set_idx_path(str(home / "sandglass.idx"))
    memid.set_db_path(str(home / "nyx.db"))
    shadow_sand.set_shadow_path(str(home / "shadow_sand.db"))
    shadow_sand._conn = None

    yield memid, sandglass_sqlite, sandglass_vault, shadow_sand, home

    shadow_sand._conn = None
    memid.set_db_path(str(tmp_path / "unused.db"))


JOURNAL = (
    "2026-09-12 10:00:00 | user | 部署方案记录\n"
    "  反向代理用 NginxProxy\n"
    "  模型网关是 ThalamusGateway\n"
    "  末行标记 TailMarker\n"
    "2026-09-12 10:00:01 | user | 另一条无关记忆\n"
    "2026-09-12 10:00:02 | agent | 会议纪要\n"
    "  结论 DecisionAlpha\n"
)


def _write(home, content=JOURNAL):
    (home / "sandglass.txt").write_text(content, encoding="utf-8")


# ── 索引粒度 ─────────────────────────────────────────────

def test_fts_indexes_records_not_lines(idx):
    memid, sq, vault, _, home = idx
    _write(home)
    n = sq.sync_all()
    assert n == 3, f"应索引 3 条逻辑记忆，实得 {n}（7 说明还在按物理行）"
    assert sq.count() == 3


def test_index_stores_full_record_text(idx):
    """索引里存的必须是整条记忆，不是首行。"""
    memid, sq, vault, _, home = idx
    _write(home)
    sq.sync_all()
    hits = sq.search("部署方案记录", limit=5)
    assert hits
    _, _, text = hits[0]
    assert "NginxProxy" in text and "TailMarker" in text, "正文被截断成首行了"


@pytest.mark.parametrize("term", ["NginxProxy", "ThalamusGateway", "TailMarker", "DecisionAlpha"])
def test_continuation_line_content_is_searchable(idx, term):
    """只出现在续行里的关键词必须能搜到 —— 这是 Step 3 的全部意义。"""
    memid, sq, vault, _, home = idx
    _write(home)
    sq.sync_all()
    vault.rebuild_index()

    from nexsandglass.core.search_router import SearchRouter
    hits = SearchRouter().search(term, limit=5)
    assert hits, f"{term} 只在续行里，搜不到说明续行仍未被索引"
    assert any(term in h[2] for h in hits)


def test_inverted_index_covers_continuations(idx):
    memid, sq, vault, _, home = idx
    _write(home)
    n_tokens = vault.rebuild_index()
    inv = vault._sync_index()
    assert n_tokens > 0
    assert "nginxproxy" in inv, "续行的 token 没进倒排"
    # 倒排指向记录首行号
    assert inv["nginxproxy"] == [1]


# ── 增量同步不切开多行记忆 ───────────────────────────────

def test_incremental_sync_respects_record_boundary(idx):
    memid, sq, vault, _, home = idx
    _write(home)
    assert sq.sync_all() == 3

    with open(home / "sandglass.txt", "a", encoding="utf-8") as f:
        f.write("2026-09-12 11:00:00 | user | 新增的多行\n  续行含 LaterMarker\n")
    sq._last_sync_mtime = 0
    added = sq.sync_incremental()
    assert added == 1, f"应只新增 1 条记录，实得 {added}"
    assert sq.count() == 4

    hits = sq.search("LaterMarker", limit=5)
    assert hits, "增量索引漏了续行"
    assert "LaterMarker" in hits[0][2]


def test_incremental_is_noop_when_unchanged(idx):
    memid, sq, vault, _, home = idx
    _write(home)
    sq.sync_all()
    sq._last_sync_mtime = 0
    sq.sync_incremental()
    assert sq.sync_incremental() == 0


# ── 实体索引重建 ─────────────────────────────────────────

def test_rebuild_entity_index_is_self_consistent(idx):
    """重建后自洽率必须 100%。生产重建前是 1.1%。"""
    memid, sq, vault, shadow, home = idx
    _write(home, JOURNAL + "2026-09-12 12:00:00 | user | Geely Starray 是我的车\n")
    st = shadow.rebuild_entity_index()
    assert st["records"] == 4
    assert st["entities"] > 0

    health = shadow.entity_index_health()
    assert health["checked"] > 0
    assert health["rate"] == 1.0, f"自洽率 {health['rate']:.1%}，样本 {health['samples']}"


def test_entity_index_health_detects_poison(idx):
    """给实体塞一个错行号，健康度必须掉下来 —— 否则这个指标没有意义。"""
    memid, sq, vault, shadow, home = idx
    _write(home, JOURNAL + "2026-09-12 12:00:00 | user | Geely Starray 是我的车\n")
    shadow.rebuild_entity_index()
    db = shadow._get_conn()
    db.execute("UPDATE entities SET line_nums='1' WHERE name='Geely Starray'")
    db.commit()
    health = shadow.entity_index_health()
    assert health["rate"] < 1.0
    assert health["suspect"] >= 1


def test_rebuild_writes_mem_ids(idx):
    memid, sq, vault, shadow, home = idx
    _write(home, "2026-09-12 12:00:00 | user | Geely Starray 是我的车\n")
    shadow.rebuild_entity_index()
    db = shadow._get_conn()
    name, lns, mids = db.execute(
        "SELECT name, line_nums, mem_ids FROM entities WHERE name='Geely Starray'").fetchone()
    assert lns == "1"
    assert mids.startswith("m_")
    assert memid.compute_mem_id("2026-09-12 12:00:00", "user", "Geely Starray 是我的车") == mids


def test_rebuild_backs_up_old_tables(idx):
    memid, sq, vault, shadow, home = idx
    _write(home)
    shadow.shadow_index("Alpha Bravo 旧数据", line_num=999)
    st = shadow.rebuild_entity_index()
    assert st["backed_up"] is True
    db = shadow._get_conn()
    old = db.execute("SELECT COUNT(*) FROM entities_pre_rebuild").fetchone()[0]
    assert old >= 1, "重建前的表没被备份"


# ── 中文实体：如实承认做不到 ─────────────────────────────

def test_chinese_entities_are_not_extracted_by_design(idx):
    """正则做不了中文实体抽取。这里固化这个事实，避免有人再"顺手补上 group 4"。

    那个模式匹配任意 2-4 个连续汉字，"我买了一辆吉利星越" 会被切成
    "我买了一"/"辆吉利星" —— 产出的全是噪音，比没有更糟。
    """
    memid, sq, vault, shadow, home = idx
    _write(home, "2026-09-12 12:00:00 | user | 我买了一辆吉利星越\n")
    shadow.rebuild_entity_index()
    db = shadow._get_conn()
    names = [r[0] for r in db.execute("SELECT name FROM entities")]
    assert not any("我买了一" in n or "辆吉利星" in n for n in names), \
        "裸中文片段被当成实体了 —— 这是噪音，不是抽取"


def test_quoted_chinese_still_works(idx):
    """引号内的中文仍然会被抽出（group 2/3），这是有意保留的。"""
    memid, sq, vault, shadow, home = idx
    _write(home, '2026-09-12 12:00:00 | user | 他说"吉利星越"很划算\n')
    shadow.rebuild_entity_index()
    db = shadow._get_conn()
    names = [r[0] for r in db.execute("SELECT name FROM entities")]
    assert "吉利星越" in names


# ── 端到端 ───────────────────────────────────────────────

def test_search_router_returns_full_records(idx):
    memid, sq, vault, shadow, home = idx
    _write(home)
    sq.sync_all()
    vault.rebuild_index()
    shadow.rebuild_entity_index()

    from nexsandglass.core.search_router import SearchRouter
    hits = SearchRouter().search("会议纪要", limit=5)
    assert hits
    assert any("DecisionAlpha" in h[2] for h in hits), "返回的正文不是整条记忆"


# ── 排序层：不许把检索对的结果扔掉 ───────────────────────

def test_ranking_does_not_discard_the_correct_record(idx):
    """索引修好了，排序层不能再把正确结果挤出去。

    回归：SearchRouter 曾在 sand_density 之后又跑一次 simhash_rerank，
    把候选全部按「与 query 的汉明距离」重排。实测正确记录从第 0 位被扔到第 30，
    top5 全是 token 碰撞的假阳性。simhash 是近重复检测用的，
    短 query 对长文档算汉明距离没有意义，而且文档越长罚得越狠。
    """
    memid, sq, vault, shadow, home = idx
    lines = []
    for i in range(1, 41):
        if i % 5 == 0:                       # 长记忆，关键词埋在续行
            lines.append(f"2026-09-12 10:00:{i:02d} | user | 长记录{i} 开头\n")
            for k in range(20):
                lines.append(f"  正文第{k}行{' Zq%04dx' % i if k == 10 else ''}\n")
        else:
            lines.append(f"2026-09-12 10:00:{i:02d} | user | 短记录{i} Zq{i:04d}x\n")
    _write(home, "".join(lines))
    sq.sync_all(); vault.rebuild_index()

    from nexsandglass.core.search_router import SearchRouter
    r = SearchRouter()
    for i in (5, 10, 20):
        term = f"Zq{i:04d}x"
        hits = r.search(term, limit=5)
        assert any(term in h[2] for h in hits), \
            f"{term} 在续行里，检索层找得到但排序层把它挤出了 top5：" \
            f"{[h[2][:16] for h in hits]}"


def test_simhash_rerank_is_not_wired_into_pipeline(idx):
    """simhash_rerank 保留为兼容函数，但绝不能再出现在检索管线里。

    只看真正的代码行 —— 注释里提到它是刻意的（解释为什么删掉）。
    """
    import inspect
    from nexsandglass.core import search_router
    src = inspect.getsource(search_router.SearchRouter.search)
    code_lines = [ln.split("#", 1)[0] for ln in src.splitlines()]
    offending = [ln.strip() for ln in code_lines if "simhash_rerank(" in ln]
    assert not offending, f"破坏性重排又被接回管线了: {offending}"


def test_health_reports_no_data_instead_of_vacuous_100(idx):
    """空索引不能报 100% —— 零样本上做除法报满分是假绿。"""
    memid, sq, vault, shadow, home = idx
    _write(home, "2026-09-12 12:00:00 | user | 纯中文没有可抽取的实体\n")
    shadow.rebuild_entity_index()
    h = shadow.entity_index_health()
    assert h["checked"] == 0
    assert h["no_data"] is True
    assert h["rate"] is None, "零样本报了具体数值 —— 又是空集合满分"


# ── 倒排缓存：既存 bug 的回归 ────────────────────────────

def test_sync_index_is_stable_across_calls(idx):
    """连续调用必须每次都返回完整索引。

    回归：`_idx_mtime` 的赋值在函数末尾，而中间分支提前 return，导致它永远是 0；
    下次进来 mtime 必不匹配 → 清缓存 → `return {}`。
    表现为**每隔一次调用返回空索引** —— 倒排和 TF-IDF 在一半的查询里完全失效，
    而调用方看到空又会触发一次全量 rebuild。
    """
    memid, sq, vault, _, home = idx
    _write(home)
    n = vault.rebuild_index()
    assert n > 0
    sizes = [len(vault._sync_index()) for _ in range(6)]
    assert all(x == sizes[0] for x in sizes), f"索引大小在调用间跳变: {sizes}"
    assert sizes[0] == n


def test_large_index_is_still_cached(idx):
    """>5000 词条不该被当成异常丢弃。

    回归：`if len(idx) > 5000: _idx_cache = {}; return {}`。
    生产索引有 20 万词条，这个"防膨胀"分支注定每次触发。
    """
    memid, sq, vault, _, home = idx
    import random
    rnd = random.Random(11)
    alpha = "abcdefghijklmnopqrstuvwxyz"
    lines = []
    for i in range(1, 1200):
        w = " ".join("".join(rnd.choice(alpha) for _ in range(7)) for _ in range(6))
        lines.append(f"2026-09-12 10:00:00 | user | 记录{i} {w}\n")
    _write(home, "".join(lines))
    n = vault.rebuild_index()
    assert n > 5000, f"这个语料应产出 >5000 词条，实得 {n}"
    assert len(vault._sync_index()) == n
    assert len(vault._sync_index()) == n          # 第二次仍然完整


def test_index_picks_up_appended_records(idx):
    """追加记录后，倒排必须增量补上（按记录边界，不按物理行）。"""
    memid, sq, vault, _, home = idx
    _write(home)
    vault.rebuild_index()
    assert "laterterm" not in vault._sync_index()
    with open(home / "sandglass.txt", "a", encoding="utf-8") as f:
        f.write("2026-09-12 11:00:00 | user | 新记录\n  续行含 LaterTerm\n")
    inv = vault._sync_index()
    assert "laterterm" in inv, "增量没补上，或者又按物理行切了"


def test_legacy_idx_file_triggers_full_rebuild(idx):
    """旧格式 idx（无 covered_lines 头）必须全量重建，不能当成"覆盖 0 行"做增量。

    否则会把全部 token 重复追加到已有 posting list 上，索引直接翻倍。
    这是 Step 3 hotfix 的升级路径 —— 服务器上那个 idx 就是旧格式写的。
    """
    memid, sq, vault, _, home = idx
    _write(home)
    vault.rebuild_index()
    good = {t: list(v) for t, v in vault._sync_index().items()}

    # 伪造旧格式：去掉 covered_lines 头
    raw = (home / "sandglass.idx").read_text(encoding="utf-8")
    legacy = "\n".join(l for l in raw.splitlines() if "covered_lines:" not in l) + "\n"
    (home / "sandglass.idx").write_text(legacy, encoding="utf-8")
    vault._idx_cache = None
    vault._idx_mtime = 0

    after = vault._sync_index()
    assert set(after) == set(good), "重建后词条集合变了"
    for t, lines in after.items():
        assert lines == good[t], f"token {t!r} 的 posting list 被重复追加: {lines} vs {good[t]}"
