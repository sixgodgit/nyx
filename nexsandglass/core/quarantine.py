"""
core/quarantine.py — 遗忘隔离区（可反悔的删除）

为什么需要它
============
v7.4 那次事故（README 已记录在案）：中枢里存在**伪行号**记录，一次 `forget`
按 line_start 抹除，误抹了生产日志里 **345 行真实对话**。当时的修复是
「消灭伪行号的来源」（缺陷 1/2 的惰性路径解析）—— 治的是那一次的病因，
没治这条路径本身的性质：

    erasure.forget() 执行完，原文在这台机器上不存在于任何地方。
    日志被原地改写（os.replace），影子副本也被打上同样的脱敏标记
    （必须如此，否则 nightwatch 会把正文从影子副本拷回主沙漏）。

而同一个仓库里，**风险更低的操作反而是可回滚的**：
`runtime/consolidation.py` 的破坏性合并会先落 `_snapshot()`，出事能 `restore_snapshot()`。

    Dream 破坏性合并   自动、高频、可再生   → 有快照
    forget 抹除正文     手动、不可逆、已误伤 → 没有

这个不对称就是本模块要补的东西。

两段式删除
==========
    forget(mode="quarantine")     ← 新的默认
        所有**检索路径**立刻读不到（与之前逐字节一致：日志脱敏、索引清除、墓碑）
        原文 + 派生行进隔离区，保留 N 天（默认 30，`NYX_FORGET_RETENTION_DAYS`）
        这一段**可反悔**：restore() 把日志/中枢/索引逐字节还原

    purge()
        隔离区到期 → 真正的物理擦除，不可逆
        （给 cron；`memid.health()` 的 pending_purge 项会盯着有没有人真的跑）

    forget(mode="purge")
        不留隔离区，等于 v7.4 的老行为。给「现在就必须没有」的场合
        （法务要求、他人隐私、误粘贴的凭据）。

诚实声明（写在代码里，不只写在 commit 里）
==========================================
隔离期内，被"忘记"的原文**仍然在磁盘上**（nyx.db 的 quarantine 表）。
它读不到、搜不到、召回不到，但它在。这是"可反悔"的代价，不是可以含糊的细节：

  - `verify_erasure()` 会把它**报出来**（`in_quarantine`），不假装干净；
  - `clean` 的语义仍然是「任何检索路径都摸不到」—— 这一项不放水；
  - 「从磁盘上真的没了」是另一个字段 `fully_purged`，只有 purge 之后才为真。

把这两件事糊成一个布尔值，正是 Déjà Vu 在讲的同一个错误：
**两种不同的状态返回同一个答案，上层就没法做正确的事。**

为什么隔离区存的是「原始行」而不是「重建所需的字段」
====================================================
restore 必须逐字节还原。从 (ts, sender, text) 重新拼一遍日志行，靠的是
「写入时的格式和现在的格式一致」这个假设 —— 而格式假设正是 v7.4 栽过的地方。
所以隔离区直接存**抹除前那几行的原文**，还原就是写回去，不做任何重建。

还原前必须验证目标行现在是脱敏标记
==================================
这是 v7.4 教训的另一半：「按 line_start 写日志前，必须先验证行号语义」。
如果目标行现在不是 `[REDACTED ...]`（日志被截断、被手工编辑、被恢复了旧备份），
restore 拒绝写入并报出来 —— 宁可还原失败，也不能覆盖一行活着的记忆。

纯 stdlib，零依赖。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Iterable, Optional

from nexsandglass.core import memid

logger = logging.getLogger(__name__)

PAYLOAD_VERSION = 1
DEFAULT_RETENTION_DAYS = 30
_TS_FMT = "%Y-%m-%d %H:%M:%S"

REDACTED_PREFIX = "[REDACTED"
REDACTED_CONT = "[REDACTED]"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS quarantine (
    mem_id          TEXT PRIMARY KEY,
    seq             INTEGER,
    ts              TEXT,
    sender          TEXT,
    text            TEXT,
    line_start      INTEGER,
    line_end        INTEGER,
    reason          TEXT,
    quarantined_at  TEXT NOT NULL,
    purge_after     TEXT NOT NULL,
    journal_path    TEXT,
    payload         TEXT,
    payload_version INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_q_purge_after ON quarantine(purge_after);
"""

_FIELDS = ("mem_id", "seq", "ts", "sender", "text", "line_start", "line_end",
           "reason", "quarantined_at", "purge_after", "journal_path",
           "payload", "payload_version")


def _now(now: datetime = None) -> str:
    return (now or datetime.now()).strftime(_TS_FMT)


def _parse_ts(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime((s or "").strip(), _TS_FMT)
    except (ValueError, TypeError):
        return None


def retention_days(default: int = DEFAULT_RETENTION_DAYS) -> int:
    """隔离期天数。0 = 不留隔离区（等于直接物理擦除）。

    每次读环境变量而不是 import 时固化 —— v7.4 缺陷1 的同一个教训。
    """
    raw = os.environ.get("NYX_FORGET_RETENTION_DAYS")
    if raw is None or str(raw).strip() == "":
        return default
    try:
        n = int(str(raw).strip())
    except ValueError:
        logger.warning("[quarantine] NYX_FORGET_RETENTION_DAYS=%r 不是整数，用默认 %d",
                       raw, default)
        return default
    return max(0, n)


def _conn() -> sqlite3.Connection:
    """隔离区与 ID 中枢同库（nyx.db）。**模块自足建表** —— v7.3 缺陷3 的教训：
    DDL 只写在别人的 `_ensure_table()` 里，第三方直连本模块就 `no such table`。
    """
    conn = memid.get_conn()
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ══════════════════════════════════════════════════════════
# 派生行的抓取与回放（通用，按 PRAGMA 读列名，不硬编码 schema）
# ══════════════════════════════════════════════════════════

def _cols(db: sqlite3.Connection, table: str) -> list:
    try:
        return [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error:
        return []


def _grab(db: sqlite3.Connection, table: str, where: str, args: tuple) -> list:
    """把命中的行抓成 [{列名: 值}]。表不存在 / 列不存在 → 空列表，不抛。

    刻意按列名而不是位置抓：wthread_triples 这些表在不同版本上被
    ALTER 过（source / valid_from / valid_until / source_mem_id 都是后加的），
    按位置回放会在旧库上错位。
    """
    cols = _cols(db, table)
    if not cols:
        return []
    try:
        rows = db.execute(
            f"SELECT {','.join(cols)} FROM {table} WHERE {where}", args).fetchall()
    except sqlite3.Error:
        return []
    return [dict(zip(cols, r)) for r in rows]


def _replay(db: sqlite3.Connection, table: str, rows: list) -> int:
    """把抓下来的行写回去。只回放当前库**仍然存在**的列。"""
    if not rows:
        return 0
    cols_now = set(_cols(db, table))
    if not cols_now:
        return 0
    n = 0
    for row in rows:
        use = [c for c in row if c in cols_now]
        if not use:
            continue
        q = ",".join("?" * len(use))
        try:
            db.execute(
                f"INSERT OR REPLACE INTO {table} ({','.join(use)}) VALUES ({q})",
                tuple(row[c] for c in use))
            n += 1
        except sqlite3.Error as e:
            logger.warning("[quarantine] 回放 %s 失败: %s", table, e)
    return n


def _read_lines(path: str) -> tuple:
    """返回 (lines, had_trailing_newline)。lines 不含行尾换行。"""
    if not path or not os.path.exists(path):
        return [], True
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    if raw == "":
        return [], True
    return raw.splitlines(), raw.endswith("\n")


def _slice(path: str, start: int, end: int) -> list:
    """取 [start, end] 闭区间的物理行（1-based）。越界返回已有部分。"""
    lines, _ = _read_lines(path)
    if start < 1:
        return []
    return lines[start - 1:end]


def _is_redacted_line(line: str, first: bool) -> bool:
    s = (line or "").lstrip()
    if first:
        # 首行形如 "ts | sender | [REDACTED ... ]"，标记在正文位置
        return REDACTED_PREFIX in s
    return s.startswith(REDACTED_PREFIX)


def _write_back(path: str, start: int, original: list) -> dict:
    """把 original 写回 [start, start+len-1]，**前提是那几行现在都是脱敏标记**。

    这是 v7.4 教训的执行点：不验证就按行号写，等于把误删的坑换个方向再踩一次。
    """
    out = {"path": path, "lines": 0, "status": "absent"}
    if not path or not os.path.exists(path) or not original:
        return out
    lines, trailing = _read_lines(path)
    end = start + len(original) - 1
    if start < 1 or end > len(lines):
        out["status"] = "out_of_range"
        out["file_lines"] = len(lines)
        out["need"] = end
        return out
    for i in range(start, end + 1):
        if not _is_redacted_line(lines[i - 1], first=(i == start)):
            out["status"] = "not_redacted"
            out["at_line"] = i
            out["found"] = lines[i - 1][:60]
            return out
    for off, text in enumerate(original):
        lines[start - 1 + off] = text
    tmp = path + ".restore.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if trailing else ""))
    os.replace(tmp, path)
    memid._line_count_cache.pop(path, None)
    out["lines"] = len(original)
    out["status"] = "restored"
    return out


def _backup_path(journal_path: str) -> str:
    return os.path.join(os.path.dirname(journal_path or ""), "sandglass.backup")


# ══════════════════════════════════════════════════════════
# 抓取（必须在级联清除**之前**调用）
# ══════════════════════════════════════════════════════════

def capture(row: tuple, *, journal_path: str, engram_path: str = None) -> dict:
    """抓一条记忆的全部现场，**在任何东西被删掉之前**。

    row = (mem_id, line_start, line_end, ts, sender, seq, text)（erasure 的行形状）

    抓的东西分两类：
      原文类（逐字节还原）：journal 行、sandglass.backup 行、engram jsonl 行
      派生类（按列回放）  ：sandglass/FTS 行、trust、fact_tags、wthread_triples、entities
    倒排索引不抓 —— 它由正文分词得出，还原时重算比回放更可靠。
    向量不抓 —— embedding 可由正文重算，还原后由 writer 补（报告里注明）。
    """
    mem_id, ls, le, ts, sender, seq, text = row
    pay: dict = {"version": PAYLOAD_VERSION}

    pay["journal"] = _slice(journal_path, ls, le or ls)
    pay["backup"] = _slice(_backup_path(journal_path), ls, le or ls)

    # engram_store.jsonl：按 ts 匹配（_purge_engram 就是按 ts 删的）
    lines = []
    if engram_path and os.path.exists(engram_path):
        with open(engram_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.rstrip("\n")
                if not s.strip():
                    continue
                try:
                    if json.loads(s).get("ts") == ts:
                        lines.append(s)
                except Exception:
                    continue
    pay["engram"] = lines

    # FTS 主表行（tokens 由正文重算，不抓）
    try:
        from nexsandglass.core import sandglass_sqlite as sq
        with sq._lock:
            pay["fts"] = _grab(sq._get_db(), "sandglass", "id=?", (ls,))
    except Exception as e:
        pay["fts"] = []
        logger.debug("[quarantine] 抓 FTS 行失败: %s", e)

    # 影子沙 + 知识图谱
    try:
        from nexsandglass.features import shadow_sand as sh
        with sh._db_lock:
            db = sh._get_conn()
            pay["trust"] = _grab(db, "trust", "line_num=?", (ls,))
            pay["fact_tags"] = _grab(db, "fact_tags", "line_num=?", (ls,))
            pay["triples"] = _grab(db, "wthread_triples", "source_mem_id=?", (mem_id,))
            # entities 是聚合行（line_nums / mem_ids 是逗号串），
            # 抓「受影响的整行」，还原时把自己那一份并回去。
            ents = []
            for e in _grab(db, "entities", "1=1", ()):
                lns = str(e.get("line_nums") or "").split(",")
                mids = str(e.get("mem_ids") or "").split(",")
                if str(ls) in [x.strip() for x in lns] or mem_id in [x.strip() for x in mids]:
                    ents.append(e)
            pay["entities"] = ents
    except Exception as e:
        pay.setdefault("trust", [])
        pay.setdefault("fact_tags", [])
        pay.setdefault("triples", [])
        pay.setdefault("entities", [])
        logger.debug("[quarantine] 抓影子沙失败: %s", e)

    return pay


def hold(rows: list, *, reason: str, journal_path: str, engram_path: str = None,
         days: int = None, now: datetime = None) -> dict:
    """把这批记忆连同现场存进隔离区。**在级联清除之前调用** ——
    先留副本再动手，中途崩了也还能还原。

    days=0 → 不留隔离区（返回 held=0），调用方即等价于老的不可逆删除。
    """
    days = retention_days() if days is None else max(0, int(days))
    out = {"held": 0, "days": days, "purge_after": "", "mem_ids": [], "skipped": 0}
    if not rows or days == 0:
        return out

    t0 = now or datetime.now()
    q_at = _now(t0)
    p_after = _now(t0 + timedelta(days=days))
    conn = _conn()
    for row in rows:
        mem_id, ls, le, ts, sender, seq, text = row
        try:
            pay = capture(row, journal_path=journal_path, engram_path=engram_path)
            conn.execute(
                f"INSERT OR REPLACE INTO quarantine ({','.join(_FIELDS)})"
                f" VALUES ({','.join('?' * len(_FIELDS))})",
                (mem_id, seq, ts, sender, text, ls, le, reason, q_at, p_after,
                 journal_path, json.dumps(pay, ensure_ascii=False), PAYLOAD_VERSION))
            out["held"] += 1
            out["mem_ids"].append(mem_id)
        except Exception as e:
            # 抓不下来就不假装抓下来了 —— 这条会变成不可逆删除，必须报出来。
            out["skipped"] += 1
            logger.error("[quarantine] 隔离 %s 失败（该条将不可恢复）: %s", mem_id, e)
    conn.commit()
    out["purge_after"] = p_after
    return out


# ══════════════════════════════════════════════════════════
# 查询
# ══════════════════════════════════════════════════════════

def get(mem_id: str) -> Optional[dict]:
    row = _conn().execute(
        f"SELECT {','.join(_FIELDS)} FROM quarantine WHERE mem_id=?", (mem_id,)).fetchone()
    return dict(zip(_FIELDS, row)) if row else None


def list_all(limit: int = 200) -> list:
    """隔离区清单（不含 payload，避免把正文摊到日志里）。"""
    fields = [f for f in _FIELDS if f != "payload"]
    rows = _conn().execute(
        f"SELECT {','.join(fields)} FROM quarantine ORDER BY quarantined_at DESC, seq"
        " LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(zip(fields, r))
        d["preview"] = (d.pop("text", "") or "")[:40].replace("\n", " ⏎ ")
        out.append(d)
    return out


def overdue(now: datetime = None) -> list:
    """已过隔离期却还没被 purge 的 —— 承诺了 N 天就该只留 N 天。"""
    cut = _now(now)
    fields = [f for f in _FIELDS if f not in ("payload", "text")]
    return [dict(zip(fields, r)) for r in _conn().execute(
        f"SELECT {','.join(fields)} FROM quarantine WHERE purge_after <= ?"
        " ORDER BY purge_after", (cut,)).fetchall()]


def stats(now: datetime = None) -> dict:
    conn = _conn()
    cut = _now(now)
    total = int(conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0])
    od = int(conn.execute("SELECT COUNT(*) FROM quarantine WHERE purge_after <= ?",
                          (cut,)).fetchone()[0])
    row = conn.execute(
        "SELECT MIN(purge_after), MAX(purge_after) FROM quarantine").fetchone()
    return {"pending": total, "overdue": od, "recoverable": total - od,
            "next_due": row[0] or "", "last_due": row[1] or "",
            "retention_days": retention_days()}


def find_text(needles: Iterable[str]) -> dict:
    """隔离区里有没有这些字符串。给 verify_erasure 用 —— 隔离期内如实报出来。"""
    conn = _conn()
    hits: dict = {}
    for nd in [n for n in needles if n]:
        for r in conn.execute(
                "SELECT mem_id, seq, purge_after, text FROM quarantine WHERE text LIKE ?"
                " OR payload LIKE ?", (f"%{nd}%", f"%{nd}%")):
            hits.setdefault(nd, []).append(
                {"mem_id": r[0], "seq": r[1], "purge_after": r[2]})
    return hits


# ══════════════════════════════════════════════════════════
# 还原
# ══════════════════════════════════════════════════════════

def restore(mem_id: str, *, journal_path: str = None, engram_path: str = None) -> dict:
    """把一条被"忘记"的记忆完整还原。

    顺序与 forget 相反：先还原派生索引，最后还原日志正文与中枢墓碑。
    中途失败时的残留状态是「索引里有、正文还是脱敏」—— 检索读的是正文，
    所以这个中间态不会让内容泄漏；反过来先还原正文则会。

    返回报告；`ok` 只认两件事（与 forget 的判据对称）：
      1. 日志正文写回来了
      2. 中枢墓碑撤了
    """
    rec = get(mem_id)
    report = {"mem_id": mem_id, "ok": False, "found": bool(rec),
              "journal": {}, "backup": {}, "hub": 0, "tombstone_cleared": 0,
              "fts": 0, "idx": 0, "engram": 0,
              "shadow": {"trust": 0, "fact_tags": 0, "triples": 0, "entities": 0},
              "problems": [], "notes": []}
    if not rec:
        report["problems"].append("隔离区里没有这条（可能已 purge，或从未隔离）")
        return report

    jpath = journal_path or rec.get("journal_path") or memid._journal_path()
    try:
        pay = json.loads(rec.get("payload") or "{}")
    except Exception as e:
        report["problems"].append("payload 解析失败: %s" % e)
        return report

    ls = int(rec["line_start"] or 0)
    conn = memid.get_conn()

    # ── 中枢：先确认这条确实处于"已删"状态，再动手 ──
    hub = conn.execute(
        "SELECT seq, line_start, deleted_at FROM memories WHERE mem_id=?", (mem_id,)).fetchone()
    if hub is None:
        report["problems"].append("中枢里没有这个 mem_id（库被换过？）")
        return report
    if not hub[2]:
        report["problems"].append("中枢里这条没有墓碑 —— 它没被删，不该还原")
        return report
    if int(hub[1] or 0) != ls:
        report["problems"].append(
            "行号不一致（中枢 %s / 隔离区 %s），拒绝按行号写回" % (hub[1], ls))
        return report

    # ── 派生索引 ──
    try:
        from nexsandglass.core import sandglass_sqlite as sq
        with sq._lock:
            db = sq._get_db()
            n = _replay(db, "sandglass", pay.get("fts") or [])
            if n:
                db.execute("INSERT OR REPLACE INTO sandglass_fts(rowid, tokens) VALUES(?,?)",
                           (ls, sq._tokenize(rec["text"] or "")))
            db.commit()
            report["fts"] = n
    except Exception as e:
        report["notes"].append("FTS 未还原（可重建）: %s" % e)

    try:
        from nexsandglass.features import shadow_sand as sh
        with sh._db_lock:
            db = sh._get_conn()
            report["shadow"]["trust"] = _replay(db, "trust", pay.get("trust") or [])
            report["shadow"]["fact_tags"] = _replay(db, "fact_tags", pay.get("fact_tags") or [])
            report["shadow"]["triples"] = _replay(db, "wthread_triples", pay.get("triples") or [])
            report["shadow"]["entities"] = _restore_entities(db, pay.get("entities") or [],
                                                            ls, mem_id)
            db.commit()
    except Exception as e:
        report["notes"].append("影子沙未还原（可重建）: %s" % e)

    # engram jsonl：按 ts 去重后追加，避免重复还原写两份
    epath = engram_path or os.path.join(
        os.environ.get("NEXSANDBASE_HOME") or os.path.dirname(jpath), "engram_store.jsonl")
    report["engram"] = _restore_engram(epath, pay.get("engram") or [])

    # ── 正文（真相来源）──
    report["journal"] = _write_back(jpath, ls, pay.get("journal") or [])
    if report["journal"].get("status") != "restored":
        report["problems"].append("日志正文未还原: %s" % report["journal"].get("status"))
    report["backup"] = _write_back(_backup_path(jpath), ls, pay.get("backup") or [])

    # ── 中枢：撤墓碑，正文写回 ──
    if report["journal"].get("status") == "restored":
        conn.execute(
            "UPDATE memories SET text=?, content_hash=?, deleted_at=NULL WHERE mem_id=?",
            (rec["text"] or "", memid.content_hash(rec["text"] or ""), mem_id))
        report["hub"] = 1
        cur = conn.execute("DELETE FROM tombstones WHERE mem_id=?", (mem_id,))
        report["tombstone_cleared"] = cur.rowcount
        conn.commit()

    # 倒排索引：由正文重算，比回放旧索引可靠
    if report["hub"]:
        report["idx"] = _restore_idx(rec["text"] or "", ls)

    report["notes"].append("向量需重算（embedding 不入隔离区）")

    report["ok"] = bool(report["hub"]) and not report["problems"]
    if report["ok"]:
        # 还原成功 → 隔离区那份必须删掉，否则同一条记忆有两个真相来源。
        # execute 与 commit 必须落在**同一个连接对象**上：中间再取一次连接，
        # 一旦底层把连接换掉（memid.get_conn 曾因缺 global 声明每次都换），
        # 未提交的 DELETE 就被回滚，而 rowcount 照样返回 1。
        qc = _conn()
        cur = qc.execute("DELETE FROM quarantine WHERE mem_id=?", (mem_id,))
        qc.commit()
        report["quarantine_cleared"] = cur.rowcount == 1
        if not report["quarantine_cleared"]:
            report["problems"].append("隔离区副本未清除，同一条记忆存在两份")
            report["ok"] = False
    return report


def _restore_entities(db: sqlite3.Connection, ents: list, line_num: int, mem_id: str) -> int:
    """把自己那一份并回实体行。行还在 → 合并；行被删了 → 整行插回。"""
    n = 0
    has_mem_ids = "mem_ids" in _cols(db, "entities")
    sql = ("SELECT rowid, line_nums, mem_ids FROM entities WHERE name=?" if has_mem_ids
           else "SELECT rowid, line_nums FROM entities WHERE name=?")
    for e in ents:
        name = e.get("name")
        if not name:
            continue
        cur = db.execute(sql, (name,)).fetchone()
        if cur is None:
            row = {k: v for k, v in e.items() if k != "rowid"}
            n += _replay(db, "entities", [row])
            continue
        lns = [x for x in str(cur[1] or "").split(",") if x.strip()]
        if str(line_num) not in lns:
            lns.append(str(line_num))
        if len(cur) > 2:
            mids = [x for x in str(cur[2] or "").split(",") if x.strip()]
            if mem_id not in mids:
                mids.append(mem_id)
            db.execute("UPDATE entities SET line_nums=?, mem_ids=? WHERE rowid=?",
                       (",".join(lns), ",".join(mids), cur[0]))
        else:
            db.execute("UPDATE entities SET line_nums=? WHERE rowid=?",
                       (",".join(lns), cur[0]))
        n += 1
    return n


def _restore_engram(path: str, lines: list) -> int:
    if not lines or not path:
        return 0
    have = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            have = {l.rstrip("\n") for l in f}
    add = [l for l in lines if l not in have]
    if not add:
        return 0
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for l in add:
            f.write(l + "\n")
    return len(add)


def _restore_idx(text: str, line_num: int) -> int:
    """把这条记忆的分词重新挂回倒排索引（只动自己的行号）。

    ⚠ 必须先弃掉进程内缓存。倒排索引的缓存键是**日志物理行数**，而脱敏与还原
    都保持行数不变 —— 于是缓存永远"命中"，读到的是一份不含本次改动的旧索引，
    `_write_idx` 再把整个文件按它重写一遍，**别的进程刚还原的那些 token 被抹掉**。
    实测：先用 CLI 还原一条（另一个进程），再在本进程还原其余 149 条，
    第一条的 posting 全部消失。代价是每次还原多读一遍 idx 文件 ——
    还原是罕见操作，用这点开销换"不静默丢别人的索引"是值得的。
    """
    try:
        from nexsandglass.features import sandglass_vault as v
        v._idx_cache, v._idx_mtime = None, 0
        idx = v._sync_index()
        if idx is None:
            return 0
        n = 0
        for token in v._tokenize(text):
            lines = idx.setdefault(token, [])
            if line_num not in lines:
                lines.append(line_num)
                n += 1
        v._write_idx(idx, v._journal_lines())
        v._idx_cache, v._idx_mtime = idx, v._journal_lines()
        return n
    except Exception as e:
        logger.warning("[quarantine] 倒排索引未还原（可重建）: %s", e)
        return 0


# ══════════════════════════════════════════════════════════
# 物理擦除
# ══════════════════════════════════════════════════════════

def purge(mem_ids: list = None, *, older_than: bool = True, apply: bool = False,
          now: datetime = None, vacuum: bool = True) -> dict:
    """真正的物理擦除：把隔离区那份删掉，此后不可恢复。

    默认 `older_than=True` 只清已过期的；`mem_ids` 指定时精确清那几条
    （用于"这条现在就要彻底没有"）。apply=False 只预演。

    VACUUM 不是可选的礼节：SQLite 删行只是标记页面可复用，原文可能仍在
    文件的空闲页里。声称"从磁盘上没了"就得真的没了。
    """
    conn = _conn()
    cut = _now(now)
    if mem_ids:
        q = ",".join("?" * len(mem_ids))
        rows = conn.execute(
            f"SELECT mem_id, seq, purge_after FROM quarantine WHERE mem_id IN ({q})",
            list(mem_ids)).fetchall()
    elif older_than:
        rows = conn.execute(
            "SELECT mem_id, seq, purge_after FROM quarantine WHERE purge_after <= ?"
            " ORDER BY purge_after", (cut,)).fetchall()
    else:
        rows = conn.execute("SELECT mem_id, seq, purge_after FROM quarantine").fetchall()

    report = {"selected": len(rows), "applied": apply, "purged": 0, "vacuumed": False,
              "cutoff": cut,
              "preview": [{"mem_id": r[0], "seq": r[1], "purge_after": r[2]}
                          for r in rows[:10]]}
    if not apply or not rows:
        report["action"] = "dry_run" if not apply else "nothing_matched"
        report["ok"] = True
        return report

    ids = [r[0] for r in rows]
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        q = ",".join("?" * len(part))
        cur = conn.execute(f"DELETE FROM quarantine WHERE mem_id IN ({q})", part)
        report["purged"] += cur.rowcount
    conn.commit()

    if vacuum:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            report["vacuumed"] = True
        except sqlite3.Error as e:
            # 报出来而不是吞掉：没 VACUUM 成功，就不能说"磁盘上没了"
            report["vacuum_error"] = str(e)
            logger.warning("[quarantine] VACUUM 失败，空闲页里可能仍有正文: %s", e)

    report["ok"] = report["purged"] == len(ids)
    report["action"] = "purged"
    return report
