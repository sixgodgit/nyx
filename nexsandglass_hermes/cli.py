"""
hermes nyx … —— Nyx 的命令行，经 Hermes 运行（v7.15.2）

    hermes nyx vault list              # 密钥保险箱：status / list / show v3 [--copy] / delete / gc / rekey / audit
    hermes nyx vault show v3           # 原文打到终端（不经过任何模型、聊天平台、会话记录）
    hermes nyx doctor                  # 自检
    hermes nyx scrub [--apply]         # 已经存下的密钥 / 密码：找出来、原地隐去
    hermes nyx repair                  # 中枢对不上日志时：按内容体检与修复
    hermes nyx status                  # 版本、数据目录

为什么要有它：新版 Hermes 把 nyx-memory 装在它自己的隔离环境里，系统 Python 里没有 nyx，
`python3 -m nexsandglass.vault` 在那里用不了。Hermes 在 nyx 是当前记忆插件时，从插件目录的 cli.py
载入 register_cli（plugins/memory/__init__.py 的 discover_plugin_cli_commands），注册成 `hermes nyx`。

每次运行 hermes 命令都会载入这个文件来搭 argparse：顶层只用标准库，真正干活的模块在子命令运行时才 import
（import nexsandglass 会把数据目录定死 —— 必须先按当前 profile 的 nyx.json 把 data_dir 落到环境变量）。
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys

TOOLS = {
    "vault": ("nexsandglass.vault", "密钥保险箱：status / list / show v3 / delete / gc / rekey / audit"),
    "doctor": ("nexsandglass.doctor", "自检：每一项 ✅ / ❌ + 原因"),
    "scrub": ("nexsandglass.scrub", "已经存下来的密钥 / 密码：找出来、原地隐去"),
    "repair": ("nexsandglass.repair", "中枢对不上日志时：按内容体检与修复"),
}


def register_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="nyx_command", metavar="{" + ",".join([*TOOLS, "status"]) + "}")
    for name, (_, help_) in TOOLS.items():
        # 后面的参数（含 --json、-h）原样交给那个工具自己的 argparse。光用 REMAINDER 不够：紧跟在子命令后面的
        # --json 会被这一层当成未知选项、一路冒到 hermes 顶层报 unrecognized arguments（实测）。
        # 所以这一层不认任何选项：前缀字符设成一个不会出现的字符，-h 也就不是这里的帮助了
        p = subs.add_parser(name, help=help_, add_help=False, prefix_chars="\x01")
        p.add_argument("nyx_args", nargs=argparse.REMAINDER)
    subs.add_parser("status", help="版本、数据目录、保险箱")
    subparser.set_defaults(func=nyx_command)


def nyx_command(args) -> int:
    name = getattr(args, "nyx_command", None)
    if not name:
        print("用法：hermes nyx {" + ",".join([*TOOLS, "status"]) + "} …（hermes nyx <子命令> -h 看选项）")
        return 2
    os.environ["NYX_VIA_HERMES"] = "1"        # 工具输出里的「下一步」提示写成 hermes nyx …
    import nexsandglass_hermes as nh
    data_dir = nh.apply_data_dir()             # 当前 Hermes profile 的 nyx.json 里的 data_dir
    cfg = nh.load_config()
    for key, env in (("vault", "NYX_VAULT"), ("vault_reveal", "NYX_VAULT_REVEAL"), ("vault_kinds", "NYX_VAULT_KINDS")):
        if cfg.get(key) and not os.environ.get(env):
            os.environ[env] = str(cfg[key])    # 与 memory_provider._load_settings 一致：环境变量 > nyx.json
    if name == "status":
        from nexsandglass.core.sandglass_paths import __version__
        print(f"nyx-memory {__version__} · Python {sys.executable}\n数据目录 {data_dir}")
        from nexsandglass import vault
        return vault.main(["status"])
    mod = importlib.import_module(TOOLS[name][0])
    rc = mod.main(list(getattr(args, "nyx_args", None) or []))
    return rc if isinstance(rc, int) else 0


nexsandglass_command = nyx_command             # memory.provider 写的是旧名 nexsandglass 时 Hermes 找这个名字
