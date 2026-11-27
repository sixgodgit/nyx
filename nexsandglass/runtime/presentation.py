"""Optional display framing; source units and protocol identities never change.

Translations are supplied by a trusted host as display data. This module does
not translate, call models, verify linguistic correctness or persist anything.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import json
import re

from .composition import ContextSection, compose_context
from .contracts import LanguageContext, _identifier

PRESENTATION_POLICY_VERSION = "1"
MAX_TRANSLATIONS = 32
MAX_TRANSLATION_CHARS = 8000


def _tag(value):
    return LanguageContext(response_language=value, response_basis="configuration").response_language


def frame_language(tag):
    parts = tag.split("-")
    if parts[0] == "en":
        return "en"
    if parts[0] == "zh" and not any(p in ("hant", "tw", "hk", "mo") for p in parts[1:]):
        return "zh"
    return "en"


def unit_digest(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("unit text must be text")
    return sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class DisplayTranslation:
    section_key: str
    item_key: str
    source_ids: tuple[str, ...]
    source_sha256: str
    target_language: str
    text: str = field(repr=False)

    def __post_init__(self):
        for name in ("section_key", "item_key"):
            _identifier(name, getattr(self, name))
        if isinstance(self.source_ids, (str, bytes)):
            raise TypeError("source_ids must be a collection")
        ids = tuple(self.source_ids)
        if not ids or len(ids) > 32 or len(ids) != len(set(ids)):
            raise ValueError("source_ids must contain unique source identifiers")
        for mid in ids:
            _identifier("source_id", mid)
        if not isinstance(self.source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("source_sha256 must be the original unit's SHA256")
        if not isinstance(self.text, str) or not self.text.strip() or len(self.text) > MAX_TRANSLATION_CHARS:
            raise ValueError("translation must be nonblank bounded text")
        object.__setattr__(self, "source_ids", ids)
        object.__setattr__(self, "target_language", _tag(self.target_language))


@dataclass(frozen=True)
class PresentationOptions:
    language: str
    translations: tuple[DisplayTranslation, ...] = field(default=(), repr=False)

    def __post_init__(self):
        object.__setattr__(self, "language", _tag(self.language))
        if isinstance(self.translations, (str, bytes)):
            raise TypeError("translations must be a collection")
        values = tuple(self.translations)
        if len(values) > MAX_TRANSLATIONS or not all(isinstance(t, DisplayTranslation) for t in values):
            raise ValueError("translations must contain at most 32 DisplayTranslation values")
        if len({(t.section_key, t.item_key) for t in values}) != len(values):
            raise ValueError("duplicate translation targets are ambiguous")
        object.__setattr__(self, "translations", values)


@dataclass(frozen=True)
class PresentationAudit:
    requested_language: str
    frame_language: str
    translations_used: int
    translations_omitted: int
    policy_version: str = PRESENTATION_POLICY_VERSION


def presentation_for(language, *, translations=()):
    if language is not None and not isinstance(language, LanguageContext):
        raise TypeError("language must be a LanguageContext or None")
    if language is None or language.response_language is None:
        if translations:
            raise ValueError("translations require an explicit response language")
        return None
    return PresentationOptions(language.response_language, translations)


_HEADINGS = {
    "profile": ("[About the user: their own words; [date] = when they said it]",
                "[关于用户：保留原话；[日期]表示说出这段话的时间]"),
    "profile.history": ("Changed since (their earlier words, replaced by what is above):",
                        "已更正的早期记录（当前表述见上方）："),
    "retrieval": ("## Nyx memory (relevant to this turn; [date] = when it was said)",
                  "## Nyx 记忆（与本轮相关；[日期]表示记录中的发言时间）"),
    "followups": ("[Follow-ups: things they told you about that are coming up or just happened; their own words, [date] = when they said it]",
                  "[待跟进：用户提过的近期事项；以下保留原话，[日期]表示发言时间]"),
}
_FOLLOWUP_GUIDANCE = ("If it fits — at the start of a conversation or in small talk — you may ask how one of these went, "
                     "or wish them luck. At most once per conversation and briefly; never interrupt a task for it, "
                     "and let it go if they don't pick it up.")


def compose_presented(sections, budget, options, *, counter=None, separator="\n\n"):
    """Admit originals first; translations may not evict an admitted source."""
    if not isinstance(options, PresentationOptions):
        raise TypeError("presentation must be PresentationOptions")
    sections = tuple(sections)
    frame = frame_language(options.language)
    localized = []
    for section in sections:
        heading = _HEADINGS.get(section.key)
        if frame == "zh" and heading and section.heading == heading[0]:
            section = replace(section, heading=heading[1])
        if frame == "zh" and section.key == "followups" and section.suffix == _FOLLOWUP_GUIDANCE:
            section = replace(section, suffix="合适时可在开场或寒暄中简短问一句结果，或祝对方顺利。每次对话至多一次；不要打断任务，对方不接话就不再追问。")
        localized.append(section)
    localized = tuple(localized)
    preamble = (
        f"首选回答语言：{options.language}。以下证据保留原文。宿主提供的译文未经语义核验，"
        "仅供参考；请引用原证据，保留来源、否定和条件。" if frame == "zh" else
        f"Preferred answer language: {options.language}. Evidence stays in its original language. "
        "Host-supplied translations are unverified display data; cite the original and retain its source, negation and conditions.")

    def compose(values):
        return compose_context(values, budget, counter=counter, separator=separator, preamble=preamble)

    result = compose(localized)
    selected = set(result.selected)
    supplied = {(t.section_key, t.item_key): t for t in options.translations}
    used = 0
    for si, section in enumerate(localized):
        for ii, item in enumerate(section.items):
            pair = (section.key, item.key)
            translation = supplied.get(pair)
            if translation is None or pair not in selected:
                continue
            ids = tuple(dict.fromkeys(e.source_mem_id for e in item.evidence))
            if (not ids or None in ids or ids != translation.source_ids
                    or translation.target_language != options.language
                    or translation.source_sha256 != unit_digest(item.text)):
                continue
            label = "宿主提供的译文（未经核验，仅供参考）" if frame == "zh" else "Host-supplied translation (unverified display data)"
            combined = item.text + "\n" + label + ": " + translation.text
            items = list(localized[si].items)
            items[ii] = replace(item, text=combined)
            proposed = list(localized)
            proposed[si] = replace(localized[si], items=tuple(items))
            candidate = compose(tuple(proposed))
            if candidate.selected == result.selected and not candidate.required_omitted:
                localized, result = tuple(proposed), candidate
                used += 1
    omitted = len(options.translations) - used
    reasons = []
    if frame != options.language.split("-")[0] or (options.language.startswith("zh-") and frame != "zh"):
        reasons.append("presentation_language_fallback")
    if omitted:
        reasons.append("translation_unavailable")
    return result, PresentationAudit(options.language, frame, used, omitted), tuple(reasons)


@dataclass(frozen=True)
class FactPresentation:
    source_json: str = field(repr=False)
    language: str
    frame_language: str
    display_text: str = field(repr=False)
    assertion: str = field(default="affirmed", repr=False)
    time_basis_json: str = field(default="null", repr=False)

    def to_dict(self):
        return {"source": json.loads(self.source_json), "presentation": {
            "language": self.language, "frame_language": self.frame_language,
            "text": self.display_text, "basis": "derived_display", "assertion": self.assertion,
            "time_resolution": json.loads(self.time_basis_json), "policy_version": PRESENTATION_POLICY_VERSION}}


def present_fact(fact, *, language="en"):
    """Format a detached extraction snapshot; never promote it to a belief."""
    from nexsandglass.core.understand import Fact
    from nexsandglass.language.ontology import canonical_predicate
    if not isinstance(fact, Fact):
        raise TypeError("fact must be an extracted Fact")
    tag = _tag(language)
    frame = frame_language(tag)
    negative = fact.polarity < 0 or fact.relation.startswith("not:")
    base = fact.relation.removeprefix("not:")
    predicate = canonical_predicate(base)
    en = {"lives_in": "lives in", "uses_vehicle": "uses", "employer": "works at", "job_title": "works as",
          "email": "email is", "phone": "phone is", "likes": "likes", "dislikes": "dislikes"}
    zh = {"lives_in": "住在", "uses_vehicle": "使用", "employer": "任职于", "job_title": "职位为",
          "email": "邮箱为", "phone": "电话为", "likes": "喜欢", "dislikes": "讨厌"}
    relation = (zh if frame == "zh" else en).get(predicate, base)
    if negative:
        neg_en = {"lives in": "does not live in", "uses": "does not use", "works at": "does not work at",
                  "works as": "does not work as", "likes": "does not like", "dislikes": "does not dislike"}
        neg_zh = {"住在": "不住在", "使用": "不使用", "任职于": "不任职于", "职位为": "职位不是",
                  "邮箱为": "邮箱不是", "电话为": "电话不是", "喜欢": "不喜欢", "讨厌": "不讨厌"}
        relation = neg_zh.get(relation, "否定：" + relation) if frame == "zh" else neg_en.get(relation, "denies: " + relation)
    label = "派生事实" if frame == "zh" else "Derived fact"
    if fact.deferred or fact.assertion != "affirmed":
        label = "暂缓使用的派生表述" if frame == "zh" else "Deferred derived statement"
    text = f"[{label}] {fact.subject} {relation} {fact.object}"
    if fact.valid_from is not None or fact.ongoing is not None:
        validity = {"valid_from": fact.to_dict()["valid_from"], "ongoing": fact.ongoing}
        text += "\n" + ("时间状态：" if frame == "zh" else "Temporal state: ") + json.dumps(validity, ensure_ascii=False)
    if fact.evidence:
        text += ("\n证据原文：" if frame == "zh" else "\nOriginal evidence: ") + fact.evidence
    return FactPresentation(json.dumps(fact.to_dict(), ensure_ascii=False, allow_nan=False), tag, frame, text,
                            fact.assertion, json.dumps(fact.time_resolution, ensure_ascii=False, allow_nan=False))


def recall_presented(request, *, translations=(), **kwargs):
    """Explicit host-rendered recall; recall_request() retains its raw contract."""
    from .host_context import recall_host_context
    options = presentation_for(request.language, translations=translations)
    return recall_host_context(request, presentation=options, **kwargs)[0]
