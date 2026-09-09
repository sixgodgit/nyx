"""
skill_distiller — Procedural Memory → Skill Candidate 自动蒸馏 (v1.0)

来源思路: memsearch "Skills from Memory" + cognee "improve 反馈蒸馏"
落点: nexsandglass/features/skill_distiller.py

原理
----
writer.py 对 procedural 记忆做 DEDUP 去重时, 只对 "近重复" 命中做 access_count += 1
(见 engram/writer.py 的 PROCEDURAL_DEDUP_THRESHOLD 分支)。这意味着:

    access_count 高  ≈  同一条规则/做事方式被反复命中  ≈  一条真实重复的工作流

本模块把这层信号显式化: 扫描 procedural 记忆, 把 access_count 达到阈值、
内容仍有效(未被 superseded) 的规则蒸馏成 SkillCandidate, 写入独立候选目录,
供人工确认后才安装进正式 skill 生态。绝不直接写入 production skill 目录。

依赖: 仅标准库 + 现有 engram.types.Memory, 零新增第三方依赖。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterable, Optional

from nexsandglass.engram.types import Memory, MemoryType


# 默认候选存储目录 (git-tracked, 不进入 production skills)
DEFAULT_CANDIDATE_DIR = ".nyx/skill-candidates"

# 一条 procedural 规则需被命中多少次才视为"值得蒸馏"
DEFAULT_MIN_ACCESS = 3

# 规则内容最短长度(避免把单字/过短无意义串当候选)
DEFAULT_MIN_LEN = 12

# 动作动词前缀, 用于把规则内容提炼成 skill 名字(启发式, 可降级为 hash 后缀)
_ACTION_VERBS = [
    "运行", "执行", "部署", "备份", "升级", "测试", "检查", "审查",
    "推送", "同步", "验证", "调用", "搜索", "生成", "读取", "写入",
    "run", "deploy", "test", "check", "push", "sync", "verify",
    "install", "start", "stop", "restart", "update", "build",
]


@dataclass
class SkillCandidate:
    """一条从 procedural 记忆蒸馏出的候选技能。"""

    title: str                          # 提炼的技能名
    trigger: str                        # 触发词(取规则片段)
    body: str                           # 规则正文 = 原记忆内容
    source_memory_id: str               # 来源 procedural 记忆
    access_count: int                   # 触发次数
    confidence: float                   # 蒸馏置信度
    distilled_at: str                   # ISO 时间戳
    tags: list[str] = field(default_factory=lambda: ["self-distilled"])

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_slug(body: str, mem_id: str) -> str:
    """从规则内容提炼文件名 slug, 失败则退回记忆 id 的短哈希。"""
    # 尝试提取动作动词后的主题词
    m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,12})", body)
    if m:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", body[:40]).strip("_") or f"skill_{mem_id[-6:]}"
    return f"skill_{mem_id[-6:]}"


def distill_procedural_skills(
    procedural_memories: Iterable[Memory],
    min_access: int = DEFAULT_MIN_ACCESS,
    min_len: int = DEFAULT_MIN_LEN,
    candidate_dir: Optional[str] = None,
) -> list[SkillCandidate]:
    """
    从 procedural 记忆中蒸馏高频重复规则为 SkillCandidate。

    Args:
        procedural_memories: 现有 procedural 记忆(仅需 type, content, access_count,
                             memory_id, superseded_by 字段)。
        min_access: access_count 达到该值才蒸馏(默认 3)。
        min_len: 内容最短长度。
        candidate_dir: 若给则把候选写成 JSON 文件(每个一文件)。

    Returns:
        蒸馏出的候选列表(仅新增, 不会重复写已存在的候选)。
    """
    now = _now()
    candidates: list[SkillCandidate] = []

    for mem in procedural_memories:
        if mem.type != MemoryType.PROCEDURAL.value:
            continue
        if mem.superseded_by is not None:
            continue  # 已被覆盖的旧规则不蒸馏
        if mem.access_count < min_access:
            continue
        content = (mem.content or "").strip()
        if len(content) < min_len:
            continue

        # 置信度: 基础 0.6 + 命中次数加成, 封顶 0.98
        confidence = min(0.98, 0.6 + 0.06 * (mem.access_count - min_access))

        candidate = SkillCandidate(
            title=content[:24],
            trigger=content[:24],
            body=content,
            source_memory_id=mem.memory_id,
            access_count=mem.access_count,
            confidence=round(confidence, 3),
            distilled_at=now,
        )
        candidates.append(candidate)

    # 若指定目录, 落盘(只写新候选, 不覆盖已有同名文件)
    if candidate_dir and candidates:
        os.makedirs(candidate_dir, exist_ok=True)
        written: list[SkillCandidate] = []
        for cand in candidates:
            slug = _candidate_slug(cand.body, cand.source_memory_id)
            path = os.path.join(candidate_dir, f"{slug}.json")
            if os.path.exists(path):
                continue  # 已存在则不重复写
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cand.to_dict(), f, ensure_ascii=False, indent=2)
            written.append(cand)
        return written

    return candidates


def load_candidates(candidate_dir: str) -> list[dict]:
    """读取全部已蒸馏的候选(供人工/Agent 审查)。"""
    out: list[dict] = []
    if not os.path.isdir(candidate_dir):
        return out
    for fn in sorted(os.listdir(candidate_dir)):
        if fn.endswith(".json"):
            with open(os.path.join(candidate_dir, fn), encoding="utf-8") as f:
                try:
                    out.append(json.load(f))
                except json.JSONDecodeError:
                    continue
    return out


def _demo() -> None:
    """内置演示: 构造几条带 access_count 的 procedural 记忆并蒸馏。"""
    from nexsandglass.engram.types import MemoryType  # noqa: F401

    demo_mems = [
        Memory(
            memory_id="proc_deploy_1", type="procedural",
            content="部署前先跑 pytest tests/ 确认测试通过再 push", access_count=5,
        ),
        Memory(
            memory_id="proc_backup_1", type="procedural",
            content="升级 Hermes 前先备份 config.yaml 和 memory 目录", access_count=4,
        ),
        Memory(
            memory_id="proc_rare_1", type="procedural",
            content="偶尔一次的操作不算高频工作流", access_count=1,
        ),
        Memory(
            memory_id="epi_x", type="episodic",
            content="昨天部署了马维斯服务器", access_count=9,  # 非 procedural, 应被跳过
        ),
    ]
    cands = distill_procedural_skills(demo_mems, candidate_dir=DEFAULT_CANDIDATE_DIR)
    print(f"蒸馏出 {len(cands)} 条候选(access>=3 的 procedural, episodic 被跳过):")
    for c in cands:
        print(f"  • [{c.access_count}x] {c.body[:30]}… (置信 {c.confidence})")
    print(f"\n候选已存: {os.path.abspath(DEFAULT_CANDIDATE_DIR)}/")


if __name__ == "__main__":
    _demo()
