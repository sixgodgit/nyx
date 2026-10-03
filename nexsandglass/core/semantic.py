"""
core/semantic.py — 语义检索的第四路（v7.13）
============================================

v3.4.0 起仓库里就有 embedding_provider / vector_store / vector_search，README 也把
「向量语义检索」列为能力 —— 但生产路径上**没有任何代码写入向量**：SearchRouter 默认
``vector=None``，存储写死在 ``~/.hermes``，键是 memory_id 而 SearchRouter 期望行号。
结果是同义改写永远召不回来（问 company、原话是 joined Contoso；问「我的猫叫什么」、
原话是「领养了一只橘猫…叫团子」）。这个模块把它接上：

  索引  vectors.db 放在 nyx 数据目录里（与 nyx.db 同处，随 hermes backup 一起备份）。
        键 = mem_id，同时存 line_start（召回侧按行号与其他几路融合）与 model（换模型
        后旧向量不参与比较，等重建）。
  写入  不进 memid.allocate 的文件锁（嵌入要几十毫秒）。由调用方在后台补：Hermes 插件
        的 sync_turn（本来就在串行后台线程上）每轮调 ``index_pending``，initialize 起一个
        后台线程补历史。召回路径**从不嵌入文档**，只嵌入查询。
  召回  ``search(query, k)`` → [(line_start, score)]，SearchRouter 用 RRF 与词法排序融合，
        纯向量命中（零词法重叠）也能进结果 —— 这正是同义改写需要的。
  遗忘  erasure.forget 级联删除向量；restore 之后由下一次 index_pending 重新嵌入；
        verify_erasure 检查「指向已删除记忆的向量」必须为 0。

后端（``NYX_EMBED``，默认 auto）
  auto   配了 EMBEDDING_API_URL 用 API；装了 sentence-transformers 用本地模型；都没有 → 关
  local  sentence-transformers（``NYX_EMBED_MODEL``，默认 paraphrase-multilingual-MiniLM-L12-v2）
  api    OpenAI 兼容 /embeddings（EMBEDDING_API_URL / EMBEDDING_API_KEY / NYX_EMBED_MODEL）
  off    关闭，召回与 v7.12 完全相同
  也可以 ``NYX_EMBED_PROVIDER=包.模块:工厂`` 指定自定义 provider（评测与测试用）。

零依赖的默认安装不受影响：没有后端时整个模块是空操作。
"""
from __future__ import annotations

import array
import logging
import math
import os
import sqlite3
import threading
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    mem_id     TEXT PRIMARY KEY,
    line_start INTEGER,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vec        BLOB NOT NULL,
    indexed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_vec_model ON vectors(model);
CREATE INDEX IF NOT EXISTS idx_vec_line  ON vectors(line_start);
"""

_lock = threading.RLock()          # 索引写入串行（sync_turn 工作线程 + 回填线程）
_provider = None                   # 已解析的 provider；False = 解析过但没有
_provider_key = None               # 解析时的配置指纹，配置变了重新解析
_matrix_cache = {}                 # db_path → (版本, model, ids, lines, 矩阵/列表)
_warming = threading.Event()


# ══════════════════════════════════════════════════════════
# 后端
# ══════════════════════════════════════════════════════════

class _Api:
    """OpenAI 兼容 /embeddings。只用标准库。"""

    def __init__(self, url: str, key: str, model: str):
        self.url, self.key, self.model = url, key, model
        self.name = f"api:{model}"

    @property
    def ready(self) -> bool:
        return True

    def encode(self, texts: List[str]) -> List[List[float]]:
        import json
        import urllib.request
        req = urllib.request.Request(
            self.url, data=json.dumps({"input": texts, "model": self.model}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)["data"]
        return [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]


class _Local:
    """sentence-transformers。模型懒加载；加载期间 ready=False，召回不等它。"""

    def __init__(self, model: str):
        self.model_name = model
        self.name = f"local:{model}"
        self._model = None
        self._failed = False
        self._load_lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        with self._load_lock:
            if self._model is None and not self._failed:
                try:
                    from sentence_transformers import SentenceTransformer
                    cache = os.environ.get("NYX_EMBED_CACHE") or os.path.expanduser(
                        "~/.cache/nexsandglass/models")
                    self._model = SentenceTransformer(self.model_name, cache_folder=cache)
                    logger.info("[semantic] 本地模型就绪: %s", self.model_name)
                except Exception as e:
                    self._failed = True
                    logger.warning("[semantic] 本地模型加载失败，语义检索关闭: %s", e)
        return self._model is not None

    def encode(self, texts: List[str]) -> List[List[float]]:
        if not self.load():
            return []
        return self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


def _config_key() -> tuple:
    return tuple(os.environ.get(k, "") for k in (
        "NYX_EMBED", "NYX_EMBED_PROVIDER", "NYX_EMBED_MODEL", "EMBEDDING_API_URL", "EMBEDDING_API_KEY"))


def _resolve():
    mode = (os.environ.get("NYX_EMBED") or "auto").strip().lower()
    if mode in ("off", "0", "false", "none", "no"):
        return None
    custom = os.environ.get("NYX_EMBED_PROVIDER", "").strip()
    if custom:
        import importlib
        mod, _, attr = custom.partition(":")
        obj = getattr(importlib.import_module(mod), attr or "provider")
        p = obj() if callable(obj) and not hasattr(obj, "encode") else obj
        if not getattr(p, "name", None):
            p.name = custom
        return p
    model = os.environ.get("NYX_EMBED_MODEL", "").strip()
    url = os.environ.get("EMBEDDING_API_URL", "").strip()
    if mode == "api" or (mode == "auto" and url):
        if not url:
            logger.warning("[semantic] NYX_EMBED=api 但没有 EMBEDDING_API_URL")
            return None
        return _Api(url, os.environ.get("EMBEDDING_API_KEY", ""), model or "text-embedding-3-small")
    if mode in ("local", "auto"):
        import importlib.util
        if importlib.util.find_spec("sentence_transformers") is None:
            if mode == "local":
                logger.warning("[semantic] NYX_EMBED=local 但没有安装 sentence-transformers"
                               "（pip install 'nyx-memory[vector]'）")
            return None
        return _Local(model or DEFAULT_LOCAL_MODEL)
    logger.warning("[semantic] 未知的 NYX_EMBED=%r，语义检索关闭", mode)
    return None


def provider():
    """当前生效的嵌入后端；没有返回 None。配置（环境变量）变了会重新解析。"""
    global _provider, _provider_key
    key = _config_key()
    if _provider is None or key != _provider_key:
        try:
            _provider = _resolve() or False
        except Exception as e:
            logger.warning("[semantic] 后端解析失败，语义检索关闭: %s", e)
            _provider = False
        _provider_key = key
    return _provider or None


def set_provider(p) -> None:
    """测试 / 评测注入。None 恢复按环境变量解析。"""
    global _provider, _provider_key
    _provider = p if p is not None else None
    _provider_key = _config_key() if p is not None else None
    _matrix_cache.clear()


def enabled() -> bool:
    return provider() is not None


def _model_id(p) -> str:
    return str(getattr(p, "name", None) or type(p).__name__)


# ══════════════════════════════════════════════════════════
# 存储
# ══════════════════════════════════════════════════════════

def _db_path() -> str:
    from nexsandglass.core import memid
    return os.path.join(os.path.dirname(memid._db_path()), "vectors.db")   # 与 nyx.db 同目录


def _conn(path: str = None) -> sqlite3.Connection:
    path = path or _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def _pack(v: Iterable[float]) -> Tuple[bytes, int]:
    v = [float(x) for x in v]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    a = array.array("f", (x / n for x in v))
    return a.tobytes(), len(a)


def _unpack(b: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(b)
    return a


def _now() -> str:
    from nexsandglass.core import clock
    return f"{clock.now():%Y-%m-%d %H:%M:%S}"


# ══════════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════════

def _texts_for(text: str) -> str:
    return (text or "").strip()[:2000]


def index_pending(limit: Optional[int] = 256, batch: int = 32) -> int:
    """给还没有（当前模型）向量的在世记忆补上向量。返回本次写入条数。

    在后台调用：sync_turn 之后、initialize 的回填线程、维护脚本。失败只记日志。
    """
    p = provider()
    if p is None:
        return 0
    from nexsandglass.core import memid
    model = _model_id(p)
    done = 0
    with _lock:
        try:
            vc = _conn()
            have = {r[0] for r in vc.execute("SELECT mem_id FROM vectors WHERE model=?", (model,))}
            todo = []
            for mid, line, text in memid.get_conn().execute(
                    "SELECT mem_id, line_start, text FROM memories WHERE deleted_at IS NULL ORDER BY seq"):
                if mid in have or not text or memid.is_redacted(text):
                    continue
                todo.append((mid, line, _texts_for(text)))
                if limit is not None and len(todo) >= limit:
                    break
            for i in range(0, len(todo), batch):
                part = todo[i:i + batch]
                vecs = p.encode([t for _, _, t in part])
                if not vecs or len(vecs) != len(part):
                    logger.warning("[semantic] 嵌入返回 %d 条，期望 %d；本批跳过",
                                   len(vecs or []), len(part))
                    continue
                rows = []
                for (mid, line, _), v in zip(part, vecs):
                    blob, dim = _pack(v)
                    rows.append((mid, line, model, dim, blob, _now()))
                vc.executemany("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?,?)", rows)
                vc.commit()
                # 嵌入期间这几条可能刚被遗忘：插入之后再核对一次，不在世的立刻删掉
                ids = [r[0] for r in rows]
                live = {r[0] for r in memid.get_conn().execute(
                    f"SELECT mem_id FROM memories WHERE deleted_at IS NULL AND mem_id IN "
                    f"({','.join('?' * len(ids))})", ids)}
                gone = [m for m in ids if m not in live]
                if gone:
                    vc.execute(f"DELETE FROM vectors WHERE mem_id IN ({','.join('?' * len(gone))})", gone)
                    vc.commit()
                done += len(rows) - len(gone)
            vc.close()
        except Exception as e:
            logger.warning("[semantic] 补索引失败（不影响写入与词法召回）: %s", e)
    if done:
        _matrix_cache.pop(_db_path(), None)
    return done


_sched_lock = threading.Lock()
_sched_running = False
_sched_again = False


def schedule_index() -> None:
    """有新记忆写入 → 后台补向量（合并突发写入；同一时刻最多一个工作线程）。

    由 sandglass_log.log_message 在每次落沙成功后调用，覆盖所有写入方（Hermes / MCP / CLI）。
    没有后端时是一次环境变量比较，什么都不做。
    """
    global _sched_running, _sched_again
    if provider() is None:
        return
    with _sched_lock:
        if _sched_running:
            _sched_again = True
            return
        _sched_running = True

    def _run():
        global _sched_running, _sched_again
        try:
            while True:
                with _sched_lock:
                    _sched_again = False
                index_all()
                with _sched_lock:
                    if not _sched_again:
                        _sched_running = False
                        return
        except Exception as e:
            logger.warning("[semantic] 后台补索引失败: %s", e)
            with _sched_lock:
                _sched_running = False

    try:
        from agent.memory_provider import spawn_context_thread
        t = spawn_context_thread(_run, name="nyx-semantic-index")
    except Exception:
        t = threading.Thread(target=_run, name="nyx-semantic-index", daemon=True)
    t.start()


def wait_idle(timeout: float = 30.0) -> bool:
    """测试 / 评测：等后台补索引跑完。"""
    import time
    end = time.time() + timeout
    while time.time() < end:
        with _sched_lock:
            if not _sched_running and not _warming.is_set():
                return True
        time.sleep(0.02)
    return False


def index_all(batch: int = 64) -> int:
    total = 0
    while True:
        n = index_pending(limit=1024, batch=batch)
        total += n
        if n == 0:
            return total


def warm_async() -> None:
    """后台：加载本地模型 + 回填历史。重复调用只跑一次。"""
    if _warming.is_set() or provider() is None:
        return
    _warming.set()

    def _run():
        try:
            p = provider()
            if p is not None and hasattr(p, "load"):
                p.load()
            n = index_all()
            if n:
                logger.info("[semantic] 回填 %d 条记忆的向量", n)
        except Exception as e:
            logger.warning("[semantic] 回填失败: %s", e)
        finally:
            _warming.clear()

    try:
        from agent.memory_provider import spawn_context_thread
        t = spawn_context_thread(_run, name="nyx-semantic-backfill")
    except Exception:
        t = threading.Thread(target=_run, name="nyx-semantic-backfill", daemon=True)
    t.start()


def delete(mem_ids: Iterable[str]) -> int:
    """删除向量（遗忘级联）。没有 vectors.db 时不创建。"""
    ids = [m for m in (mem_ids or []) if m]
    path = _db_path()
    if not ids or not os.path.exists(path):
        return 0
    with _lock:
        c = _conn(path)
        n = 0
        for i in range(0, len(ids), 400):
            part = ids[i:i + 400]
            n += c.execute(f"DELETE FROM vectors WHERE mem_id IN ({','.join('?' * len(part))})",
                           part).rowcount
        c.commit()
        c.close()
    _matrix_cache.pop(path, None)
    return n


# ══════════════════════════════════════════════════════════
# 召回
# ══════════════════════════════════════════════════════════

def _load(path: str, model: str):
    """当前模型的全部向量（按 data_version 缓存）。个人记忆规模（十万条以内）暴力检索足够。"""
    c = _conn(path)
    try:
        ver = (c.execute("SELECT COUNT(*), MAX(rowid) FROM vectors WHERE model=?", (model,)).fetchone(),
               os.path.getmtime(path))
        hit = _matrix_cache.get(path)
        if hit and hit[0] == ver and hit[1] == model:
            return hit
        ids, lines, vecs = [], [], []
        for mid, line, blob in c.execute(
                "SELECT mem_id, line_start, vec FROM vectors WHERE model=?", (model,)):
            ids.append(mid)
            lines.append(line)
            vecs.append(_unpack(blob))
    finally:
        c.close()
    try:
        import numpy as np
        mat = np.vstack([np.frombuffer(v.tobytes(), dtype=np.float32) for v in vecs]) if vecs else None
    except Exception:
        mat = vecs
    entry = (ver, model, ids, lines, mat)
    _matrix_cache[path] = entry
    return entry


def search(query: str, k: int = 20, min_score: float = None) -> List[Tuple[int, float]]:
    """查询 → [(line_start, 余弦)]，高到低。没有后端 / 没有向量 / 模型未就绪 → []。

    只返回仍在世的记忆（中枢 deleted_at IS NULL）。
    """
    p = provider()
    if p is None or not (query or "").strip():
        return []
    if not getattr(p, "ready", True):
        warm_async()                    # 不在召回路径上等模型加载
        return []
    path = _db_path()
    if not os.path.exists(path):
        return []
    try:
        _, model, ids, lines, mat = _load(path, _model_id(p))
        if not ids:
            return []
        qv = p.encode([_texts_for(query)])
        if not qv:
            return []
        qb, _ = _pack(qv[0])
        q = _unpack(qb)
        try:
            import numpy as np
            scores = (mat @ np.frombuffer(q.tobytes(), dtype=np.float32)).tolist()
        except Exception:
            scores = [sum(a * b for a, b in zip(v, q)) for v in mat]
        cut = _standout_threshold(scores)
        if min_score is None:
            min_score = float(os.environ.get("NYX_EMBED_MIN_SCORE", "0") or 0)
        cut = max(cut, min_score)
        ranked = sorted(((i, s) for i, s in enumerate(scores) if s > cut), key=lambda x: -x[1])
        cand = [(ids[i], lines[i], s) for i, s in ranked[: max(k * 3, k)]]
        if not cand:
            return []
        from nexsandglass.core import memid
        live = {r[0]: r[1] for r in memid.get_conn().execute(
            f"SELECT mem_id, line_start FROM memories WHERE deleted_at IS NULL AND mem_id IN "
            f"({','.join('?' * len(cand))})", [m for m, _, _ in cand])}
        return [(live[m], s) for m, _, s in cand if m in live][:k]
    except Exception as e:
        logger.debug("[semantic] 检索失败（退回词法）: %s", e)
        return []


def _standout_threshold(scores: List[float]) -> float:
    """「比背景明显更像」的门槛：中位数 + Z × 1.4826 × MAD（稳健 z 分数）。

    向量检索永远会返回最像的几条，哪怕全都不相关；把这些背景噪声送进 RRF，
    它们会凭两票把精确的字面命中挤出前几名（v7.13 事故演练实测）。
    只有和背景拉开距离的命中才算语义证据。全部一样像（MAD=0）→ 只有严格高于中位数
    一个最小间隔的才算。Z 由 NYX_EMBED_Z 配置，默认 2。
    """
    n = len(scores)
    if n == 0:
        return float("inf")
    srt = sorted(scores)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    dev = sorted(abs(x - med) for x in scores)
    mad = dev[n // 2] if n % 2 else (dev[n // 2 - 1] + dev[n // 2]) / 2
    try:
        z = float(os.environ.get("NYX_EMBED_Z", "2") or 2)
    except ValueError:
        z = 2.0
    return med + max(z * 1.4826 * mad, 0.02)


class RouterHook:
    """SearchRouter 的第五路接口：search(query, limit) → [(line, score)]。"""

    def search(self, query: str, limit: int = 10):
        return search(query, k=max(limit * 2, 20))


def router_hook() -> Optional[RouterHook]:
    return RouterHook() if enabled() else None


# ══════════════════════════════════════════════════════════
# 体检
# ══════════════════════════════════════════════════════════

def stats() -> dict:
    p = provider()
    out = {"enabled": p is not None, "backend": _model_id(p) if p else "off",
           "ready": bool(p and getattr(p, "ready", True)), "indexed": 0, "pending": 0,
           "stale_model": 0, "orphans": 0}
    path = _db_path()
    try:
        from nexsandglass.core import memid
        live = {r[0] for r in memid.get_conn().execute(
            "SELECT mem_id FROM memories WHERE deleted_at IS NULL")}
        if os.path.exists(path):
            c = _conn(path)
            rows = c.execute("SELECT mem_id, model FROM vectors").fetchall()
            c.close()
        else:
            rows = []
        model = _model_id(p) if p else None
        cur = {m for m, md in rows if md == model}
        out["indexed"] = len(cur & live)
        out["pending"] = len(live - cur) if p else 0
        out["stale_model"] = sum(1 for _, md in rows if md != model) if p else 0
        out["orphans"] = sum(1 for m, _ in rows if m not in live)
    except Exception as e:
        out["error"] = str(e)
    return out


def orphans() -> List[str]:
    """指向已删除 / 不存在记忆的向量 —— 遗忘没删干净的证据。"""
    path = _db_path()
    if not os.path.exists(path):
        return []
    from nexsandglass.core import memid
    live = {r[0] for r in memid.get_conn().execute(
        "SELECT mem_id FROM memories WHERE deleted_at IS NULL")}
    c = _conn(path)
    try:
        return [r[0] for r in c.execute("SELECT mem_id FROM vectors") if r[0] not in live]
    finally:
        c.close()
