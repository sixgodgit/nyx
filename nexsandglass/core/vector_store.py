"""
core/vector_store.py — 向量存储（轻量、后端可切换）【v7.13.3 起弃用】

⚠ 召回主路径不用这个模块。唯一的向量索引是 core/semantic.py（semantic.db / 表 embeddings）。
这里只为旧代码的 API 兼容保留，并且**不再碰任何共享位置**：
  - 以前的默认路径是 ~/.hermes/nexsandglass/vectors.db，表名 vectors —— 数据目录恰好就是
    ~/.hermes/nexsandglass 时，它先建了 2 列的 vectors 表，语义层的 CREATE TABLE IF NOT EXISTS 静默跳过，
    此后语义层每次写入都 `no such column: model`，一个月没写进去一条向量（2026-10-09 诊断报告）
  - 现在默认路径是数据目录下专属的 legacy_vector_store.{json,db}，表名 legacy_vectors；
    get_vector_store() 只返回 JSON 后端并给出 DeprecationWarning

设计：
- 抽象 VectorStore 接口
- 默认后端：JSON 文件（零依赖、离线、足够中小规模）
- 可选后端：sqlite-vec（如果安装了 sqlite-vec 扩展）
- 存储 memory_id → embedding 映射，支持 top-k 余弦相似度检索
- Fail-safe：任何后端失败返回空列表

依赖：
- 默认 JSON 后端：纯标准库，零依赖
- 可选 sqlite-vec：pip install sqlite-vec（首次使用提示安装）
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（长度不等或空向量返回 0）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def _legacy_path(ext: str) -> str:
    from nexsandglass.core import memid
    return os.path.join(os.path.dirname(memid._db_path()), f"legacy_vector_store.{ext}")


class VectorStore:
    """向量存储抽象接口。"""

    def upsert(self, memory_id: str, embedding: list[float]) -> None:
        raise NotImplementedError

    def upsert_batch(self, items: list[tuple[str, list[float]]]) -> None:
        raise NotImplementedError

    def search(self, query_embedding: list[float], top_k: int = 10) -> list[tuple[str, float]]:
        """返回 [(memory_id, similarity_score), ...]，按分数降序。"""
        raise NotImplementedError

    def delete(self, memory_id: str) -> None:
        raise NotImplementedError

    def get(self, memory_id: str) -> Optional[list[float]]:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError


class JsonVectorStore(VectorStore):
    """JSON 文件后端（零依赖，离线可用）。

    适合中小规模（<10k 条），全量加载内存检索。
    """

    def __init__(self, path: str = None):
        self._path = os.path.expanduser(path) if path else _legacy_path("json")
        self._data: dict[str, list[float]] = {}
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
            except Exception as e:
                logger.warning("[JsonVectorStore] 加载失败: %s", e)
                self._data = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            from nexsandglass.core.fsutil import atomic_write
            with atomic_write(self._path) as f:
                json.dump(self._data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning("[JsonVectorStore] 保存失败: %s", e)

    def upsert(self, memory_id: str, embedding: list[float]) -> None:
        self._data[memory_id] = embedding
        self._save()

    def upsert_batch(self, items: list[tuple[str, list[float]]]) -> None:
        for mid, emb in items:
            self._data[mid] = emb
        self._save()

    def search(self, query_embedding: list[float], top_k: int = 10) -> list[tuple[str, float]]:
        if not query_embedding or not self._data:
            return []
        scored = []
        for mid, emb in self._data.items():
            sim = _cosine(query_embedding, emb)
            scored.append((mid, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def delete(self, memory_id: str) -> None:
        self._data.pop(memory_id, None)
        self._save()

    def get(self, memory_id: str) -> Optional[list[float]]:
        return self._data.get(memory_id)

    def count(self) -> int:
        return len(self._data)


class SqliteVecStore(VectorStore):
    """sqlite-vec 后端（更快，适合大规模）。

    需要：pip install sqlite-vec
    """

    def __init__(self, path: str = None):
        self._path = os.path.expanduser(path) if path else _legacy_path("db")
        self._conn = None
        self._dim = 384
        self._init_db()

    def _init_db(self):
        try:
            import sqlite_vec
            self._conn = sqlite3.connect(self._path)
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS legacy_vectors "
                "(memory_id TEXT PRIMARY KEY, embedding FLOAT[%d])" % self._dim
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_legacy_vec ON legacy_vectors(embedding)"
            )
        except Exception as e:
            logger.warning("[SqliteVecStore] 初始化失败（将使用 JSON 后端）: %s", e)
            self._conn = None

    @property
    def available(self) -> bool:
        return self._conn is not None

    def upsert(self, memory_id: str, embedding: list[float]) -> None:
        if not self._conn:
            return
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO legacy_vectors (memory_id, embedding) VALUES (?, ?)",
                (memory_id, json.dumps(embedding)),
            )
            self._conn.commit()
        except Exception as e:
            logger.warning("[SqliteVecStore] upsert 失败: %s", e)

    def upsert_batch(self, items: list[tuple[str, list[float]]]) -> None:
        for mid, emb in items:
            self.upsert(mid, emb)

    def search(self, query_embedding: list[float], top_k: int = 10) -> list[tuple[str, float]]:
        if not self._conn or not query_embedding:
            return []
        try:
            # 简化：全表扫描 + 余弦（sqlite-vec 扩展时可改为向量索引）
            rows = self._conn.execute("SELECT memory_id, embedding FROM legacy_vectors").fetchall()
            scored = []
            for mid, emb_json in rows:
                emb = json.loads(emb_json)
                sim = _cosine(query_embedding, emb)
                scored.append((mid, sim))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:top_k]
        except Exception as e:
            logger.warning("[SqliteVecStore] search 失败: %s", e)
            return []

    def delete(self, memory_id: str) -> None:
        if self._conn:
            self._conn.execute("DELETE FROM legacy_vectors WHERE memory_id = ?", (memory_id,))
            self._conn.commit()

    def get(self, memory_id: str) -> Optional[list[float]]:
        if not self._conn:
            return None
        row = self._conn.execute(
            "SELECT embedding FROM legacy_vectors WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def count(self) -> int:
        if not self._conn:
            return 0
        return self._conn.execute("SELECT COUNT(*) FROM legacy_vectors").fetchone()[0]


# ── 全局单例 ──────────────────────────────────────────────────

_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    """【弃用】旧接口。只返回数据目录下专属文件的 JSON 后端；召回主路径请用 core.semantic。"""
    global _store
    import warnings
    warnings.warn("nexsandglass.core.vector_store 已弃用；向量索引请用 nexsandglass.core.semantic",
                  DeprecationWarning, stacklevel=2)
    if _store is not None:
        return _store
    _store = JsonVectorStore()
    return _store
