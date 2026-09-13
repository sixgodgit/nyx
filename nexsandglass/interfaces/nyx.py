"""Nyx 夜之感知层 —— Déjà Vu 的 Hermes 适配器。

本文件**不含任何算法实现**。Veil / Mist / 分词 / 寻回全在
``nexsandglass.dejavu`` 包里，这里只做两件事：

1. 把模块级路径（``NEXSANDBASE_HOME``）解析成显式 ``storage_dir``
2. 把新的 dataclass 结果转回调用方沿用的 dict 形状

保留旧函数名与旧返回结构，是为了让 ``sandglass_mcp`` / ``nyx_server`` /
``runtime.orchestrator`` 三个调用方零改动。新代码请直接用::

    from nexsandglass.dejavu import DejaVu
    dv = DejaVu("/path/to/storage")
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from nexsandglass.dejavu import DejaVu

logger = logging.getLogger(__name__)

# ── 存储路径（惰性解析，避免 import 时固化）────────────────

_DEFAULT_NB = os.path.join(os.path.expanduser("~"), ".nyx")


def _default_nb() -> str:
    """默认数据目录。

    仅作为**未配置环境变量时**的兜底；调用方应显式设置
    ``NEXSANDBASE_HOME``。为兼容既有部署，该名字沿用旧约定。
    """
    return os.environ.get("NEXSANDBASE_HOME") or _DEFAULT_NB


_inst: Optional[DejaVu] = None
_inst_dir: Optional[str] = None
_lock = threading.RLock()


def _instance() -> DejaVu:
    """取模块级单例。数据目录变化时自动重建（含持久化旧实例）。"""
    global _inst, _inst_dir
    with _lock:
        want = os.path.abspath(_default_nb())
        if _inst is None or _inst_dir != want:
            if _inst is not None:
                try:
                    _inst.close()
                except Exception:
                    pass
            _inst = DejaVu(want)
            _inst_dir = want
        return _inst


# ── 公共 API（保持旧签名与旧返回结构）──────────────────────

def nyx_kindle(line_num, moment: str, text: str) -> int:
    """落沙时调用：Veil 留印 + 铭刻 phantom。

    ``line_num`` 现在是**任意引用**——适配层把它转成字符串交给
    ``DejaVu.imprint(key=...)``。Déjà Vu 不再假设它是行号。
    """
    try:
        return _instance().imprint(str(line_num), text, ts=moment)
    except Exception:
        logger.warning("nyx: kindle failed", exc_info=True)
        return 0


def nyx_sense(text: str) -> dict:
    """感知熟悉度。返回旧 dict 形状（供 orchestrator / MCP 消费）。"""
    try:
        r = _instance().sense(text)
    except Exception:
        logger.warning("nyx: sense failed", exc_info=True)
        return {"familiar_ratio": 0.0, "known_tokens": [], "unknown_tokens": [],
                "total": 0, "familiar": False, "reason": "sense error"}
    return {
        "familiar_ratio": r.score,
        "known_tokens": r.phantoms,
        "unknown_tokens": r.unknown,
        "total": r.total,
        "familiar": r.familiar,
        "reason": r.reason,
    }


def nyx_hunt(query: str, limit: int = 5) -> dict:
    """追猎幽灵。返回旧 dict 形状。"""
    try:
        h = _instance().hunt_detailed(query, limit=limit)
    except Exception:
        logger.warning("nyx: hunt failed", exc_info=True)
        return {"hunted": False, "conviction": 0.0, "phantoms": []}
    return {
        "hunted": h.hunted,
        "conviction": h.conviction,
        "phantoms": [
            {
                "token": p.token,
                "born": p.born,
                "last_spotted": p.last_spotted,
                "sightings": p.sightings,
                "whisper": p.snippet,
            }
            for p in h.phantoms
        ],
    }


def nyx_rest() -> None:
    """安息——持久化 Veil。"""
    try:
        _instance().persist()
    except Exception:
        logger.warning("nyx: rest failed", exc_info=True)


def nyx_gaze() -> dict:
    """凝视——系统统计。"""
    try:
        g = _instance().gaze()
    except Exception:
        logger.warning("nyx: gaze failed", exc_info=True)
        return {"veil_items": 0, "mist_total": 0, "restless": []}
    return {
        "veil_items": g.veil_items,
        "veil_bits": g.veil_bits,
        "veil_hashes": g.veil_hashes,
        "veil_fill_ratio": g.veil_fill_ratio,
        "mist_total": g.mist_total,
        "restless": g.restless,
        "storage_dir": g.storage_dir,
    }


def nyx_forget(token: str) -> dict:
    """遗忘某个 token。返回旧 dict 形状。"""
    try:
        n = _instance().forget(token)
    except Exception:
        logger.warning("nyx: forget failed", exc_info=True)
        return {"forgotten": 0, "token": token}
    return {"forgotten": n, "token": token}


def nyx_cleanup(days: int = 90) -> dict:
    """清理久未出现的幽灵。"""
    try:
        n = _instance().cleanup(days)
    except Exception:
        logger.warning("nyx: cleanup failed", exc_info=True)
        return {"removed": 0, "days": days}
    return {"removed": n, "days": days}


def nyx_reindex() -> dict:
    """从 Mist 重建 Veil。"""
    try:
        n = _instance().reindex()
    except Exception:
        logger.warning("nyx: reindex failed", exc_info=True)
        return {"rebuilt": 0}
    return {"rebuilt": n}


# ── 兼容别名（旧名字 → 新概念）────────────────────────────

#: 旧代码可能直接引用的路径常量（保持可用，惰性求值）
def __getattr__(name: str):
    if name in ("_VEIL_PATH",):
        return os.path.join(_default_nb(), "veil.bin")
    if name in ("_MIST_PATH",):
        return os.path.join(_default_nb(), "mist.db")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
