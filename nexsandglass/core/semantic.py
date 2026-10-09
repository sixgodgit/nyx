"""
core/semantic.py — 语义检索的第四路（v7.13）
============================================

v3.4.0 起仓库里就有 embedding_provider / vector_store / vector_search，README 也把
「向量语义检索」列为能力 —— 但生产路径上**没有任何代码写入向量**：SearchRouter 默认
``vector=None``，存储写死在 ``~/.hermes``，键是 memory_id 而 SearchRouter 期望行号。
结果是同义改写永远召不回来（问 company、原话是 joined Contoso；问「我的猫叫什么」、
原话是「领养了一只橘猫…叫团子」）。这个模块把它接上：

  索引  semantic.db（表 embeddings）放在 nyx 数据目录里（与 nyx.db 同处，随 hermes backup 一起备份）。
        键 = mem_id，同时存 line_start（召回侧按行号与其他几路融合）与 model（换模型
        后旧向量不参与比较，等重建）。
        v7.13–v7.13.2 用的是 vectors.db / 表 vectors —— 和旧 vector_store 撞了文件名和表名：
        数据目录恰好是 ~/.hermes/nexsandglass 时，旧模块先建了 2 列的 vectors 表，这里的
        CREATE TABLE IF NOT EXISTS 静默跳过，此后每次写入都 `no such column: model`，
        失败又被降级成一行 warning —— 生产上一条向量都没写进去过（2026-10-09 诊断报告）。
        现在：独立文件名 + 独立表名 + 打开时校验结构（对不上就挪开重建，向量是派生数据）
        + 失败计入 health()，Hermes 会在系统提示里提醒、`python3 -m nexsandglass.doctor` 会报红。
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

from nexsandglass.core import sqlite_open

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
DB_NAME = "semantic.db"
TABLE = "embeddings"
SCHEMA_VERSION = "1"
OWNER = "nexsandglass.semantic"
_COLUMNS = ["mem_id", "line_start", "model", "dim", "vec", "indexed_at"]
_SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    mem_id     TEXT PRIMARY KEY,
    line_start INTEGER,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vec        BLOB NOT NULL,
    indexed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_emb_model ON embeddings(model);
CREATE INDEX IF NOT EXISTS idx_emb_line  ON embeddings(line_start);
CREATE TABLE IF NOT EXISTS semantic_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
# 打开时逐条编译（EXPLAIN 不执行）—— 表结构对不上会在这里当场失败，而不是在某次写入里被吞掉
_STATEMENTS = [
    "INSERT OR REPLACE INTO embeddings (mem_id, line_start, model, dim, vec, indexed_at) VALUES (?,?,?,?,?,?)",
    "SELECT mem_id, line_start, vec FROM embeddings WHERE model=?",
    "SELECT mem_id FROM embeddings WHERE model=?",
    "DELETE FROM embeddings WHERE mem_id=?",
]

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
                    self.load_error = f"{type(e).__name__}: {e}"
                    logger.error("[semantic] 本地模型 %s 加载失败，语义检索不可用: %s", self.model_name, e)
        return self._model is not None

    def encode(self, texts: List[str]) -> List[List[float]]:
        if not self.load():
            return []
        return self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


def _config_key() -> tuple:
    return tuple(os.environ.get(k, "") for k in (
        "NYX_EMBED", "NYX_EMBED_PROVIDER", "NYX_EMBED_MODEL", "EMBEDDING_API_URL", "EMBEDDING_API_KEY"))


def _problem(msg: str):
    """配置写了要语义检索、实际却用不了 —— 记下来进 health()，而不只是一行 warning。"""
    global _resolve_problem
    _resolve_problem = msg
    logger.error("[semantic] %s", msg)
    return None


def _resolve():
    global _resolve_problem
    _resolve_problem = None
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
            return _problem("NYX_EMBED=api 但没有设置 EMBEDDING_API_URL，语义检索不可用")
        return _Api(url, os.environ.get("EMBEDDING_API_KEY", ""), model or "text-embedding-3-small")
    if mode in ("local", "auto"):
        import importlib.util
        if importlib.util.find_spec("sentence_transformers") is None:
            if mode == "local":
                return _problem("NYX_EMBED=local 但没有安装 sentence-transformers"
                                "（pip install 'nyx-memory[vector]'），语义检索不可用")
            return None
        return _Local(model or DEFAULT_LOCAL_MODEL)
    return _problem(f"未知的 NYX_EMBED={mode!r}（可选 auto / local / api / off），语义检索不可用")


def provider():
    """当前生效的嵌入后端；没有返回 None。配置（环境变量）变了会重新解析。"""
    global _provider, _provider_key
    key = _config_key()
    if _provider is None or key != _provider_key:
        try:
            _provider = _resolve() or False
        except Exception as e:
            _problem(f"语义后端解析失败（{type(e).__name__}: {e}），语义检索不可用")
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
    return os.path.join(os.path.dirname(memid._db_path()), DB_NAME)   # 与 nyx.db 同目录


# ── 故障状态：不再只是一行 warning ───────────────────────────
_status: dict = {}          # db 路径 → {last_error, last_error_at, failures, last_ok_at, where}
_logged: set = set()        # 已经按 ERROR 报过的 (路径, 错误)；同一个错误不刷屏
_resolve_problem: Optional[str] = None


def _fail(where: str, e: BaseException, path: str = None) -> None:
    path = path or _db_path()
    st = _status.setdefault(path, {"failures": 0})
    msg = f"{type(e).__name__}: {e}"
    st.update(last_error=msg, last_error_at=_now(), where=where)
    st["failures"] = st.get("failures", 0) + 1
    if (path, where, msg) not in _logged:
        _logged.add((path, where, msg))
        logger.error("[semantic] %s 失败：%s —— 语义检索这一路不可用（词法召回不受影响）。"
                     "运行 `python3 -m nexsandglass.doctor` 查看。库：%s", where, msg, path)
    else:
        logger.debug("[semantic] %s 再次失败: %s", where, msg)


def _ok(path: str = None) -> None:
    st = _status.setdefault(path or _db_path(), {"failures": 0})
    st.update(last_error=None, last_ok_at=_now())


# ── 打开 + 校验 ───────────────────────────────────────────────
_validated: set = set()     # 已校验过的 (路径, inode)
_open_lock = threading.Lock()


def _columns(c: sqlite3.Connection, table: str) -> list:
    return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]


def _problems(c: sqlite3.Connection) -> list:
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not tables:
        return []
    out = []
    if TABLE in tables and _columns(c, TABLE) != _COLUMNS:
        out.append(f"表 {TABLE} 的列是 {_columns(c, TABLE)}，期望 {_COLUMNS}")
    if "semantic_meta" not in tables:
        out.append("缺少 semantic_meta（不是本模块建的库）")
    else:
        meta = dict(c.execute("SELECT key, value FROM semantic_meta"))
        if meta.get("owner") != OWNER:
            out.append(f"owner={meta.get('owner')!r}，不是 {OWNER}")
        if meta.get("schema_version") != SCHEMA_VERSION:
            out.append(f"schema_version={meta.get('schema_version')!r}，期望 {SCHEMA_VERSION}")
    return out


def _move_aside(path: str, why: str) -> str:
    stamp = _now().replace(" ", "_").replace(":", "")
    dest = f"{path}.mismatch-{stamp}"
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.replace(path + suffix, dest + suffix)
    logger.error("[semantic] %s 的结构不对（%s）。已挪到 %s 并重建 —— 向量是派生数据，会在后台重新嵌入",
                 path, why, dest)
    return dest


def _migrate_from_v713(c: sqlite3.Connection, path: str) -> int:
    """v7.13–v7.13.2 把向量写在同目录的 vectors.db / 表 vectors。是本模块的结构就搬过来（省掉重新嵌入）；
    是旧 vector_store 的 2 列表（或别的东西）就原样不碰 —— 那不是我们的文件。"""
    old = os.path.join(os.path.dirname(path), "vectors.db")
    if not os.path.exists(old):
        return 0
    try:
        oc = sqlite3.connect(f"file:{old}?mode=ro", uri=True, timeout=5)
        try:
            cols = _columns(oc, "vectors")
        finally:
            oc.close()
        if cols != _COLUMNS:
            logger.info("[semantic] 同目录的 vectors.db 是旧 vector_store 的表（%s），不使用、不改动", cols or "无 vectors 表")
            return 0
        c.execute("ATTACH DATABASE ? AS old", (old,))
        try:
            n = c.execute(f"INSERT OR IGNORE INTO {TABLE} ({','.join(_COLUMNS)}) "
                          f"SELECT {','.join(_COLUMNS)} FROM old.vectors").rowcount
            c.commit()
        finally:
            c.execute("DETACH DATABASE old")
        from nexsandglass.core import memid
        live = {r[0] for r in memid.get_conn().execute("SELECT mem_id FROM memories WHERE deleted_at IS NULL")}
        dead = [r[0] for r in c.execute(f"SELECT mem_id FROM {TABLE}") if r[0] not in live]
        for i in range(0, len(dead), 400):
            part = dead[i:i + 400]
            c.execute(f"DELETE FROM {TABLE} WHERE mem_id IN ({','.join('?' * len(part))})", part)
        c.commit()
        logger.info("[semantic] 从 v7.13 的 vectors.db 迁移了 %d 条向量到 %s（旧文件保留未动；丢弃已遗忘的 %d 条）",
                    n - len(dead), path, len(dead))
        return n - len(dead)
    except Exception as e:
        logger.warning("[semantic] 迁移 v7.13 vectors.db 失败（会重新嵌入）: %s", e)
        return 0


def _create_schema(c: sqlite3.Connection) -> None:
    c.executescript(_SCHEMA)
    c.execute("INSERT OR REPLACE INTO semantic_meta VALUES ('owner', ?)", (OWNER,))
    c.execute("INSERT OR REPLACE INTO semantic_meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    c.commit()


def _conn(path: str = None) -> sqlite3.Connection:
    """打开语义库。第一次打开时校验结构：对不上就挪开重建；所有语句先编译一遍。"""
    path = path or _db_path()
    c = sqlite_open.connect(path, timeout=30)
    try:
        key = (path, os.stat(path).st_ino)
    except OSError:
        key = None
    if key is not None and key in _validated:
        return c
    with _open_lock:
        fresh = not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
        problems = _problems(c)
        if problems:
            c.close()
            _move_aside(path, "；".join(problems))
            c = sqlite_open.connect(path, timeout=30)
            fresh = True
        sqlite_open.retry_locked(c, _create_schema, 30)
        for sql in _STATEMENTS:
            c.execute("EXPLAIN " + sql, tuple([None] * sql.count("?")))
        if fresh:
            _migrate_from_v713(c, path)
        _validated.add((path, os.stat(path).st_ino))
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
            have = {r[0] for r in vc.execute(f"SELECT mem_id FROM {TABLE} WHERE model=?", (model,))}
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
                    raise RuntimeError(f"嵌入后端返回 {len(vecs or [])} 条向量，期望 {len(part)} 条")
                rows = []
                for (mid, line, _), v in zip(part, vecs):
                    blob, dim = _pack(v)
                    rows.append((mid, line, model, dim, blob, _now()))
                vc.executemany(_STATEMENTS[0], rows)
                # 进度戳：回填几千条要几十分钟，期间「待补很多」是正常的 —— 别的进程（doctor）靠它分辨
                vc.execute("INSERT OR REPLACE INTO semantic_meta VALUES ('last_index_at', ?)", (_now(),))
                vc.commit()
                # 嵌入期间这几条可能刚被遗忘：插入之后再核对一次，不在世的立刻删掉
                ids = [r[0] for r in rows]
                live = {r[0] for r in memid.get_conn().execute(
                    f"SELECT mem_id FROM memories WHERE deleted_at IS NULL AND mem_id IN "
                    f"({','.join('?' * len(ids))})", ids)}
                gone = [m for m in ids if m not in live]
                if gone:
                    vc.execute(f"DELETE FROM {TABLE} WHERE mem_id IN ({','.join('?' * len(gone))})", gone)
                    vc.commit()
                done += len(rows) - len(gone)
            vc.close()
            _ok()
        except Exception as e:
            _fail("补索引", e)
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
            _fail("后台补索引", e)
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
            _fail("回填", e)
        finally:
            _warming.clear()

    try:
        from agent.memory_provider import spawn_context_thread
        t = spawn_context_thread(_run, name="nyx-semantic-backfill")
    except Exception:
        t = threading.Thread(target=_run, name="nyx-semantic-backfill", daemon=True)
    t.start()


def delete(mem_ids: Iterable[str]) -> int:
    """删除向量（遗忘级联）。没有语义库时不创建。"""
    ids = [m for m in (mem_ids or []) if m]
    path = _db_path()
    if not ids or not os.path.exists(path):
        return 0
    with _lock:
        c = _conn(path)
        n = 0
        for i in range(0, len(ids), 400):
            part = ids[i:i + 400]
            n += c.execute(f"DELETE FROM {TABLE} WHERE mem_id IN ({','.join('?' * len(part))})",
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
        ver = (c.execute(f"SELECT COUNT(*), MAX(rowid) FROM {TABLE} WHERE model=?", (model,)).fetchone(),
               os.path.getmtime(path))
        hit = _matrix_cache.get(path)
        if hit and hit[0] == ver and hit[1] == model:
            return hit
        ids, lines, vecs = [], [], []
        for mid, line, blob in c.execute(_STATEMENTS[1], (model,)):
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
        _fail("检索", e, path)
        return []


def _standout_threshold(scores: List[float]) -> float:
    """「比背景明显更像」的门槛：中位数 + Z × 1.4826 × MAD（稳健 z 分数）。

    向量检索永远会返回最像的几条，哪怕全都不相关；把这些背景噪声送进 RRF，
    它们会凭两票把精确的字面命中挤出前几名（v7.13 事故演练实测）。
    只有和背景拉开距离的命中才算语义证据。全部一样像（MAD=0）→ 只有严格高于中位数
    一个最小间隔的才算。Z 由 NYX_EMBED_Z 配置，默认 2。
    """
    if not scores:
        return float("inf")
    # 背景只用「最像的那几条之外」的分数估（v7.13.3）。以前对全部分数取中位数：只有两条记忆时，
    # 中位数是命中与噪声的平均，门槛高过命中本身 —— 新用户的头几条记忆永远语义召回不到
    srt = sorted(scores)
    bg = srt[:-max(1, len(srt) // 10)] or srt
    if len(srt) == 1:
        return min(srt[0], 0.0) - 1e-9          # 只有一条：没有背景可比，交给 RRF 与字面优先去排
    n = len(bg)
    med = bg[n // 2] if n % 2 else (bg[n // 2 - 1] + bg[n // 2]) / 2
    dev = sorted(abs(x - med) for x in bg)
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

def _live_ids() -> set:
    from nexsandglass.core import memid
    return {r[0] for r in memid.get_conn().execute("SELECT mem_id FROM memories WHERE deleted_at IS NULL")}


def stats() -> dict:
    """如实报告。库打不开时 pending 按全部在世记忆算 —— 以前这种情况报的是 pending=0，看起来像全部完成。"""
    p = provider()
    path = _db_path()
    model = _model_id(p) if p else None
    out = {"enabled": p is not None, "backend": model or "off",
           "ready": bool(p and getattr(p, "ready", True)), "db": path,
           "indexed": 0, "pending": 0, "stale_model": 0, "orphans": 0}
    st = _status.get(path) or {}
    out.update(last_error=st.get("last_error"), last_error_at=st.get("last_error_at"),
               failures=st.get("failures", 0), last_ok_at=st.get("last_ok_at"))
    if _resolve_problem:
        out["config_problem"] = _resolve_problem
    try:
        live = _live_ids()
    except Exception as e:
        out["error"] = f"读不了 ID 中枢: {e}"
        return out
    rows = []
    if os.path.exists(path):
        try:
            c = _conn(path)
            try:
                rows = c.execute(f"SELECT mem_id, model FROM {TABLE}").fetchall()
            finally:
                c.close()
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
    cur = {m for m, md in rows if md == model}
    out["indexed"] = len(cur & live)
    out["pending"] = len(live - cur) if p else 0
    out["stale_model"] = sum(1 for _, md in rows if md != model) if p else 0
    out["orphans"] = sum(1 for m, _ in rows if m not in live)
    return out


def _oldest_pending_age_minutes(pending_ids: set) -> Optional[float]:
    if not pending_ids:
        return None
    from datetime import datetime
    from nexsandglass.core import memid, clock
    ids = list(pending_ids)[:5000]
    row = memid.get_conn().execute(
        f"SELECT MIN(ts) FROM memories WHERE mem_id IN ({','.join('?' * len(ids))})", ids).fetchone()
    try:
        return (clock.now() - datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
    except Exception:
        return None


PENDING_GRACE_MINUTES = 10
PROGRESS_FRESH_MINUTES = 5


def _last_index_at():
    path = _db_path()
    if not os.path.exists(path):
        return None
    from datetime import datetime
    c = _conn(path)
    try:
        row = c.execute("SELECT value FROM semantic_meta WHERE key='last_index_at'").fetchone()
    finally:
        c.close()
    try:
        return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S") if row else None
    except ValueError:
        return None


def health_check() -> dict:
    """给 memid.health() / doctor / Hermes 用：语义这一路是否真的在工作。

    不健康（ok=False）的情形 —— 每一种都曾经或可能静默发生：
      - 配了语义后端却解析不出来（例如 NYX_EMBED=local 但没装 sentence-transformers）
      - 最近一次补索引 / 检索失败，之后没有成功过
      - 本地模型加载失败
      - 有在世记忆超过 10 分钟还没有向量（写进去了却一直没被索引）
      - 有向量指向已经遗忘的记忆（遗忘没删干净）
    没配后端 = 关闭，ok=True（这是用户的选择，不是故障）。
    """
    s = stats()
    s["ok"], s["problems"] = True, []
    if s.get("config_problem"):
        s["problems"].append(s["config_problem"])
    if not s["enabled"]:
        s["ok"] = not s["problems"]
        s["state"] = "disabled"
        return s
    p = provider()
    if getattr(p, "_failed", False):
        s["problems"].append(f"本地嵌入模型加载失败：{getattr(p, 'load_error', '')}")
    if s.get("error"):
        s["problems"].append(f"语义库读不了：{s['error']}")
    if s.get("last_error"):
        s["problems"].append(f"最近一次失败（{s.get('last_error_at')}）：{s['last_error']}")
    if s["orphans"]:
        s["problems"].append(f"{s['orphans']} 条向量指向已遗忘的记忆")
    if s["pending"]:
        try:
            pend = _live_ids() - _indexed_ids(_model_id(p))
            age = _oldest_pending_age_minutes(pend)
        except Exception:
            age = None
        s["oldest_pending_minutes"] = None if age is None else round(age, 1)
        if age is not None and age > PENDING_GRACE_MINUTES:
            from nexsandglass.core import clock
            last = _last_index_at()
            fresh = last is not None and (clock.now() - last).total_seconds() / 60 <= PROGRESS_FRESH_MINUTES
            if fresh and not s.get("last_error"):
                s["backfilling"] = True       # 正在回填且有进展：不是故障
                s["last_index_at"] = f"{last:%Y-%m-%d %H:%M:%S}"
            else:
                s["problems"].append(f"{s['pending']} 条记忆还没有向量，最早的已等了 {age:.0f} 分钟"
                                     + ("" if last is None else f"，最近一次成功写入向量在 {last:%Y-%m-%d %H:%M}"))
    s["ok"] = not s["problems"]
    s["state"] = ("backfilling" if s.get("backfilling") else "ok") if s["ok"] else "failing"
    return s


def _indexed_ids(model: str) -> set:
    path = _db_path()
    if not os.path.exists(path):
        return set()
    c = _conn(path)
    try:
        return {r[0] for r in c.execute(_STATEMENTS[2], (model,))}
    finally:
        c.close()


def orphans() -> List[str]:
    """指向已删除 / 不存在记忆的向量 —— 遗忘没删干净的证据。"""
    path = _db_path()
    if not os.path.exists(path):
        return []
    live = _live_ids()
    c = _conn(path)
    try:
        return [r[0] for r in c.execute(f"SELECT mem_id FROM {TABLE}") if r[0] not in live]
    finally:
        c.close()
