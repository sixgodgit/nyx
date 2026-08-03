"""
迁移脚本：将 sandglass.db 中的沙粒导入 ChromaDB 语义索引。
用法：python migrate_to_chromadb.py
"""
import sys, os

# 确保能导入 NexSandglass 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sandglass_paths import _NB
from sandglass_chroma import _get_collection, count as chroma_count, delete_all
from sandglass_sqlite import _get_db


def migrate(force: bool = False) -> int:
    """将 sandglass.db 中的沙粒全部导入 ChromaDB。
    返回导入的条数。
    已存在数据时不重复导入，除非 force=True。
    """
    existing = chroma_count()
    if existing > 0 and not force:
        print(f"ChromaDB 已有 {existing} 条索引，跳过。如需重新导入请用 force=True")
        return existing

    if force:
        delete_all()

    # 从 sandglass.db 读取所有沙粒
    conn = _get_db()
    rows = conn.execute("SELECT id, ts, text FROM sandglass ORDER BY id").fetchall()
    conn.close()

    print(f"从 sandglass.db 读取到 {len(rows)} 条沙粒")

    coll = _get_collection()

    # 分批写入 ChromaDB（避免单次太大）
    batch_size = 100
    imported = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        ids = [str(r[0]) for r in batch]
        documents = [r[2] or "" for r in batch]
        metadatas = [{"line": r[0], "ts": r[1] or ""} for r in batch]
        coll.add(ids=ids, documents=documents, metadatas=metadatas)
        imported += len(batch)
        print(f"  已导入 {imported}/{len(rows)} 条...")

    print(f"迁移完成：共 {imported} 条沙粒已导入 ChromaDB")
    return imported


if __name__ == "__main__":
    force = "--force" in sys.argv
    migrate(force=force)
