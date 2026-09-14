"""Déjà Vu 的对外数据类型。

用 dataclass 而非 dict：调用方写 ``result.score`` 有补全、拼错立刻报错；
dict 的 ``.get()`` 会静默返回默认值——在「到底聊过没有」这种判断上，
静默失败比报错危险得多。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Phantom:
    """一道鬼影——某个实体在历史中出现过的痕迹。

    属性
    ----
    token       被嗅出的实体/词（小写归一化）
    born        首次出现时间
    last_spotted 最近一次出现时间
    sightings   出现次数
    refs        调用方通过 ``imprint(key=...)`` 传入的引用（默认取最近 50 个）
    snippet     首次出现时的文本片段
    """

    token: str
    born: str = ""
    last_spotted: str = ""
    sightings: int = 1
    refs: List[str] = field(default_factory=list)
    snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FamiliarityResult:
    """``sense()`` 的结果——这片黑暗里有没有熟悉的气息。

    属性
    ----
    familiar    是否感知到熟悉（有已知 token）
    score       熟悉度 0.0~1.0（已知 token 占比）
    phantoms    已知 token 列表
    unknown     未知 token 列表
    reason      人类可读的解释，便于调试与日志
    """

    familiar: bool = False
    score: float = 0.0
    phantoms: List[str] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)
    total: int = 0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HuntResult:
    """``hunt()`` 的结果——寻回尝试。

    ``hunt()`` 本身返回 ``list[Phantom]``；本类型用于需要置信度与
    是否命中判定的场景（如 MCP 输出）。
    """

    hunted: bool = False
    conviction: float = 0.0
    phantoms: List[Phantom] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["phantoms"] = [p.to_dict() if isinstance(p, Phantom) else p
                         for p in self.phantoms]
        return d


@dataclass
class GazeReport:
    """``gaze()`` 的结果——系统统计。"""

    veil_items: int = 0
    veil_bits: int = 0
    veil_hashes: int = 0
    veil_fill_ratio: float = 0.0
    mist_total: int = 0
    restless: List[Dict[str, Any]] = field(default_factory=list)
    storage_dir: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _coerce_optional_str(v: Optional[Any]) -> str:
    return "" if v is None else str(v)
