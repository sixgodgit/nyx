"""
打开 SQLite 库的唯一入口（v7.13.4）。

为什么要有它：v7.13.3 的发布流水线在 Python 3.11 上挂了 —— 4 个线程同时第一次打开同一个新库，
其中一个在 `PRAGMA journal_mode=WAL` 上直接抛 `database is locked`，timeout=30 也没用。
切换日志模式要排他锁；SQLite 发现锁成环（一边持共享锁等升级、一边在建表）时不调用 busy handler，
立即返回 BUSY。生产上一样会撞：Hermes 启动时预热线程、语义补索引线程、写入线程几乎同时开库；
网关进程和 MCP 进程同时启动也会。以前每个模块各自 connect + 设 WAL + 建表，7 处，各有各的写法。

规则（所有开库都走 connect()）：
1. 同一进程内，同一个库文件的「开库 + 设 WAL + 建表/迁移」按路径串行；
2. 只有当前不是 WAL 时才切换（WAL 持久化在库文件里，切过一次之后只需读一下）；
3. 跨进程撞上时，回滚后退避重试，直到 timeout 用完才抛 —— 不再是「第一下没拿到锁就失败」。

tests/test_structural_guards.py 扫源码：除本模块外不许再直接设 journal_mode。
"""
from __future__ import annotations

import os
import random
import sqlite3
import threading
import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

_locks: dict = {}
_locks_guard = threading.Lock()


def path_lock(path: str) -> threading.RLock:
    """同一个库文件在本进程内的开库锁（按真实路径；可重入）。"""
    key = os.path.realpath(path)
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.RLock()
        return lk


def is_lock_error(e: BaseException) -> bool:
    if not isinstance(e, sqlite3.OperationalError):
        return False
    m = str(e).lower()
    return "locked" in m or "busy" in m


def retry_locked(conn: sqlite3.Connection, fn: Callable[[sqlite3.Connection], T], timeout: float = 30.0) -> T:
    """执行 fn(conn)；遇到 locked / busy 就回滚、退避、重来，直到 timeout 用完。fn 必须幂等。"""
    deadline = time.monotonic() + timeout
    delay = 0.005
    while True:
        try:
            return fn(conn)
        except sqlite3.OperationalError as e:
            if not is_lock_error(e) or time.monotonic() + delay > deadline:
                raise
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            time.sleep(delay * (0.5 + random.random()))
            delay = min(delay * 2, 0.25)


def _ensure_wal(c: sqlite3.Connection) -> str:
    mode = str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if mode != "wal":
        mode = str(c.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
    return mode


def connect(path: str, *, timeout: float = 30.0, check_same_thread: bool = True,
            synchronous: Optional[str] = None,
            setup: Optional[Callable[[sqlite3.Connection], None]] = None) -> sqlite3.Connection:
    """打开 path：WAL、可选的 synchronous、可选的 setup（建表/迁移，须自行 commit 且幂等）。

    本进程内同一个库的这几步串行；跨进程撞锁时退避重试到 timeout。失败则关掉连接再抛。
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with path_lock(path):
        c = sqlite3.connect(path, timeout=timeout, check_same_thread=check_same_thread)
        try:
            retry_locked(c, _ensure_wal, timeout)
            if synchronous:
                c.execute(f"PRAGMA synchronous={synchronous}")
            if setup is not None:
                retry_locked(c, setup, timeout)
        except BaseException:
            c.close()
            raise
    return c
