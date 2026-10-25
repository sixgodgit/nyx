"""Nyx 夜神 —— Hermes 记忆插件（v7.15.1）

用 Hermes 自己的插件命令装（不需要先在系统 Python 里装任何东西）：

    hermes plugins install sixgodgit/nyx/hermes-plugin-vector --enable    # 带语义检索的本地模型

Hermes 把这个目录放到 $HERMES_HOME/plugins/nyx/，按 plugin.yaml 的 python_dependencies 把 nyx-memory
装进它自己的环境（hermes update 之后会重新应用），--enable 设 memory.provider: nyx。
这个文件只是入口：真正的代码在 nyx-memory 里（nexsandglass_hermes.register → ctx.register_memory_provider）。
"""
import logging
import sys

try:
    from nexsandglass_hermes import register as _register
    _missing = None
except ImportError as e:
    _register, _missing = None, e


def register(ctx):
    if _register is None:
        logging.getLogger("nyx").error(
            "Nyx：Hermes 用的 Python（%s）里没有 nyx-memory（%s）。运行：hermes plugins install "
            "sixgodgit/nyx/hermes-plugin --force（老版本 Hermes：%s -m pip install nyx-memory），然后重启 Hermes",
            sys.executable, _missing, sys.executable)
        return
    _register(ctx)
