"""暂停记录（v7.20.0）—— /nyx pause 之后的对话不写进 Nyx，直到 /nyx resume

聊私事、帮别人问东西、试一段不想留下的对话时用。暂停的只是被动记录（每轮对话、会话结束的补记、子任务结果、
内置记忆的镜像）；你明确让 agent 做的（记下一条事实、忘掉某件事）照常执行，召回也照常。
状态是数据目录里的一个文件：重启 Hermes 之后仍然是暂停的，换一个会话也一样。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional


def _path() -> str:
    from nexsandglass.core.sandglass_paths import _NB
    return os.path.join(_NB, "recording_paused.json")


def paused() -> Optional[dict]:
    """暂停着就返回 {since, by}，否则 None。"""
    p = _path()
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f) or {"since": ""}
    except Exception:
        return {"since": ""}                # 文件坏了也按暂停算：宁可少记，不可多记


def pause(by: str = "") -> dict:
    from nexsandglass.core.fsutil import atomic_write
    p = _path()
    state = paused() or {"since": datetime.now().isoformat(timespec="seconds"), "by": by}
    with atomic_write(p) as f:
        json.dump(state, f, ensure_ascii=False)
    return state


def resume() -> bool:
    """恢复记录。原来是暂停的返回 True。"""
    try:
        os.remove(_path())
        return True
    except FileNotFoundError:
        return False
