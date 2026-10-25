"""hermes nyx … —— Nyx 的命令行（vault / doctor / scrub / repair / status）。

Hermes 在 nyx 是当前记忆插件时载入这个文件的 register_cli。这里只是转一下：真正的代码在 nyx-memory 里
（nexsandglass_hermes/cli.py）。
"""
try:
    from nexsandglass_hermes.cli import nexsandglass_command, nyx_command, register_cli  # noqa: F401
except ImportError as _e:
    _missing = _e

    def nyx_command(args):
        print(f"Nyx：Hermes 的环境里没有 nyx-memory（{_missing}）。"
              "运行：hermes plugins install sixgodgit/nyx/hermes-plugin --force，然后重启 Hermes")
        return 1

    def register_cli(subparser):
        subparser.set_defaults(func=nyx_command)

    nexsandglass_command = nyx_command
