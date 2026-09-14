"""
NexSandglass — 影子沙 (Shadow Sand)
=====================================
轻量SQLite投影层。不碰沙子原文，只存索引元数据。
投石问路之前先查影子沙——脱口而出级速度。
零依赖：sqlite3是Python stdlib。
"""
import sqlite3
import os
import re
import logging
import threading
from collections import defaultdict

from nexsandglass.core.sandglass_paths import _NB

logger = logging.getLogger(__name__)

_SHADOW_DB = os.path.join(_NB, "shadow_sand.db")


def set_shadow_path(path: str):
    """重定向影子沙路径——基准测试用。"""
    global _SHADOW_DB, _conn
    _SHADOW_DB = path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trust (
    line_num    INTEGER PRIMARY KEY,  -- 对应sandglass.txt行号
    score       REAL DEFAULT 0.5,     -- 信任分 [0,1]
    helpful     INTEGER DEFAULT 0,    -- 好评次数
    unhelpful   INTEGER DEFAULT 0,    -- 差评次数
    retrievals  INTEGER DEFAULT 0,    -- 被检索次数
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entities (
    name        TEXT NOT NULL,
    line_nums   TEXT DEFAULT '',      -- 逗号分隔的行号列表
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

CREATE TABLE IF NOT EXISTS fact_tags (
    line_num    INTEGER PRIMARY KEY,
    category    TEXT DEFAULT 'general',
    tags        TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

# 实体抽取：英文大写多词专名 + 引号内文本。
#
# ⚠ 这里**不含**裸中文。历史版本有第四个分支 `([\u4e00-\u9fff]{2,4})`，
# 但提取代码只读 group 1-3，从未读过它 —— 于是裸中文实体一条都进不去。
# 补上 group 4 不是修复：那个模式匹配任意 2-4 个连续汉字，
# "我买了一辆吉利星越" 会被切成 "我买了一"/"辆吉利星"，产出的全是噪音。
# 所以这里把死分支删掉，如实承认：**正则做不了中文实体抽取**。
# 真要做需要词典或小模型（见 llm_extract），那是独立决策，不在本层伪装。
#
# 后果：中文实体索引基本为空。这不再要紧 —— FTS5/倒排现在索引整条记忆全文，
# 主检索路径不依赖这张表。
_ENTITY_RE = re.compile(
    r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b|'   # 英文大写多词专名
    r'"([^"]+)"|'                                   # 双引号内文本
    r"'([^']+)'"                                    # 单引号内文本
)

_conn = None
_db_lock = threading.Lock()  # 共享连接由 SearchRouter 多线程并发访问——需要外部锁


def _get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_SHADOW_DB, timeout=10, check_same_thread=False)
        # WAL 模式：允许读写并发，显著降低同库多连接时的锁竞争
        try:
            _conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn

def _ensure_memid_cols(db) -> bool:
    """确保 mem_id / mem_ids 列存在（迁移过的库已有，全新库没有）。

    返回 True 表示可以写 mem_id。失败不抛 —— 退回纯 line_num 模式。
    """
    try:
        for table, col in (("trust", "mem_id"), ("fact_tags", "mem_id"), ("entities", "mem_ids")):
            cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
            if col not in cols:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        return True
    except Exception:
        return False


def _maybe_commit():
    """每次写操作结束立即提交，释放 SQLite 写锁。

    原实现按『累积 3 次写才 commit 一次』批提交，导致持久连接 `_conn`
    在两次操作之间的空闲期仍持有 shadow_sand.db 的未提交写事务锁，
    阻塞同库其他连接（如 weavethread 直写、resolve_temporal_conflict）
    写入 → `database is locked`，写入被吞掉、数据静默丢失。

    改为每次写操作后立即 commit：单操作内多次写仍在一个事务里（原子），
    操作结束即释放写锁，不跨操作持锁。`_commit_pending` 计数器随之移除。
    """
    _get_conn().commit()


# ═══════════════════ 查询（脱口而出层） ═══════════════════

def shadow_search(query: str, limit: int = 10) -> list:
    """影子沙优先搜索。返回 [(行号, 信任分), ...]"""
    with _db_lock:
        return _shadow_search_unlocked(query, limit)


def _shadow_search_unlocked(query: str, limit: int = 10) -> list:
    db = _get_conn()
    words = [w for w in re.findall(r'\w+', query.lower()) if len(w) > 1]
    # 方法1: 实体名匹配（最快）
    results = []
    for w in words:
        rows = db.execute(
            "SELECT line_nums FROM entities WHERE name LIKE ? LIMIT 1",
            (f"%{w}%",)
        ).fetchall()
        for row in rows:
            for ln in row[0].split(","):
                if ln.strip().isdigit():
                    results.append(int(ln.strip()))

    # 方法2: 标签匹配
    tag_rows = db.execute(
        "SELECT line_num FROM fact_tags WHERE tags LIKE ? OR category LIKE ? LIMIT ?",
        (f"%{query}%", f"%{query}%", limit)
    ).fetchall()
    for row in tag_rows:
        results.append(row[0])

    # 去重 + 信任加权排序
    if results:
        unique = list(set(results))
        scored = []
        for ln in unique[:limit * 3]:
            tr = db.execute(
                "SELECT score FROM trust WHERE line_num = ?", (ln,)
            ).fetchone()
            score = tr[0] if tr else 0.5
            scored.append((score, ln))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:limit]

    return []


def shadow_boost(candidate_lines: set, limit: int = 10) -> list:
    """对投石问路的候选行号做影子加权排序。
    返回 [(行号, 信任分), ...]"""
    if not candidate_lines:
        return []
    with _db_lock:
        db = _get_conn()
        placeholders = ",".join("?" * len(candidate_lines))
        rows = db.execute(
            f"SELECT line_num, score FROM trust WHERE line_num IN ({placeholders})",
            list(candidate_lines)
        ).fetchall()
        trust_map = {r[0]: r[1] for r in rows}
        scored = [(trust_map.get(ln, 0.5), ln) for ln in candidate_lines]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:limit]


# ═══════════════════ 写入（落沙后同步） ═══════════════════

def shadow_index(text: str, category: str = "general", tags: str = "",
                 line_num: int = 0, mem_id: str = None) -> None:
    """落沙后同步索引。

    line_num / mem_id 由 ID 中枢（memid.allocate）给出，**不再自己猜**。
    历史实现在 line_num<=0 时用 ``COUNT(*)+1`` 估算，一次写入失败就永久错位；
    该退路仍保留以兼容老调用，但会打 WARNING —— 它不该再被走到。
    """
    try:
        from nexsandglass.features.sandglass_think import scene_mode
        if scene_mode() == 'exam':
            category = 'exam_' + category
    except Exception:
        pass
    with _db_lock:
        db = _get_conn()
        has_memid = _ensure_memid_cols(db)
        # 行号由 ID 中枢给出。走到下面这个分支说明调用方没传 —— 那就只能猜，
        # 而猜出来的行号正是历史 provenance 全面污染的来源。
        if line_num <= 0:
            line_num = db.execute("SELECT COUNT(*) FROM trust").fetchone()[0] + 1
            logger.warning(
                "[shadow_index] 未收到行号，退回 COUNT(*)+1 估算（%s）—— "
                "这会造成 provenance 错位，调用方应改为经 memid.allocate 获取", line_num)

        # 提取实体
        for m in _ENTITY_RE.finditer(text):
            name = m.group(1) or m.group(2) or m.group(3) or ""
            name = name.strip()
            if name and len(name) > 1:
                row = db.execute(
                    "SELECT line_nums FROM entities WHERE name = ?", (name,)
                ).fetchone()
                if row:
                    nums = set(row[0].split(",")) | {str(line_num)}
                    db.execute(
                        "UPDATE entities SET line_nums = ? WHERE name = ?",
                        (",".join(sorted(nums, key=int)), name)
                    )
                    if has_memid and mem_id:
                        cur = db.execute(
                            "SELECT mem_ids FROM entities WHERE name = ?", (name,)).fetchone()
                        mids = {m for m in (cur[0] or "").split(",") if m} | {mem_id}
                        db.execute("UPDATE entities SET mem_ids = ? WHERE name = ?",
                                   (",".join(sorted(mids)), name))
                else:
                    db.execute(
                        "INSERT INTO entities (name, line_nums, mem_ids) VALUES (?, ?, ?)"
                        if has_memid else
                        "INSERT INTO entities (name, line_nums) VALUES (?, ?)",
                        (name, str(line_num), mem_id or "") if has_memid else (name, str(line_num))
                    )

        # 写入信任记录
        db.execute(
            "INSERT OR IGNORE INTO trust (line_num, score) VALUES (?, 0.5)",
            (line_num,)
        )
        if has_memid and mem_id:
            db.execute("UPDATE trust SET mem_id = ? WHERE line_num = ?", (mem_id, line_num))

        # 写入标签
        if category != "general" or tags:
            db.execute(
                "INSERT OR REPLACE INTO fact_tags (line_num, category, tags, mem_id)"
                " VALUES (?, ?, ?, ?)" if has_memid else
                "INSERT OR REPLACE INTO fact_tags (line_num, category, tags) VALUES (?, ?, ?)",
                (line_num, category, tags, mem_id) if has_memid else (line_num, category, tags)
            )

        _maybe_commit()


# ═══════════════════ 反馈 ═══════════════════

def shadow_feedback(line_num: int, helpful: bool) -> dict:
    """信任评分反馈。"""
    with _db_lock:
        db = _get_conn()
        row = db.execute(
            "SELECT score, helpful, unhelpful FROM trust WHERE line_num = ?",
            (line_num,)
        ).fetchone()
        if not row:
            db.execute("INSERT INTO trust (line_num, score) VALUES (?, 0.5)", (line_num,))
            old_score = 0.5
        else:
            old_score = row[0]

        delta = 0.05 if helpful else -0.10
        new_score = max(0.0, min(1.0, old_score + delta))
        col = "helpful" if helpful else "unhelpful"

        db.execute(
            f"UPDATE trust SET score = ?, {col} = {col} + 1, updated_at = datetime('now') WHERE line_num = ?",
            (new_score, line_num)
        )
        _maybe_commit()
        return {"line_num": line_num, "old_trust": old_score, "new_trust": new_score}


def shadow_retrieval_bump(line_nums: list) -> None:
    """标记检索——增加retrievals计数。"""
    if not line_nums:
        return
    with _db_lock:
        db = _get_conn()
        placeholders = ",".join("?" * len(line_nums))
        db.execute(
            f"UPDATE trust SET retrievals = retrievals + 1 WHERE line_num IN ({placeholders})",
            line_nums
        )
        _maybe_commit()


# ═══════════════════ 重建（Step 3） ═══════════════════

def rebuild_entity_index(progress=None) -> dict:
    """从 ID 中枢全量重建实体 / 信任 / 标签索引。

    为什么必须重建而不是继续回填：历史 line_num 是 `COUNT(*)+1` 猜出来的，
    生产实测 3458 条 entity→line 映射里只有 38 条自洽（1.1%）。
    在一张 98.9% 错的表上做增量修补没有意义 —— 推倒，用中枢的真实
    line_start / mem_id 重来。

    trust 表的 helpful/unhelpful 是用户反馈，但它们挂在错误的行号上，
    无法映射回正确的记忆，因此不保留（旧表会先备份到 *_pre_rebuild）。

    返回统计 dict。
    """
    from nexsandglass.core import memid
    stats = {"records": 0, "entities": 0, "links": 0,
             "trust_rows": 0, "feedback_lost": 0, "backed_up": False}

    with _db_lock:
        db = _get_conn()
        # 备份旧表（只备份带用户反馈的部分，纯默认值没有保留价值）
        try:
            stats["feedback_lost"] = db.execute(
                "SELECT COUNT(*) FROM trust WHERE helpful>0 OR unhelpful>0").fetchone()[0]
        except Exception:
            stats["feedback_lost"] = -1
        for t in ("entities", "trust", "fact_tags"):
            try:
                db.execute(f"DROP TABLE IF EXISTS {t}_pre_rebuild")
                db.execute(f"CREATE TABLE {t}_pre_rebuild AS SELECT * FROM {t}")
            except Exception:
                pass
        stats["backed_up"] = True

        for t in ("entities", "trust", "fact_tags"):
            try:
                db.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        _ensure_memid_cols(db)

        ent_lines: dict = {}
        ent_mids: dict = {}
        for rec in memid.parse_journal_records():
            if memid.is_redacted(rec):
                continue                          # 已抹除：不重建索引
            stats["records"] += 1
            mid = memid.compute_mem_id(rec["ts"], rec["sender"], rec["text"])
            ln = rec["line_start"]
            db.execute("INSERT OR IGNORE INTO trust (line_num, score) VALUES (?, 0.5)", (ln,))
            db.execute("UPDATE trust SET mem_id=? WHERE line_num=?", (mid, ln))
            stats["trust_rows"] += 1
            for m in _ENTITY_RE.finditer(rec["text"]):
                name = (m.group(1) or m.group(2) or m.group(3) or "").strip()
                if len(name) > 1:
                    ent_lines.setdefault(name, set()).add(ln)
                    ent_mids.setdefault(name, set()).add(mid)
                    stats["links"] += 1
            if progress and stats["records"] % 1000 == 0:
                progress(stats["records"])

        for name, lns in ent_lines.items():
            db.execute("INSERT INTO entities (name, line_nums, mem_ids) VALUES (?,?,?)",
                       (name, ",".join(str(x) for x in sorted(lns)),
                        ",".join(sorted(ent_mids[name]))))
        stats["entities"] = len(ent_lines)
        _maybe_commit()
    return stats


def entity_index_health() -> dict:
    """实体索引自洽率：实体说自己在第 N 行 —— 那条记忆里到底有没有它。

    这是唯一能量化索引质量的指标。生产迁移前是 1.1%。
    """
    from nexsandglass.core import memid
    line2text = {}
    for rec in memid.parse_journal_records():
        for n in range(rec["line_start"], rec["line_end"] + 1):
            line2text[n] = rec["text"]
    ok = bad = 0
    samples = []
    with _db_lock:
        db = _get_conn()
        try:
            rows = db.execute("SELECT name, line_nums FROM entities").fetchall()
        except Exception:
            return {"checked": 0, "ok": 0, "suspect": 0, "rate": 0.0, "samples": []}
    for name, lns in rows:
        for x in (lns or "").split(","):
            x = x.strip()
            if not x.isdigit():
                continue
            t = line2text.get(int(x))
            if t is not None and name.lower() in t.lower():
                ok += 1
            else:
                bad += 1
                if len(samples) < 5:
                    samples.append((name, int(x)))
    tot = ok + bad
    # 零样本不算 100%。空集合上做除法报满分，是"失败与成功不可区分"的老毛病 ——
    # 索引是空的和索引全对，指标必须给出不同的答案。
    return {"checked": tot, "ok": ok, "suspect": bad,
            "rate": (ok / tot) if tot else None,
            "no_data": tot == 0, "samples": samples}
