"""Veil —— Bloom Filter，分辨「见过 / 没见过」的薄纱。

设计取舍
--------
为什么不精确集合？
    精确集合要存全部 token 原文。1M 条记忆的 token 集合远超 128KB，
    且要处理序列化、去重、内存占用。Bloom 用固定 128KB 覆盖任意规模——
    代价是假阳性（说"见过"其实没见过）。

为什么允许假阳性？
    这一层的用途是**分流**，不是判案：
      - 假阳性 → 多走一次 Mist 查询，查到空，退化为"没有"。
        代价是一次多余的 SQLite 查询。
      - 假阴性 → 不可能（Bloom 无假阴性）。
    所以假阳性的代价是**性能**，不是**正确性**。

本模块零外部依赖，只用 stdlib。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_BITS = 1 << 20      # 1M bits = 128 KiB
DEFAULT_HASHES = 7

_MAGIC = b"DVVEIL01"        # 文件头，用于识别格式与版本


class Veil:
    """定长位图 + k 个独立哈希。

    线程安全：``touch`` / ``probe`` 走同一把锁。

    参数
    ----
    bits    : 位图大小（默认 1<<20 = 1,048,576 bits = 128 KiB）
    hashes  : 哈希函数个数 k（默认 7）
    """

    __slots__ = ("_bits", "_count", "_size", "_hashes", "_lock")

    def __init__(self, bits: int = DEFAULT_BITS, hashes: int = DEFAULT_HASHES):
        if bits <= 0:
            raise ValueError("bits must be positive")
        if hashes <= 0:
            raise ValueError("hashes must be positive")
        self._size = int(bits)
        self._hashes = int(hashes)
        self._bits = bytearray((self._size + 7) // 8)
        self._count = 0
        self._lock = threading.RLock()

    # ── 哈希 ────────────────────────────────────────────────
    @staticmethod
    def _fold(item: str, seed: int) -> int:
        """双重哈希的简化实现：sha256(f"{seed}:{item}") 取前 4 字节。

        用 sha256 而非内置 ``hash()``：后者受 PYTHONHASHSEED 影响，
        进程重启后不一致，持久化位图会立刻失效。
        """
        h = hashlib.sha256(f"{seed}:{item}".encode("utf-8")).digest()
        return int.from_bytes(h[:4], "big")

    # ── 写入 ────────────────────────────────────────────────
    def touch(self, item: str) -> None:
        """在薄纱上留下印记（置 k 个位）。"""
        if not item:
            return
        with self._lock:
            for s in range(self._hashes):
                bit = self._fold(item, s) % self._size
                self._bits[bit >> 3] |= (1 << (bit & 7))
            self._count += 1

    def touch_many(self, items) -> None:
        with self._lock:
            for it in items:
                self.touch(it)

    # ── 查询 ────────────────────────────────────────────────
    def probe(self, item: str) -> bool:
        """探知是否曾接触过此物。

        返回 True 表示「可能见过」（存在假阳性）；
        返回 False 表示「一定没见过」（Bloom 无假阴性）。
        """
        if not item:
            return False
        with self._lock:
            for s in range(self._hashes):
                bit = self._fold(item, s) % self._size
                if not (self._bits[bit >> 3] & (1 << (bit & 7))):
                    return False
            return True

    # ── 持久化 ──────────────────────────────────────────────
    def persist(self, path: str) -> None:
        """把位图写入文件（原子替换，避免写一半崩溃留下坏文件）。"""
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with self._lock:
            with open(tmp, "wb") as f:
                f.write(_MAGIC)
                f.write(self._size.to_bytes(4, "big"))
                f.write(bytes([self._hashes]))
                f.write(self._count.to_bytes(8, "big"))
                f.write(bytes(self._bits))
            os.replace(tmp, path)

    def load(self, path: str) -> bool:
        """从文件恢复。返回 False 表示没有可用文件或格式不兼容。"""
        if not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as f:
                magic = f.read(len(_MAGIC))
                if magic != _MAGIC:
                    logger.warning("dejavu: veil file has unknown format, starting fresh")
                    return False
                stored_size = int.from_bytes(f.read(4), "big")
                if stored_size != self._size:
                    logger.warning(
                        "dejavu: veil size mismatch (%s vs %s), starting fresh",
                        stored_size, self._size,
                    )
                    return False
                hashes = f.read(1)[0]
                count = int.from_bytes(f.read(8), "big")
                bits = f.read()
            with self._lock:
                self._hashes = hashes
                self._count = count
                self._bits = bytearray(bits)
            return True
        except Exception:
            logger.warning("dejavu: veil load failed, starting fresh", exc_info=True)
            return False

    # ── 维护 ────────────────────────────────────────────────
    def reset(self) -> None:
        """清空位图（``reindex`` 用）。"""
        with self._lock:
            self._bits = bytearray((self._size + 7) // 8)
            self._count = 0

    def stats(self) -> dict:
        with self._lock:
            filled = sum(bin(b).count("1") for b in self._bits)
            return {
                "bits": self._size,
                "hashes": self._hashes,
                "items": self._count,
                "filled_bits": filled,
                "fill_ratio": round(filled / self._size, 6) if self._size else 0.0,
                "bytes": len(self._bits),
                "theoretical_fp": self.theoretical_fp(),
                "estimated_fp": self.estimated_fp(),
            }

    # ── 假阳性估算 ──────────────────────────────────────────

    def theoretical_fp(self, items: Optional[int] = None) -> float:
        """理论假阳性率 (1 - e^(-kn/m))^k。

        ``items`` 省略时用当前计数。
        """
        n = self._count if items is None else items
        if n <= 0:
            return 0.0
        m, k = self._size, self._hashes
        return (1 - pow(2.718281828459045, -k * n / m)) ** k

    def estimated_fp(self) -> float:
        """按实际置位密度估算的假阳性率 p^k（比理论值更贴近现实）。"""
        with self._lock:
            filled = sum(bin(b).count("1") for b in self._bits)
            m = self._size
            if m <= 0:
                return 0.0
            p = filled / m
            return p ** self._hashes

    @property
    def density(self) -> int:
        """已写入条目数（非去重计数）。"""
        return self._count
