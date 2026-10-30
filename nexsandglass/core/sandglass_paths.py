"""
NexSandglass 路径配置 — 单一真相来源 V2.2
===========================================
所有模块从这里获取 _NB，不再各自计算。
用法: from nexsandglass.core.sandglass_paths import _NB, _SCRIPTS, _PERSONA, ... 
"""

import os
import sys


def _boot_data_dir():
    """Hermes profile 的 nyx.json data_dir：nexsandglass_hermes.apply_data_dir 在 import nexsandglass 之前定下。

    v7.17.0 以前是写进 NEXSANDBASE_HOME 环境变量 —— 整个进程（和它起的子进程）都看得见，一个进程服务
    多个 profile 时第一个 profile 的目录粘住所有 profile。现在只在这里读一次；配了别的目录的 profile
    由记忆插件拒绝（memory_provider.unavailable_reason），不会悄悄写进这里。
    """
    nh = sys.modules.get("nexsandglass_hermes")
    return getattr(nh, "_BOOT_DATA_DIR", None) if nh is not None else None


def configured_data_dir():
    """显式配置的数据目录：NEXSANDBASE_HOME，或者 Hermes 的 data_dir；都没有返回 None（调用方用自己的默认）。"""
    return os.environ.get("NEXSANDBASE_HOME") or _boot_data_dir()


_NB = configured_data_dir() or os.path.join(os.path.expanduser("~"), ".neurobase")

# 很多模块在 import 时就把路径定死了（_NB 及其派生路径），之后改环境变量也追不回来。
# 所以测试进程在这里、在任何文件被写之前就要拦下（v7.13.9，2026-09-13 事故：测试把合成记忆写进了生产库）。
from nexsandglass.core import prodguard as _prodguard  # noqa: E402
_prodguard.check(_NB, "数据目录")
__version__ = '7.17.0'
_SCRIPTS = os.path.join(_NB, "scripts")
_PERSONA = os.path.join(_NB, "persona")
_ARCHIVE = os.path.join(_NB, "archive")

# 常用文件路径
_SANDGLASS = os.path.join(_NB, "sandglass.txt")
_SANDGLASS_DB = os.path.join(_NB, "sandglass.db")
_SANDGLASS_IDX = os.path.join(_NB, "sandglass.idx")
_SHADOW_DB = os.path.join(_NB, "shadow_sand.db")
_DECISION_PARTICLES = os.path.join(_NB, "decision_particles.txt")
_DECISION_VOCAB = os.path.join(_NB, "decision_vocab.txt")
_ECHO_WIND = os.path.join(_NB, "echo_wind.jsonl")
_EMOTION_VOCAB = os.path.join(_NB, "emotion_vocab.jsonl")
_IRON_RULES = os.path.join(_NB, "iron_rules.txt")

# 启动时必须存在的目录
_REQUIRED_DIRS = [_NB, _SCRIPTS, _PERSONA, _ARCHIVE]


def validate() -> dict:
    """启动时路径验证——创建缺失目录，返回状态报告。"""
    created = []
    existed = []
    for d in _REQUIRED_DIRS:
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
            created.append(d)
        else:
            existed.append(d)
    return {"nb": _NB, "created": created, "existed": existed, "ok": True}
