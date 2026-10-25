"""
core/rerank.py — 交叉编码器重排（v7.14.0，默认关）

思路来自 hindsight（召回后用 cross-encoder 重排）和 MemPalace（用 LLM 从前 20 条里挑）。
接在 SearchRouter 那一步：路由多取 NYX_RERANK_DEPTH 条，交叉编码器把（问句，记忆）逐对打分，
再取原来的条数交给后面的聚合 —— 后面的流水线（影子沙、图谱、意图排序、信任门）一点不动。
只在路由里换顺序是没用的：agent 看到的 12 条基本就是路由的前 10 条，要让排在 11–40 的答案进得来才有意义。

  NYX_RERANK        off（默认）/ local
  NYX_RERANK_MODEL  默认 cross-encoder/mmarco-mMiniLMv2-L12-H384-v1（多语言，含中文；Apache-2.0）
  NYX_RERANK_DEPTH  路由先取多少条再重排，默认 50
  NYX_RERANK_MAXLEN 每对（问句 + 记忆）截到多少 token，默认 128：长记忆上耗时减半（4 线程 30 条：1.2 秒 → 0.5 秒）
  NYX_RERANK_BUDGET_MS  一次重排最多花多少毫秒，默认 2000。分批打分，到点还没打完就只在打过分的那部分里重排、
                    其余按原顺序接在后面 —— 编排层给路由 3 秒，超了路由这一路整个丢掉，比不重排还糟
  NYX_RERANK_MODE   pure：完全按交叉编码器排
                    rrf：交叉编码器的名次与原名次各投一票（RRF）
                    pinned：问句的词全部出现的那几条（路由本来就钉在最前）不动，其余按交叉编码器排
默认值（深度 50、rrf、截 128 token）是按两个基准各取一半选的、在另一半上验证的（见 CHANGELOG v7.14.0）：
LoCoMo 证据召回明显变好，LongMemEval 小幅变好，但**偏好类问题变差**、每轮召回多 0.3–1.5 秒 —— 所以默认关。
模型没装 / 加载失败 / 打分出错：原样返回，召回不受影响（失败记日志，不抛）。
模型在后台加载（Hermes 启动时、第一次用到时），**加载完之前召回照常、不重排** —— 编排层给路由的超时只有 3 秒，
在召回路径上同步加载模型，第一次召回会超时，路由这一路整个丢掉。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
RRF_K = 60

_lock = threading.Lock()
_model = None
_model_name: Optional[str] = None
_failed: Optional[str] = None
_loading = threading.Event()
_stats = {"calls": 0, "cut": 0, "last_ms": None}


def enabled() -> bool:
    return (os.environ.get("NYX_RERANK") or "off").strip().lower() in ("local", "on", "1")


def depth() -> int:
    try:
        return max(1, int(os.environ.get("NYX_RERANK_DEPTH") or 50))
    except ValueError:
        return 50


def mode() -> str:
    m = (os.environ.get("NYX_RERANK_MODE") or "rrf").strip().lower()
    return m if m in ("pure", "rrf", "pinned") else "rrf"


def max_len() -> int:
    try:
        return max(32, int(os.environ.get("NYX_RERANK_MAXLEN") or 128))
    except ValueError:
        return 128


def budget_s() -> float:
    try:
        return max(0.1, int(os.environ.get("NYX_RERANK_BUDGET_MS") or 2000) / 1000)
    except ValueError:
        return 2.0


def model_name() -> str:
    return (os.environ.get("NYX_RERANK_MODEL") or DEFAULT_MODEL).strip()


def _load():
    """加载交叉编码器（只加载一次；换了 NYX_RERANK_MODEL 重新加载）。失败返回 None。"""
    global _model, _model_name, _failed
    name = f"{model_name()}@{max_len()}"
    with _lock:
        if _model is not None and _model_name == name:
            return _model
        if _failed and _model_name == name:
            return None
        _model_name = name
        try:
            from sentence_transformers import CrossEncoder
            cache = os.environ.get("NYX_EMBED_CACHE") or os.path.join(os.path.expanduser("~"), ".cache",
                                                                      "nexsandglass", "models")
            try:
                _model = CrossEncoder(model_name(), max_length=max_len(), device="cpu", cache_folder=cache)
            except TypeError:                               # 旧版 sentence-transformers 没有 cache_folder
                _model = CrossEncoder(model_name(), max_length=max_len(), device="cpu")
            _failed = None
            return _model
        except Exception as e:
            _model, _failed = None, str(e)
            logger.warning("[rerank] 模型加载失败，召回不重排: %s", e)
            return None


def warm() -> bool:
    """同步加载（评测开始前用）。"""
    return _load() is not None


def warm_async() -> None:
    """后台加载（Hermes 启动时、第一次用到时）。重复调用无害。"""
    if not enabled() or ready() or _loading.is_set():
        return
    _loading.set()

    def run():
        try:
            _load()
        finally:
            _loading.clear()
    threading.Thread(target=run, name="nyx-rerank-warm", daemon=True).start()


def ready() -> bool:
    return _model is not None and _model_name == f"{model_name()}@{max_len()}"


def _pinned(items: list, query: str) -> set:
    """问句的词全部出现的候选（与 SearchRouter._rrf 的判据一致）。"""
    from nexsandglass.core.search_router import PIN_MIN_COVERAGE
    from nexsandglass.features.sandglass_vault import _query_tokens
    lows = {it[0]: (it[2] if len(it) > 2 else "").lower() for it in items}
    allt = {t.lower() for t in (_query_tokens(query) or ()) if t}
    toks = [t for t in allt if any(t in low for low in lows.values())]
    if not toks or len(toks) < PIN_MIN_COVERAGE * len(allt):
        return set()
    return {ln for ln, low in lows.items() if all(t in low for t in toks)}


def rerank(query: str, items: list, how: str = None) -> list:
    """items：SearchRouter 的结果 [(line, ts, text), ...]（已按原顺序）。返回重排后的列表。"""
    if not items or len(items) < 2 or not (query or "").strip():
        return items
    if not ready():
        warm_async()                     # 不在召回路径上等模型
        return items
    model = _load()
    if model is None:
        return items
    how = how or mode()
    t0, budget, scores = time.perf_counter(), budget_s(), []
    try:
        from nexsandglass.core import secret_filter
        q = secret_filter.clean(query)
        # 从原排名靠前的开始分批打分；到了时间预算就停（剩下的按原顺序接在后面）
        for i in range(0, len(items), 16):
            batch = items[i:i + 16]
            scores += [float(s) for s in model.predict(
                [(q, str(it[2] if len(it) > 2 else it[-1])[:1000]) for it in batch],
                batch_size=16, show_progress_bar=False)]
            if i + 16 < len(items) and time.perf_counter() - t0 > budget:
                _stats["cut"] += 1
                break
    except Exception as e:
        logger.warning("[rerank] 打分失败，召回不重排: %s", e)
        return items
    finally:
        _stats["calls"] += 1
        _stats["last_ms"] = round((time.perf_counter() - t0) * 1000)
    rest, items = items[len(scores):], items[:len(scores)]
    return _order(items, scores, how, query) + rest


def _order(items: list, scores: list, how: str, query: str) -> list:
    by_ce = sorted(range(len(items)), key=lambda i: -scores[i])
    if how == "pure":
        return [items[i] for i in by_ce]
    if how == "pinned":
        pins = _pinned(items, query)
        return [it for it in items if it[0] in pins] + [items[i] for i in by_ce if items[i][0] not in pins]
    ce_rank = {i: r for r, i in enumerate(by_ce)}
    fused = sorted(range(len(items)), key=lambda i: -(1.0 / (RRF_K + i + 1) + 1.0 / (RRF_K + ce_rank[i] + 1)))
    return [items[i] for i in fused]


def status() -> dict:
    return {"enabled": enabled(), "model": model_name(), "depth": depth(), "mode": mode(), "max_len": max_len(),
            "budget_ms": int(budget_s() * 1000), "loaded": ready(), "error": _failed, **_stats}
