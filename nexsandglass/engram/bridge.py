#!/usr/bin/env python3
"""Engram 认知记忆接入桥接层。

将 engram 的认知记忆机制（四类记忆分类）接入 Nyx 主流程：
在消息落沙时对用户内容做记忆类型分类并写入 engram 记忆库，
使 engram 真正参与主流程（而非孤立模块）。
"""
import json
import os
import re
from datetime import datetime

try:
    from nexsandglass.core.sandglass_paths import _NB
except Exception:
    _NB = "/root/.hermes/nexsandglass"

_STORE = os.path.join(_NB, "engram_store.jsonl")

# 记忆类型分类规则
_PROCEDURAL = re.compile(r"(怎么|如何|步骤|教程|部署|配置|安装|运行|启动|操作|流程|方法)")
_EMOTIONAL = re.compile(r"(开心|难过|生气|焦虑|喜欢|讨厌|高兴|伤心|烦|累|失望|兴奋|紧张|害怕|感动|委屈)")
_EPISODIC = re.compile(r"(昨天|今天|上周|下个月|买了|去了|做了|见到|发生|期间|当时|月份|年度|时候|那[天次])")


def classify_memory_type(text: str) -> str:
    """本地规则：将文本分类为 semantic / episodic / emotional / procedural。"""
    if not text:
        return "semantic"
    if _PROCEDURAL.search(text):
        return "procedural"
    if _EMOTIONAL.search(text):
        return "emotional"
    if _EPISODIC.search(text):
        return "episodic"
    return "semantic"


def ingest(text: str) -> str:
    """分类用户消息并写入 engram 记忆库。返回记忆类型。"""
    try:
        mem_type = classify_memory_type(text)
        os.makedirs(os.path.dirname(_STORE), exist_ok=True)
        with open(_STORE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "content": text[:300],
                "type": mem_type,
            }, ensure_ascii=False) + "\n")
        return mem_type
    except Exception:
        return "semantic"


def stats() -> dict:
    """记忆类型分布统计。"""
    counts = {"semantic": 0, "episodic": 0, "emotional": 0, "procedural": 0}
    total = 0
    if os.path.exists(_STORE):
        try:
            for line in open(_STORE, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line).get("type", "semantic")
                except Exception:
                    t = "semantic"
                counts[t] = counts.get(t, 0) + 1
                total += 1
        except Exception:
            pass
    return {"total": total, **counts}


def recent(n: int = 10) -> list:
    """最近 n 条 engram 记忆。"""
    out = []
    if os.path.exists(_STORE):
        try:
            for line in open(_STORE, encoding="utf-8").readlines()[-n:]:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass
    return out
