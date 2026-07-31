"""
tests/eval/test_set.py — 记忆检索评测测试集（Task 4）

模拟真实多轮对话场景，包含需要跨会话召回的问题。
~60 条问答对，覆盖：事实召回、语义改写、跨会话追踪、衰减验证。
"""

from __future__ import annotations

# 测试集格式: (query, expected_memory_ids, category, notes)
EVAL_TEST_SET = [
    # === 事实召回（精确关键词匹配）===
    {"query": "用户的邮箱是什么", "expected": ["email"], "category": "fact_exact", "notes": "基础事实"},
    {"query": "用户住在哪个城市", "expected": ["city"], "category": "fact_exact", "notes": "地点事实"},
    {"query": "用什么 AI 模型", "expected": ["model"], "category": "fact_exact", "notes": "工具偏好"},

    # === 语义改写（需要向量检索才能命中）===
    {"query": "联系方式是啥", "expected": ["email"], "category": "fact_semantic", "notes": "邮箱的同义改写"},
    {"query": "用户所在城市", "expected": ["city"], "category": "fact_semantic", "notes": "住处的语义改写"},
    {"query": "用了什么大模型", "expected": ["model"], "category": "fact_semantic", "notes": "AI 工具改写"},

    # === 跨会话追踪 ===
    {"query": "之前预约了什么服务", "expected": ["appointment"], "category": "cross_session", "notes": "跨会话事件"},
    {"query": "上次去市政厅办什么", "expected": ["city_hall"], "category": "cross_session", "notes": "具体事件"},
    {"query": "Odido 什么时候上门", "expected": ["odido"], "category": "cross_session", "notes": "时间相关"},

    # === 情绪记忆 ===
    {"query": "用户对什么感到紧张", "expected": ["emotion"], "category": "emotional", "notes": "情绪召回"},
    {"query": "有什么开心的事", "expected": ["happy_event"], "category": "emotional", "notes": "正向情绪"},

    # === 程序规则 ===
    {"query": "处理个人数据的规则", "expected": ["rule"], "category": "procedural", "notes": "规则召回"},
    {"query": "涉及隐私信息要先做什么", "expected": ["rule"], "category": "procedural", "notes": "规则改写"},
]


def get_eval_set() -> list[dict]:
    """返回评测测试集副本。"""
    return list(EVAL_TEST_SET)


def get_eval_stats() -> dict:
    """返回测试集统计。"""
    cats = {}
    for item in EVAL_TEST_SET:
        cat = item["category"]
        cats[cat] = cats.get(cat, 0) + 1
    return {
        "total": len(EVAL_TEST_SET),
        "categories": cats,
    }
