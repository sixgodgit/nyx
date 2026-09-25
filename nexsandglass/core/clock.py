"""
core/clock.py — 可注入的时钟（v7.9）

为什么需要
==========
双时态记忆的正确性只能在「时间真的流过」的时候验证：6 个月的生活记录、
「3 月时我以为你住哪」、隔了几个月才听说的旧事。写入路径原先各自调
`datetime.now()`，模拟六个月的历史时，所有记录都落在同一秒里 ——
记录时间轴塌成一个点，known_at 类问题根本测不出对错。

也是具身/仿真场景的前提：回放一段传感器日志时，"现在"是日志里的时刻，
不是回放机器的挂钟。

范围（刻意很小）
================
只接管**时间轴的源头**：ID 中枢分配的记录时间戳、时序事实的记录时刻。
隔离区的到期时间、擦除墓碑的时间仍用真实挂钟 —— 那些是对用户的承诺
（"30 天后真的删"），不能被模拟时钟挪动。

用法
====
    from nexsandglass.core import clock
    with clock.frozen("2026-03-01 10:00:00"):
        runtime.observe(...)           # 记录时间 = 2026-03-01 10:00:00

    NYX_NOW="2026-03-01T10:00:00" python3 ...   # 跨进程

模拟时钟下的时间一律视为 UTC 的"墙上时刻"，中枢的本地时间戳与时序层的
UTC 时间戳取同一个值 —— 仿真里不存在时区差。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

_local = threading.local()
_global: Optional[datetime] = None


def _parse(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else \
            value.astimezone(timezone.utc).replace(tzinfo=None)
    s = str(value).strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时钟时间: {value!r}")


def _override() -> Optional[datetime]:
    t = getattr(_local, "frozen", None)
    if t is not None:
        return t
    if _global is not None:
        return _global
    env = os.environ.get("NYX_NOW")
    return _parse(env) if env else None


def is_simulated() -> bool:
    return _override() is not None


def now() -> datetime:
    """本地"墙上时刻"（naive）—— ID 中枢的记录时间戳用它。"""
    o = _override()
    return o if o is not None else datetime.now()


def utcnow() -> datetime:
    """UTC 时刻（aware）—— 时序事实层用它。模拟时钟下与 now() 取同一个墙上值。"""
    o = _override()
    return o.replace(tzinfo=timezone.utc) if o is not None else datetime.now(timezone.utc)


@contextmanager
def frozen(value):
    """在当前线程内把"现在"固定为 value。可嵌套。"""
    prev = getattr(_local, "frozen", None)
    _local.frozen = _parse(value)
    try:
        yield _local.frozen
    finally:
        _local.frozen = prev


def set_global(value) -> None:
    """进程级固定（跨线程，例如 RecallPlanner 的线程池）。None 还原为真实时钟。"""
    global _global
    _global = None if value is None else _parse(value)
