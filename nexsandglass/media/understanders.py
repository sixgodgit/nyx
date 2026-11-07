"""
media/understanders.py — 把图片、声音、视频变成文字的「理解后端」（v7.21.0 框架）

一张照片、一段语音、一段视频本身检索不到；能检索的是从里面得出的文字：
  image → caption（看到了什么）/ ocr（图里的字）
  audio → transcript（说了什么，带时间段）
  video → description / shot（画面）+ transcript（声音）

这一层只定接口，不绑任何模型。默认一个后端都没有 —— 写入不调用模型、不联网的原则不变：
没有后端时素材照样登记（谁、什么时候、什么文件、你的附言），之后配好后端再用
`hermes nyx media understand --pending` 补上描述。

后端从三处来：
  1. 内置的 CommandUnderstander：调用你本机的程序（whisper.cpp、llama.cpp 的多模态 CLI……），
     设置 NYX_MEDIA_IMAGE_CMD / NYX_MEDIA_AUDIO_CMD / NYX_MEDIA_VIDEO_CMD（nyx.json：media_image_cmd …），
     命令里的 {path} 换成文件路径，程序打印到标准输出的文字就是结果。不经过 shell。
  2. 代码里 register(...)
  3. pip 包的入口点组 `nyx.media_understanders`：值是一个后端对象，或者返回后端（或后端列表）的函数

一个后端只需要：name、kinds（能处理哪几类）、available()（能用返回 None，不能用返回原因）、
understand(asset, path) → [Moment]。
"""
from __future__ import annotations

import logging
import shlex
import subprocess
from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

KINDS = ("image", "audio", "video")
ENTRY_POINT_GROUP = "nyx.media_understanders"


@dataclass
class Moment:
    """从素材里得出的一段文字。start_s / end_s：在音视频里的位置（秒），图片没有。"""
    kind: str                       # caption / ocr / transcript / description / shot / tags …
    text: str
    start_s: Optional[float] = None
    end_s: Optional[float] = None


@runtime_checkable
class Understander(Protocol):
    name: str
    kinds: tuple

    def available(self) -> Optional[str]: ...

    def understand(self, asset: dict, path: str) -> List[Moment]: ...


# ══════════════════════════════════════════════════════════
# 内置：调用本机程序
# ══════════════════════════════════════════════════════════

_CMD_SETTING = {"image": "NYX_MEDIA_IMAGE_CMD", "audio": "NYX_MEDIA_AUDIO_CMD", "video": "NYX_MEDIA_VIDEO_CMD"}
_CMD_MOMENT = {"image": "caption", "audio": "transcript", "video": "description"}


class CommandUnderstander:
    """把文件交给一个本机程序，取它的标准输出。命令按 shlex 拆分、不经过 shell；{path} 是文件路径。"""

    def __init__(self, kind: str, timeout: float = 600.0):
        self.kind = kind
        self.kinds = (kind,)
        self.name = f"command:{kind}"
        self.timeout = timeout

    def _cmd(self) -> str:
        from nexsandglass.core import settings
        return (settings.get(_CMD_SETTING[self.kind]) or "").strip()

    def available(self) -> Optional[str]:
        return None if self._cmd() else f"{_CMD_SETTING[self.kind]} is not set"

    def understand(self, asset: dict, path: str) -> List[Moment]:
        argv = [a.replace("{path}", path) for a in shlex.split(self._cmd())]
        if not any("{path}" in a for a in shlex.split(self._cmd())):
            argv.append(path)
        r = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError(f"{argv[0]} exited {r.returncode}: {(r.stderr or '').strip()[-300:]}")
        text = (r.stdout or "").strip()
        return [Moment(_CMD_MOMENT[self.kind], text)] if text else []


# ══════════════════════════════════════════════════════════
# 注册表
# ══════════════════════════════════════════════════════════

_REGISTRY: dict = {}
_DISCOVERED = False


def register(u) -> None:
    """登记一个后端（同名的替换）。"""
    if not getattr(u, "name", None) or not getattr(u, "kinds", None):
        raise ValueError("an understander needs a name and kinds")
    _REGISTRY[u.name] = u


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def _discover() -> None:
    global _DISCOVERED
    if _DISCOVERED:
        return
    _DISCOVERED = True
    for k in KINDS:
        _REGISTRY.setdefault(f"command:{k}", CommandUnderstander(k))
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        eps = []
    for ep in eps:
        try:
            obj = ep.load()
            got = obj() if callable(obj) and not hasattr(obj, "understand") else obj
            for u in (got if isinstance(got, (list, tuple)) else [got]):
                register(u)
        except Exception as e:                  # 一个坏插件不能让素材登记整个失败
            logger.warning("media understander %s failed to load: %s", ep.name, e)


def all_understanders() -> List:
    _discover()
    return list(_REGISTRY.values())


def for_kind(kind: str) -> List:
    """能处理这一类、现在能用的后端。"""
    out = []
    for u in all_understanders():
        if kind not in tuple(u.kinds):
            continue
        try:
            why = u.available()
        except Exception as e:
            why = str(e)
        if why is None:
            out.append(u)
    return out


def describe() -> List[dict]:
    """每个后端：名字、类别、能不能用（不能用的原因）。"""
    rows = []
    for u in all_understanders():
        try:
            why = u.available()
        except Exception as e:
            why = str(e)
        rows.append({"name": u.name, "kinds": list(u.kinds), "available": why is None, "reason": why or ""})
    return rows


def run(u, asset: dict, path: str) -> List[Moment]:
    got: Iterable = u.understand(asset, path) or []
    out = []
    for m in got:
        if isinstance(m, Moment) and str(m.text or "").strip():
            out.append(m)
        elif isinstance(m, dict) and str(m.get("text") or "").strip():
            out.append(Moment(str(m.get("kind") or "description"), str(m["text"]), m.get("start_s"), m.get("end_s")))
    return out
