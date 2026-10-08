"""
core/fsutil.py — 原子写文件（v7.13.3）
=========================================

仓库里原来有 8 处「写 path + ".tmp" 再 os.replace」。临时文件名是**固定的**：
两个写入方（两个线程，或 Hermes 与 MCP 两个进程）同时写同一个文件时 ——

- 一方 os.replace 把 .tmp 挪走，另一方的 os.replace 报 FileNotFoundError
  （生产日志里的 ``sandglass.idx.tmp -> sandglass.idx``）
- 更糟：两方以 "w" 打开同一个 .tmp，互相截断、交错写入，拼出来的坏文件被当成正式文件换上去

这里统一成：同目录下 mkstemp 出的唯一临时文件 → 写完 fsync → os.replace。
仓库里不允许再出现固定名字的临时文件（tests/test_structural_guards.py 会扫源码）。
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from typing import Iterator, TextIO


@contextlib.contextmanager
def atomic_write(path: str, mode: str = "w", encoding: str = "utf-8", fsync: bool = True) -> Iterator[TextIO]:
    """``with atomic_write(p) as f: f.write(...)`` —— 成功才替换，异常时原文件不动、临时文件清掉。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        kw = {} if "b" in mode else {"encoding": encoding}
        with os.fdopen(fd, mode, **kw) as f:
            yield f
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        try:                         # 保留原文件权限（mkstemp 建的是 0600）
            os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
