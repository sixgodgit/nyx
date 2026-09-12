"""
NeuroBase Sandglass — 插件源码备份 + 读取工具
==============================================
V2.4.0: 去掉DPAPI，明文存储。
部署位置：
  - plugins/sandglass/__init__.py  ← Gateway 插件
  - .neurobase/scripts/sandglass.py ← vault 备份

用法：
  from nexsandglass.core.sandglass import read, count
  read(10)   # 读取最近10条
  count()    # 总行数
"""
import logging
import os
from datetime import datetime
from nexsandglass.core.sandglass_paths import _NB

logger = logging.getLogger(__name__)

_SANDGLASS = os.path.join(_NB, "sandglass.txt")
_ERRFLAG = os.path.join(_NB, ".sandglass_error")


# ── 插件核心 ──

def _on_message(event, **_kw) -> None:
    """pre_gateway_dispatch 钩子——所有平台消息到达时落沙。"""
    try:
        os.makedirs(os.path.dirname(_SANDGLASS), exist_ok=True)
        sender = getattr(event.source, "user_id", "?") or "?"
        text = getattr(event, "text", "") or "(media)"
        # 走 ID 中枢，不再直写（同 interfaces/plugin.py）
        from nexsandglass.core.sandglass_log import log_message
        log_message(text, sender=sender)
    except Exception:
        logger.exception("sandglass: FAILED")
        try:
            with open(_ERRFLAG, "w") as f:
                f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass


def register(ctx) -> None:
    """注册消息钩子。与 interfaces/plugin.py 共用同一个钩子守卫 ——
    这两份插件是同一段逻辑的两个副本，谁先注册谁生效，绝不能都挂上去。"""
    from nexsandglass.core.sandglass_log import claim_hook
    if not claim_hook("pre_gateway_dispatch"):
        logger.warning("sandglass: pre_gateway_dispatch 已被认领，本次注册忽略")
        return
    ctx.register_hook("pre_gateway_dispatch", _on_message)


# ── 读取工具 ──

def read(limit: int = 10) -> list:
    """读取最近 N 条沙漏消息。返回 [(时间戳, 发送人, 明文), ...]"""
    path = _SANDGLASS
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()[-limit:]
    results = []
    for line in lines:
        line = line.strip()
        if not line or " | " not in line:
            continue
        parts = line.split(" | ", 2)
        if len(parts) != 3:
            continue
        results.append((parts[0], parts[1], parts[2]))
    return results


def count() -> int:
    """沙漏总行数。"""
    path = _SANDGLASS
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for _ in f)
