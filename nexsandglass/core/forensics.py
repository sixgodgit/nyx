"""
core/forensics.py — 中枢 / 日志 / 墓碑的内容级体检与修复（v7.13.9）

为什么要有它：2026-09-13 生产事故 —— 测试把 107 条合成记忆（sender=golden / test / e2e / mcp）
直接写进了生产中枢（日志里根本没有），76 条真实记忆被覆盖，agent 把「用户喜欢喝拿铁」
「预算 2000 欧」当成真记忆说了出来。之后自检报：中枢落后日志 278 条、全文索引对不上、
163 个墓碑「泄漏」。

按 seq 判断会把三件事搞反，所以这里一律按**内容身份**（时间 | 发送者 | 正文）对：

  1. 「中枢落后日志」不是孤儿问题：日志里有、中枢里没有的记录要**补进**中枢。
     只删中枢行（drop_orphans）修不了它，反而让计数更难看懂
  2. 「墓碑泄漏」不一定是真泄漏：合成记录被打了墓碑，墓碑上的 seq 却指着日志里一条**真实记录**。
     按「泄漏」去抹那个 seq，抹掉的是真实记忆，而且不可逆 —— 这正是「清理孤儿不能走 forget()」的原因。
     墓碑上的 mem_id 是内容哈希：和日志那条对得上才是真泄漏（遗忘没做完）；对不上的只是指错了位置
  3. 合成记录派生出来的事实（双时态信念、engram、实体、向量）不在日志里，重建索引清不掉 ——
     只删中枢行的话，「用户喜欢喝拿铁」还会被说出来。这些只能按 mem_id 清，**不能按行号**（孤儿的行号不可信）

被覆盖的真实记忆认得出来：mem_id 就是它原文的哈希。中枢里某一行的 mem_id 等于日志某条记录的哈希、
正文却换成了别的 —— 那一行就是那条记录，把正文还回去，mem_id 和写入时绑定的来源都不变。

原则：
  - 日志是唯一真相。修复**不改日志一个字节**（除非显式 finish_erasures，且只抹身份已核实的那几行）
  - 对得上的记录保留原 mem_id 与来源绑定，只是挪到正确的位置；只能从日志重新解析的记录标「恢复（未经证实）」，
    没有规则权（见 memid._record_recovered）
  - 被遗忘的记忆绝不复活：日志没抹干净的那几条在中枢里保持「已删除」，并从所有索引里撤下
  - 修之前自动备份；修的全过程持有日志锁（期间的写入排队）；修完自动复检

用法见 nexsandglass/repair.py（python3 -m nexsandglass.repair）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional

from nexsandglass.core import memid

logger = logging.getLogger(__name__)

# 只用来在报告里标注「像测试数据」，**不作为删除依据**（mcp 也可能是真实的 MCP 写入）
SUSPECT_SENDERS = ("golden", "test", "e2e", "pytest", "fixture", "mock")
_RED_MEM = re.compile(r"mem=(m_[0-9a-f]{16}(?:-\d+)?)")
_MID = re.compile(r"^(m_[0-9a-f]{16})(?:-\d+)?$")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _key(ts, sender, text) -> tuple:
    return (ts or "", (sender or "").strip(), (text or "").strip())


def _base(mem_id: str) -> str:
    m = _MID.match(mem_id or "")
    return m.group(1) if m else (mem_id or "")


def _load(journal_path: str):
    recs = memid.parse_journal_records(journal_path)
    conn = memid.get_conn()
    hub = conn.execute("SELECT seq, mem_id, ts, sender, text, deleted_at FROM memories ORDER BY seq").fetchall()
    tombs = conn.execute("SELECT mem_id, seq, reason, deleted_at FROM tombstones").fetchall()
    return recs, hub, tombs


def _plan(recs: list, hub: list, tombs: list) -> dict:
    """算出「对齐到日志之后」的中枢应该长什么样，并给每一类不一致分账。纯计算，不写任何东西。

    第一遍按内容配对（正文相同就是同一条，不管它现在在哪个 seq）；
    第二遍处理剩下的日志记录：按 mem_id 认回被覆盖的行 → 认出没做完的遗忘 → 都不是才当缺失补进来。
    """
    J = {r["seq"]: r for r in recs}
    redacted = {s for s, r in J.items() if memid.is_redacted(r)}
    live = [h for h in hub if not h[5]]
    dead = [h for h in hub if h[5]]
    dead_by_mid = {h[1]: h for h in dead}
    dead_by_seq = {h[0]: h for h in dead}
    by_key = defaultdict(list)
    live_by_base = defaultdict(list)
    for h in live:
        by_key[_key(h[2], h[3], h[4])].append(h)
        live_by_base[_base(h[1])].append(h)
    tomb_by_mid = {t[0]: t for t in tombs}
    gone_by_base = defaultdict(list)            # 删过的：墓碑 + 中枢里已删除的行
    for t in tombs:
        gone_by_base[_base(t[0])].append((t[0], t[3]))
    for h in dead:
        if h[1] not in tomb_by_mid:
            gone_by_base[_base(h[1])].append((h[1], h[5]))
    taken = {h[1] for h in hub} | set(tomb_by_mid)    # 新分配的 id 不许撞上旧 id（旧 id 上可能还挂着派生数据）

    used, slot = set(), {}

    def put(s, **kw):
        r = J[s]
        slot[s] = dict(seq=s, line_start=r["line_start"], line_end=r["line_end"], **kw)

    # ── 第一遍：按内容配对 ──
    for s in sorted(J):
        r = J[s]
        if s in redacted:
            m = _RED_MEM.search(r["text"])
            h = dead_by_mid.get(m.group(1)) if m else None
            if h is None or h[1] in used:
                h = dead_by_seq.get(s)
            if h is not None and h[1] not in used:
                used.add(h[1])
                put(s, mem_id=h[1], ts=h[2], sender=h[3], text="", deleted_at=h[5], kind="erased")
            continue
        cands = [h for h in by_key.get(_key(r["ts"], r["sender"], r["text"]), []) if h[1] not in used]
        h = next((c for c in cands if c[0] == s), None) or (cands[0] if cands else None)
        if h is not None:
            used.add(h[1])
            put(s, mem_id=h[1], ts=h[2], sender=h[3], text=h[4], deleted_at=None,
                kind="kept" if h[0] == s else "moved")

    def new_mid(r):
        base = memid.compute_mem_id(r["ts"], r["sender"], r["text"])
        mid, n = base, 1
        while mid in used or mid in taken:
            n += 1
            mid = f"{base}-{n}"
        return mid

    # ── 第二遍：剩下的日志记录 ──
    for s in sorted(J):
        if s in slot:
            continue
        r = J[s]
        if s in redacted:        # 日志抹了、中枢里却没有这一行：补一个已删除的占位（计数才对得上）
            m = _RED_MEM.search(r["text"])
            mid = m.group(1) if m and m.group(1) not in used else new_mid(r)
            used.add(mid)
            put(s, mem_id=mid, ts=r["ts"], sender=r["sender"], text="",
                deleted_at=(tomb_by_mid.get(mid) or (None,) * 4)[3] or _now(), kind="erased_new")
            continue
        base = memid.compute_mem_id(r["ts"], r["sender"], r["text"])
        # 被覆盖：中枢里有一行的 mem_id 就是这条原文的哈希，正文却换了 —— 认回来，原 id 原来源
        h = next((x for x in live_by_base.get(base, []) if x[1] not in used), None)
        if h is not None:
            used.add(h[1])
            put(s, mem_id=h[1], ts=r["ts"], sender=r["sender"], text=r["text"], deleted_at=None,
                kind="overwritten")
            continue
        # 被遗忘过（墓碑 mem_id = 这条原文的哈希）、日志正文却还在：遗忘没做完。绝不复活
        g = next((x for x in gone_by_base.get(base, []) if x[0] not in used), None)
        if g is not None:
            used.add(g[0])
            put(s, mem_id=g[0], ts=r["ts"], sender=r["sender"], text="", deleted_at=g[1] or _now(),
                kind="unfinished_erasure")
            continue
        mid = new_mid(r)
        used.add(mid)
        put(s, mem_id=mid, ts=r["ts"], sender=r["sender"], text=r["text"], deleted_at=None, kind="restored")

    target = [slot[s] for s in sorted(slot)]
    orphans = [h for h in live if h[1] not in used]
    stray_dead = [h for h in dead if h[1] not in used]
    keep_tombs = {t["mem_id"] for t in target if t["deleted_at"]}
    stray_tombs = [t for t in tombs if t[0] not in keep_tombs]
    # 「泄漏」重新归类：健康检查按墓碑 seq 看日志那一条有没有抹 —— 指错位置的墓碑会被误报成泄漏
    false_leaks = [t for t in stray_tombs if t[1] in J and t[1] not in redacted]
    by = defaultdict(list)
    for t in target:
        by[t["kind"]].append(t)
    return {"target": target, "by_kind": by, "orphans": orphans, "stray_dead": stray_dead,
            "stray_tombs": stray_tombs, "false_leaks": false_leaks,
            "journal_records": len(J), "hub_rows": len(hub), "hub_live": len(live)}


def _ranges(seqs) -> list:
    """[3,4,5,9] → ["3-5", "9"]：报告里只给位置，不给正文。"""
    out, seqs = [], sorted(seqs)
    i = 0
    while i < len(seqs):
        j = i
        while j + 1 < len(seqs) and seqs[j + 1] == seqs[j] + 1:
            j += 1
        out.append(str(seqs[i]) if i == j else f"{seqs[i]}-{seqs[j]}")
        i = j + 1
    return out


def _summary(plan: dict, journal_path: str) -> dict:
    by = plan["by_kind"]
    orph = plan["orphans"]
    n = lambda k: len(by.get(k, []))
    suspect_in_journal = Counter(t["sender"] for t in plan["target"]
                                 if t["deleted_at"] is None and (t["sender"] or "").lower() in SUSPECT_SENDERS)
    out = {
        "journal": journal_path, "journal_records": plan["journal_records"],
        "hub_rows": plan["hub_rows"], "hub_live": plan["hub_live"],
        # 对得上的
        "aligned": n("kept"), "erased_ok": n("erased"),
        # 要修的（中枢那一侧）
        "misplaced": n("moved"),
        "overwritten": n("overwritten"), "overwritten_seqs": _ranges(t["seq"] for t in by.get("overwritten", [])),
        "missing_from_hub": n("restored"), "missing_seqs": _ranges(t["seq"] for t in by.get("restored", [])),
        "orphans": len(orph), "orphans_by_sender": dict(Counter(h[3] for h in orph).most_common()),
        "orphan_seqs": _ranges(h[0] for h in orph),
        "redacted_unregistered": n("erased_new"),
        "stray_tombstones": len(plan["stray_tombs"]), "false_leaks": len(plan["false_leaks"]),
        "false_leak_seqs": _ranges(t[1] for t in plan["false_leaks"]),
        "stray_deleted_rows": len(plan["stray_dead"]),
        # 要人决定的（日志那一侧）
        "unfinished_erasures": n("unfinished_erasure"),
        "unfinished_erasure_seqs": _ranges(t["seq"] for t in by.get("unfinished_erasure", [])),
        "suspect_senders_in_journal": dict(suspect_in_journal.most_common()),
    }
    out["hub_clean"] = not (out["misplaced"] or out["overwritten"] or out["missing_from_hub"] or out["orphans"]
                            or out["redacted_unregistered"] or out["stray_tombstones"]
                            or out["stray_deleted_rows"])
    out["clean"] = out["hub_clean"] and not out["unfinished_erasures"]
    return out


def _undo_closures(db, bad: list, bad_ids: set) -> dict:
    """合成事实写进来的时候，可能把一条真实的现任信念截断了（单值关系：「偏好 茶」被「偏好 拿铁」关掉）。
    只删合成那一行的话，真实那条还关着 —— agent 会以为用户不再偏好茶。

    只处理「就地截断」这一种（record_fact 的标准情形）：被截断那一行的 closed_at = 合成事实的记录时刻、
    valid_until = 合成事实的起点。同一时刻同一主语/关系还有留下来的写入（分不清是谁截断的）就不动。
    截断之后又来过真实的新事实：按「合成那条从没出现过」重算 —— 终点是下一条真实事实的起点。
    「撤回再插回」那几种更正路径不自动改，只报数（beliefs_to_review）。
    """
    out = {"beliefs_reopened": 0, "beliefs_reclosed": 0, "beliefs_to_review": 0}
    for rid, subj, rel, vf, rec in bad:
        if not rec:
            continue
        same_moment = db.execute("SELECT id FROM wthread_triples WHERE subject=? AND relation=? AND recorded_at=?",
                                 (subj, rel, rec)).fetchall()
        if any(r[0] not in bad_ids for r in same_moment):
            out["beliefs_to_review"] += 1
            continue
        out["beliefs_to_review"] += db.execute(
            "SELECT COUNT(*) FROM wthread_triples WHERE subject=? AND relation=? AND retracted_at=?",
            (subj, rel, rec)).fetchone()[0]
        closed = db.execute("SELECT id, valid_from FROM wthread_triples WHERE subject=? AND relation=?"
                            " AND closed_at=? AND valid_until=? AND retracted_at IS NULL",
                            (subj, rel, rec, vf)).fetchall()
        for cid, cvf in closed:
            if cid in bad_ids:
                continue
            nxt = [r for r in db.execute(
                "SELECT id, valid_from, recorded_at FROM wthread_triples WHERE subject=? AND relation=?"
                " AND retracted_at IS NULL AND valid_from > ? ORDER BY valid_from, id", (subj, rel, cvf))
                if r[0] not in bad_ids]
            if nxt:
                db.execute("UPDATE wthread_triples SET valid_until=?, closed_at=? WHERE id=?",
                           (nxt[0][1], nxt[0][2], cid))
                out["beliefs_reclosed"] += 1
            else:
                db.execute("UPDATE wthread_triples SET valid_until=NULL, closed_at=NULL WHERE id=?", (cid,))
                out["beliefs_reopened"] += 1
    return out


def _derived(nb: str, dropped: set, drop_src: set, live_ids: set, junk_ts: set, erased_ts: set,
             line_text: dict) -> dict:
    """撤下派生数据。只认身份：来源 mem_id 是合成 / 孤儿记录（dropped）或已删除记录（drop_src）的才删。

    孤儿一律不按行号动刀 —— 它们的行号是在别的日志上分配的，指着的是本日志里的真实记录。
    engram 旧行没有 source_mem_id，只能按时间戳认：只删「孤儿的时间戳、且日志里没有这个时间戳」的，
    以及没做完的遗忘那几条的时间戳（forget 本来就这么删）。
    来源 mem_id 哪儿都找不到的（比如更早的修复改过 id）分不清真假，不删，只报数（*_unknown_source）。
    """
    out = {"triples": 0, "entity_links": 0, "entities_removed": 0, "engram": 0, "vectors": 0,
           "triples_unknown_source": 0, "engram_unknown_source": 0}
    try:
        from nexsandglass.features import shadow_sand as sh
        with sh._db_lock:
            db = sh._get_conn()
            try:
                cols = {r[1] for r in db.execute("PRAGMA table_info(wthread_triples)")}
                if {"source_mem_id", "recorded_at", "closed_at", "retracted_at"} <= cols:
                    rows = db.execute("SELECT id, subject, relation, valid_from, recorded_at, source_mem_id "
                                      "FROM wthread_triples WHERE source_mem_id IS NOT NULL "
                                      "AND source_mem_id <> ''").fetchall()
                    bad = [r[:5] for r in rows if r[5] in drop_src]
                    out["triples_unknown_source"] = sum(1 for r in rows if r[5] not in live_ids and r[5] not in drop_src)
                    bad_ids = {r[0] for r in bad}
                    out.update(_undo_closures(db, bad, bad_ids))
                    ids = sorted(bad_ids)
                    for i in range(0, len(ids), 400):
                        part = ids[i:i + 400]
                        db.execute(f"DELETE FROM wthread_triples WHERE id IN ({','.join('?' * len(part))})", part)
                    out["triples"] = len(ids)
            except sqlite3.Error:
                pass                                 # 还没有这张表
            try:
                for rowid, name, lns, mids in db.execute(
                        "SELECT rowid, name, line_nums, mem_ids FROM entities").fetchall():
                    ids = [x for x in (mids or "").split(",") if x]
                    keep = [x for x in ids if x not in dropped]
                    if len(keep) == len(ids):
                        continue
                    out["entity_links"] += len(ids) - len(keep)
                    if not keep:                     # 来源全是合成记录：整个实体都是它们带进来的
                        db.execute("DELETE FROM entities WHERE rowid=?", (rowid,))
                        out["entities_removed"] += 1
                        continue
                    # 合成记录带进来的行号指着别的记录：只留正文里确实有这个实体名的行
                    lines = [x for x in (lns or "").split(",") if x.strip().isdigit()
                             and (name or "").lower() in (line_text.get(int(x)) or "").lower()]
                    db.execute("UPDATE entities SET mem_ids=?, line_nums=? WHERE rowid=?",
                               (",".join(keep), ",".join(lines), rowid))
            except sqlite3.Error:
                pass
            db.commit()
    except Exception as e:
        out["shadow_error"] = str(e)
    try:
        store = os.path.join(nb, "engram_store.jsonl")
        if os.path.exists(store):
            kept, removed = [], 0
            for line in open(store, encoding="utf-8", errors="replace"):
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    kept.append(line)
                    continue
                src = row.get("source_mem_id")
                if (src and src in drop_src) or (not src and row.get("ts") in (junk_ts | erased_ts)):
                    removed += 1
                else:
                    kept.append(line)
                    if src and src not in live_ids:
                        out["engram_unknown_source"] += 1
            if removed:
                from nexsandglass.core.fsutil import atomic_write
                with atomic_write(store) as f:
                    for l in kept:
                        f.write(l + "\n")
            out["engram"] = removed
    except Exception as e:
        out["engram_error"] = str(e)
    try:
        from nexsandglass.core import semantic
        out["vectors"] = semantic.delete(semantic.orphans())
    except Exception as e:
        out["vectors_error"] = str(e)
    return out


def _backup(nb: str) -> str:
    """数据目录里的文件整份拷一份（子目录不拷）。.db 用 SQLite 在线备份：WAL 里还没合并的也带上。"""
    dest = os.path.join(nb, "backups", "repair-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    os.makedirs(dest, exist_ok=True)
    for name in sorted(os.listdir(nb)):
        src = os.path.join(nb, name)
        if not os.path.isfile(src) or name.endswith(("-wal", "-shm", "-journal", ".lock")):
            continue
        if name.endswith(".db"):
            s = sqlite3.connect(src)
            d = sqlite3.connect(os.path.join(dest, name))
            try:
                s.backup(d)
            finally:
                d.close()
                s.close()
        else:
            shutil.copy2(src, os.path.join(dest, name))
    return dest


def diagnose(journal_path: str = None) -> dict:
    """只读体检：按内容逐条核对中枢、日志、墓碑。只输出计数与 seq 范围，不输出正文。"""
    jp = journal_path or memid._journal_path()
    return _summary(_plan(*_load(jp)), jp)


def repair(apply: bool = False, journal_path: str = None, finish_erasures: bool = False,
           forget_senders: Optional[list] = None) -> dict:
    """把中枢对齐到日志（见模块文档）。apply=False 只体检，什么都不写。

    finish_erasures：把「遗忘没做完」的那几条在日志里抹掉 —— 身份已按内容哈希核实，只动这几行。
    forget_senders：对齐之后，把这些发送者在日志里的记录送进隔离区（30 天内可还原）。
        此时中枢行号已经和日志一致，按行号抹日志才安全；对齐没成功就不做。
    """
    jp = journal_path or memid._journal_path()
    if not apply:
        rep = diagnose(jp)
        rep["applied"] = False
        return rep
    nb = os.path.dirname(os.path.abspath(jp))
    report = {"applied": True, "backup": _backup(nb)}
    with memid.journal_lock(jp):
        recs, hub, tombs = _load(jp)
        plan = _plan(recs, hub, tombs)
        report["before"] = _summary(plan, jp)
        target, by = plan["target"], plan["by_kind"]
        rec_by_seq = {r["seq"]: r for r in recs}
        live_ids = {t["mem_id"] for t in target if t["deleted_at"] is None}
        dropped = {h[1] for h in hub} - {t["mem_id"] for t in target}
        tomb_reason = {t[0]: t[2] for t in tombs}
        conn = memid.get_conn()
        kept_prov = 0
        try:
            # 整张表按目标状态重写（一个事务）。没有外键，不会级联到别处
            conn.execute("DELETE FROM memories")
            conn.executemany(
                "INSERT INTO memories (mem_id, seq, ts, sender, text, content_hash, line_start, line_end,"
                " deleted_at) VALUES (?,?,?,?,?,?,?,?,?)",
                [(t["mem_id"], t["seq"], t["ts"], t["sender"], t["text"],
                  memid.content_hash(t["text"]) if t["text"] else "",
                  t["line_start"], t["line_end"], t["deleted_at"]) for t in target])
            conn.execute("DELETE FROM tombstones")
            conn.executemany(
                "INSERT INTO tombstones (mem_id, seq, reason, deleted_at) VALUES (?,?,?,?)",
                [(t["mem_id"], t["seq"], tomb_reason.get(t["mem_id"], "repair_redacted"), t["deleted_at"])
                 for t in target if t["deleted_at"]])
            for mid in dropped:
                conn.execute("DELETE FROM provenance WHERE mem_id=?", (mid,))
            for t in by.get("restored", []):
                # 这个 id 上还留着写入时绑定的来源（行被整行替换掉了，来源表没动）：id 是这条原文的哈希，
                # 来源就是它的，留着。没有才标「从日志恢复、未经证实」
                if conn.execute("SELECT 1 FROM provenance WHERE mem_id=? AND bound=1",
                                (t["mem_id"],)).fetchone():
                    kept_prov += 1
                else:
                    memid._record_recovered(conn, t["mem_id"], rec_by_seq[t["seq"]])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        report["restored_with_original_provenance"] = kept_prov

        unfinished = by.get("unfinished_erasure", [])
        journal_ts = {r["ts"] for r in recs}
        junk_ts = {h[2] for h in plan["orphans"]} - journal_ts
        line_text = {}
        for r in recs:
            for n in range(r["line_start"], r["line_end"] + 1):
                line_text[n] = r["text"]
        drop_src = dropped | {t["mem_id"] for t in target if t["deleted_at"]}
        report["derived_removed"] = _derived(nb, dropped, drop_src, live_ids, junk_ts,
                                             {t["ts"] for t in unfinished}, line_text)

        if finish_erasures and unfinished:
            from nexsandglass.core import erasure
            rows = [(t["mem_id"], t["line_start"], t["line_end"], t["ts"], t["sender"]) for t in unfinished]
            report["finished_erasures"] = erasure._redact_journal(rows, "repair_finish_erasure", jp)
            report["finished_erasures_backup"] = erasure._redact_backup(rows, "repair_finish_erasure", jp)

    # 派生索引从日志全量重建（比按行号动刀安全），再把没做完的遗忘从索引里撤下
    from nexsandglass.core import erasure, sandglass_sqlite, semantic
    from nexsandglass.features import sandglass_vault
    report["fts_rebuilt"] = sandglass_sqlite.sync_all()
    sandglass_vault.rebuild_index()
    if unfinished and not finish_erasures:
        lines = [t["line_start"] for t in unfinished]
        erasure._purge_fts(lines)
        erasure._purge_idx(lines)
    if unfinished:            # 这几行的身份已核实，行号可信
        report["derived_removed"]["unfinished_shadow"] = erasure._purge_shadow(
            [t["mem_id"] for t in unfinished], [t["line_start"] for t in unfinished])
    try:
        report["vectors_realigned"] = semantic.realign(
            {t["mem_id"]: t["line_start"] for t in target if t["deleted_at"] is None})
        semantic.schedule_index()             # 补进来的记录在后台补向量
    except Exception as e:
        report["vectors_error"] = str(e)

    if forget_senders:
        v = memid.verify(jp)
        if not v["ok"]:
            report["forget_senders"] = {"skipped": "对齐之后中枢与日志仍不一致，按行号抹日志不安全"}
        else:
            report["forget_senders"] = erasure.forget({"senders": list(forget_senders)}, apply=True,
                                                      journal_path=jp, reason="test_pollution",
                                                      mode="quarantine")
    report["after"] = diagnose(jp)
    report["health_after"] = {k: v.get("ok") for k, v in memid.health(jp)["checks"].items()}
    return report
