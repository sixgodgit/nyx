"""Nyx 夜神 —— Hermes 记忆插件（v7.15.3）

用 Hermes 自己的插件命令装（不需要先在系统 Python 里装任何东西）：

    hermes plugins install sixgodgit/nyx/hermes-plugin-vector --enable    # 带语义检索的本地模型

Hermes 把这个目录放到 $HERMES_HOME/plugins/nyx/，按 plugin.yaml 的 python_dependencies 把 nyx-memory
装进它自己的环境（hermes update 之后会重新应用）。然后 hermes config set memory.provider nyx。
这个文件只是入口：真正的代码在 nyx-memory 里。
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
            "Nyx：Hermes 用的 Python（%s）里没有 nyx-memory（%s）。运行：hermes plugins install "
            "sixgodgit/nyx/hermes-plugin --force（老版本 Hermes：%s -m pip install nyx-memory），然后重启 Hermes",
            sys.executable, _missing, sys.executable)
        return
    _apply_data_dir()                    # 先按当前 profile 的 nyx.json 定数据目录，import nexsandglass 之后就改不了了
    from nexsandglass.core.memory_provider import NexSandglassProvider
    # 这一行必须写在这个文件里：新版 Hermes 解析插件目录的语法树、找 register_memory_provider(...) 调用来认记忆插件
    # （plugins/memory/__init__.py 的 _defines_memory_provider），注释和文档里的字不算。认不出时
    # `hermes plugins install --enable` 只把它当普通插件启用，不设 memory.provider（v7.15.2 实测）
    ctx.register_memory_provider(NexSandglassProvider())
