#!/usr/bin/env python3
"""Engram 认知记忆接入桥接层 + 双向映射。

将 engram 的认知记忆机制（四类记忆分类）接入 Nyx 主流程：
在消息落沙时对用户内容做记忆类型分类并写入 engram 记忆库，
使 engram 真正参与主流程（而非孤立模块）。

V3.5.0: 增加 Canonical MemoryObject 双向映射 —— 在 Sandglass 行 /
shadow 记录 / engram Memory / MemoryObject 之间建立可逆映射，旧数据保持可读。
"""
import json
import os
import re
from datetime import datetime

try:
    from nexsandglass.core.sandglass_paths import _NB
except Exception:
    _NB = "/root/.hermes/nexsandglass"

from nexsandglass.engram.types import MemoryObject

_STORE = os.path.join(_NB, "engram_store.jsonl")
_SANDGLASS = os.path.join(_NB, "sandglass.txt")

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


# ══════════════════════════════════════════════════════════
# Canonical MemoryObject 双向映射（V3.5.0）
# ══════════════════════════════════════════════════════════

# Sandglass 行格式: "ts | sender | text"
_LINE_SEP = " | "


def sandglass_line_to_dict(line: str) -> dict | None:
    """解析一条 Sandglass 行为 dict；非标准行返回 None。旧数据可读。"""
    if _LINE_SEP not in line:
        return None
    parts = line.strip().split(_LINE_SEP, 2)
    if len(parts) < 3:
        return None
    return {"ts": parts[0], "sender": parts[1], "text": parts[2].strip()}


def sandglass_line_to_object(line: str) -> "MemoryObject":
    """Sandglass 行 → MemoryObject（source_id 用行号或 ts）。"""
    parsed = sandglass_line_to_dict(line)
    if parsed is None:
        return MemoryObject(content=line, type="semantic")
    return MemoryObject(
        content=parsed["text"],
        type=classify_memory_type(parsed["text"]),
        created_at=parsed["ts"],
        source_id=f"sandglass:{parsed['ts']}",
        provenance="sandglass",
        status="observed",
    )


def object_to_sandglass_line(obj: "MemoryObject") -> str:
    """MemoryObject → Sandglass 行。"""
    ts = obj.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sender = "hermes"
    return f"{ts}{_LINE_SEP}{sender}{_LINE_SEP}{obj.content}"


def engram_row_to_object(row: dict) -> "MemoryObject":
    """engram_store.jsonl 旧行（{ts, content, type}）→ MemoryObject。旧数据可读。"""
    return MemoryObject(
        content=row.get("content", ""),
        type=row.get("type", "semantic"),
        created_at=row.get("ts", ""),
        source_id=f"engram:{row.get('ts', '')}",
        provenance="engram_store",
        status="observed",
    )


def object_to_engram_row(obj: "MemoryObject") -> dict:
    """MemoryObject → engram_store.jsonl 行（兼容旧格式）。"""
    return {
        "ts": obj.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "content": obj.content,
        "type": obj.type,
    }


def shadow_row_to_object(line_num: int, text: str, score: float = 0.5) -> "MemoryObject":
    """shadow 记录（line_num + 信任分）→ MemoryObject。"""
    return MemoryObject(
        content=text,
        type=classify_memory_type(text),
        source_id=f"shadow:{line_num}",
        provenance="shadow_sand",
        confidence=min(max(score, 0.0), 1.0),
        status="observed",
    )


def object_to_shadow_row(obj: "MemoryObject", line_num: int, score: float = 0.5) -> dict:
    """MemoryObject → shadow 记录（写入 trust 表的行）。"""
    return {
        "line_num": line_num,
        "content": obj.content,
        "score": min(max(score, 0.0), 1.0),
        "created_at": obj.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def memory_to_object(mem) -> "MemoryObject":
    """现有 engram Memory dataclass → MemoryObject（字段升级）。"""
    return MemoryObject.from_memory(mem)


def object_to_memory(obj: "MemoryObject"):
    """MemoryObject → 现有 engram Memory dataclass（字段降级）。"""
    return obj.to_memory()
