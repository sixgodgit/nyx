"""
测试进程不许碰临时目录以外的记忆库（v7.13.9）。

2026-09-13 生产事故：测试把 107 条合成记忆（sender=golden / test / e2e / mcp）写进了生产中枢，
76 条真实记忆被覆盖，agent 把「用户喜欢喝拿铁」「预算 2000 欧」当成真记忆说了出来。
v7.13.8 时复现：不设 NEXSANDBASE_HOME 直接跑测试，真实数据目录的日志里照样多出
「e2e | 用户喜欢手冲咖啡」这类记录 —— 只要「测试能写生产」这件事还可能，就迟早会再发生，
而且发生时没有任何东西会喊一声。

两道防线：
  1. tests/conftest.py：测试会话一开始、任何模块 import 之前，把数据目录强制指到这次会话的临时目录
     （环境里原来设了什么都覆盖 —— 事故现场 NEXSANDBASE_HOME 就是设了、就指着生产）
  2. 这里：测试进程里，所有开库（core/sqlite_open.connect）和日志追加（memid.allocate）
     碰到临时目录以外的路径就直接报错。判据是「测试只该用临时目录」，不是路径长什么样、
     也不是某个环境变量有没有设。只在 pytest 进程里生效（见 in_test），生产不受影响、不会误报

显式豁免：NYX_ALLOW_PROD_DB=1（要打字，意外做不到）。
"""
from __future__ import annotations

import os
import tempfile


def _under_temp(path: str) -> bool:
    real = os.path.realpath(os.path.abspath(path))
    roots = {os.path.realpath(tempfile.gettempdir())}
    if os.environ.get("TMPDIR"):
        roots.add(os.path.realpath(os.environ["TMPDIR"]))
    return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def in_test() -> bool:
    """测试进程？测试执行期间 pytest 设 PYTEST_CURRENT_TEST；收集阶段（import 测试模块、很多路径在这时被定死）
    还没有它，靠 PYTEST_VERSION（pytest ≥ 8.2 在会话开始时设）。更老的 pytest：已加载且入口就是 pytest。
    不单凭「_pytest 已加载」判断 —— 生产进程万一间接 import 了它，守卫会把真实数据挡在门外。"""
    import sys
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PYTEST_VERSION"):
        return True
    entry = sys.argv[0] if sys.argv else ""
    return "_pytest" in sys.modules and ("pytest" in entry or "py.test" in entry)


def check(path: str, what: str = "记忆库") -> None:
    """测试进程要打开 / 写入临时目录以外的 path → RuntimeError。非测试进程什么都不做。"""
    if not in_test() or os.environ.get("NYX_ALLOW_PROD_DB") == "1":
        return
    if not path or path == ":memory:" or _under_temp(path):
        return
    raise RuntimeError(
        f"测试进程试图打开临时目录以外的{what}：{path}\n"
        f"测试只能用自己建在临时目录里的库（2026-09-13 事故：测试把合成记忆写进了生产库）。"
        f"确实需要的话设 NYX_ALLOW_PROD_DB=1")
