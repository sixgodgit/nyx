"""
NexSandglass SQLite FTS5 加速层
================================
V1.4.4：sandglass.txt不动，SQLite分词FTS5平行加速。
纯 stdlib，零依赖。FTS5挂了自动降级。
"""

import logging
import os
import re
import sqlite3
import threading

logger = logging.getLogger(__name__)

from nexsandglass.core.sandglass_paths import _NB
_DB = os.path.join(_NB, "sandglass.db")
_lock = threading.Lock()
_last_sync_mtime = 0  # 记录上次同步时的 sandglass.txt 修改时间


def _tokenize(text: str) -> str:
    """FTS5专用分词：英文全词 + 中文2-gram。不用滑动窗口（滑动窗口用于mmap OR匹配）。"""
    tokens = set()
    t = text.lower()
    # 英文全词（2+字母的数字词）
    tokens.update(re.findall(r"[a-zA-Z0-9_]{2,}", t))
    # 中文2字词
    chars = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    for i in range(len(chars) - 1):
        tokens.add(chars[i : i + 2])
    return " ".join(sorted(t for t in tokens if t))


def _get_db():
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    conn = sqlite3.connect(_DB)
    conn.execute("PRAGMA journal_mode=WAL")  # 支持多进程并发
    conn.execute("PRAGMA synchronous=NORMAL")  # 性能优化，安全够用
    conn.execute("CREATE TABLE IF NOT EXISTS sandglass "
                 "(id INTEGER PRIMARY KEY, ts TEXT, sender TEXT, text TEXT, line_end INTEGER)")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS sandglass_fts USING fts5(tokens)")
    # 旧版按物理行建表、没有 line_end —— 这是派生缓存，直接重建无损。
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sandglass)")]
    if "line_end" not in cols:
        logger.warning("[sandglass_sqlite] 检测到旧版逐行索引，重建为按逻辑记忆索引")
        conn.execute("DROP TABLE IF EXISTS sandglass")
        conn.execute("DROP TABLE IF EXISTS sandglass_fts")
        conn.execute("CREATE TABLE sandglass "
                     "(id INTEGER PRIMARY KEY, ts TEXT, sender TEXT, text TEXT, line_end INTEGER)")
        conn.execute("CREATE VIRTUAL TABLE sandglass_fts USING fts5(tokens)")
    conn.commit()
    return conn


def sync_all() -> int:
    """全量同步。返回条数，失败返回-1。

    **按逻辑记忆索引，不按物理行。**
    旧实现 `_parse_line(line); if not ts: continue` 会把多行消息的续行整个丢掉 ——
    实测生产日志 59462 行里 50611 行（85%）是续行，从未进过索引：
    关键词只出现在续行里时，FTS5 / 倒排 / TF-IDF 全部 0 命中。
    id 用记录首行号（line_start），文本存**整条记忆**。
    """
    try:
        from nexsandglass.core import memid
        from nexsandglass.features.sandglass_vault import _SANDGLASS
        with _lock:
            conn = _get_db()
            conn.execute("DELETE FROM sandglass")
            conn.execute("DELETE FROM sandglass_fts")
            rows = []; fts = []
            for rec in memid.parse_journal_records(_SANDGLASS):
                if memid.is_redacted(rec):
                    continue                      # 已抹除：不进索引
                rows.append((rec["line_start"], rec["ts"], rec["sender"],
                             rec["text"], rec["line_end"]))
                fts.append((rec["line_start"], _tokenize(rec["text"])))
            conn.executemany("INSERT INTO sandglass VALUES(?,?,?,?,?)", rows)
            conn.executemany("INSERT INTO sandglass_fts(rowid, tokens) VALUES(?,?)", fts)
            conn.commit()
            return len(rows)
    except Exception:
        logger.warning("[sandglass_sqlite] sync_all 失败", exc_info=True)
        return -1


def sync_incremental() -> int:
    """增量同步。文件没变则跳过。返回新增条数。

    从上次索引到的 line_end + 1 继续解析 —— 那是记录边界，
    不会把多行消息从中间切开。
    """
    global _last_sync_mtime
    try:
        from nexsandglass.core import memid
        from nexsandglass.features.sandglass_vault import _SANDGLASS
        with _lock:
            # mtime检查在锁内——防止 TOCTOU 竞态
            if os.path.exists(_SANDGLASS):
                mtime = os.path.getmtime(_SANDGLASS)
                if mtime == _last_sync_mtime and _last_sync_mtime > 0:
                    return 0
                _last_sync_mtime = mtime
            conn = _get_db()
            row = conn.execute(
                "SELECT COALESCE(MAX(line_end), 0), COUNT(*) FROM sandglass").fetchone()
            last_end, indexed = int(row[0]), int(row[1])
            rows = []; fts = []
            for rec in memid.parse_journal_records(
                    _SANDGLASS, from_line=last_end + 1, seq_start=indexed + 1):
                if memid.is_redacted(rec):
                    continue
                rows.append((rec["line_start"], rec["ts"], rec["sender"],
                             rec["text"], rec["line_end"]))
                fts.append((rec["line_start"], _tokenize(rec["text"])))
            if rows:
                conn.executemany("INSERT OR REPLACE INTO sandglass VALUES(?,?,?,?,?)", rows)
                conn.executemany(
                    "INSERT OR REPLACE INTO sandglass_fts(rowid, tokens) VALUES(?,?)", fts)
                conn.commit()
            return len(rows)
    except Exception:
        logger.warning("[sandglass_sqlite] sync_incremental 失败", exc_info=True)
        return 0


def search_in(line_ids: list, query: str, limit: int = 100) -> list:
    """FTS5 在指定行号列表中搜索排序。用于 mmap 初筛后的精排。"""
    try:
        tokens = _tokenize(query)
        if not tokens.strip() or not line_ids:
            return []
        ids_str = ",".join(str(int(i)) for i in line_ids)
        with _lock:
            conn = _get_db()
            sql = f"SELECT s.id, s.ts, s.text FROM sandglass_fts f JOIN sandglass s ON s.id=f.rowid WHERE s.id IN ({ids_str}) AND sandglass_fts MATCH ? ORDER BY rank"
            cur = conn.execute(sql, (tokens,))
            return [(row[0], row[1], row[2]) for row in cur.fetchall()]
    except Exception:
        return []


def search_year(query: str, year: str, limit: int = -1) -> list:
    """FTS5 按年份搜索。year='2026' 只搜该年。"""
    try:
        tokens = _tokenize(query)
        if not tokens.strip():
            return []
        with _lock:
            conn = _get_db()
            sql = "SELECT s.id, s.ts, s.text FROM sandglass_fts f JOIN sandglass s ON s.id=f.rowid WHERE s.ts LIKE ? AND sandglass_fts MATCH ? ORDER BY rank"
            if limit > 0:
                sql += f" LIMIT {limit}"
            cur = conn.execute(sql, (f"{year}%", tokens))
            return [(row[0], row[1], row[2]) for row in cur.fetchall()]
    except Exception:
        return []


def search(query: str, limit: int = 10) -> list:
    """FTS5搜索。limit=-1 全量。返回[(行号,时间,明文),...]。
    中文用AND语义，英文自动切换OR避免n-gram碎片化。"""
    try:
        tokens = _tokenize(query)
        if not tokens.strip():
            return []
        # 英文查询：OR语义（n-gram太多AND匹配不到）
        if any(c.isascii() and c.isalpha() for c in query):
            tokens = " OR ".join(tokens.split())
        with _lock:
            conn = _get_db()
            sql = "SELECT s.id, s.ts, s.text FROM sandglass_fts f JOIN sandglass s ON s.id = f.rowid WHERE sandglass_fts MATCH ? ORDER BY rank"
            if limit > 0:
                sql += f" LIMIT {limit}"
            cur = conn.execute(sql, (tokens,))
            return [(row[0], row[1], row[2]) for row in cur.fetchall()]
    except Exception:
        return []


def count() -> int:
    try:
        with _lock:
            return _get_db().execute("SELECT COUNT(*) FROM sandglass").fetchone()[0]
    except Exception:
        return 0


def get_records(ids: list) -> dict:
    """按 id（记录首行号）批量取回**整条记忆**。返回 {id: (ts, sender, text)}。

    检索各路拿到的是行号，但要展示/排序的是整条记忆的正文。
    直接 open(file).readlines()[ln-1] 只能拿到首行 —— 多行消息的其余部分丢失。
    """
    if not ids:
        return {}
    out = {}
    try:
        with _lock:
            conn = _get_db()
            ids = [int(i) for i in ids]
            for i in range(0, len(ids), 400):
                part = ids[i:i + 400]
                q = ",".join("?" * len(part))
                for r in conn.execute(
                        f"SELECT id, ts, sender, text FROM sandglass WHERE id IN ({q})", part):
                    out[r[0]] = (r[1], r[2], r[3])
    except Exception:
        logger.warning("[sandglass_sqlite] get_records 失败", exc_info=True)
    return out


def all_records() -> list:
    """全部记忆 [(id, ts, text), ...]，按 id 升序。供 TF-IDF 之类需要全量扫描的路径用。"""
    try:
        with _lock:
            conn = _get_db()
            return [(r[0], r[1], r[2]) for r in
                    conn.execute("SELECT id, ts, text FROM sandglass ORDER BY id")]
    except Exception:
        return []
