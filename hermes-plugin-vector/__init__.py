"""Nyx — Hermes memory provider (v7.15.3)

Install it with Hermes's own plugin command (nothing needs to be installed in the system Python first):

    hermes plugins install sixgodgit/nyx/hermes-plugin-vector --enable    # with the local semantic-search model

Hermes puts this directory at $HERMES_HOME/plugins/nyx/ and installs nyx-memory into its own environment from
python_dependencies in plugin.yaml (re-applied after hermes update), then hermes config set memory.provider nyx.
This file is only the entry point: the real code is in nyx-memory.
"""
import logging
import sys

try:
    from nexsandglass_hermes import apply_data_dir as _apply_data_dir
    _missing = None
except ImportError as e:
    _apply_data_dir, _missing = None, e


def register(ctx):
    if _missing is not None:
        logging.getLogger("nyx").error(
            "Nyx: nyx-memory is not installed in the Python Hermes uses (%s; %s). Run: hermes plugins install "
            "sixgodgit/nyx/hermes-plugin --force (older Hermes: %s -m pip install nyx-memory), then restart Hermes",
            sys.executable, _missing, sys.executable)
        return
    _apply_data_dir()                    # 先按当前 profile 的 nyx.json 定数据目录，import nexsandglass 之后就改不了了
    from nexsandglass.core.memory_provider import NexSandglassProvider
    # 这一行必须写在这个文件里：新版 Hermes 解析插件目录的语法树、找 register_memory_provider(...) 调用来认记忆插件
    # （plugins/memory/__init__.py 的 _defines_memory_provider），注释和文档里的字不算。认不出时
    # `hermes plugins install --enable` 只把它当普通插件启用，不设 memory.provider（v7.15.2 实测）
    ctx.register_memory_provider(NexSandglassProvider())
