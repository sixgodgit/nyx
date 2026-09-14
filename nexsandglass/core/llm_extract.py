"""
core/llm_extract.py — 轻量 LLM 抽取接口（可选、可降级）

用于知识图谱的三元组补充抽取和实体归一化。
默认关闭（不依赖任何 LLM），通过环境变量启用：

  WTHREAD_LLM_EXTRACTION=1  开启 LLM 抽取
  LLM_EXTRACT_API_URL=...   LLM API 端点（可选，默认使用 thalamus 网关）

Fail-safe：任何失败返回空列表，不阻塞正则抽取。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def llm_extract_triples(text: str, existing_regex: list = None) -> list:
    """
    调用 LLM 从文本中补充抽取三元组。

    Args:
        text: 原始文本
        existing_regex: 正则已抽取的结果（去重用）

    Returns:
        [(subject, relation, object, confidence), ...]
        失败返回 []。
    """
    if not os.environ.get("WTHREAD_LLM_EXTRACTION"):
        return []
    api_url = os.environ.get("LLM_EXTRACT_API_URL", "")
    if not api_url:
        # 默认使用本地 thalamus 网关
        api_url = "http://127.0.0.1:9880/v1/chat/completions"
    try:
        import httpx
        existing_str = ""
        if existing_regex:
            existing_str = "\n已有正则抽取（请补充漏掉的，不要重复）：\n"
            existing_str += "\n".join(f"- {s} {r} {o}" for s, r, o in existing_regex)
        prompt = (
            "从以下文本中提取实体关系三元组（subject, relation, object）。"
            "返回 JSON 数组，每项格式：{\"subject\": \"...\", \"relation\": \"...\", \"object\": \"...\", \"confidence\": 0.0-1.0}"
            f"{existing_str}\n\n文本：\n{text}\n\nJSON:"
        )
        resp = httpx.post(
            api_url,
            json={
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        # 解析 JSON
        start = content.find("[")
        end = content.rfind("]") + 1
        if start == -1 or end == 0:
            return []
        items = json.loads(content[start:end])
        result = []
        for item in items:
            subj = str(item.get("subject", "")).strip()
            rel = str(item.get("relation", "")).strip()
            obj = str(item.get("object", "")).strip()
            conf = float(item.get("confidence", 0.7))
            if subj and rel and obj:
                result.append((subj, rel, obj, conf))
        return result
    except Exception as e:
        logger.debug("[llm_extract_triples] 失败: %s", e)
        return []
