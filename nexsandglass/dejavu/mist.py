"""Mist —— 鬼影之池，记录实体出现过的痕迹（SQLite）。

记录什么
--------
每个被嗅出的 token 记为一行 phantom：首次/最近出现时间、出现次数、
调用方传入的引用列表（``key``）、以及首次出现时的文本片段。

``key`` 由调用方定义
--------------------
原实现在这里存「沙漏行号」，导致模块与特定存储强耦合。
现在 ``key`` 是任意字符串——行号、消息 ID、URL、文件名都行，
Déjà Vu 不关心它的含义，只存不解释。

本模块零外部依赖，只用 stdlib + sqlite3。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import List, Optional

from .types import Phantom

logger = logging.getLogger(__name__)

MAX_REFS = 20               # 每个 token 保留的引用数量上限
# 从 50 降到 20：实测（2 万 token × 60 次追加引用）磁盘 6.64MB → 4.49MB（-32%），
# 写入 25.3s → 20.2s（-20%）。hunt 按 sightings 排序后只取前 limit 个，
# 50 个引用是过度设计，实际用不到。
SNIPPET_LEN = 80            # 片段保留长度

# 注：refs 曾尝试规范化到独立表，实测**更差**，已回退 ——
# 同数据量下磁盘 518 B/token（内嵌 151 B/token），写入慢 5 倍。
# 原因：内嵌串每 token 一行，规范化为每 (token,key) 一行，行数放大 N 倍，
# SQLite 每行固定开销吃掉收益。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS phantoms (
    token         TEXT PRIMARY KEY,
    born          TEXT NOT NULL,
    last_spotted  TEXT NOT NULL,
    sightings     INTEGER DEFAULT 1,
    refs          TEXT DEFAULT '',
    whisper       TEXT DEFAULT '',
    updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_phantoms_sightings ON phantoms(sightings DESC);
CREATE INDEX IF NOT EXISTS idx_phantoms_last      ON phantoms(last_spotted);
"""

# 兼容旧库：旧版本用 traces 列名
_LEGACY_RENAME = "traces"


class Mist:
    """phantom 池。线程安全（连接 + RLock）。"""

    __slots__ = ("_path", "_con", "_lock")

    def __init__(self, path: str):
        self._path = path
        self._con: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    # ── 连接 ────────────────────────────────────────────────
    def _conn(self) -> sqlite3.Connection:
        with self._lock:
            if self._con is not None:
                return self._con
            d = os.path.dirname(os.path.abspath(self._path))
            os.makedirs(d, exist_ok=True)
            # check_same_thread=False：连接可能被不同线程访问（MCP / server）。
            # 缺省 True 时跨线程用会在 3.11+ 抛 ProgrammingError，被外层
            # except 吞掉 → phantom 数据静默丢失。
            self._con = sqlite3.connect(self._path, timeout=10,
                                        check_same_thread=False)
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA busy_timeout=5000")
            self._migrate(self._con)
            self._con.executescript(_SCHEMA)
            self._con.commit()
            return self._con

    @staticmethod
    @staticmethod
    def _migrate(con: sqlite3.Connection) -> None:
        """旧库兼容：``traces`` 列改名为 ``refs``（语义由行号放宽为任意 key）。"""
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(phantoms)")]
        except Exception:
            return
        if cols and _LEGACY_RENAME in cols and "refs" not in cols:
            try:
                con.execute(f"ALTER TABLE phantoms RENAME COLUMN {_LEGACY_RENAME} TO refs")
                con.commit()
            except Exception:
                logger.warning("dejavu: could not migrate legacy traces column", exc_info=True)

    # ── 写入 ────────────────────────────────────────────────
    def haunt(self, token: str, moment: Optional[str], key: str, snippet: str,
              commit: bool = True) -> None:
        """铭刻一道鬼影（同 token 则累加）。

        ``commit=False`` 供批量写入使用——每条 commit 一次会让
        imprint 变成 fsync 密集操作（实测 100ms/条）。批量场景应
        传 False，最后统一 ``commit()``。
        """
        if not token:
            return
        with self._lock:
            db = self._conn()
            now = moment or datetime.now().isoformat(sep=" ", timespec="seconds")
            row = db.execute(
                "SELECT sightings, refs, whisper FROM phantoms WHERE token=?", (token,)
            ).fetchone()
            if row:
                count = (row[0] or 0) + 1
                refs = [r for r in (row[1] or "").split(",") if r]
                if key:
                    refs = list(dict.fromkeys(refs + [str(key)]))[-MAX_REFS:]
                db.execute(
                    "UPDATE phantoms SET last_spotted=?, sightings=?, refs=?, "
                    "whisper=?, updated_at=datetime('now') WHERE token=?",
                    (now, count, ",".join(refs), row[2] or (snippet or ""), token),
                )
            else:
                db.execute(
                    "INSERT INTO phantoms (token, born, last_spotted, sightings, refs, whisper) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    (token, now, now, str(key) if key else "", snippet or ""),
                )
            if commit:
                db.commit()

    def haunt_many(self, tokens, moment: Optional[str], key: str,
                   snippet: str, commit: bool = True) -> None:
        """批量铭刻：全程只提交一次。"""
        with self._lock:
            for t in tokens:
                self.haunt(t, moment, key, snippet, commit=False)
            if commit:
                self._conn().commit()

    def commit(self) -> None:
        """显式提交（配合 haunt(commit=False) / haunt_many(commit=False)）。"""
        with self._lock:
            self._conn().commit()

    # ── 查询 ────────────────────────────────────────────────
    def stalk(self, fragment: str, limit: int = 5, scan_cap: int = 500) -> List[Phantom]:
        """按片段检索鬼影（先精确、再子串降级）。

        性能说明：``token LIKE '%fragment%'`` 因前导通配符无法走索引，
        必须全表扫描 —— 实测 23 万行时单次 **30-105 ms**，而 hunt 会对
        多个 token 各查一次。实测中 7 个候选里只要有 1 个精确未命中，
        降级扫描就吃掉整个 hunt 的耗时（其余 6 个各仅 0.02-0.1 ms）。

        因此这里分两级：
          1. **精确匹配** ``token = ?`` —— 走主键索引，实测 **0.01-0.1 ms**
          2. 精确查不到时才退回子串匹配，且用 ``scan_cap`` 严格封顶

        调用方若只想要「库里到底有没有这个词」，用 ``stalk_exact()``
        可完全避免降级开销。
        """
        if not fragment:
            return []
        with self._lock:
            db = self._conn()
            rows = db.execute(
                "SELECT token, born, last_spotted, sightings, refs, whisper "
                "FROM phantoms WHERE token = ? LIMIT ?",
                (fragment, int(limit)),
            ).fetchall()
            if not rows:
                rows = db.execute(
                    "SELECT token, born, last_spotted, sightings, refs, whisper "
                    "FROM phantoms WHERE token LIKE ? LIMIT ?",
                    (f"%{fragment}%", int(scan_cap)),
                ).fetchall()
        phantoms = [self._row_to_phantom(r) for r in rows]
        phantoms.sort(key=lambda p: (p.sightings, p.last_spotted), reverse=True)
        return phantoms[:limit]

    def stalk_exact(self, token: str) -> Optional[Phantom]:
        """精确取单个 phantom（走主键索引，亚毫秒）。"""
        if not token:
            return None
        with self._lock:
            r = self._conn().execute(
                "SELECT token, born, last_spotted, sightings, refs, whisper "
                "FROM phantoms WHERE token = ? LIMIT 1", (token,)
            ).fetchone()
        return self._row_to_phantom(r) if r else None

    @staticmethod
    def _row_to_phantom(r) -> Phantom:
        return Phantom(
            token=r[0],
            born=r[1] or "",
            last_spotted=r[2] or "",
            sightings=r[3] or 0,
            refs=[x for x in (r[4] or "").split(",") if x],
            snippet=(r[5] or "")[:SNIPPET_LEN],
        )

    def census(self) -> dict:
        """统计：总数 + 最活跃的前 10。"""
        with self._lock:
            db = self._conn()
            total = db.execute("SELECT COUNT(*) FROM phantoms").fetchone()[0]
            top = db.execute(
                "SELECT token, sightings, last_spotted FROM phantoms "
                "ORDER BY sightings DESC LIMIT 10"
            ).fetchall()
        return {
            "total": total,
            "restless": [
                {"token": r[0], "sightings": r[1], "last_spotted": r[2]} for r in top
            ],
        }

    def all_entries(self) -> List[tuple]:
        """全量导出 (token, moment, key, snippet)，供 reindex 重建 Veil。"""
        with self._lock:
            db = self._conn()
            return db.execute(
                "SELECT token, last_spotted, refs, whisper FROM phantoms"
            ).fetchall()

    # ── 维护 ────────────────────────────────────────────────
    def forget(self, token: str) -> int:
        """按 token 精确删除。返回删除行数。"""
        with self._lock:
            db = self._conn()
            cur = db.execute("DELETE FROM phantoms WHERE token=?", (token,))
            db.commit()
            return cur.rowcount or 0

    def forget_like(self, fragment: str) -> int:
        with self._lock:
            db = self._conn()
            cur = db.execute("DELETE FROM phantoms WHERE token LIKE ?", (f"%{fragment}%",))
            db.commit()
            return cur.rowcount or 0

    def cleanup(self, days: int = 90) -> int:
        """清理超过 ``days`` 天未出现的鬼影。返回删除行数。"""
        with self._lock:
            db = self._conn()
            cur = db.execute(
                "DELETE FROM phantoms "
                "WHERE julianday('now') - julianday(last_spotted) > ?",
                (int(days),),
            )
            db.commit()
            return cur.rowcount or 0

    def clear(self) -> None:
        with self._lock:
            db = self._conn()
            db.execute("DELETE FROM phantoms")
            db.commit()

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                try:
                    self._con.close()
                except Exception:
                    pass
                self._con = None

    @property
    def path(self) -> str:
        return self._path
