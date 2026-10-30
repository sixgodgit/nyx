"""
core/lang.py — which language Nyx speaks to people (v7.16.0). English first.

Two kinds of text, two rules:

- **Model-facing** (tool descriptions and results, recalled-memory labels, the system prompt frame,
  redaction placeholders): always English. Models read it best, and some of it ends up in stored data.
- **Human-facing** (CLI output, `hermes memory setup`, install messages): English, or Chinese when asked —
  ``NYX_LANG=zh`` (Hermes: ``lang: zh`` in nyx.json). Without NYX_LANG, a Chinese locale
  (LC_ALL / LC_MESSAGES / LANG starting with ``zh``) also picks Chinese.

    from nexsandglass.core.lang import t
    print(t("No vault key on this machine", "这台机器上没有保险箱密钥"))
"""
from __future__ import annotations

import os


def is_zh() -> bool:
    from nexsandglass.core import settings      # Hermes 里按 profile 的 nyx.json lang（v7.17.0）
    v = settings.get("NYX_LANG").strip().lower()
    if v and v != "auto":
        return v.startswith("zh")
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        loc = (os.environ.get(key) or "").strip().lower()
        if loc:
            return loc.startswith("zh")
    return False


def t(en: str, zh: str) -> str:
    """Human-facing text: English unless Chinese was asked for."""
    return zh if is_zh() else en
