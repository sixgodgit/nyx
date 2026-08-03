"""
NexSandglass ChromaDB 语义搜索后端
====================================
与现有 TF-IDF 并存，不替换。
使用持久化 ChromaDB 存储沙粒嵌入，支持语义搜索。
"""
import os, json, logging
from datetime import datetime

from sandglass_paths import _NB

_CHROMA_DIR = os.path.join(_NB, "chroma_sand")
_COLLECTION_NAME = "nexsandglass_sands"

logger = logging.getLogger(__name__)

# 延迟加载 ChromaDB（只在首次使用时导入）
_client = None
_collection = None


def _get_collection():
    """获取或创建 ChromaDB collection（延迟初始化）"""
    global _client, _collection
    if _collection is not None:
        return _collection
    import chromadb
    os.makedirs(_CHROMA_DIR, exist_ok=True)
    _client = chromadb.PersistentClient(path=_CHROMA_DIR)
    # 尝试获取已有 collection，不存在则创建
    try:
        _collection = _client.get_collection(_COLLECTION_NAME)
    except Exception:
        _collection = _client.create_collection(
            _COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
    return _collection


def index_line(line_num: int, ts: str, text: str, metadata: dict = None) -> bool:
    """将一条沙粒写入 ChromaDB 索引。
    返回 True 表示写入成功。
    """
    try:
        coll = _get_collection()
        meta = metadata or {}
        meta.update({"line": line_num, "ts": ts})
        coll.add(
            ids=[str(line_num)],
            documents=[text],
            metadatas=[meta]
        )
        return True
    except Exception as e:
        logger.error(f"ChromaDB index_line error: {e}")
        return False


def search(query: str, limit: int = 10) -> list:
    """ChromaDB 语义搜索。返回 [(行号, 时间, 明文), ...]"""
    try:
        coll = _get_collection()
        results = coll.query(query_texts=[query], n_results=limit)
        if not results or not results["ids"]:
            return []
        out = []
        for i, doc_id in enumerate(results["ids"][0]):
            ln = int(doc_id)
            meta = results["metadatas"][0][i] if results.get("metadatas") else {}
            ts = meta.get("ts", "")
            text = results["documents"][0][i] if results.get("documents") else ""
            out.append((ln, ts, text))
        return out
    except Exception as e:
        logger.error(f"ChromaDB search error: {e}")
        return []


def count() -> int:
    """返回 ChromaDB 中索引的沙粒数"""
    try:
        coll = _get_collection()
        return coll.count()
    except Exception:
        return 0


def delete_all() -> bool:
    """清空 ChromaDB 索引（用于重新导入）"""
    try:
        global _client, _collection
        client = chromadb.PersistentClient(path=_CHROMA_DIR)
        try:
            client.delete_collection(_COLLECTION_NAME)
        except Exception:
            pass
        _collection = None
        return True
    except Exception as e:
        logger.error(f"ChromaDB delete_all error: {e}")
        return False
