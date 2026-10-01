"""Nyx 夜神 —— Hermes Agent 记忆提供器插件入口（v7.12）

三种装法，同一份代码：

1. pip（推荐）：``pip install nyx-memory`` → Hermes 经 entry point
   ``hermes_agent.memory_providers`` 自动发现（名字 ``nyx``，旧名 ``nexsandglass`` 兼容）。
   然后在 Hermes 配置里 ``memory.provider: nyx``。
2. 目录插件：把本目录拷到 ``$HERMES_HOME/plugins/nyx/``（仍需 pip 装好 nyx-memory）。
3. 旧部署：``nexsandglass.core.memory_provider.register`` 仍然可用。

这个模块**顶层只用标准库**：nexsandglass 的数据目录在第一次 import 时就定下来了
（``sandglass_paths._NB``），所以必须先把 ``data_dir`` 配置落到 ``NEXSANDBASE_HOME``，
再 import nexsandglass。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "nyx.json"
DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), ".neurobase")


def hermes_home(explicit: Optional[str] = None) -> Optional[str]:
    """当前 Hermes profile 的 home。没有 Hermes（独立运行 / 测试）→ None。"""
    if explicit:
        return str(explicit)
    try:
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:
        return os.environ.get("HERMES_HOME") or None


def config_path(home: Optional[str] = None) -> Optional[str]:
    home = hermes_home(home)
    return os.path.join(home, CONFIG_FILENAME) if home else None


def load_config(home: Optional[str] = None) -> Dict[str, Any]:
    path = config_path(home)
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("nyx: 读取 %s 失败（按空配置处理）: %s", path, e)
        return {}


def save_config(values: Dict[str, Any], home: Optional[str] = None) -> Optional[str]:
    """合并写入 ``$HERMES_HOME/nyx.json``（原子替换）。返回写入路径。"""
    path = config_path(home)
    if not path:
        return None
    data = load_config(home)
    data.update({k: v for k, v in (values or {}).items() if v is not None})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def resolve_data_dir(home: Optional[str] = None) -> str:
    """nyx 记忆目录：``NEXSANDBASE_HOME`` > 配置 ``data_dir`` > ``~/.neurobase``。

    默认与 nyx 的 MCP / CLI 共用同一份记忆 —— 一个人一份记忆，不按 agent 切开。
    """
    env = os.environ.get("NEXSANDBASE_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    d = load_config(home).get("data_dir")
    if d:
        return os.path.abspath(os.path.expanduser(str(d)))
    return DEFAULT_DATA_DIR


def apply_data_dir(home: Optional[str] = None) -> str:
    """在 import nexsandglass 之前把数据目录落到环境变量。已经 import 过且目录不同 → 警告。"""
    target = resolve_data_dir(home)
    paths = sys.modules.get("nexsandglass.core.sandglass_paths")
    if paths is None:
        os.environ["NEXSANDBASE_HOME"] = target
    elif os.path.abspath(getattr(paths, "_NB", target)) != target:
        logger.warning("nyx: nexsandglass 已用 %s 初始化，配置的 data_dir=%s 需要重启才生效",
                       getattr(paths, "_NB", "?"), target)
    return target


def register(ctx) -> None:
    """Hermes 插件入口：``ctx.register_memory_provider(provider)``。"""
    apply_data_dir()
    from nexsandglass.core.memory_provider import NexSandglassProvider
    ctx.register_memory_provider(NexSandglassProvider())


__all__ = ["register", "load_config", "save_config", "resolve_data_dir", "apply_data_dir",
           "hermes_home", "config_path", "CONFIG_FILENAME", "DEFAULT_DATA_DIR"]
