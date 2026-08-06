"""NeuroBase Sandglass Plugin — 全平台消息拦截落沙。V2.4.0: 明文存储。"""
import logging
import os
from nexsandglass.core.sandglass_paths import _NB
from datetime import datetime

logger = logging.getLogger(__name__)

_SANDGLASS = os.path.join(_NB, "sandglass.txt")
_ERRFLAG = os.path.join(_NB, ".sandglass_error")


def _on_message(event, **_kw) -> None:
    """pre_gateway_dispatch 钩子——所有平台消息到达时落沙。"""
    try:
        os.makedirs(os.path.dirname(_SANDGLASS), exist_ok=True)
        sender = getattr(event.source, "user_id", "") or ""
        if not sender: return  # 只记用户消息——AI回复不落沙
        text = getattr(event, "text", "") or "(media)"
        with open(_SANDGLASS, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {sender} | {text}\n")
        # ── 决策粒子记录（激活幽灵决策/偏移率数据源）──
        try:
            from nexsandglass.features.decision_particles import _is_decision, log as dp_log
            if _is_decision(text):
                dp_log(question=text, choice=text, ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), chain=[])
        except Exception as _de:
            logger.debug("sandglass: decision record skipped: %s", _de)
        # ── 情绪记录（为回音折/情绪熵提供累积数据源）──
        try:
            import json as _json
            from nexsandglass.core.emotion_vocab import detect as _ed
            _mood = _ed(text).get("mood", "")
            if _mood:
                _elog = os.path.join(os.path.dirname(_SANDGLASS), "emotion_log.jsonl")
                with open(_elog, "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps(
                        {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "mood": _mood},
                        ensure_ascii=False,
                    ) + "\n")
        except Exception:
            pass
        # ── engram 认知记忆分类（四类记忆加工）──
        try:
            from nexsandglass.engram.bridge import ingest as _engram_ingest
            _engram_ingest(text)
        except Exception:
            pass
    except Exception:
        logger.exception("sandglass: FAILED")
        try:
            with open(_ERRFLAG, "w") as f:
                f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _on_message)
