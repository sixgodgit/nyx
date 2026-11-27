"""Shared segmentation boundary with explicit legacy evidence policies."""
from __future__ import annotations
import re

_PROFILE = re.compile(r"(?<=[。！？!?])|(?<=\.)\s+|\n+")
_FACTS = re.compile(r"[。！？!?\n]+")


def sentences(text: str, *, policy: str = "profile") -> list[str]:
    """Preserve complete input units; no translation or character clipping."""
    if policy == "facts":
        return [s for s in _FACTS.split(text or "") if s.strip()]
    if policy != "profile":
        raise ValueError("unknown segmentation policy")
    out = [s.strip() for s in _PROFILE.split(text or "") if s and s.strip()]
    return out or [str(text or "").strip()]
