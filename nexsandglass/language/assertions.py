"""Conservative assertion and polarity cues; identity is not truth status."""
from __future__ import annotations
import re

_HYPOTHETICAL = re.compile(r"如果|假如|假设|万一|倘若|\bif\b|\bunless\b|\bsuppos(?:e|ing)\b|\bwhat if\b", re.I)
_UNCERTAIN = re.compile(r"可能|也许|不确定|说不准|好像|或许|\bmaybe\b|\bperhaps\b|\bmight\b|\bmay\s+(?:be|have|like|live|work|not|go|cancel\w*|need|change|happen)\b|"
                        r"\bnot sure\b|\bpossibly\b|\bi think i\b", re.I)
_HISTORICAL = re.compile(r"以前|曾经|过去|原来|小时候|\bused to\b|\bpreviously\b|\bformerly\b|\bback then\b|\bin the past\b", re.I)
_DOUBLE = re.compile(r"不是不|并非不|不只是|不仅|\bnot only\b|\bnot unlike\b|\bdon'?t not\b", re.I)
_NEG = re.compile(r"(?:不再|不|没有|没|未)(?:太|很|怎么)?$|\b(?:not|never|no longer|don'?t|do not|doesn'?t|didn'?t|"
                  r"stopped|stop)\b(?:\s+(?:really|actually|still|ever|to))*\s*$", re.I)


def assertion_state(text: str) -> str:
    if (text.rstrip().endswith(("?", "？")) or re.match(r"^\s*(?:do|does|did|can|could|should|would|am|are)\s+(?:i|we)\b", text, re.I)
            or re.search(r"(?:吗|是否|是不是)(?:[？?。.!！]*)$", text)):
        return "question"
    if _HYPOTHETICAL.search(text):
        return "conditional"
    if _UNCERTAIN.search(text):
        return "uncertain"
    if _DOUBLE.search(text):
        return "ambiguous_negation"
    if _HISTORICAL.search(text):
        return "historical"
    return "affirmed"


def polarity_before(text: str, start: int) -> int:
    return -1 if _NEG.search(text[max(0, start-55):start]) else 1


_PREF_EN = re.compile(r"\b(love|like|enjoy|prefer|adore|hate|dislike)\s+([a-z][a-z' -]{0,45}?)"
                      r"(?=[,.;!?]|$|\s+(?:and|but|because|so|now|anymore|any more|these days|lately|too)\b)", re.I)
_PREF_ZH = re.compile(r"(喜欢|偏好|偏爱|爱吃|爱喝|爱看|热爱|讨厌|反感|厌恶|受不了)\s*([^，。！？、\s了的啊呀吧]{1,15})")


def preference_hits(text: str):
    from .bilingual import _EN_DET
    out = []
    for rx in (_PREF_EN, _PREF_ZH):
        for m in rx.finditer(text):
            verb, obj = m.groups()
            if rx is _PREF_EN:
                obj = _EN_DET.sub("", obj.strip())
            else:
                obj = re.sub(r"^(吃|喝|看|玩|听|用)", "", obj)
            if not obj or obj.lower() in ("you", "it", "that", "this", "them"):
                continue
            relation = "反感" if verb.lower() in ("hate", "dislike", "讨厌", "反感", "厌恶", "受不了") else "偏好"
            out.append((relation, obj, m.group(), m.start(), polarity_before(text, m.start())))
    return out
