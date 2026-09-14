"""
core/vector_store.py — 向量存储（轻量、后端可切换）

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

    def __init__(self, path: str = "~/.hermes/nexsandglass/vectors.json"):
        self._path = os.path.expanduser(path)
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
            tmp = self._path + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self._path)  # 原子替换，避免写坏主文件
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

    def __init__(self, path: str = "~/.hermes/nexsandglass/vectors.db"):
        self._path = os.path.expanduser(path)
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
                "CREATE TABLE IF NOT EXISTS vectors "
                "(memory_id TEXT PRIMARY KEY, embedding FLOAT[%d])" % self._dim
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vec ON vectors(embedding)"
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
                "INSERT OR REPLACE INTO vectors (memory_id, embedding) VALUES (?, ?)",
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
            rows = self._conn.execute("SELECT memory_id, embedding FROM vectors").fetchall()
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
            self._conn.execute("DELETE FROM vectors WHERE memory_id = ?", (memory_id,))
            self._conn.commit()

    def get(self, memory_id: str) -> Optional[list[float]]:
        if not self._conn:
            return None
        row = self._conn.execute(
            "SELECT embedding FROM vectors WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def count(self) -> int:
        if not self._conn:
            return 0
        return self._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]


# ── 全局单例 ──────────────────────────────────────────────────

_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    """获取全局 VectorStore（优先 sqlite-vec，否则 JSON）。"""
    global _store
    if _store is not None:
        return _store
    # 尝试 sqlite-vec
    try:
        import sqlite_vec  # noqa: F401
        store = SqliteVecStore()
        if store.available:
            _store = store
            return _store
    except Exception:
        pass
    # 回退 JSON
    _store = JsonVectorStore()
    return _store
