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

一条记忆现在散落在 8 个地方，删除必须全部覆盖：

    1. nyx.db          memories 行（中枢身份）
    2. sandglass.txt   日志正文 ← 召回真正读的地方
    3. sandglass.db    FTS5 全文索引
    4. sandglass.idx   倒排索引
    5. shadow_sand.db  trust / entities / fact_tags
    6. shadow_sand.db  wthread_triples（知识图谱）
    7. engram_store.jsonl
    8. consolidation_snapshots/*.json（巩固快照：记忆全文，restore_snapshot 能捞回来）

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

from nexsandglass.core import memid, quarantine
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
    """把 selector 解析为 mem_id 列表。**只选，不删。**

    sender / senders（v7.13.9）：发送者精确匹配。单独用 = 选这些发送者的全部在世记录；
    和别的键一起用 = 只在别的键选中的里面留这些发送者的（收窄，不扩大 —— 删除宁少勿多）。
    注意 mcp 也是正常的生产发送者（MCP 写入的默认值），按发送者删之前先看 dry-run。
    """
    conn = memid.get_conn()
    out: list = []

    senders = [selector[k] for k in ("sender",) if selector.get(k)]
    senders += list(selector.get("senders") or [])
    senders = [s.strip() for s in senders if isinstance(s, str) and s.strip()]
    if senders:
        q = ",".join("?" * len(senders))
        by_sender = [r[0] for r in conn.execute(
            f"SELECT mem_id FROM memories WHERE deleted_at IS NULL AND sender IN ({q}) ORDER BY seq", senders)]
        rest = {k: v for k, v in selector.items() if k not in ("sender", "senders")
                and v is not None and v is not False and not (isinstance(v, (str, list, tuple, set)) and not v)}
        if not rest:
            return by_sender
        allowed = set(by_sender)
        return [m for m in _select(rest) if m in allowed]

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

    from nexsandglass.core.fsutil import atomic_write
    n_lines = 0
    # 必须和追加写入用同一把锁（v7.13.3）：读整份 → 写临时文件 → 替换，中间有人追加的那一行会被替换掉，
    # 中枢里有它、日志里没了（压测实测每轮丢 1–5 条）。锁是可重入的：forget 外层已经持有
    with memid.journal_lock(journal_path):
        with open(journal_path, "r", encoding="utf-8", errors="replace") as src, \
                atomic_write(journal_path) as dst:
            for n, line in enumerate(src, 1):
                dst.write(mark[n] + "\n" if n in mark else line)
                n_lines += 1
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

    from nexsandglass.core.fsutil import atomic_write
    with open(bak, "r", encoding="utf-8", errors="replace") as src, atomic_write(bak) as dst:
        for n, line in enumerate(src, 1):
            dst.write(mark[n] + "\n" if n in mark else line)
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
    idx = v.index_for_update()
    if not idx:
        return 0
    removed, out = 0, {}
    for token, lines in idx.items():
        keep = [l for l in lines if l not in drop]
        removed += len(lines) - len(keep)
        if keep:
            out[token] = keep
    v._set_index(out, v._idx_mtime or v._journal_lines())
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


def _purge_engram(ts_list: list, mem_ids: list = ()) -> int:
    """按 ts 或来源 mem_id 删（只按 ts 会漏：写日志和写 engram 跨过秒的边界时时间戳对不上）。"""
    store = os.path.join(_NB, "engram_store.jsonl")
    if not os.path.exists(store) or not (ts_list or mem_ids):
        return 0
    drop, drop_ids = set(ts_list or ()), set(mem_ids or ())
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
            if row.get("ts") in drop or (row.get("source_mem_id") and row.get("source_mem_id") in drop_ids):
                removed += 1
            else:
                kept.append(line)
    if removed:
        from nexsandglass.core.fsutil import atomic_write
        with atomic_write(store) as f:
            for l in kept:
                f.write(l + "\n")
    return removed


_ENGRAM_CLIP = 300      # engram 只存正文前 300 字（bridge.ingest / ingest_classified）


def _snapshot_files() -> list:
    """runtime/consolidation.py 的 _snapshot() 写在数据目录的 consolidation_snapshots/ 下。"""
    d = os.path.join(_NB, "consolidation_snapshots")
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    return [os.path.join(d, n) for n in names if n.endswith(".json")]


def _snapshot_hit(entry: dict, ids: set, texts: list) -> bool:
    """快照里的这一条是不是被删记忆的副本：来源 mem_id 对上，或者正文相同（engram 只存前 300 字）。

    快照条目来自 engram 行（facade.consolidate）：source_id = 来源 mem_id，content = 正文前 300 字。
    **不按 memory_id = engram:<ts> 认**：同一秒写进来的记忆共用这个 ts（批量写入时几十条），
    按它删会把别的记忆的回滚点一起删掉。
    """
    mid, src = entry.get("memory_id"), entry.get("source_id")
    if (isinstance(mid, str) and mid in ids) or (isinstance(src, str) and src in ids):
        return True
    c = str(entry.get("content") or "").strip()
    return bool(c) and any(c == t or c == t[:_ENGRAM_CLIP].strip() for t in texts)


def _purge_snapshots(mem_ids: Iterable[str], texts: Iterable[str] = ()) -> dict:
    """巩固快照里被删记忆的副本：整条删掉，文件里一条不剩就删文件。

    快照存记忆全文，给 restore_snapshot() 回滚用。不删的话，「忘记」之后原文还在磁盘上，回滚一次还会捞回来。
    快照是可再生的回滚点，这里删掉的条目 restore() 不还原。
    读不懂格式的文件不改；里面还有被删正文的，报在 left 里（调用方据此判 partial，不假装删干净了）。
    """
    ids = {m for m in mem_ids if m}
    texts = [t.strip() for t in texts if t and t.strip()]
    out = {"files": 0, "entries": 0, "left": []}
    if not ids and not texts:
        return out
    from nexsandglass.core.fsutil import atomic_write
    for p in _snapshot_files():
        name = os.path.basename(p)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError as e:
            out["left"].append(f"{name}（读不了：{e}）")
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, list):
            if any(t[:_ENGRAM_CLIP].strip() in raw for t in texts):
                out["left"].append(name)
            continue
        keep = [e for e in data if not (isinstance(e, dict) and _snapshot_hit(e, ids, texts))]
        if len(keep) == len(data):
            continue
        # 改不动也只记下来、不抛：forget 走到这里时日志已经抹了，抛出去墓碑就打不上了
        try:
            if keep:
                with atomic_write(p) as f:
                    json.dump(keep, f, ensure_ascii=False, indent=1)
            else:
                os.unlink(p)
        except OSError as e:
            out["left"].append(f"{name}（改不了：{e}）")
            continue
        out["entries"] += len(data) - len(keep)
        out["files"] += 1
    return out


def wipe_free_space() -> dict:
    """真删之后：删掉的正文不留在任何库文件里（v7.14.0）。

    以前只有隔离区 purge 对 nyx.db 做了 VACUUM。全文库 sandglass.db 自己也存整段正文，
    删行后正文还在空闲页里；FTS5 更麻烦 —— 删掉的词元留在索引段里，**VACUUM 都清不掉**，
    要先 optimize 合并段（实测）。影子沙、向量库同样。这些地方 verify_erasure 用 SQL 是查不到的，
    按原始字节一读就出来了。返回每个库的结果：ok 或失败原因。
    """
    from nexsandglass.core import sandglass_sqlite as sq
    from nexsandglass.core import semantic, sqlite_open
    from nexsandglass.features import shadow_sand as sh
    out = {}
    try:
        with sq._lock:
            if os.path.exists(sq._DB):
                c = sq._get_db()
                try:
                    c.execute("INSERT INTO sandglass_fts(sandglass_fts) VALUES('optimize')")
                    c.commit()
                finally:
                    c.close()
                out[os.path.basename(sq._DB)] = sqlite_open.vacuum(sq._DB) or "ok"
    except Exception as e:
        out["fts"] = str(e)
    with sh._db_lock:
        if sh._conn is not None:
            try:
                sh._conn.commit()
            except Exception:
                pass
        if os.path.exists(sh._SHADOW_DB):
            out[os.path.basename(sh._SHADOW_DB)] = sqlite_open.vacuum(sh._SHADOW_DB) or "ok"
    for path in (memid._db_path(), semantic._db_path()):
        if os.path.exists(path):
            out[os.path.basename(path)] = sqlite_open.vacuum(path) or "ok"
    try:
        from nexsandglass.core import secret_vault       # v7.15.0：来源记忆都没了的密钥条目也删掉
        if os.path.exists(secret_vault.db_path()):
            secret_vault.gc()
            out[secret_vault.DB_NAME] = secret_vault.wipe() or "ok"
    except Exception as e:
        out["vault"] = str(e)
    bad = {k: v for k, v in out.items() if v != "ok"}
    if bad:
        logger.warning("[erasure] 空闲页没抹干净（停掉 Hermes / MCP 服务再跑 purge）: %s", bad)
    return out


def _purge_vectors(mem_ids: list) -> int:
    """语义索引（数据目录里的 vectors.db）级联删除。

    旧实现调 vector_store.get_vector_store() —— 那是一个写死在 ~/.hermes 的 JSON 存储，
    生产路径从来没往里写过；调用它反而会在用户 home 下创建文件，并且对每个 id 都报「删了 1 条」。
    """
    try:
        from nexsandglass.core import semantic
        return semantic.delete(mem_ids)
    except Exception as e:
        logger.warning("[erasure] 向量删除失败: %s", e)
        return 0


def forget(selector: dict, reason: str = "user_forget", apply: bool = False,
           journal_path: str = None, mode: str = "quarantine",
           retention_days: int = None) -> dict:
    """见 _forget_impl。真正动手时整个过程持有日志锁（v7.13.3）：选中 → 留副本 → 抹正文 → 清索引 → 墓碑
    之间不许有人追加，否则改写日志时会把新追加的那一行替换掉。预演不拿锁。"""
    if not apply:
        return _forget_impl(selector, reason, apply, journal_path, mode, retention_days)
    with memid.journal_lock(journal_path or memid._journal_path()):
        report = _forget_impl(selector, reason, apply, journal_path, mode, retention_days)
    if report.get("mode") == "purge" and report.get("found"):
        # 保险箱里只被这几条提到的密钥跟着真删（v7.15.0）
        refs = report.pop("vault_refs", None)
        if refs:
            try:
                from nexsandglass.core import secret_vault
                report["vault_deleted"] = [f"v{i}" for i in secret_vault.drop_unreferenced(refs)]
            except Exception as e:
                report["problems"].append(f"保险箱里对应的条目没删掉：{e}")
        # 当场不可逆 = 磁盘上也没了：库文件空闲页、FTS5 段里的旧词元（v7.14.0）。
        # **在日志锁外面做**：VACUUM 要几秒（库大时更久），持锁做的话，别的进程（MCP、网关）追加日志
        # 等锁超时就写不进去 —— 连删几条就会丢消息（并发测试在机器忙的时候实测到）
        report["disk"] = wipe_free_space()
        bad = {k: v for k, v in report["disk"].items() if v != "ok"}
        if bad:
            report["problems"].append("库文件空闲页没抹干净：" + "；".join(f"{k} {v}" for k, v in bad.items()))
            report["ok"], report["action"] = False, "partial"
    return report


def _forget_impl(selector: dict, reason: str = "user_forget", apply: bool = False,
                 journal_path: str = None, mode: str = "quarantine",
                 retention_days: int = None) -> dict:
    """真正的删除：中枢 + 日志正文 + 全部索引。

    apply=False（默认）只列出会删什么，不动任何东西。

    两种 mode（检索侧的效果**完全一样**，区别只在能不能反悔）：

      mode="quarantine"（默认）
          删除前先把原文与派生行留进隔离区（`core/quarantine.py`），
          保留 N 天（默认 30，`NYX_FORGET_RETENTION_DAYS`）。
          期间 `restore(mem_id)` 可逐字节还原；到期由 `purge()` 物理擦除。
      mode="purge"
          不留隔离区，当场不可逆 —— v7.4 之前的老行为。
          给「现在就必须没有」的场合（法务、他人隐私、误粘贴的凭据）。

    为什么默认改成隔离区：v7.4 有一次 `forget` 误抹了 345 行真实对话，
    而同一个仓库里风险更低的 dream 破坏性合并反而有快照可回滚。
    详见 `core/quarantine.py` 的模块文档（含隔离期内原文仍在磁盘上的诚实声明）。
    """
    journal_path = journal_path or memid._journal_path()
    mode = (mode or "quarantine").strip().lower()
    if mode not in ("quarantine", "purge"):
        raise ValueError("mode 只能是 quarantine / purge，收到 %r" % mode)
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
        "shadow": {}, "engram": 0, "vectors": 0, "backup": {}, "snapshots": {},
        "mode": mode, "quarantined": 0, "purge_after": "", "recoverable": False,
    }
    if not apply or not rows:
        report["ok"] = True
        report["removed"] = 0
        report["action"] = "dry_run" if not apply else "nothing_matched"
        report["problems"] = []
        if not apply:
            # 预演也要说清楚「删完还能不能反悔」—— 这是用户按下确认前最该知道的事
            days = quarantine.retention_days() if retention_days is None else retention_days
            report["retention_days"] = days
            report["recoverable"] = mode == "quarantine" and days > 0
        return report

    line_starts = [r[1] for r in rows]
    ids = [r[0] for r in rows]
    ts_list = [r[3] for r in rows]
    if mode == "purge":
        from nexsandglass.core import secret_vault
        report["vault_refs"] = secret_vault.refs_in(r[6] for r in rows)

    # 隔离区**先于**级联清除 —— 先留副本再动手。
    # 反过来的话，级联中途失败就会留下「正文已抹、副本没留」的不可恢复状态。
    if mode == "quarantine":
        held = quarantine.hold(
            rows, reason=reason, journal_path=journal_path,
            engram_path=os.path.join(_NB, "engram_store.jsonl"),
            days=retention_days)
        report["quarantined"] = held["held"]
        report["purge_after"] = held["purge_after"]
        report["retention_days"] = held["days"]
        report["recoverable"] = held["held"] > 0
        if held["skipped"]:
            # 抓不下来的那几条会变成不可逆删除，不能静默
            report.setdefault("warnings", []).append(
                "%d 条未能进隔离区，删除后不可恢复" % held["skipped"])

    # 顺序有讲究：先抹日志正文（真相来源），再清各索引。
    # 反过来的话，中途失败会留下"索引没了但正文还在"——那还是能被搜到。
    report["journal_lines"] = _redact_journal(
        [(r[0], r[1], r[2], r[3], r[4]) for r in rows], reason, journal_path)
    report["backup"] = _redact_backup(
        [(r[0], r[1], r[2], r[3], r[4]) for r in rows], reason, journal_path)
    report["fts"] = _purge_fts(line_starts)
    report["idx"] = _purge_idx(line_starts)
    report["shadow"] = _purge_shadow(ids, line_starts)
    report["engram"] = _purge_engram(ts_list, ids)
    report["snapshots"] = _purge_snapshots(ids, [r[6] for r in rows])
    for mid in ids:
        if memid.tombstone(mid, reason=reason):
            report["hub"] += 1
    # 向量放在墓碑**之后**：后台补索引的线程只给在世记忆嵌入，墓碑先落下，
    # 它就不可能在「删向量」与「打墓碑」之间把向量又补回来（v7.13）
    report["vectors"] = _purge_vectors(ids)

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
    if report["snapshots"].get("left"):
        problems.append("巩固快照里正文仍在（文件读不懂或改不了）：%s"
                        % "、".join(report["snapshots"]["left"]))
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

    三个返回字段，**刻意不合并成一个**：

      clean          任何检索路径都摸不到（召回/搜索/索引/正文/影子副本）
      in_quarantine  隔离区里还留着（可 restore，到期 purge）
      fully_purged   clean 且隔离区里也没有 —— 「从磁盘上真的没了」

    `clean=True, fully_purged=False` 是隔离期内的正常状态：
    用户读不到了，但还能反悔。把这一格并进 clean 会让"可恢复"和"已彻底删除"
    返回同一个答案 —— 那正是 Déjà Vu 在讲的那类错误。
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

    # 巩固快照：记忆全文，restore_snapshot() 能把它捞回来。先按 JSON 读一遍再找，
    # 按 ensure_ascii 写的文件里中文是 \uXXXX，直接按字面找不到
    for p in _snapshot_files():
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            hit("snapshots_error", f"{os.path.basename(p)}: {e}")
            continue
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False)
        except ValueError:
            pass
        for nd in needles:
            if nd in text:
                hit("snapshots", os.path.basename(p))

    # 语义索引里不存正文，按「指向已删除记忆的向量」验收：遗忘之后必须为 0
    try:
        from nexsandglass.core import semantic
        for m in semantic.orphans():
            hit("vectors", f"orphan vector {m}")
    except Exception as e:
        hit("vectors_error", str(e))

    try:
        q_hits = quarantine.find_text(needles)
    except Exception as e:
        q_hits = {}
        logger.warning("[erasure] 隔离区扫描失败: %s", e)

    return {"clean": not found, "needles": list(needles), "found_in": found,
            "in_quarantine": q_hits, "fully_purged": (not found) and not q_hits}


# ══════════════════════════════════════════════════════════
# 反悔与到期擦除（门面在此，实现在 core/quarantine.py）
# ══════════════════════════════════════════════════════════

def restore(mem_id: str, journal_path: str = None) -> dict:
    """把一条被"忘记"的记忆从隔离区完整还原（逐字节写回日志正文）。

    只在隔离期内有效；purge 之后隔离区那份已经没了，返回 found=False。
    """
    return quarantine.restore(
        mem_id, journal_path=journal_path or memid._journal_path(),
        engram_path=os.path.join(_NB, "engram_store.jsonl"))


def purge(mem_ids: list = None, apply: bool = False, older_than: bool = True) -> dict:
    """隔离区到期擦除。默认只清已过期的；apply=False 只预演。"""
    return quarantine.purge(mem_ids, older_than=older_than, apply=apply)


def quarantine_list(limit: int = 200) -> list:
    """隔离区清单（含 purge_after，供人核对还剩几天可反悔）。"""
    return quarantine.list_all(limit=limit)


def tombstoned_seqs() -> set:
    """已墓碑的 seq 集合。任何重建都必须跳过它们，否则删掉的内容会被捞回来。"""
    conn = memid.get_conn()
    return {r[0] for r in conn.execute(
        "SELECT seq FROM memories WHERE deleted_at IS NOT NULL")}
