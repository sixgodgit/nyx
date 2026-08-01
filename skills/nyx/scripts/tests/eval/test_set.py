"""
tests/eval/test_set.py — 记忆检索评测测试集（Task 4）

模拟真实多轮对话场景，包含需要跨会话召回的问题。
60 条问答对，覆盖：事实召回、语义改写、跨会话追踪、衰减验证、情感、规则、技术细节。

每条格式：
  query: 检索问题（可能含同义改写）
  expected: 期望命中的记忆 id 集合
  category: 分类
  memory: 对应的记忆原文（用于构造测试库）
"""

from __future__ import annotations

# ── 基础记忆库（模拟已存储的记忆）──────────────────────────────
BASE_MEMORIES = [
    {"id": "email", "text": "用户邮箱是 enfys@hvh.expert", "type": "semantic"},
    {"id": "city", "text": "用户住在海牙", "type": "semantic"},
    {"id": "model", "text": "用户使用 DeepSeek V4 Flash 模型", "type": "semantic"},
    {"id": "car", "text": "用户买了 Geely Starray EM-i Max+ 汽车", "type": "semantic"},
    {"id": "car_dealer", "text": "车是从 ECAR (Bagnoles B.V.) 买的", "type": "semantic"},
    {"id": "phone", "text": "用户手机是 iPhone", "type": "semantic"},
    {"id": "odido", "text": "Odido 光纤预约了 7 月 30 日上门安装", "type": "episodic"},
    {"id": "city_hall", "text": "7 月 30 日下午去了海牙市政厅 Laak 分局办签名认证", "type": "episodic"},
    {"id": "appointment", "text": "市政厅预约在 Stadsdeelkantoor Laak, Slachthuisplein 25", "type": "episodic"},
    {"id": "apple_alert", "text": "用户收到 Apple 账户密码重设提醒，比较紧张", "type": "emotional"},
    {"id": "car_happy", "text": "用户买到新车很开心，比官方价便宜 5000 欧", "type": "emotional"},
    {"id": "rule_retrieve", "text": "涉及个人数据必须先检索再回答，不能凭记忆", "type": "procedural"},
    {"id": "rule_lang", "text": "用户偏好中文回复", "type": "procedural"},
    {"id": "nginx", "text": "服务器用 nginx 做反向代理", "type": "semantic"},
    {"id": "docker", "text": "部署用 docker-compose 编排", "type": "semantic"},
    {"id": "thalamus", "text": "模型路由走本地 thalamus 网关 127.0.0.1:9880", "type": "semantic"},
    {"id": "nyx_repo", "text": "Nyx 代码仓库是 sixgodgit/nyx", "type": "semantic"},
    {"id": "tax", "text": "用户公司在荷兰，holding 账户有 40 万欧", "type": "semantic"},
    {"id": "restaurant", "text": "用户经营中餐馆生意", "type": "semantic"},
    {"id": "flitsmeister", "text": "导航用 Apple Maps + Flitsmeister 组合", "type": "semantic"},
]

# ── 评测问题集（60 条）────────────────────────────────────────
EVAL_TEST_SET = [
    # === 事实召回（精确关键词，10 条）===
    {"query": "用户的邮箱是什么", "expected": ["email"], "category": "fact_exact"},
    {"query": "用户住在哪个城市", "expected": ["city"], "category": "fact_exact"},
    {"query": "用什么 AI 模型", "expected": ["model"], "category": "fact_exact"},
    {"query": "用户买了什么车", "expected": ["car"], "category": "fact_exact"},
    {"query": "车从哪里买的", "expected": ["car_dealer"], "category": "fact_exact"},
    {"query": "用户手机是什么牌子", "expected": ["phone"], "category": "fact_exact"},
    {"query": "Nyx 仓库地址", "expected": ["nyx_repo"], "category": "fact_exact"},
    {"query": "用户经营什么生意", "expected": ["restaurant"], "category": "fact_exact"},
    {"query": "部署用什么编排", "expected": ["docker"], "category": "fact_exact"},
    {"query": "网关地址是什么", "expected": ["thalamus"], "category": "fact_exact"},

    # === 语义改写（需向量检索，15 条）===
    {"query": "联系方式是啥", "expected": ["email"], "category": "fact_semantic"},
    {"query": "用户所在城市", "expected": ["city"], "category": "fact_semantic"},
    {"query": "用了什么大模型", "expected": ["model"], "category": "fact_semantic"},
    {"query": "开的什么车", "expected": ["car"], "category": "fact_semantic"},
    {"query": "车在哪儿提的", "expected": ["car_dealer"], "category": "fact_semantic"},
    {"query": "用的什么手机", "expected": ["phone"], "category": "fact_semantic"},
    {"query": "Nyx 代码放哪", "expected": ["nyx_repo"], "category": "fact_semantic"},
    {"query": "用户做啥的", "expected": ["restaurant"], "category": "fact_semantic"},
    {"query": "反向代理用啥", "expected": ["nginx"], "category": "fact_semantic"},
    {"query": "模型路由怎么走的", "expected": ["thalamus"], "category": "fact_semantic"},
    {"query": "用户住处", "expected": ["city"], "category": "fact_semantic"},
    {"query": "怎么收发邮件的", "expected": ["email"], "category": "fact_semantic"},
    {"query": "AI 助手是哪个", "expected": ["model"], "category": "fact_semantic"},
    {"query": "购车渠道", "expected": ["car_dealer"], "category": "fact_semantic"},
    {"query": "服务器反代", "expected": ["nginx"], "category": "fact_semantic"},

    # === 跨会话追踪（10 条）===
    {"query": "之前预约了什么服务", "expected": ["odido", "appointment"], "category": "cross_session"},
    {"query": "Odido 什么时候上门", "expected": ["odido"], "category": "cross_session"},
    {"query": "上次去市政厅办什么", "expected": ["city_hall"], "category": "cross_session"},
    {"query": "市政厅预约在哪", "expected": ["appointment"], "category": "cross_session"},
    {"query": "光纤什么时候装", "expected": ["odido"], "category": "cross_session"},
    {"query": "Laak 分局是干嘛的", "expected": ["appointment", "city_hall"], "category": "cross_session"},
    {"query": "之前的预约地点", "expected": ["appointment"], "category": "cross_session"},
    {"query": "上门安装是啥", "expected": ["odido"], "category": "cross_session"},
    {"query": "签名认证去哪办", "expected": ["city_hall"], "category": "cross_session"},
    {"query": "上个月安排的事", "expected": ["odido", "city_hall"], "category": "cross_session"},

    # === 情绪记忆（8 条）===
    {"query": "用户对什么感到紧张", "expected": ["apple_alert"], "category": "emotional"},
    {"query": "有什么开心的事", "expected": ["car_happy"], "category": "emotional"},
    {"query": "为什么用户焦虑", "expected": ["apple_alert"], "category": "emotional"},
    {"query": "最近什么让用户高兴", "expected": ["car_happy"], "category": "emotional"},
    {"query": "账户安全问题", "expected": ["apple_alert"], "category": "emotional"},
    {"query": "买车心情", "expected": ["car_happy"], "category": "emotional"},
    {"query": "担心什么事", "expected": ["apple_alert"], "category": "emotional"},
    {"query": "好事是什么", "expected": ["car_happy"], "category": "emotional"},

    # === 程序规则（8 条）===
    {"query": "处理个人数据的规则", "expected": ["rule_retrieve"], "category": "procedural"},
    {"query": "涉及隐私信息要先做什么", "expected": ["rule_retrieve"], "category": "procedural"},
    {"query": "用什么语言回复", "expected": ["rule_lang"], "category": "procedural"},
    {"query": "查资料前必须干嘛", "expected": ["rule_retrieve"], "category": "procedural"},
    {"query": "回答语言偏好", "expected": ["rule_lang"], "category": "procedural"},
    {"query": "不能凭记忆回答什么", "expected": ["rule_retrieve"], "category": "procedural"},
    {"query": "用户喜欢什么语言", "expected": ["rule_lang"], "category": "procedural"},
    {"query": "检索铁律是什么", "expected": ["rule_retrieve"], "category": "procedural"},

    # === 技术细节（9 条）===
    {"query": "thalamus 端口", "expected": ["thalamus"], "category": "tech"},
    {"query": "反向代理软件", "expected": ["nginx"], "category": "tech"},
    {"query": "容器编排工具", "expected": ["docker"], "category": "tech"},
    {"query": "公司注册地", "expected": ["tax"], "category": "tech"},
    {"query": "holding 账户金额", "expected": ["tax"], "category": "tech"},
    {"query": "导航用什么组合", "expected": ["flitsmeister"], "category": "tech"},
    {"query": "测速提醒用什么", "expected": ["flitsmeister"], "category": "tech"},
    {"query": "网关 base_url", "expected": ["thalamus"], "category": "tech"},
    {"query": "模型路由走哪", "expected": ["thalamus"], "category": "tech"},
]


def get_eval_set() -> list[dict]:
    """返回评测测试集副本。"""
    return list(EVAL_TEST_SET)


def get_base_memories() -> list[dict]:
    """返回基础记忆库副本（用于构造评测用检索库）。"""
    return list(BASE_MEMORIES)


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
