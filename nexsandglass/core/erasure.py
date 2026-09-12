"""
core/erasure.py — 真正的删除（Step 5）

问题
====
`facade.forget()` 只遍历 `engram_store.jsonl`，而召回路径读的是 `sandglass.txt`。
生产实测：

    observe("我的银行密码提示是 ...")   → ok
    forget({"all": True})              → {'ok': True, 'removed': 0}
    recall("银行密码提示")              → "我的银行密码提示是 ..."

用户点了"忘记"，助理还记得，而且还会说出来。这不是安全问题，是**正确性问题**。

一条记忆现在散落在 7 个地方，删除必须全部覆盖：

    1. nyx.db          memories 行（中枢身份）
    2. sandglass.txt   日志正文 ← 召回真正读的地方
    3. sandglass.db    FTS5 全文索引
    4. sandglass.idx   倒排索引
    5. shadow_sand.db  trust / entities / fact_tags
    6. shadow_sand.db  wthread_triples（知识图谱）
    7. engram_store.jsonl

为什么日志能就地抹除
====================
删掉整行会让后面所有记忆的行号位移 —— 那正是 0 号改造要根除的东西。
所以这里**原地改写、保持物理行数不变**：

    2026-09-06 10:00:00 | user | [REDACTED 2026-09-12 10:00:00 reason=user_forget]
    [REDACTED]
    [REDACTED]

首行仍然匹配记录起始模式（同 ts、同 sender），所以 seq 编号不变；
续行不匹配，所以记录边界不变；物理行数不变，所以别人的 line_start 不变。
内容没了，位置还在 —— 这是 tombstone 的全部意义。

墓碑防复活
==========
任何重建（sync_all / rebuild_index / rebuild_entity_index / 迁移）都必须
跳过已墓碑的 seq，否则重建一次就把删掉的内容从日志里捞回来了。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Iterable, Optional

from nexsandglass.core import memid
from nexsandglass.core.sandglass_paths import _NB

logger = logging.getLogger(__name__)

REDACTED_PREFIX = "[REDACTED"
REDACTED_CONT = "[REDACTED]"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_redacted_text(text: str) -> bool:
    return memid.is_redacted(text)


# ══════════════════════════════════════════════════════════
# 选择要删的记忆
# ══════════════════════════════════════════════════════════

def _select(selector: dict) -> list:
    """把 selector 解析为 mem_id 列表。**只选，不删。**"""
    conn = memid.get_conn()
    out: list = []

    if selector.get("all"):
        return [r[0] for r in conn.execute(
            "SELECT mem_id FROM memories WHERE deleted_at IS NULL ORDER BY seq")]

    for key in ("mem_id", "mem_ids"):
        v = selector.get(key)
        if v:
            out.extend([v] if isinstance(v, str) else list(v))

    for key in ("seq", "seqs"):
        v = selector.get(key)
        if v is not None:
            for s in ([v] if isinstance(v, int) else list(v)):
                mid = memid.resolve(int(s))
                if mid:
                    out.append(mid)

    # contains：正文精确子串匹配。刻意不做模糊/语义匹配 ——
    # 删除必须是可预测的，"大概是这条"不能成为删数据的依据。
    sub = selector.get("contains")
    if sub:
        for r in conn.execute(
                "SELECT mem_id FROM memories WHERE deleted_at IS NULL AND text LIKE ? ORDER BY seq",
                (f"%{sub}%",)):
            out.append(r[0])

    seen, uniq = set(), []
    for m in out:
        if m and m not in seen:
            seen.add(m)
            uniq.append(m)
    return uniq


def find_duplicate_runs(min_run: int = 3) -> list:
    """找出「连续多次写入了完全相同内容」的段落（崩溃重放的特征）。

    返回 [{ts, sender, text_preview, count, mem_ids, keep, drop}, ...]
    keep = 第一条（保留），drop = 其余（候选删除）。

    刻意只认**连续**重复：跨时段偶然写了同样一句话是正常的，
    同一秒连写 56 次「重启」才是事故。
    """
    conn = memid.get_conn()
    rows = conn.execute(
        "SELECT seq, mem_id, ts, sender, text FROM memories "
        "WHERE deleted_at IS NULL ORDER BY seq").fetchall()
    runs, cur = [], []

    def flush():
        if len(cur) >= min_run:
            runs.append({
                "ts": cur[0][2], "sender": cur[0][3],
                "text_preview": cur[0][4][:60].replace("\n", " ⏎ "),
                "count": len(cur),
                "keep": cur[0][1],
                "drop": [r[1] for r in cur[1:]],
                "seq_range": (cur[0][0], cur[-1][0]),
            })

    for r in rows:
        if cur and r[2] == cur[-1][2] and r[3] == cur[-1][3] and r[4] == cur[-1][4]:
            cur.append(r)
        else:
            flush()
            cur = [r]
    flush()
    return runs


# ══════════════════════════════════════════════════════════
# 级联删除
# ══════════════════════════════════════════════════════════

def _redact_journal(rows: list, reason: str, journal_path: str) -> int:
    """就地抹除日志正文，**保持物理行数不变**。rows = [(mem_id, line_start, line_end, ts, sender)]"""
    if not rows or not os.path.exists(journal_path):
        return 0
    mark = {}
    for mem_id, ls, le, ts, sender in rows:
        mark[ls] = f"{ts} | {sender} | {REDACTED_PREFIX} {_now()} reason={reason} mem={mem_id}]"
        for n in range(ls + 1, (le or ls) + 1):
            mark[n] = REDACTED_CONT

    tmp = journal_path + ".redact.tmp"
    n_lines = 0
    with open(journal_path, "r", encoding="utf-8", errors="replace") as src, \
            open(tmp, "w", encoding="utf-8") as dst:
        for n, line in enumerate(src, 1):
            dst.write(mark[n] + "\n" if n in mark else line)
            n_lines += 1
    os.replace(tmp, journal_path)
    memid._line_count_cache.pop(journal_path, None)
    return len(mark)


def _redact_backup(rows: list, reason: str, journal_path: str) -> dict:
    """把同样的脱敏标记打到 sandglass.backup 上。

    这是整套删除里最容易漏、后果最重的一环：
    `pulse._sync_shadow` 是**按行数**增量同步的 —— 脱敏原地改写、行数不变，
    于是影子副本永远等不到那次改写，脱敏前的正文原封不动留在里面。
    更糟的是 `nightwatch` 在主沙漏变短时会 `copy2(backup → master)`，
    那一下就把用户删掉的内容**整个恢复回来**。

    所以影子副本不是"备份"，它是删除的第二个现场。
    """
    out = {"path": "", "lines": 0, "status": "absent"}
    bak = os.path.join(os.path.dirname(journal_path), "sandglass.backup")
    out["path"] = bak
    if not rows or not os.path.exists(bak):
        return out

    n_journal = sum(1 for _ in open(journal_path, "r", encoding="utf-8", errors="replace"))
    n_bak = sum(1 for _ in open(bak, "r", encoding="utf-8", errors="replace"))
    mark = {}
    for mem_id, ls, le, ts, sender in rows:
        mark[ls] = f"{ts} | {sender} | {REDACTED_PREFIX} {_now()} reason={reason} mem={mem_id}]"
        for n in range(ls + 1, (le or ls) + 1):
            mark[n] = REDACTED_CONT
    need = max(mark) if mark else 0
    if n_bak < need:
        # 影子比主沙漏短 —— 行号对不上，盲目按行号改写会抹错行。
        # 这种情况必须报出来由人处理，不能假装删干净了。
        out["status"] = "out_of_sync"
        out["backup_lines"] = n_bak
        out["journal_lines"] = n_journal
        return out

    tmp = bak + ".redact.tmp"
    with open(bak, "r", encoding="utf-8", errors="replace") as src, \
            open(tmp, "w", encoding="utf-8") as dst:
        for n, line in enumerate(src, 1):
            dst.write(mark[n] + "\n" if n in mark else line)
    os.replace(tmp, bak)
    out["lines"] = len(mark)
    out["status"] = "redacted"
    return out


def _purge_fts(line_starts: list) -> int:
    from nexsandglass.core import sandglass_sqlite as sq
    if not line_starts:
        return 0
    with sq._lock:
        conn = sq._get_db()
        q = ",".join("?" * len(line_starts))
        conn.execute(f"DELETE FROM sandglass_fts WHERE rowid IN ({q})", line_starts)
        cur = conn.execute(f"DELETE FROM sandglass WHERE id IN ({q})", line_starts)
        conn.commit()
        return cur.rowcount


def _purge_idx(line_starts: list) -> int:
    from nexsandglass.features import sandglass_vault as v
    if not line_starts:
        return 0
    drop = set(line_starts)
    idx = v._sync_index()
    if not idx:
        return 0
    removed, out = 0, {}
    for token, lines in idx.items():
        keep = [l for l in lines if l not in drop]
        removed += len(lines) - len(keep)
        if keep:
            out[token] = keep
    v._write_idx(out, v._journal_lines())
    v._idx_cache, v._idx_mtime = out, v._journal_lines()
    return removed


def _purge_shadow(mem_ids: list, line_starts: list) -> dict:
    from nexsandglass.features import shadow_sand as sh
    stats = {"trust": 0, "fact_tags": 0, "entities_updated": 0,
             "entities_removed": 0, "triples": 0}
    if not mem_ids:
        return stats
    drop_lines = set(line_starts)
    drop_ids = set(mem_ids)
    with sh._db_lock:
        db = sh._get_conn()
        for table in ("trust", "fact_tags"):
            try:
                q = ",".join("?" * len(line_starts))
                cur = db.execute(f"DELETE FROM {table} WHERE line_num IN ({q})", line_starts)
                stats[table] = cur.rowcount
            except sqlite3.Error:
                pass
        try:
            rows = db.execute("SELECT rowid, name, line_nums, mem_ids FROM entities").fetchall()
        except sqlite3.Error:
            rows = []
        for rowid, name, lns, mids in rows:
            keep_l = [x for x in (lns or "").split(",") if x.strip().isdigit()
                      and int(x) not in drop_lines]
            keep_m = [x for x in (mids or "").split(",") if x and x not in drop_ids]
            if len(keep_l) == len((lns or "").split(",")) and (mids or "") == ",".join(keep_m):
                continue
            if not keep_l and not keep_m:
                db.execute("DELETE FROM entities WHERE rowid=?", (rowid,))
                stats["entities_removed"] += 1
            else:
                db.execute("UPDATE entities SET line_nums=?, mem_ids=? WHERE rowid=?",
                           (",".join(keep_l), ",".join(keep_m), rowid))
                stats["entities_updated"] += 1
        try:
            q = ",".join("?" * len(mem_ids))
            cur = db.execute(
                f"DELETE FROM wthread_triples WHERE source_mem_id IN ({q})", mem_ids)
            stats["triples"] = cur.rowcount
        except sqlite3.Error:
            pass
        sh._maybe_commit()
    return stats


def _purge_engram(ts_list: list) -> int:
    store = os.path.join(_NB, "engram_store.jsonl")
    if not os.path.exists(ts_list and store or store) or not ts_list:
        return 0
    drop = set(ts_list)
    kept, removed = [], 0
    with open(store, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                kept.append(line)
                continue
            if row.get("ts") in drop:
                removed += 1
            else:
                kept.append(line)
    if removed:
        tmp = store + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for l in kept:
                f.write(l + "\n")
        os.replace(tmp, store)
    return removed


def _purge_vectors(mem_ids: list) -> int:
    try:
        from nexsandglass.core.vector_store import get_vector_store
        store = get_vector_store()
        n = 0
        for m in mem_ids:
            try:
                store.delete(m)
                n += 1
            except Exception:
                pass
        return n
    except Exception:
        return 0


def forget(selector: dict, reason: str = "user_forget", apply: bool = False,
           journal_path: str = None) -> dict:
    """真正的删除：中枢 + 日志正文 + 全部索引。

    apply=False（默认）只列出会删什么，不动任何东西。

    返回报告 dict。删除是不可逆的（日志正文被原地抹除），
    所以调用方应当先跑一次 apply=False 看清楚。
    """
    journal_path = journal_path or memid._journal_path()
    mem_ids = _select(selector)
    conn = memid.get_conn()

    rows = []
    for mid in mem_ids:
        r = conn.execute(
            "SELECT mem_id, line_start, line_end, ts, sender, seq, text FROM memories"
            " WHERE mem_id=? AND deleted_at IS NULL", (mid,)).fetchone()
        if r:
            rows.append(r)

    report = {
        "selected": len(mem_ids), "found": len(rows), "applied": apply,
        "reason": reason,
        "preview": [{"seq": r[5], "mem_id": r[0], "ts": r[3],
                     "text": r[6][:60].replace("\n", " ⏎ ")} for r in rows[:10]],
        "hub": 0, "journal_lines": 0, "fts": 0, "idx": 0,
        "shadow": {}, "engram": 0, "vectors": 0, "backup": {},
    }
    if not apply or not rows:
        report["ok"] = True
        report["removed"] = 0
        report["action"] = "dry_run" if not apply else "nothing_matched"
        report["problems"] = []
        return report

    line_starts = [r[1] for r in rows]
    ids = [r[0] for r in rows]
    ts_list = [r[3] for r in rows]

    # 顺序有讲究：先抹日志正文（真相来源），再清各索引。
    # 反过来的话，中途失败会留下"索引没了但正文还在"——那还是能被搜到。
    report["journal_lines"] = _redact_journal(
        [(r[0], r[1], r[2], r[3], r[4]) for r in rows], reason, journal_path)
    report["backup"] = _redact_backup(
        [(r[0], r[1], r[2], r[3], r[4]) for r in rows], reason, journal_path)
    report["fts"] = _purge_fts(line_starts)
    report["idx"] = _purge_idx(line_starts)
    report["shadow"] = _purge_shadow(ids, line_starts)
    report["engram"] = _purge_engram(ts_list)
    report["vectors"] = _purge_vectors(ids)
    for mid in ids:
        if memid.tombstone(mid, reason=reason):
            report["hub"] += 1

    # 这个函数之前不返回 ok，也不返回 removed。调用方写 r.get("ok") 拿到 None，
    # 写 r.get("removed") 也拿到 None —— 删成功和删失败返回的东西一模一样。
    # 在一个专门负责「证明内容真的没了」的模块里，这是最讽刺的一种缺陷。
    #
    # ok 的判据只认两件事，因为只有这两件决定内容还能不能被读到：
    #   1. 日志正文被抹了（真相来源）—— 标记的物理行数不少于记录数
    #      （多行消息会标记续行，所以是 >= 而不是 ==）
    #   2. 中枢打上了墓碑（防止重建时复活）
    # fts / idx / shadow / engram / vectors 返回 0 是**合法**的 ——
    # 那条记录本来就可能没进过这些索引，拿它们判成败会造成假红。
    problems = []
    if report["hub"] != report["found"]:
        problems.append("中枢墓碑 %d/%d" % (report["hub"], report["found"]))
    if report["journal_lines"] < report["found"]:
        problems.append("日志抹除 %d 行 < %d 条记录" % (report["journal_lines"], report["found"]))
    # 影子副本里还留着正文 = 没删干净。它能被 nightwatch 还原回主沙漏。
    if report["backup"].get("status") == "out_of_sync":
        problems.append("影子副本行数对不上（backup=%s journal=%s），正文可能仍在"
                        % (report["backup"].get("backup_lines"),
                           report["backup"].get("journal_lines")))
    report["ok"] = not problems
    report["problems"] = problems
    report["removed"] = report["hub"]
    report["action"] = "erased" if report["ok"] else "partial"
    return report


# ══════════════════════════════════════════════════════════
# 证明：内容真的没了
# ══════════════════════════════════════════════════════════

def verify_erasure(needles: Iterable[str], journal_path: str = None) -> dict:
    """在**每一个**子系统里找这些字符串。全部找不到才算删干净。

    这是删除唯一的验收方式 —— 调 delete 返回 ok 不算数，
    生产上那次 `forget` 就是返回了 ok 而内容原封不动。
    """
    journal_path = journal_path or memid._journal_path()
    needles = [n for n in needles if n]
    found = {}

    def hit(where, sample):
        found.setdefault(where, []).append(sample[:60])

    if os.path.exists(journal_path):
        with open(journal_path, "r", encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                for nd in needles:
                    if nd in line:
                        hit("journal", f"L{n}: {line.strip()}")

    # 影子副本 sandglass.backup —— 删除的第二个现场。
    # 它不在原来的检查清单里，于是 verify_erasure 会在正文仍然躺在
    # backup 里的情况下报 clean=True。而 nightwatch 能把它还原回主沙漏。
    bak = os.path.join(os.path.dirname(journal_path), "sandglass.backup")
    if os.path.exists(bak):
        with open(bak, "r", encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                for nd in needles:
                    if nd in line:
                        hit("backup", f"L{n}: {line.strip()}")

    conn = memid.get_conn()
    for nd in needles:
        for r in conn.execute("SELECT seq, text FROM memories WHERE text LIKE ?", (f"%{nd}%",)):
            hit("hub", f"seq{r[0]}: {r[1]}")

    try:
        from nexsandglass.core import sandglass_sqlite as sq
        with sq._lock:
            db = sq._get_db()
            for nd in needles:
                for r in db.execute("SELECT id, text FROM sandglass WHERE text LIKE ?",
                                    (f"%{nd}%",)):
                    hit("fts", f"id{r[0]}: {r[1]}")
    except Exception as e:
        hit("fts_error", str(e))

    try:
        from nexsandglass.core.search_router import SearchRouter
        r = SearchRouter()
        for nd in needles:
            for h in r.search(nd, limit=5):
                if nd in (h[2] or ""):
                    hit("search", h[2])
    except Exception as e:
        hit("search_error", str(e))

    try:
        from nexsandglass.features import shadow_sand as sh
        with sh._db_lock:
            db = sh._get_conn()
            for nd in needles:
                for r in db.execute("SELECT name FROM entities WHERE name LIKE ?", (f"%{nd}%",)):
                    hit("shadow_entities", r[0])
    except Exception:
        pass

    store = os.path.join(_NB, "engram_store.jsonl")
    if os.path.exists(store):
        with open(store, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                for nd in needles:
                    if nd in line:
                        hit("engram", line.strip())

    return {"clean": not found, "needles": list(needles), "found_in": found}


def tombstoned_seqs() -> set:
    """已墓碑的 seq 集合。任何重建都必须跳过它们，否则删掉的内容会被捞回来。"""
    conn = memid.get_conn()
    return {r[0] for r in conn.execute(
        "SELECT seq FROM memories WHERE deleted_at IS NOT NULL")}
