"""
nyx-reveal —— Nyx 密钥保险箱的「直达取回」（v7.15.0，配套的 Hermes 普通插件）

nyx_secret_reveal 交给模型的只是一个句柄（⟦nyx:v3⟧）。回复发给主人之前，Hermes 调
transform_llm_output 钩子（agent/turn_finalizer.py），这里把句柄换成原文 —— 模型服务商看不到原文，
Hermes 存的会话记录（来自 messages）里也只有句柄。见 docs/secret-vault.md §6.2。

为什么单独一个插件：Hermes 把记忆提供器当成独占插件单独加载，给它的 register_hook 是空函数，
钩子只能由普通插件注册。随 nyx-memory 一起装（入口点 hermes_agent.plugins），启用一次即可：

    hermes plugins enable nyx-reveal
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _transform(response_text: str = "", session_id: str = "", **kwargs):
    """有这个会话待发的原文 / 确认短语就返回改写后的回复；没有返回 None（Hermes：None = 不改写）。"""
    try:
        from nexsandglass.core import secret_vault
        return secret_vault.render(session_id or "", response_text or "")
    except Exception as e:
        logger.warning("nyx-reveal: replacement failed (the reply went out as is; the secret was not sent): %s", e)
        return None


def register(ctx) -> None:
    # import nexsandglass 会定下数据目录：先按当前 profile 的 nyx.json 定，否则这个插件比记忆插件先加载时，
    # 进程用的是默认目录，配了 data_dir 的 profile 会被记忆插件拒绝（v7.17.0）
    import nexsandglass_hermes
    nexsandglass_hermes.apply_data_dir()
    from nexsandglass.core import secret_vault
    ctx.register_hook("transform_llm_output", _transform)
    secret_vault.mark_hook_registered()
