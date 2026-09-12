"""
NexSandglass 通用落沙 — 任何 Agent 都能用
==========================================
不依赖 Hermes plugin。任何 Python 脚本 import 即可。
V2.4.0: 去掉 DPAPI/base64 加密，明文存储。靠 OS 层全盘加密保护（BitLocker/FileVault/LUKS）。

用法：
  from nexsandglass.core.sandglass_log import log_message
  log_message("用户：今天天气真好")
  log_message("Assistant：明天有雨，记得带伞")
"""

import logging
import os
import re
import time as _time
from datetime import datetime

logger = logging.getLogger(__name__)

# ── AI无意义回复过滤器（V2.1.10修复：长度判断替代^锚定）──
_AI_TRIVIAL = re.compile(
    r'(好的|明白了|没问题|请稍等|我来看看|是的|对的|'
    r'你说得对|当然可以|不用担心|不客气|谢谢|可以|'
    r'好|嗯|OK|ok|嗯嗯|好的呢|没问题呢|知道了|收到)'
)


def _estimate_info_value(text: str) -> float:
    """评估消息信息量。0.0=纯确认词，1.0=高价值。"""
    score = 0.3
    if len(text) > 50:                score += 0.2
    if re.search(r'\d+', text):       score += 0.2
    if re.search(r'[。：；]', text):  score += 0.1
    if any(kw in text for kw in [
        '建议', '需要', '注意', '因为', '方案',
        '步骤', '第一种', '第二种', '推荐',
        '区别', '对比', '优点是', '缺点是',
    ]):                                 score += 0.2
    # 短文本+纯确认词 → 零价值；长文本开头是确认词 → 仍可加分
    stripped = text.strip()
    if _AI_TRIVIAL.match(stripped) and len(stripped) <= 10:
        score = 0.0
    return min(score, 1.0)


from nexsandglass.core.sandglass_paths import _NB

_SANDGLASS = os.path.join(_NB, "sandglass.txt")


def log_message(text: str, sender: str = "agent", return_id: bool = False):
    """写入一条消息到沙漏。

    Step 2（ID 中枢接入写路径）：落盘不再自己拼行、自己抢锁，而是委托
    ``memid.allocate()`` —— 它在同一把文件锁内完成「INSERT memories」+
    「append sandglass.txt」，文件失败则回滚 DB。

    这消灭了漂移的根源：**行号不再需要被猜**。
    旧实现把 ``line_num=0`` 传给下游，下游只好用 ``COUNT(*)+1`` 估，
    一旦某次索引抛异常被这里吞掉，计数就永久错位一格，之后每条记忆的
    provenance 全错（实测一次失败即污染 80% 的实体映射）。
    现在真实的 ``line_start`` 和 ``mem_id`` 由中枢返回并直接下发，
    下游索引失败只影响它自己那一条，不再传染。

    返回：默认 True/False（与旧调用完全兼容）；return_id=True 时返回
    mem_id 字符串，失败返回 ""。
    """
    fail = "" if return_id else False
    try:
        # AI 低价值回复过滤（V2.1）—— 这类内容不入沙漏，也不进中枢
        if sender == "agent" and _estimate_info_value(text) < 0.3:
            return fail

        from nexsandglass.core import memid

        # 原子分配：拿到 mem_id 的同时，日志行已经写好
        try:
            # 必须把本模块的 _SANDGLASS 传下去 —— 它可能被重定向（测试/多实例），
            # 不传的话中枢写自己的路径，调用方读另一个，等于又开了第二个真相来源。
            mem_id = memid.allocate(text, sender=sender, journal_path=_SANDGLASS)
        except TimeoutError as e:
            # 旧实现在锁超时后"强制裸写"，那会写出一条中枢不知道的记忆。
            # 宁可这一条写失败并让调用方知道，也不制造中枢与日志的分叉。
            logger.error("落沙锁超时，本条未写入（拒绝裸写以免中枢分叉）: %s", e)
            return fail

        row = memid.get(mem_id) or {}
        line_num = row.get("line_start", 0)

        # 影子沙——用中枢给的真实行号 + mem_id，不再让它自己猜
        try:
            from nexsandglass.features.shadow_sand import shadow_index
            shadow_index(text, line_num=line_num, mem_id=mem_id)
        except Exception as e:
            # 仍然吞掉：索引失败不该让记忆本身丢失。
            # 但现在它只影响这一条 —— 行号来自中枢，不会再累积错位。
            logger.warning("影子沙索引跳过（不影响行号，仅本条缺索引）: %s", e)

        # 知识图谱
        if sender == "user":
            try:
                from nexsandglass.features.weavethread import wthread_store
                wthread_store(text, line_num=line_num, mem_id=mem_id)
            except Exception as e:
                logger.warning("织线抽取跳过: %s", e)

        return mem_id if return_id else True
    except Exception as e:
        logger.error(f"沙漏写入失败: {e}")
        return fail


def log_conversation(user_msg: str, agent_msg: str) -> int:
    """写入一轮对话（用户+Agent）。返回新写入的行数。"""
    count = 0
    if user_msg:
        if log_message(user_msg, sender="user"): count += 1
    if agent_msg:
        if log_message(agent_msg, sender="agent"): count += 1
    return count


# ── 钩子注册守卫 ──────────────────────────────────────────
# 仓库里有**两份**网关插件（core/sandglass.py 与 interfaces/plugin.py），
# 各自 register_hook("pre_gateway_dispatch")。两份都被加载 → 每条消息写两份；
# 网关每次重连再各加一次 → N 随重连次数增长，进程重启归零。
# 这正是 2026-09-02~09-09 那次写入放大的形状（3 倍 → 56 倍 → 重启回落 → 再爬）。
# 守卫按钩子名去重，跨模块共享 —— 谁先注册谁生效，后来者一律拒绝。
_HOOKS_CLAIMED: set = set()


def claim_hook(name: str) -> bool:
    """认领一个钩子名。同名第二次认领返回 False。"""
    if name in _HOOKS_CLAIMED:
        return False
    _HOOKS_CLAIMED.add(name)
    return True
