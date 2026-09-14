"""NexSandglass 评测框架（v7.0）—— formation P/R、temporal accuracy、token utility、p95 latency。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from nexsandglass.runtime.promotion import PromotionEngine
from nexsandglass.runtime.orchestrator import get_orchestrator

logger = logging.getLogger(__name__)


@dataclass
class EvalReport:
    """评测结果汇总。"""
    formation_precision: float = 0.0
    formation_recall: float = 0.0
    temporal_accuracy: float = 0.0
    token_utility: float = 0.0
    p95_latency_ms: float = 0.0
    samples: int = 0
    detail: dict = field(default_factory=dict)


def eval_formation(golden: list[tuple[str, str]]) -> dict:
    """formation P/R：golden 是 [(text, 应晋升?)]。

    positive = 应晋升；PromotionEngine 判定 = 实际晋升。
    """
    eng = PromotionEngine(use_llm=False)
    tp = fp = fn = tn = 0
    for text, should_promote in golden:
        cand = eng.observe(text)
        actual = cand.disposition == "promote"
        if should_promote and actual:
            tp += 1
        elif should_promote and not actual:
            fn += 1
        elif not should_promote and actual:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 3), "recall": round(recall, 3)}


def eval_temporal(pairs: list[tuple]) -> float:
    """temporal accuracy：current_only 是否正确返回最新值。

    pairs: [(subject, predicate, 期望当前值), ...]
    """
    from nexsandglass.features.temporal_facts import get_current
    correct = 0
    for subject, predicate, expected in pairs:
        cur = get_current(subject, predicate)
        if cur and cur[0]["object"] == expected:
            correct += 1
    return correct / len(pairs) if pairs else 0.0


def eval_token_utility(queries: list[str], budget: int = 500) -> float:
    """token utility：召回内容中"有效相关"占比。用 recall 返回的 est_tokens 与信息量。
    """
    orch = get_orchestrator()
    total_util = 0.0
    for q in queries:
        mc = orch.recall(q, token_budget=budget)
        if not mc.strings:
            continue
        # 相关度近似：query 关键词在结果中出现的比例
        q_tokens = set(q) - set("的了是在我你他她它们")
        hits = 0
        for s in mc.strings:
            if any(t in s for t in q_tokens):
                hits += 1
        total_util += hits / len(mc.strings)
    return round(total_util / len(queries), 3) if queries else 0.0


def eval_p95_latency(fn, iterations: int = 10, *args, **kwargs) -> float:
    """p95 latency（ms）。"""
    latencies = []
    for _ in range(iterations):
        t0 = time.time()
        try:
            fn(*args, **kwargs)
        except Exception:
            pass
        latencies.append((time.time() - t0) * 1000)
    latencies.sort()
    idx = int(len(latencies) * 0.95) - 1
    return round(latencies[max(idx, 0)], 1)


def run_eval(
    formation_golden: list[tuple[str, str]] | None = None,
    temporal_pairs: list[tuple] | None = None,
    token_queries: list[str] | None = None,
    latency_fn=None,
    latency_iterations: int = 10,
) -> EvalReport:
    """运行完整评测。"""
    report = EvalReport()
    if formation_golden:
        f = eval_formation(formation_golden)
        report.formation_precision = f["precision"]
        report.formation_recall = f["recall"]
        report.detail["formation"] = f
    if temporal_pairs:
        report.temporal_accuracy = eval_temporal(temporal_pairs)
        report.detail["temporal_pairs"] = len(temporal_pairs)
    if token_queries:
        report.token_utility = eval_token_utility(token_queries)
        report.detail["token_queries"] = len(token_queries)
    if latency_fn:
        report.p95_latency_ms = eval_p95_latency(latency_fn, latency_iterations)
        report.detail["latency_iterations"] = latency_iterations
    report.samples = max(
        len(formation_golden or []),
        len(temporal_pairs or []),
        len(token_queries or []),
    )
    return report
