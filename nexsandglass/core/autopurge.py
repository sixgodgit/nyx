"""
core/autopurge.py — 隔离区到期自动清理（v7.22.0）

遗忘先进隔离区，保留期（默认 30 天）内能原样救回；到期之后应该真的删掉。以前到期这一步要人手动跑
（`erasure.purge(apply=True)` 或者自己配 cron）—— 大多数人不会配，于是「忘掉」的东西无限期留在磁盘上，
只有 doctor 会报 pending_purge ❌。

现在 Hermes 的记忆插件在后台预热时顺手做：距上次自动清理超过一天，就清掉**已经过了保留期**的
（没到期的一条都不动，照样能救回），由它们得出的素材文件一起删（v7.21.0 的 media）。
只在主会话里做（子 agent、定时任务这些只读上下文不做）。上次清理的时间记在数据目录的 auto_purge.json，
doctor 的 pending_purge 一项会带上它。

关掉：NYX_AUTO_PURGE=off（Hermes：nyx.json 里 auto_purge: off）。手动清理照旧可用。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

INTERVAL = timedelta(hours=24)
_OFF = ("off", "0", "false", "no")


def _path() -> str:
    from nexsandglass.core.sandglass_paths import _NB
    return os.path.join(_NB, "auto_purge.json")


def enabled() -> bool:
    from nexsandglass.core import settings
    return (settings.get("NYX_AUTO_PURGE") or "on").strip().lower() not in _OFF


def last_run() -> Optional[dict]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def run_if_due(force: bool = False, now: Optional[datetime] = None) -> Optional[dict]:
    """到时间了（或 force）就清理已过保留期的；没到时间返回 None。"""
    now = now or datetime.now()
    last = last_run() or {}
    try:
        prev = datetime.fromisoformat(str(last.get("at") or ""))
    except ValueError:
        prev = None
    if not force and prev is not None and now - prev < INTERVAL:
        return None
    from nexsandglass.runtime import facade
    rep = facade.purge_forgotten(apply=True)          # 只清过了保留期的（erasure.purge 的默认）
    state = {"at": now.isoformat(timespec="seconds"), "ok": bool(rep.get("ok")),
             "purged": int(rep.get("purged") or 0), "media_erased": int(rep.get("media_erased") or 0)}
    if not rep.get("ok"):
        state["error"] = str(rep.get("error") or "")[:300]
    from nexsandglass.core.fsutil import atomic_write
    with atomic_write(_path()) as f:
        json.dump(state, f, ensure_ascii=False)
    if state["purged"] or state["media_erased"]:
        logger.info("Nyx: erased %d forgotten memories past their retention period (%d media files)",
                    state["purged"], state["media_erased"])
    return state
