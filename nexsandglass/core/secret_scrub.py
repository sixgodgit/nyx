"""
core/secret_scrub.py — 升级之前已经存下来的密钥：找出来、原地隐去（v7.14.0）

core/secret_filter.py 只管以后的写入。在那之前写进来的 token / 密码还在：日志、影子副本、中枢、
隔离区里留着的原文、engram、信念图谱、各种 jsonl、全文 / 倒排索引、向量，以及 SQLite 删行之后的空闲页里。

scan()  只读：每个存储里有几处、什么类别（**从不输出原文**）
scrub(apply=True)：
  - nyx 认识格式的，原地换成占位符：日志格式的文件（sandglass.txt / .backup / archive）逐条记录改、
    **行数不变**（行号是别处的主键）；中枢、隔离区、engram、信念、实体、数据目录里 nyx 自己的文本文件
  - 派生的重建：全文库整个删掉重建（FTS5 删行后旧词元还在段里），倒排索引重建，
    改过正文的向量删掉、按隐去后的正文重新补
  - 改过的 SQLite 库 wal_checkpoint + VACUUM：删掉的原文不留在空闲页里
  - 最后按**原始字节**把整个数据目录再扫一遍，证明找不到了；nyx 不认识格式的文件（旧备份里的 .db 等）只报路径
不做备份：备份会把密钥再复制一份。改的只是密钥那几个字符，别的一个字节不动。

Hermes 自己的会话记录（~/.hermes 下）不归 nyx 管，这里不碰。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections import Counter
from typing import Dict, List, Optional

from nexsandglass.core import memid, secret_filter

logger = logging.getLogger(__name__)

_SKIP_DIRS = {"scripts"}                   # 用户自己放的脚本：不替他改
_DERIVED = {"sandglass.db", "sandglass.idx"}   # 从日志重建，不逐字改


def _is_journal(name: str) -> bool:
    return name.startswith("sandglass") and name.endswith((".txt", ".backup"))


def _count(findings) -> Counter:
    return Counter(f.label for f in findings)


# ── JSON：按字段改 ────────────────────────────────────────────
# 不能把 JSON 当纯文本改：按默认 ensure_ascii 写的 JSON 里「密码」是 \u5bc6\u7801，中文规则认不出来；
# 隔离区的 payload 里还嵌着整行 engram JSON（JSON 套 JSON）

def _scrub_obj(obj, mode, found):
    if isinstance(obj, str):
        t = obj.strip()
        if t[:1] in "{[" and t[-1:] in "}]":
            try:
                inner = json.loads(t)
            except ValueError:
                inner = None
            if inner is not None:
                n = len(found)
                new = _scrub_obj(inner, mode, found)
                return json.dumps(new, ensure_ascii=False) if len(found) > n else obj
        res = secret_filter.scrub(obj, mode)
        found.extend(res.findings)
        return res.text
    if isinstance(obj, list):
        return [_scrub_obj(x, mode, found) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub_obj(v, mode, found) for k, v in obj.items()}
    return obj


def _scrub_json_text(text: str, mode: str):
    """text 是 JSON 就按字段隐去；不是就返回 None。返回 (新文本, findings)。"""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, (dict, list)):
        return None
    found = []
    new = _scrub_obj(obj, mode, found)
    return (json.dumps(new, ensure_ascii=False) if found else text), found


# ── 日志格式的文件：逐条记录改，行数不变 ─────────────────────────

def _journal_pass(path: str, mode: str, apply: bool, cap: list = None, vault=None) -> dict:
    out = {"found": 0, "kinds": Counter(), "seqs": []}
    recs = memid.parse_journal_records(path)
    changes = {}
    for r in recs:
        if memid.is_redacted(r):
            continue
        res = secret_filter.scrub(r["text"], mode, keep_lines=True, capture=cap, vault=vault)
        if res.changed:
            out["found"] += len(res.findings)
            out["kinds"].update(_count(res.findings))
            out["seqs"].append(r["seq"])
            changes[r["line_start"]] = (r["line_end"], res.text.split("\n"))
    if not apply or not changes:
        return out
    from nexsandglass.core.fsutil import atomic_write
    import re
    with open(path, "rb") as f:
        lines = f.read().splitlines(keepends=True)      # 与文本模式的行号一致：\n、\r\n、\r 都算换行
    for ls, (le, new) in changes.items():
        if len(new) != le - ls + 1:
            raise RuntimeError(f"{path} 第 {ls} 行：隐去后行数变了，放弃改写")
        for i, n in enumerate(range(ls, le + 1)):
            raw = lines[n - 1]
            body = raw.rstrip(b"\r\n")
            eol = raw[len(body):]
            if i == 0:
                m = re.match(rb"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| [^|]*? \| )", body)
                if not m:
                    raise RuntimeError(f"{path} 第 {ls} 行不是记录开头，放弃改写")
                lines[n - 1] = m.group(1) + new[0].encode("utf-8") + eol
            else:
                lines[n - 1] = new[i].encode("utf-8") + eol
    with atomic_write(path, mode="wb") as f:
        f.write(b"".join(lines))
    memid._line_count_cache.pop(path, None)
    return out


# ── 逐行的文本文件（jsonl / txt / md）───────────────────────────

def _lines_pass(path: str, mode: str, apply: bool) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None                              # 不是文本：交给按字节扫描
    out = {"found": 0, "kinds": Counter()}
    lines = text.splitlines(keepends=True)
    changed = False
    whole = _scrub_json_text(text, mode) if path.endswith(".json") else None
    if whole is not None:                         # 整个文件是一份 JSON（可能跨行）
        new, found = whole
        if found:
            out["found"], changed = len(found), True
            out["kinds"].update(_count(found))
            lines = [new + ("\n" if text.endswith("\n") else "")]
        lines_to_check = []
    else:
        lines_to_check = list(enumerate(lines))
    for i, line in lines_to_check:
        body = line.rstrip("\r\n")
        js = _scrub_json_text(body, mode) if body[:1] in "{[" else None
        new, found = js if js is not None else (lambda r: (r.text, r.findings))(secret_filter.scrub(body, mode))
        if found:
            out["found"] += len(found)
            out["kinds"].update(_count(found))
            lines[i] = new + line[len(body):]
            changed = True
    if apply and changed:
        from nexsandglass.core.fsutil import atomic_write
        with atomic_write(path, mode="wb") as f:
            f.write("".join(lines).encode("utf-8"))
    return out


# ── SQLite：nyx 自己的几张表 ───────────────────────────────────

def _db_rows_pass(db: sqlite3.Connection, table: str, key: str, cols: List[str], mode: str, apply: bool,
                  keep_lines: bool = False, as_json: bool = False, cap: list = None, vault=None) -> dict:
    out = {"found": 0, "kinds": Counter(), "keys": []}
    try:
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return out
    cols = [c for c in cols if c in have]
    if not cols:
        return out
    for row in db.execute(f"SELECT {key}, {', '.join(cols)} FROM {table}").fetchall():
        new, hit = {}, False
        for c, v in zip(cols, row[1:]):
            if not isinstance(v, str) or not v:
                continue
            js = _scrub_json_text(v, mode) if as_json else None
            if js is not None:
                text, findings = js
            else:
                res = secret_filter.scrub(v, mode, keep_lines=keep_lines, capture=cap, vault=vault)
                text, findings = res.text, res.findings
            if findings:
                hit = True
                new[c] = text
                out["found"] += len(findings)
                out["kinds"].update(_count(findings))
        if hit:
            out["keys"].append(row[0])
            if apply:
                sets = ", ".join(f"{c}=?" for c in new)
                extra = ", content_hash=?" if table == "memories" and "text" in new else ""
                vals = list(new.values()) + ([memid.content_hash(new["text"])] if extra else [])
                db.execute(f"UPDATE {table} SET {sets}{extra} WHERE {key}=?", vals + [row[0]])
    return out


# ── 同一个字符串在别处：实体名、信念、标签里的「裸」密码 ─────────────
# 规则靠上下文认密码（「密码是 …」）。实体抽取会把引号里的东西单独存成实体名、倒排把它拆成单词 ——
# 到了那里只剩一个裸字符串，规则认不出来。所以从日志 / 中枢里隐去的原文，记在内存里（不写盘、不输出），
# 拿去别的存储里按字面找。太短的、纯数字的不拿来找（「123456」会误伤订单号）

_NEEDLE_MARK = secret_filter.MARK + "密钥]"


def _needles(cap: list) -> List[str]:
    out = set()
    for secret in cap or []:
        for part in str(secret).split("\n"):
            part = part.strip()
            if len(part) >= 8 and not any(c in part for c in "\"\\") and \
                    re.search(r"[A-Za-z]", part) and re.search(r"\d", part) and secret_filter.MARK not in part:
                out.add(part)
    return sorted(out, key=len, reverse=True)       # 长的先换：短的可能是长的一部分


def _replace_needles(text: str, needles: List[str]):
    n = 0
    for nd in needles:
        if nd in text:
            n += text.count(nd)
            text = text.replace(nd, _NEEDLE_MARK)
    return text, n


def _needle_pass(nb: str, needles: List[str], apply: bool, stores: Dict[str, dict]) -> None:
    if not needles:
        return

    def add(name, n):
        if n:
            st = stores.setdefault(name, {"found": 0, "kinds": {}})
            st["found"] += n
            st["kinds"]["同一个密钥（别处）"] = st["kinds"].get("同一个密钥（别处）", 0) + n

    def sweep(db, dbname, table, key, cols):
        try:
            have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            return
        for c in [c for c in cols if c in have]:
            for k, v in db.execute(f"SELECT {key}, {c} FROM {table}").fetchall():
                if isinstance(v, str):
                    new, n = _replace_needles(v, needles)
                    if n:
                        add(f"{dbname}:{table}", n)
                        if apply:
                            db.execute(f"UPDATE {table} SET {c}=? WHERE {key}=?", (new, k))
        if apply:
            db.commit()

    hub_db = os.path.basename(memid._db_path())
    sweep(memid.get_conn(), hub_db, "quarantine", "mem_id", ["text", "payload"])
    try:
        from nexsandglass.features import shadow_sand as sh
        with sh._db_lock:
            db = sh._get_conn()
            shadow_db = os.path.basename(sh._SHADOW_DB)
            sweep(db, shadow_db, "wthread_triples", "rowid", ["subject", "object"])
            sweep(db, shadow_db, "fact_tags", "rowid", ["tags"])
            try:                                  # 实体名就是那个裸字符串：整个实体都是它带进来的
                for rowid, name in db.execute("SELECT rowid, name FROM entities").fetchall():
                    if isinstance(name, str) and any(nd in name for nd in needles):
                        add(f"{shadow_db}:entities", 1)
                        if apply:
                            db.execute("DELETE FROM entities WHERE rowid=?", (rowid,))
                if apply:
                    db.commit()
            except sqlite3.Error:
                pass
    except Exception as e:
        logger.warning("[secret_scrub] 影子沙按字面查找失败: %s", e)
    for root, name in _walk(nb):
        p = os.path.join(root, name)
        if _is_journal(name) or name.endswith((".db", "-wal", "-shm", "-journal", ".lock")) or \
                (os.path.abspath(root) == os.path.abspath(nb) and name in _DERIVED):
            continue
        try:
            text = open(p, "rb").read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new, n = _replace_needles(text, needles)
        if n:
            add(os.path.relpath(p, nb), n)
            if apply:
                from nexsandglass.core.fsutil import atomic_write
                with atomic_write(p, mode="wb") as f:
                    f.write(new.encode("utf-8"))


def _vacuum(path: str) -> Optional[str]:
    from nexsandglass.core import sqlite_open
    return sqlite_open.vacuum(path)


def _bytes_scan(path: str, mode: str, needles: List[str] = ()) -> int:
    """按原始字节找：库文件的空闲页、WAL、看不懂格式的文件。needles：清理时隐去的原文（不分大小写）。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return 0
    text = data.decode("utf-8", errors="ignore")
    # JSON 转义过的中文（\u5bc6\u7801 = 密码）也要认得出来
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    low = text.lower()
    literal = sum(low.count(nd.lower()) for nd in needles)
    by_rule = sum(len(secret_filter.scrub(chunk, mode).findings) for chunk in text.split("\x00") if len(chunk) >= 8)
    return max(literal, by_rule)          # 同一处常常两种方法都认得出：不重复计数


def _walk(nb: str):
    for root, dirs, files in os.walk(nb):
        rel = os.path.relpath(root, nb)
        if rel != "." and rel.split(os.sep)[0] in _SKIP_DIRS:
            dirs[:] = []
            continue
        for name in sorted(files):
            yield root, name


def _to_vault(jp: str, mode: str, apply: bool) -> int:
    """--to-vault（v7.15.0）：主人在日志里说过的密钥先收进保险箱（只收，不改写；改写由下面的各步按编号统一做）。

    先单独收一遍、之后每一步都只查不收：日志和中枢必须拿到同一个编号，否则两边正文对不上（hub_vs_journal 会红）。
    返回收进 / 将收进的条数。
    """
    from nexsandglass.core import secret_vault
    n = 0
    for r in memid.parse_journal_records(jp):
        if memid.is_redacted(r) or r.get("sender") != "user":
            continue
        if not apply:
            n += sum(1 for f in secret_filter.scrub(r["text"], mode).findings if secret_vault.wants(f.kind, f.label))
            continue
        col = secret_vault.Collector()
        col.finish(secret_filter.scrub(r["text"], mode, vault=col).text)
        n += len(col.ids)
    return n


def run(apply: bool = False, mode: str = "on", journal_path: str = None, to_vault: bool = False) -> dict:
    """见模块文档。mode：on（token + 密码 + 私密标记）/ tokens（只动 token）。

    to_vault：主人自己说过的那些先收进保险箱、占位符带编号（要先开 NYX_VAULT 且后端可用）。
    """
    jp = journal_path or memid._journal_path()
    nb = os.path.dirname(os.path.abspath(jp))
    stores: Dict[str, dict] = {}
    report = {"applied": apply, "mode": mode, "data_dir": nb, "stores": stores, "report_only": [], "errors": [],
              "derived_copies_removed": []}
    vault = None
    if to_vault:
        from nexsandglass.core import secret_vault
        why = secret_vault.unavailable_reason()
        if why:
            raise RuntimeError(f"保险箱用不了：{why}")
        vault = secret_vault.Collector(only_lookup=True)

    def add(name, r):
        if r and r.get("found"):
            stores[name] = {"found": r["found"], "kinds": dict(r["kinds"])}
            if r.get("seqs"):
                stores[name]["seqs"] = r["seqs"]

    cap: list = []                                # 隐去的原文：只在内存里，拿去别处按字面找
    with memid.journal_lock(jp):
        if to_vault:
            report["to_vault"] = _to_vault(jp, mode, apply)
        # 1. 日志格式的文件
        for root, name in _walk(nb):
            p = os.path.join(root, name)
            if _is_journal(name) and not name.endswith((".lock", ".tmp")):
                add(os.path.relpath(p, nb), _journal_pass(p, mode, apply, cap, vault=vault))
        # 2. 中枢 + 隔离区（nyx.db）
        conn = memid.get_conn()
        hub = _db_rows_pass(conn, "memories", "seq", ["text"], mode, apply, keep_lines=True, cap=cap, vault=vault)
        hub_db = os.path.basename(memid._db_path())
        add(f"{hub_db}:memories", hub)
        add(f"{hub_db}:quarantine", _db_rows_pass(conn, "quarantine", "mem_id", ["text"], mode, apply, keep_lines=True))
        add(f"{hub_db}:quarantine.payload", _db_rows_pass(conn, "quarantine", "mem_id", ["payload"], mode, apply,
                                                               as_json=True))
        if apply:
            conn.commit()
        changed_ids = []
        if hub["keys"]:
            q = ",".join("?" * len(hub["keys"]))
            changed_ids = [r[0] for r in conn.execute(f"SELECT mem_id FROM memories WHERE seq IN ({q})", hub["keys"])]
        # 3. 影子沙：信念、实体、标签
        try:
            from nexsandglass.features import shadow_sand as sh
            shadow_db = os.path.basename(sh._SHADOW_DB)
            with sh._db_lock:
                db = sh._get_conn()
                add(f"{shadow_db}:wthread_triples",
                    _db_rows_pass(db, "wthread_triples", "rowid", ["subject", "object", "source"], mode, apply))
                add(f"{shadow_db}:entities", _db_rows_pass(db, "entities", "rowid", ["name"], mode, apply))
                add(f"{shadow_db}:fact_tags", _db_rows_pass(db, "fact_tags", "rowid", ["tags"], mode, apply))
                if apply:
                    db.commit()
        except Exception as e:
            report["errors"].append(f"shadow_sand: {e}")
        # 4. nyx 自己的其它文本文件（engram、l0 缓冲、jsonl、persona……）
        for root, name in _walk(nb):
            p = os.path.join(root, name)
            top = os.path.abspath(root) == os.path.abspath(nb)
            if not top and name.split("-")[0] in _DERIVED:
                # 备份里的全文库 / 倒排索引副本：存的是一个个词，没加上下文的密码认不出来；
                # 它们都能从同一份备份里的日志重建，删掉不丢任何东西
                if apply:
                    os.unlink(p)
                    report["derived_copies_removed"].append(os.path.relpath(p, nb))
                continue
            if _is_journal(name) or (top and name in _DERIVED) or \
                    name.endswith((".db", "-wal", "-shm", "-journal", ".lock")):
                continue                          # 顶层的全文库 / 倒排索引从日志重建
            r = _lines_pass(p, mode, apply)
            if r is None:
                if _bytes_scan(p, mode):
                    report["report_only"].append(os.path.relpath(p, nb))
                continue
            add(os.path.relpath(p, nb), r)
        # 5. 同一个密钥在别处（实体名、信念、标签、文本文件里的裸字符串）
        needles = _needles(cap)
        _needle_pass(nb, needles, apply, stores)

    if apply:
        # 6. 派生的重建
        from nexsandglass.core import sandglass_sqlite, semantic
        from nexsandglass.features import sandglass_vault
        fts = getattr(sandglass_sqlite, "_DB", os.path.join(nb, "sandglass.db"))
        with sandglass_sqlite._lock:
            sandglass_sqlite._reset_db()        # 复用的连接（v7.15.4）先关掉，再删文件
            for suf in ("", "-wal", "-shm"):
                try:
                    os.unlink(fts + suf)
                except OSError:
                    pass
        report["fts_rebuilt"] = sandglass_sqlite.sync_all()
        sandglass_vault.rebuild_index()
        report["vectors_dropped"] = semantic.delete(changed_ids) if changed_ids else 0
        # 7. 空闲页
        from nexsandglass.features import shadow_sand as sh
        for p in (memid._db_path(), sh._SHADOW_DB, semantic._db_path()):
            if os.path.exists(p):
                err = _vacuum(p)
                if err:
                    report["errors"].append(f"VACUUM {os.path.basename(p)}: {err}（停掉 Hermes / MCP 服务再跑一次）")
        try:
            semantic.schedule_index()           # 改过正文的记忆按隐去后的正文重新补向量
        except Exception:
            pass

    # 8. 按原始字节复查整个数据目录：改完之后应该一处都没有（不改时这就是「磁盘上有几处」）
    residual = {}
    for root, name in _walk(nb):
        p = os.path.join(root, name)
        n = _bytes_scan(p, mode, needles)
        if n:
            residual[os.path.relpath(p, nb)] = n
    report["on_disk"] = residual
    report["found"] = sum(s["found"] for s in stores.values())
    report["clean"] = not residual
    return report


def journal_check(journal_path: str = None) -> dict:
    """给 doctor 用：日志里还有几处没隐去的密钥（按当前档位）。只读、只给类别和 seq。"""
    mode = secret_filter.level()
    if mode == "off":
        return {"ok": True, "disabled": True}
    r = _journal_pass(journal_path or memid._journal_path(), mode, apply=False)
    return {"ok": r["found"] == 0, "found": r["found"], "kinds": dict(r["kinds"]),
            "records": len(r["seqs"]), "first_seqs": r["seqs"][:10]}
