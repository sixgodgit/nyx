"""
engram/loops/temporal_fact.py — 双时态事实（v7.7）

两条时间轴，不能混为一条
========================
    有效时间（valid time）   这件事在世界里**何时为真**     valid_from / valid_until
    记录时间（record time）  系统**何时知道**这件事          recorded_at / closed_at / retracted_at

v7.6 及以前只有第一条轴，而且那条轴上填的其实是第二条轴的值：
`valid_from = now`、`valid_until = now` —— 把"写进库的那一刻"当成了"事情发生的那一刻"。
于是用户说「我去年就搬到伦敦了」，系统记下的是「今天起住伦敦」；
而「我当时以为是什么、后来为什么改了看法」这个问题，根本没有数据可以回答。

人的记忆珍贵的恰恰是后者。一个陪人一辈子的记忆系统，必须能区分：

    「我 3 月以为你住海牙」            ← 记录时间轴上的一次信念
    「其实你 1 月就搬去阿姆斯特丹了」  ← 有效时间轴上的一次更正

这也是来源追踪 / 投毒防御的地基：污染发生在「知道」的那一刻，不在「为真」的那一刻。

列的语义
========
    valid_from    何时开始为真。调用方陈述了就用陈述值，否则只能假设 = recorded_at
    valid_until   何时不再为真（NULL = 仍然为真）
    valid_basis   'stated'  有效时间是陈述出来的（"我 2023 年起住海牙"）
                  'assumed' 有效时间是假设的 —— 只知道何时得知，不知道何时开始
                  **这一列是诚实性的核心**：假设的时间不能冒充陈述的时间
    recorded_at   系统何时得知这条事实
    closed_at     系统何时得知它**结束了**（即何时写入 valid_until）
    retracted_at  系统何时**整条撤回**这个版本（被更精确的版本取代）

为什么是 closed_at 而不是每次都复制一行
--------------------------------------
最常见的情形是"开放区间被新事实截断"（搬家、换车）。这时旧行的起点信念没变，
只是多知道了一个终点 —— 就地写 valid_until + closed_at 就能完整重建任意时刻的信念：

    在记录时刻 K 看到的 valid_until = (closed_at <= K) ? valid_until : NULL

只有**已经有终点的区间被改写**（乱序到达的更正、陈述时间精化假设时间）时，
才撤回旧版本（retracted_at）并插入新版本 —— 否则会丢掉"K 时刻我以为终点在哪"。

单值关系（TEMPORAL_RELATIONS）的写入规则
========================================
    新事实落在某个已知区间内、且对象不同  → 截断那个区间（冲突）
    新事实落在所有已知区间之前（乱序到达）→ 新事实的终点 = 下一个区间的起点，不动现任
    同对象、已被覆盖                      → 去重，不重复写
    同对象、原来是 assumed、现在陈述了更早的起点 → 精化（撤回旧版本，插入 stated 版本）

非时效关系（"喜欢"、"对比"……）可以同时有多个值，不做冲突截断。

纯 stdlib，零依赖。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# 关系类型：地点/状态类关系需要时效性处理
# 语义约定：只有本集合内的 relation 才做「冲突→旧值截断+写新值」的时序覆盖；
# 集合外的 relation（如"座驾"、"喜欢"）走非时序分支，不查冲突。
# 集成者如需对某 relation 做时序覆盖，须先加入此集合。
TEMPORAL_RELATIONS = frozenset({
    "住在", "位于", "使用", "常用", "主要用",
    "邮箱", "电话", "地址", "住址",
    "公司", "职位",
})

_FMT = "%Y-%m-%dT%H:%M:%SZ"
_FAR = "9999-12-31T23:59:59Z"          # "现在"的记录时刻：看最新信念

_BITEMPORAL_COLS = (
    ("valid_from", "TEXT"), ("valid_until", "TEXT"),
    ("recorded_at", "TEXT"), ("closed_at", "TEXT"), ("retracted_at", "TEXT"),
    ("valid_basis", "TEXT"), ("source_mem_id", "TEXT"),
)


@dataclass
class TemporalFactReport:
    """一次写入的报告。前三个字段保持 v6.0 形状（调用方兼容）。"""

    expired: list[str] = field(default_factory=list)    # 被截断/撤回的冲突行 id
    inserted: bool = False
    conflicts: list[dict] = field(default_factory=list)
    # ── v7.7 双时态 ──
    inserted_id: Optional[int] = None
    recorded_at: str = ""
    valid_from: str = ""
    valid_until: Optional[str] = None
    valid_basis: str = ""
    late_arrival: bool = False      # 新事实早于已知的现任 → 作为历史插入，不动现任
    refined: list[int] = field(default_factory=list)    # 被"陈述时间"精化的 assumed 行
    retracted: list[int] = field(default_factory=list)  # 被撤回的旧版本


# ══════════════════════════════════════════════════════════
# 时间规范化
# ══════════════════════════════════════════════════════════

def normalize_ts(value) -> Optional[str]:
    """把各种时间写法统一成 `YYYY-MM-DDTHH:MM:SSZ`（UTC）。

    必须统一：比较全靠字符串序。库里历史上混着 `2026-09-25 10:00:00`
    （SQLite datetime('now')）和 `2026-09-25T10:00:00Z` 两种格式，
    `' ' < 'T'`，同一天的两种写法比较结果是错的。

    接受 datetime、`2025`、`2025-06`、`2025-06-01`、带时分秒的各种变体、带时区的 ISO。
    解析不了就抛 —— 猜一个时间比没有时间更糟。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime(_FMT)
    s = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(s, fmt).strftime(_FMT)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return normalize_ts(d)
    except ValueError:
        raise ValueError(f"无法解析时间: {value!r}")


# ══════════════════════════════════════════════════════════
# 建表与迁移（模块自足 —— v7.3 缺陷3 的教训）
# ══════════════════════════════════════════════════════════

def _ensure_table(db_path: str) -> None:
    """确保 wthread_triples 表存在（temporal_fact 自足，不依赖外部建表）。

    表 DDL 原本只存在于 `weavethread._ensure_table()`，且仅在 weavethread
    的写入口被调用。第三方若直接使用本模块而未先触发 weavethread 初始化，
    `wthread_triples` 不存在 → `no such table`，写路径被吞、读路径崩溃。
    """
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wthread_triples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                relation TEXT NOT NULL,
                object TEXT NOT NULL,
                source_line INTEGER,
                confidence REAL DEFAULT 0.5,
                source TEXT DEFAULT 'regex',
                valid_from TEXT,
                valid_until TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wthread_subject ON wthread_triples(subject)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wthread_relation ON wthread_triples(relation)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wthread_object ON wthread_triples(object)")
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """补齐双时态列，并回填旧行。幂等，每次调用都安全。

    旧行回填规则（全部标 assumed —— 这是诚实，不是保守）：
      recorded_at = created_at（统一成 ISO Z 格式）
      valid_from  = 原值；原值为空则 = recorded_at
                    （旧的 wthread_store 从不写 valid_from，导致 as_of 永远查不到它们）
      closed_at   = valid_until（旧代码在得知新值的那一刻写 valid_until，二者本就同一时刻）
      valid_basis = 'assumed'（旧代码的 valid_from 就是写入时刻，从来不是陈述出来的）
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(wthread_triples)")}
    for col, typ in _BITEMPORAL_COLS:
        if col not in cols:
            conn.execute(f"ALTER TABLE wthread_triples ADD COLUMN {col} {typ}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wthread_recorded ON wthread_triples(recorded_at)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wthread_subj_rel ON wthread_triples(subject, relation)")
    # 先查后写：查询路径每次都会走到这里，无条件 UPDATE 会在读路径上抢写锁 ——
    # 同一个 shadow_sand.db 在 v7.3 出过 `database is locked` 死锁，不再给它加一个写者
    if conn.execute(
            "SELECT 1 FROM wthread_triples WHERE recorded_at IS NULL LIMIT 1").fetchone() is None:
        conn.commit()
        return
    rec = ("CASE WHEN created_at IS NULL OR created_at = '' "
           "     THEN COALESCE(valid_from, '1970-01-01T00:00:00Z') "
           "     WHEN instr(created_at, 'T') > 0 THEN created_at "
           "     ELSE replace(created_at, ' ', 'T') || 'Z' END")
    # SQLite 的 SET 表达式都读**旧行**的值，所以这里一条语句同时回填四列是安全的
    conn.execute(
        f"UPDATE wthread_triples SET "
        f"  valid_basis = COALESCE(valid_basis, 'assumed'), "
        f"  closed_at = CASE WHEN valid_until IS NOT NULL AND closed_at IS NULL "
        f"                   THEN valid_until ELSE closed_at END, "
        f"  valid_from = COALESCE(valid_from, {rec}), "
        f"  recorded_at = {rec} "
        f"WHERE recorded_at IS NULL")
    conn.commit()


def ensure_temporal_columns(db_path: str) -> None:
    """建表（若无）+ 补双时态列 + 回填旧行。幂等。"""
    _ensure_table(db_path)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        _migrate(conn)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════════

def _cols(conn) -> list:
    return [r[1] for r in conn.execute("PRAGMA table_info(wthread_triples)")]


def _insert(conn, row: dict) -> int:
    cols = set(_cols(conn))
    use = [k for k in row if k in cols and k != "id"]
    cur = conn.execute(
        f"INSERT INTO wthread_triples ({','.join(use)}) VALUES ({','.join('?' * len(use))})",
        tuple(row[k] for k in use))
    return cur.lastrowid


def _row(conn, row_id: int) -> dict:
    c = _cols(conn)
    r = conn.execute(f"SELECT {','.join(c)} FROM wthread_triples WHERE id=?",
                     (row_id,)).fetchone()
    return dict(zip(c, r)) if r else {}


def _retract_and_copy(conn, row_id: int, now_s: str, **override) -> int:
    """撤回一个版本，插入修改后的新版本。返回新版本 id。

    新版本的 recorded_at 必须是 now —— 否则在 (旧 recorded_at, now) 之间的
    任意记录时刻，旧版本和新版本会同时"被相信"，信念重建出两份。
    """
    old = _row(conn, row_id)
    conn.execute("UPDATE wthread_triples SET retracted_at=? WHERE id=?", (now_s, row_id))
    new = {k: v for k, v in old.items() if k != "id"}
    new.update(recorded_at=now_s, retracted_at=None, created_at=now_s)
    new.update(override)
    return _insert(conn, new)


def record_fact(conn: sqlite3.Connection, subject: str, relation: str, obj: str, *,
                valid_from=None, now=None, source_line: int = 0, source: str = "regex",
                source_mem_id: str = None, confidence: float = None,
                dedup_non_temporal: bool = True) -> TemporalFactReport:
    """在一个**已迁移**的连接上写入一条事实（不 commit，由调用方提交）。

    valid_from: 事实开始为真的时刻。给了 → valid_basis='stated'；
                没给 → 只能假设 = 记录时刻，valid_basis='assumed'。
    now:        记录时刻（测试注入用；默认 UTC 当前）。
    """
    rep = TemporalFactReport()
    now_s = normalize_ts(now or datetime.now(timezone.utc))
    stated = valid_from is not None and valid_from != ""
    vf = normalize_ts(valid_from) if stated else now_s
    basis = "stated" if stated else "assumed"
    rep.recorded_at, rep.valid_from, rep.valid_basis = now_s, vf, basis

    base = {"subject": subject, "relation": relation, "object": obj,
            "source_line": source_line, "source": source, "source_mem_id": source_mem_id,
            "valid_from": vf, "valid_until": None, "recorded_at": now_s,
            "closed_at": None, "retracted_at": None, "valid_basis": basis,
            "created_at": now_s}
    if confidence is not None:
        base["confidence"] = confidence

    believed = conn.execute(
        "SELECT id, object, valid_from, valid_until, valid_basis FROM wthread_triples "
        "WHERE subject=? AND relation=? AND retracted_at IS NULL "
        "ORDER BY valid_from, id", (subject, relation)).fetchall()

    # ── 非时效关系：多值共存，只去重 ──
    if relation not in TEMPORAL_RELATIONS:
        if dedup_non_temporal and any(o == obj and vu is None for _, o, _, vu, _ in believed):
            return rep
        rep.inserted_id = _insert(conn, base)
        rep.inserted = True
        return rep

    # ── 单值时序关系 ──
    containing = [r for r in believed
                  if r[2] <= vf and (r[3] is None or r[3] > vf)]
    same = [r for r in containing if r[1] == obj]
    for rid, old_obj, old_vf, old_vu, _ in containing:
        if old_obj == obj:
            continue
        if old_vu is None:
            # 标准情形：开放区间被新事实截断 → 就地写终点 + closed_at
            conn.execute(
                "UPDATE wthread_triples SET valid_until=?, closed_at=? WHERE id=?",
                (vf, now_s, rid))
        else:
            # 已有终点的区间被更正：撤回旧版本，插回截短的版本（起点之前才有残段）
            rep.retracted.append(rid)
            conn.execute("UPDATE wthread_triples SET retracted_at=? WHERE id=?", (now_s, rid))
            if old_vf < vf:
                old = _row(conn, rid)
                new = {k: v for k, v in old.items() if k != "id"}
                new.update(valid_until=vf, closed_at=now_s, recorded_at=now_s,
                           retracted_at=None, created_at=now_s)
                _insert(conn, new)
            # 新事实继承被更正区间的终点
            base["valid_until"], base["closed_at"] = old_vu, now_s
        rep.expired.append(str(rid))
        rep.conflicts.append({"expired_id": rid, "old_object": old_obj,
                              "new_object": obj, "subject": subject, "relation": relation})

    if same:
        # 同一对象在这个时刻已知为真 → 去重（但冲突方已被上面截断，保持 v6.0 的自愈行为）
        return rep

    if not containing:
        # 新事实落在所有已知区间之前（或空档里）→ 终点 = 下一个区间的起点
        nxt = next((r for r in believed if r[2] > vf), None)
        if nxt is not None:
            if nxt[1] == obj and nxt[4] == "assumed" and stated:
                # 精化：原来只知道"何时得知"，现在知道了"何时开始"
                rep.refined.append(nxt[0])
                rep.retracted.append(nxt[0])
                rep.inserted_id = _retract_and_copy(
                    conn, nxt[0], now_s, valid_from=vf, valid_basis="stated",
                    source_line=source_line, source=source,
                    source_mem_id=source_mem_id or _row(conn, nxt[0]).get("source_mem_id"))
                rep.inserted = True
                return rep
            base["valid_until"], base["closed_at"] = nxt[2], now_s
            rep.late_arrival = True

    rep.valid_until = base["valid_until"]
    rep.inserted_id = _insert(conn, base)
    rep.inserted = True
    return rep


def resolve_temporal_conflict(
    db_path: str,
    subject: str,
    relation: str,
    new_object: str,
    source_line: int = 0,
    source: str = "regex",
    now: Optional[datetime] = None,
    valid_from=None,
    source_mem_id: str = None,
) -> TemporalFactReport:
    """写入一条事实并处理时序冲突（v6.0 入口，签名向后兼容）。

    非时效关系保持 v6.0 语义：每次都写（不去重）。wthread_store 走 record_fact
    并打开去重 —— 自动抽取会反复看到同一句话，那里去重是对的。
    """
    ensure_temporal_columns(db_path)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        rep = record_fact(conn, subject, relation, new_object, valid_from=valid_from,
                          now=now, source_line=source_line, source=source,
                          source_mem_id=source_mem_id, dedup_non_temporal=False)
        conn.commit()
        return rep
    except Exception as e:
        logger.warning("[resolve_temporal_conflict] 失败: %s", e)
        conn.rollback()
        return TemporalFactReport()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════
# 查询：默认看"现在的信念"，known_at 回到过去某个记录时刻的信念
# ══════════════════════════════════════════════════════════

def _connect(db_path: str):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _believed(known_at=None) -> tuple:
    """"在记录时刻 K 被相信的版本"子查询。

    valid_until 被改写成 K 时刻看到的终点：那时还不知道它结束了，就是 NULL。
    """
    k = normalize_ts(known_at) or _FAR
    sql = ("SELECT *, CASE WHEN closed_at IS NOT NULL AND closed_at <= :k "
           "              THEN valid_until ELSE NULL END AS eff_until "
           "FROM wthread_triples "
           "WHERE recorded_at <= :k AND (retracted_at IS NULL OR retracted_at > :k)")
    return sql, {"k": k}


def _out(rows) -> list:
    out = []
    for r in rows:
        d = dict(r)
        d["valid_until"] = d.pop("eff_until", d.get("valid_until"))
        out.append(d)
    return out


def get_current(db_path: str, subject: str = None, predicate: str = None,
                known_at=None) -> list:
    """当前有效事实。known_at 给定时 = "那个时刻我以为当前是什么"。"""
    ensure_temporal_columns(db_path)
    inner, p = _believed(known_at)
    sql = f"SELECT * FROM ({inner}) WHERE eff_until IS NULL"
    if subject:
        sql += " AND (subject=:s OR object=:s)"
        p["s"] = subject
    if predicate:
        sql += " AND relation=:r"
        p["r"] = predicate
    sql += " ORDER BY valid_from DESC, id DESC"
    conn = _connect(db_path)
    try:
        return _out(conn.execute(sql, p).fetchall())
    finally:
        conn.close()


def as_of(db_path: str, timestamp, known_at=None, subject: str = None,
          predicate: str = None) -> list:
    """有效时间快照：t 时刻**为真**的事实（按 known_at 时刻的信念，默认现在）。

    区间端点沿用 v6.0 的闭区间语义（valid_until >= t 仍算有效）。
    """
    ensure_temporal_columns(db_path)
    inner, p = _believed(known_at)
    p["t"] = normalize_ts(timestamp)
    sql = (f"SELECT * FROM ({inner}) WHERE valid_from <= :t "
           f"AND (eff_until IS NULL OR eff_until >= :t)")
    if subject:
        sql += " AND (subject=:s OR object=:s)"
        p["s"] = subject
    if predicate:
        sql += " AND relation=:r"
        p["r"] = predicate
    sql += " ORDER BY id DESC"
    conn = _connect(db_path)
    try:
        return _out(conn.execute(sql, p).fetchall())
    finally:
        conn.close()


def known_at(db_path: str, timestamp, subject: str = None, predicate: str = None) -> list:
    """记录时间快照：t 时刻**我相信**的全部事实（含当时已知的历史区间）。"""
    ensure_temporal_columns(db_path)
    inner, p = _believed(timestamp)
    sql = f"SELECT * FROM ({inner}) WHERE 1=1"
    if subject:
        sql += " AND (subject=:s OR object=:s)"
        p["s"] = subject
    if predicate:
        sql += " AND relation=:r"
        p["r"] = predicate
    sql += " ORDER BY valid_from DESC, id DESC"
    conn = _connect(db_path)
    try:
        return _out(conn.execute(sql, p).fetchall())
    finally:
        conn.close()


def history_of(db_path: str, subject: str, predicate: str = None, known_at=None,
               include_retracted: bool = False) -> list:
    """演变链（有效时间倒序，最新在前）。默认只含现在仍相信的版本。"""
    ensure_temporal_columns(db_path)
    if include_retracted:
        inner, p = ("SELECT *, valid_until AS eff_until FROM wthread_triples", {})
    else:
        inner, p = _believed(known_at)
    sql = f"SELECT * FROM ({inner}) WHERE subject=:s"
    p["s"] = subject
    if predicate:
        sql += " AND relation=:r"
        p["r"] = predicate
    sql += " ORDER BY valid_from DESC, id DESC"
    conn = _connect(db_path)
    try:
        return _out(conn.execute(sql, p).fetchall())
    finally:
        conn.close()


def belief_timeline(db_path: str, subject: str, predicate: str = None) -> list:
    """看法是怎么变的：按**记录时间**排列的事件（得知 / 得知结束 / 撤回）。

    回答的是「我什么时候开始这么以为、什么时候改了主意」——
    单时态系统里这个问题没有数据可以回答。
    """
    ensure_temporal_columns(db_path)
    sql = "SELECT * FROM wthread_triples WHERE subject=?"
    p: list = [subject]
    if predicate:
        sql += " AND relation=?"
        p.append(predicate)
    conn = _connect(db_path)
    try:
        rows = [dict(r) for r in conn.execute(sql, p).fetchall()]
    finally:
        conn.close()
    ev = []
    for r in rows:
        base = {"id": r["id"], "subject": r["subject"], "relation": r["relation"],
                "object": r["object"], "valid_from": r["valid_from"],
                "valid_until": r["valid_until"], "valid_basis": r.get("valid_basis")}
        ev.append({**base, "at": r["recorded_at"], "event": "learned"})
        if r.get("closed_at"):
            ev.append({**base, "at": r["closed_at"], "event": "closed"})
        if r.get("retracted_at"):
            ev.append({**base, "at": r["retracted_at"], "event": "retracted"})
    # 同一记录时刻：先"得知新事实"，再"因此截断/撤回旧事实"——因果顺序，不是 id 顺序
    order = {"learned": 0, "closed": 1, "retracted": 2}
    ev.sort(key=lambda e: (e["at"] or "", order[e["event"]], e["id"]))
    return ev


def evolution_chain(db_path: str, subject: str, predicate: str = None, limit: int = 8) -> str:
    """压缩的演变链文本（供 history intent 注入）。

    假设的时间不许冒充陈述的时间：assumed 行只说「得知于」，不说「自…起」。
    """
    hist = history_of(db_path, subject, predicate)
    if not hist:
        return f"{subject}: 无历史记录"
    parts = []
    for h in hist[:limit]:
        vu = h.get("valid_until")
        status = "当前" if vu is None else ("曾" + (vu or "")[:10])
        vf = (h.get("valid_from") or "")[:10]
        if h.get("valid_basis") == "stated":
            when = f"（自 {vf} 起）"
        else:
            when = f"（得知于 {(h.get('recorded_at') or vf)[:10]}，起始时间未陈述）"
        parts.append(f"[{status}] {h['subject']} {h['relation']} {h['object']}{when}")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════
# 存量修复：v7.6 以前 wthread_store 绕过冲突处理留下的"多个现任"
# ══════════════════════════════════════════════════════════

def repair_open_conflicts(db_path: str, apply: bool = False, now=None) -> dict:
    """单值关系上同时有多个"仍然为真"的对象 → 按有效时间顺序截断旧的。

    来源：v7.6 以前生产写入口 wthread_store 直接 INSERT，从不走冲突处理，
    于是「最终用特斯拉」「后来改用吉利」之后 get_current 同时返回两者。

    只截断（写 valid_until + closed_at），不删不撤回 —— 用 known_at(修复前的时刻)
    仍能看到修复前的信念。apply=False 只预演。
    """
    ensure_temporal_columns(db_path)
    now_s = normalize_ts(now or datetime.now(timezone.utc))
    conn = sqlite3.connect(db_path, timeout=10)
    report = {"groups": [], "closed": 0, "applied": apply}
    try:
        q = ",".join("?" * len(TEMPORAL_RELATIONS))
        groups = conn.execute(
            f"SELECT subject, relation FROM wthread_triples "
            f"WHERE relation IN ({q}) AND retracted_at IS NULL AND valid_until IS NULL "
            f"GROUP BY subject, relation HAVING COUNT(DISTINCT object) > 1",
            tuple(TEMPORAL_RELATIONS)).fetchall()
        for subj, rel in groups:
            rows = conn.execute(
                "SELECT id, object, valid_from FROM wthread_triples "
                "WHERE subject=? AND relation=? AND retracted_at IS NULL "
                "AND valid_until IS NULL ORDER BY valid_from, recorded_at, id",
                (subj, rel)).fetchall()
            g = {"subject": subj, "relation": rel,
                 "open": [r[1] for r in rows], "keep": rows[-1][1], "close": []}
            for cur, nxt in zip(rows, rows[1:]):
                if cur[1] == nxt[1]:
                    continue
                g["close"].append({"id": cur[0], "object": cur[1], "valid_until": nxt[2]})
                if apply:
                    conn.execute(
                        "UPDATE wthread_triples SET valid_until=?, closed_at=? WHERE id=?",
                        (nxt[2], now_s, cur[0]))
                    report["closed"] += 1
            report["groups"].append(g)
        if apply:
            conn.commit()
    finally:
        conn.close()
    return report
