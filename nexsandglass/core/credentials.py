"""
core/credentials.py — 读外部服务的密钥（v7.15.2）

在 Hermes 里通过 agent.secret_scope.get_secret 读：它按当前 profile 解析 —— 一个进程里跑着多个 profile 时，
直接读 os.environ 可能拿到别的 profile 的 key（Hermes 插件目录规则 11）。没有绑定 profile 作用域、Hermes 按规则
拒绝（UnscopedSecretError）时按「没有」处理，不退回去读环境变量。
不在 Hermes 里（MCP、单独运行）就读环境变量。

只走这两条路：不读 .env、keys.json、别的工具的登录文件。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def get(name: str, default: str = "") -> str:
    try:
        from agent.secret_scope import get_secret
    except Exception:
        return os.environ.get(name) or default
    try:
        return get_secret(name) or default
    except Exception as e:
        logger.debug("[credentials] Hermes 没有给出 %s（%s）：按没有处理", name, type(e).__name__)
        return default
