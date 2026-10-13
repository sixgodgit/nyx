"""测试会话的数据目录隔离（v7.13.9）。

pytest 先 import 这个文件，再 import 任何测试模块 —— 所以这里设的环境变量，在 nexsandglass 各模块
import 时把路径定死之前就已生效。**无条件覆盖**：2026-09-13 事故现场 NEXSANDBASE_HOME 就是设了、
就指着生产库，测试把 107 条合成记忆写进了生产中枢。v7.13.8 时复现：不设它直接跑测试，
真实数据目录的日志里多出「e2e | 用户喜欢手冲咖啡」。

第二道防线在 nexsandglass/core/prodguard.py：测试进程碰临时目录以外的库直接报错。
"""
import os
import tempfile

_SESSION = tempfile.mkdtemp(prefix="nyx-pytest-")
os.environ["NEXSANDBASE_HOME"] = os.path.join(_SESSION, "nb")
os.environ["HERMES_HOME"] = os.path.join(_SESSION, "hermes")
os.environ.pop("NYX_ALLOW_PROD_DB", None)
