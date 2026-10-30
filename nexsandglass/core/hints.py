"""
core/hints.py — 提示主人运行哪条命令（v7.15.2）

新版 Hermes 把 nyx-memory 装在它自己的隔离环境里，`python3 -m nexsandglass.vault …` 在那里用不了 ——
系统 Python 里根本没有 nyx。那里的入口是 `hermes nyx …`（插件目录里的 cli.py）。
所以给主人的提示（agent 转告的「请运行…」、doctor 的「下一步」）按所在的环境给：

  - 在 Hermes 里（agent 进程；或者经 `hermes nyx` 进来，它在 settings scope 里设 NYX_VIA_HERMES=1）→ `hermes nyx vault show v3`
  - 其它（单独运行、MCP）→ `python3 -m nexsandglass.vault show v3`
"""
from __future__ import annotations

import sys


def via_hermes() -> bool:
    from nexsandglass.core import settings
    return settings.get("NYX_VIA_HERMES") == "1" or "hermes_cli" in sys.modules


def cmd(tool: str, rest: str = "") -> str:
    """tool：vault / doctor / scrub / repair。rest：后面的参数。"""
    base = f"hermes nyx {tool}" if via_hermes() else f"python3 -m nexsandglass.{tool}"
    return f"{base} {rest}".rstrip()
