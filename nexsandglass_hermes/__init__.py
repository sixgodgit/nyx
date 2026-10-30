"""Nyx 夜神 —— Hermes Agent 记忆提供器插件入口（v7.12）

装法（v7.15.1）：``hermes plugins install sixgodgit/nyx/hermes-plugin --enable``。仓库里的 hermes-plugin/ 是入口文件 +
plugin.yaml（python_dependencies: nyx-memory），Hermes 把依赖装进它自己的环境。老版本 Hermes（plugins install 不装依赖、
也不认 pip 入口点，如 0.19.x）：``pip install nyx-memory`` 之后 ``python3 -m nexsandglass_hermes install``（见 install.py）。
pyproject 里的入口点 ``hermes_agent.memory_providers``（``nyx`` / 旧名 ``nexsandglass``）留着，给认入口点的 Hermes 版本用。
旧部署：``nexsandglass.core.memory_provider.register`` 仍然可用。

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
        logger.warning("nyx: could not read %s (using an empty config): %s", path, e)
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


# nexsandglass.core.sandglass_paths 在 import 时读它（v7.17.0；以前写进 NEXSANDBASE_HOME 环境变量）
_BOOT_DATA_DIR: Optional[str] = None


def apply_data_dir(home: Optional[str] = None) -> str:
    """在 import nexsandglass 之前定下数据目录。已经 import 过且目录不同 → 警告（记忆插件会拒绝这个 profile）。

    不写环境变量：一个 Hermes 进程服务多个 profile，环境变量会让第一个 profile 的目录粘住整个进程和它的子进程。
    """
    global _BOOT_DATA_DIR
    target = resolve_data_dir(home)
    paths = sys.modules.get("nexsandglass.core.sandglass_paths")
    if paths is None:
        _BOOT_DATA_DIR = target
    elif os.path.abspath(getattr(paths, "_NB", target)) != target:
        logger.warning("nyx: nexsandglass was already initialised with %s; the configured data_dir=%s takes effect after a restart",
                       getattr(paths, "_NB", "?"), target)
    return target


def register(ctx) -> None:
    """Hermes 插件入口：``ctx.register_memory_provider(provider)``。"""
    apply_data_dir()
    from nexsandglass.core.memory_provider import NexSandglassProvider
    ctx.register_memory_provider(NexSandglassProvider())


__all__ = ["register", "load_config", "save_config", "resolve_data_dir", "apply_data_dir",
           "hermes_home", "config_path", "CONFIG_FILENAME", "DEFAULT_DATA_DIR"]
