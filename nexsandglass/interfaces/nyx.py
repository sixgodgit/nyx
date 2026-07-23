"""Nyx — 夜之感知层
   知晓黑暗中有何物，纵使目不可及。

   Veil 分辨是否接触过（Bloom Filter，128KB）。
   Mist 铭刻鬼影——何时、何地、几次（SQLite）。

   invoke:
     nyx_kindle(ln, ts, text)  落沙时点亮
     nyx_sense(text)           感知熟悉度
     nyx_hunt(query)           追猎幽灵
     nyx_rest()                安息持久化
     nyx_gaze()                凝视疆域
     nyx_forget(token)         遗忘单条鬼影
     nyx_cleanup(days=90)      清理长期未现的幽灵
     nyx_reindex()             从 Mist 重建 Veil

   (希腊夜神，与 Hypnos 同源。"""

import hashlib
import logging
import os
import re
import sqlite3
from datetime import datetime

from sandglass_paths import _NB

# ── 夜之疆域 ────────────────────────────────────────
_VEIL_PATH = os.path.join(_NB, "nyx_veil.bin")        # Bloom Filter 位图
_MIST_PATH = os.path.join(_NB, "nyx_mist.db")         # Phantom SQLite

# ── Veil 参数 ───────────────────────────────────────
_VEIL_BITS = 1 << 20                                   # 1M bits = 128KB
_VEIL_HASHES = 7

# ── 嗅探模式 ────────────────────────────────────────
_ENTITY_PATTERN = re.compile(
    r'([A-Z][a-z]+(?:[-\s][A-Z][a-z]+)+)|'
    r'"([^"]+)"|'
    r"'([^']+)'|"
    r'[\u4e00-\u9fff]{2,4}'
)
_TOKEN_PATTERN = re.compile(r'[a-zA-Z][\w.-]{2,}')

logger = logging.getLogger(__name__)


# ═══════════════════ 第一层：Veil ═══════════════════

class _Veil:
    """Bloom Filter —— 分辨「见过/没见过」的薄纱。"""

    def __init__(self):
        self._bits = bytearray((_VEIL_BITS + 7) // 8)
        self._count = 0
        self._size = _VEIL_BITS
        self._hashes = _VEIL_HASHES

    @staticmethod
    def _fold(item: str, seed: int) -> int:
        h = hashlib.sha256(f"{seed}:{item}".encode()).digest()
        return int.from_bytes(h[:4], 'big')

    def touch(self, item: str):
        """在薄纱上留下印记。"""
        for s in range(self._hashes):
            bit = self._fold(item, s) % self._size
            self._bits[bit >> 3] |= (1 << (bit & 7))
        self._count += 1

    def probe(self, item: str) -> bool:
        """探知是否曾接触过此物。"""
        for s in range(self._hashes):
            bit = self._fold(item, s) % self._size
            if not (self._bits[bit >> 3] & (1 << (bit & 7))):
                return False
        return True

    def rest(self, path: str):
        """安息——持久化到位图文件。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(self._size.to_bytes(4, 'big'))
            f.write(bytes([self._hashes]))
            f.write(self._count.to_bytes(4, 'big'))
            f.write(self._bits)

    def stir(self, path: str) -> bool:
        """唤醒——从位图文件恢复。"""
        if not os.path.exists(path):
            return False
        with open(path, 'rb') as f:
            stored = int.from_bytes(f.read(4), 'big')
            if stored != self._size:
                logger.warning(f"nyx: veil size mismatch ({stored} vs {self._size}), fresh start")
                return False
            self._hashes = f.read(1)[0]
            self._count = int.from_bytes(f.read(4), 'big')
            self._bits = bytearray(f.read())
        return True

    @property
    def density(self) -> int:
        return self._count


# ═══════════════════ 第二层：Mist ═══════════════════

class _Mist:
    """Shades 之池——实体出现痕迹的 SQLite 存储。"""

    def __init__(self):
        self._con = None

    def _drift(self):
        """漂入迷雾。"""
        if self._con is not None:
            return self._con
        self._con = sqlite3.connect(_MIST_PATH, timeout=10)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA busy_timeout=5000")
        self._con.execute("""CREATE TABLE IF NOT EXISTS phantoms (
            token         TEXT PRIMARY KEY,
            born          TEXT NOT NULL,
            last_spotted  TEXT NOT NULL,
            sightings     INTEGER DEFAULT 1,
            traces        TEXT DEFAULT '',
            whisper       TEXT DEFAULT '',
            updated_at    TEXT DEFAULT (datetime('now'))
        )""")
        self._con.commit()
        return self._con

    def haunt(self, token: str, moment: str, trace: int, snippet: str):
        """铭刻一道鬼影。"""
        db = self._drift()
        now = moment or datetime.now().isoformat()
        row = db.execute(
            "SELECT sightings, traces, whisper FROM phantoms WHERE token=?", (token,)
        ).fetchone()
        if row:
            count = row[0] + 1
            old = (row[1] or "").split(",")
            lines = set(old) | {str(trace)}
            new_traces = ",".join(sorted(lines, key=int)[-50:])
            prev_whisper = row[2] or snippet
            db.execute(
                """UPDATE phantoms SET last_spotted=?, sightings=?, traces=?,
                   whisper=?, updated_at=datetime('now') WHERE token=? """,
                (now, count, new_traces, prev_whisper, token)
            )
        else:
            db.execute(
                """INSERT INTO phantoms (token, born, last_spotted, sightings, traces, whisper)
                   VALUES (?, ?, ?, 1, ?, ?) """,
                (token, now, now, str(trace), snippet)
            )
        db.commit()

    def stalk(self, fragment: str, limit: int = 5) -> list:
        """追踪——按片段检索鬼影。"""
        db = self._drift()
        rows = db.execute(
            """SELECT token, born, last_spotted, sightings, whisper
               FROM phantoms WHERE token LIKE ?
               ORDER BY sightings DESC LIMIT ? """,
            (f"%{fragment}%", limit)
        ).fetchall()
        return [
            {"token": r[0], "born": r[1], "last_spotted": r[2],
             "sightings": r[3], "whisper": (r[4] or "")[:80]}
            for r in rows
        ]

    def census(self) -> dict:
        """凝视迷雾——统计。"""
        db = self._drift()
        total = db.execute("SELECT COUNT(*) FROM phantoms").fetchone()[0]
        top = db.execute(
            "SELECT token, sightings, last_spotted FROM phantoms ORDER BY sightings DESC LIMIT 10"
        ).fetchall()
        return {
            "total": total,
            "restless": [{"token": r[0], "sightings": r[1], "last_spotted": r[2]} for r in top]
        }


# ═══════════════════ 第三层：Nyx 公共接口 ═══════════════════

_veil = None
_mist = None


def _awaken():
    global _veil, _mist
    if _veil is None:
        _veil = _Veil()
        if not _veil.stir(_VEIL_PATH):
            logger.info("nyx: fresh veil")
    if _mist is None:
        _mist = _Mist()
        _mist._drift()


def _scent(text: str) -> list:
    """从文本嗅出实体——命名实体 + token。"""
    tokens = set()
    for m in _ENTITY_PATTERN.finditer(text):
        t = (m.group(0) or "").strip()
        if len(t) >= 2:
            tokens.add(t.lower())
    for m in _TOKEN_PATTERN.finditer(text):
        t = m.group(0).lower()
        if 3 <= len(t) <= 40:
            tokens.add(t)
    return list(tokens)


# ── 公共 API ──────────────────────────────────────────

def nyx_kindle(line_num: int, moment: str, text: str):
    """在夜中点亮一簇记忆火花。
       落沙时调用：Veil 留印 + 铭刻 Shades。"""
    _awaken()
    phantoms = _scent(text)
    if not phantoms:
        return
    for p in phantoms:
        _veil.touch(p)
    try:
        snippet = text[:80]
        for p in phantoms:
            _mist.haunt(p, moment, line_num, snippet)
    except Exception:
        logger.warning("nyx: haunt failed", exc_info=True)
    if _veil.density > 0 and _veil.density % 100 == 0:
        try:
            _veil.rest(_VEIL_PATH)
        except Exception:
            pass


def nyx_sense(text: str) -> dict:
    """感知——这片黑暗中有熟悉的气息吗？"""
    _awaken()
    phantoms = _scent(text)
    if not phantoms:
        return {"familiar_ratio": 0.0, "unknown_tokens": [], "known_tokens": [], "total": 0}
    known = [p for p in phantoms if _veil.probe(p)]
    unknown = [p for p in phantoms if not _veil.probe(p)]
    return {
        "familiar_ratio": round(len(known) / len(phantoms), 2),
        "known_tokens": known[:10],
        "unknown_tokens": unknown[:10],
        "total": len(phantoms)
    }


def nyx_hunt(query: str, limit: int = 5) -> dict:
    """追猎幽灵——深入夜中搜寻失落记忆的影子。"""
    sense = nyx_sense(query)
    if sense["familiar_ratio"] == 0.0:
        return {"hunted": False, "conviction": 0.0, "phantoms": []}
    try:
        _awaken()
        # 拆解查询为独立实体，逐个跟踪
        hunts = _scent(query)
        if not hunts:
            return {"hunted": False, "conviction": 0.0, "phantoms": []}
        keys = sense.get("known_tokens", [])
        candidates = hunts if not keys else [t for t in hunts if t in keys]
        if not candidates:
            candidates = hunts[:3]
        gathered = {}
        for token in candidates:
            for p in _mist.stalk(token, limit * 2):
                gathered[p["token"]] = p
        phantoms = sorted(gathered.values(),
                          key=lambda x: x.get("sightings", 0), reverse=True)[:limit]
        if not phantoms:
            # 熟悉但 stalk 不到精确匹配：按整句 LIKE 搜一次作为降级
            fallback = _mist.stalk(query, limit)
            if fallback:
                phantoms = fallback
            else:
                return {"hunted": True, "conviction": round(sense["familiar_ratio"] * 0.3, 2),
                        "phantoms": []}
        top = max(p["sightings"] for p in phantoms)
        conviction = round(
            sense["familiar_ratio"] * 0.6 + min(1.0, top / 20) * 0.4, 2
        )
        return {"hunted": True, "conviction": conviction, "phantoms": phantoms[:limit]}
    except Exception:
        logger.warning("nyx: hunt failed", exc_info=True)
        return {"hunted": sense["familiar_ratio"] > 0.5,
                "conviction": round(sense["familiar_ratio"] * 0.3, 2),
                "phantoms": []}


def nyx_rest():
    """安息——持久化 Veil。"""
    _awaken()
    try:
        _veil.rest(_VEIL_PATH)
    except Exception:
        pass


def nyx_gaze() -> dict:
    """凝视——窥探 Nyx 的疆域全貌。"""
    _awaken()
    try:
        c = _mist.census()
        return {
            "veil_density": _veil.density,
            "veil_size_kb": round(len(_veil._bits) / 1024, 1),
            "total_phantoms": c["total"],
            "restless": c["restless"]
        }
    except Exception:
        return {"veil_density": _veil.density, "total_phantoms": 0}


def nyx_forget(token: str) -> dict:
    """遗忘——从迷雾中抹去一道鬼影。

    将一个实体及其所有痕迹从 Mist 中删除。
    Veil Bloom Filter 不受影响（单向不可逆）。

    参数:
        token: 要遗忘的实体 token（大小写不敏感，内部已存储为小写）

    返回:
        {"forgotten": bool, "token": str, "removed": {...} | "reason": str}
    """
    _awaken()
    try:
        db = _mist._drift()
        token_lower = token.lower().strip()
        row = db.execute(
            "SELECT token, born, last_spotted, sightings FROM phantoms WHERE token=?",
            (token_lower,)
        ).fetchone()
        if row is None:
            return {"forgotten": False, "token": token, "reason": "not_found"}
        removed = {
            "token": row[0],
            "born": row[1],
            "last_spotted": row[2],
            "sightings": row[3]
        }
        db.execute("DELETE FROM phantoms WHERE token=?", (token_lower,))
        db.commit()
        logger.info(f"nyx: forgot phantom '{token_lower}' ({removed['sightings']} sightings)")
        return {"forgotten": True, "token": token_lower, "removed": removed}
    except Exception as e:
        logger.warning(f"nyx: forget failed for '{token}'", exc_info=True)
        return {"forgotten": False, "token": token, "reason": str(e)}


def nyx_cleanup(days: int = 90) -> dict:
    """清理——放逐长期未现的幽灵。

    删除 last_spotted 距今超过 days 天的 Phantom。
    这些幽灵「太久未被召唤」，从迷雾中消散。

    参数:
        days: 未出现天数阈值（默认 90）

    返回:
        {"purged": int, "threshold_days": int, "detail": [...]}
    """
    _awaken()
    try:
        db = _mist._drift()
        rows = db.execute(
            """SELECT token, last_spotted, sightings FROM phantoms
               WHERE julianday('now') - julianday(substr(last_spotted,1,10)) > ?""",
            (days,)
        ).fetchall()
        if not rows:
            return {"purged": 0, "threshold_days": days, "detail": []}
        detail = [{"token": r[0], "last_spotted": r[1], "sightings": r[2]} for r in rows]
        tokens = [r[0] for r in rows]
        placeholders = ",".join("?" * len(tokens))
        db.execute(f"DELETE FROM phantoms WHERE token IN ({placeholders})", tokens)
        db.commit()
        logger.info(f"nyx: cleanup purged {len(tokens)} phantoms (> {days}d dormant)")
        return {"purged": len(tokens), "threshold_days": days, "detail": detail}
    except Exception as e:
        logger.warning("nyx: cleanup failed", exc_info=True)
        return {"purged": 0, "threshold_days": days, "reason": str(e)}


def nyx_reindex():
    """重索引——从 Mist 重建 Veil，保证两者一致。

    遍历 Mist 中所有已知 Phantom token，重新『触摸』Veil，
    确保 Bloom Filter 与 SQLite 记录同步。
    用于 Veil 位图损坏、迁移后或数据恢复。

    Veil 是单向不可逆结构，无法反向重建 Mist，
    但可以保证所有 Mist token 在 Veil 中都有印记。
    """
    _awaken()
    try:
        db = _mist._drift()
        veil_before = _veil.density
        mist_count = db.execute("SELECT COUNT(*) FROM phantoms").fetchone()[0]
        # Rebuild Veil from Mist: clear and re-touch
        _veil._bits = bytearray((_VEIL_BITS + 7) // 8)
        _veil._count = 0
        rows = db.execute("SELECT token FROM phantoms").fetchall()
        for (token,) in rows:
            _veil.touch(token)
        _veil.rest(_VEIL_PATH)
        logger.info(
            f"nyx: reindex — veil {veil_before}→{_veil.density} touches "
            f"from {mist_count} mist phantoms"
        )
        return {
            "reindexed": True,
            "veil_before": veil_before,
            "mist_phantoms": mist_count,
            "veil_after": _veil.density
        }
    except Exception as e:
        logger.warning("nyx: reindex failed", exc_info=True)
        return {"reindexed": False, "reason": str(e)}
