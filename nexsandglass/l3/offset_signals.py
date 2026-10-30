"""NexSandglass 偏移信号词库 — 单一真相来源。
sandglass_think 和 decision_particles 都从这里导入，消除循环依赖。"""

import logging

# ── 偏移信号词库 ──
_OFFSET_SIGNALS = {
    "frugal": ["免费", "不花钱", "自己搞", "本地", "省钱", "性价比", "开源"],
    "spend": ["花钱", "省事", "买", "付费", "订阅", "不值", "效率优先"],
    "drift_放弃": ["不管了", "放弃", "不搞了", "不做了", "算了不弄", "不想管", "懒得弄", "不折腾了"],
    "drift_妥协": ["能用就行", "不纠结", "就那样", "将就", "凑合", "差不多得了", "无所谓了", "先这样吧"],
    "drift_烦躁": ["随便", "算了", "就这样", "烦死了", "受不了", "太麻烦", "真无语", "够了"],
}

# ── LLM 配置（单一来源）──
# v7.15.2 起要明确打开：NYX_L3_LLM=1（Hermes 里是 nyx.json 的 l3_llm: on）。以前只要环境里有 DEEPSEEK_API_KEY /
# OPENROUTER_API_KEY 就用 —— 在 Hermes 里这几乎总是成立（主模型多半就走 OpenRouter），于是每次搭系统提示，
# 画像和最近的决策记录都被发给了 OpenRouter，主人没有选过。往第三方发数据必须由主人自己打开（Hermes 插件目录规则 13）。
# 每次调用时现读（不在 import 时定死）：nyx.json 的开关、Hermes 按 profile 给的 key 都在 import 之后才有。

def l3_llm_enabled() -> bool:
    from nexsandglass.core import settings      # 按 profile（v7.17.0）：别的 profile 打开了，这里不跟着开
    return settings.get("NYX_L3_LLM").strip().lower() in ("1", "on", "true", "yes")


def llm_config() -> tuple:
    """(key, endpoint, model)；没打开或没有 key → ("", "", "")。"""
    if not l3_llm_enabled():
        return "", "", ""
    from nexsandglass.core import credentials
    ds = credentials.get("DEEPSEEK_API_KEY")
    if ds:
        return ds, "https://api.deepseek.com/v1/chat/completions", "deepseek-v4-flash"
    orr = credentials.get("OPENROUTER_API_KEY")
    if orr:
        return orr, "https://openrouter.ai/api/v1/chat/completions", "deepseek/deepseek-v4-flash"
    return "", "", ""


def llm_on() -> bool:
    return bool(llm_config()[0])


def _fail_open(default):
    """装饰器：任何异常返回 default 值并 log warning。"""
    logger = logging.getLogger(__name__)
    def deco(func):
        def wrapper(*a, **kw):
            try:
                return func(*a, **kw)
            except Exception as e:
                logger.warning(f"{func.__name__} failed, returning default: {e}")
                return default
        return wrapper
    return deco
