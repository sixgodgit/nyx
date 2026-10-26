"""hermes nyx … — Nyx's command line (vault / doctor / scrub / repair / status).

Hermes loads register_cli from this file when nyx is the active memory provider. It only forwards: the real code
is in nyx-memory (nexsandglass_hermes/cli.py).
"""
try:
    from nexsandglass_hermes.cli import nexsandglass_command, nyx_command, register_cli  # noqa: F401
except ImportError as _e:
    _missing = _e

    def nyx_command(args):
        print(f"Nyx: nyx-memory is not installed in Hermes's environment ({_missing}). "
              "Run: hermes plugins install sixgodgit/nyx/hermes-plugin --force, then restart Hermes")
        return 1

    def register_cli(subparser):
        subparser.set_defaults(func=nyx_command)

    nexsandglass_command = nyx_command
