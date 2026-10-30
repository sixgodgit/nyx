"""
core/settings.py — 按 profile 生效的设置（v7.17.0）

Hermes 的一个进程（网关、桌面端）同时服务好几个 profile，每个 profile 有自己的 nyx.json。v7.17.0 以前，
记忆插件把 nyx.json 里的设置（l3_llm、vault、嵌入地址……）复制进 os.environ —— 第一个 profile 的设置就粘住了
整个进程：A 打开了 l3_llm，B 的记忆片段也会被发给 DeepSeek / OpenRouter（Hermes 评审指出）。

现在设置放在记忆插件实例上，每次 Hermes 调插件时用 ``scope()`` 绑到当前上下文（contextvars：只对这次调用、
以及它用 ``spawn_context_thread`` 起的后台线程可见）。读设置的地方一律用 ``get()``：

    from nexsandglass.core import settings
    if settings.get("NYX_L3_LLM") in ("1", "on"): ...

优先级不变：进程环境变量（运维显式设的）> 当前 profile 的 nyx.json > 默认值。
不在 Hermes 里（MCP、命令行、Python）时没有 scope，就是原来的环境变量。
"""
from __future__ import annotations

import contextlib
import contextvars
import os
from typing import Iterator, Mapping, Optional

_SCOPE: contextvars.ContextVar[Optional[Mapping[str, str]]] = contextvars.ContextVar("nyx_settings", default=None)


def get(name: str, default: str = "") -> str:
    """环境变量（非空）> 当前 scope > default。"""
    v = os.environ.get(name)
    if v:
        return v
    scope = _SCOPE.get()
    if scope:
        v = scope.get(name)
        if v:
            return str(v)
    return default


@contextlib.contextmanager
def scope(values: Optional[Mapping[str, str]]) -> Iterator[None]:
    """在这段代码（以及从这里带上下文起的线程）里，``get()`` 读到 *values*。"""
    token = _SCOPE.set(dict(values) if values else None)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def current() -> dict:
    return dict(_SCOPE.get() or {})
