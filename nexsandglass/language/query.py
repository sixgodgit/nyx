"""Bounded lexical equivalence for retrieval; never assertions or entity IDs."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata

from .ontology import ALIASES

QUERY_POLICY_VERSION = "1"
MAX_QUERY_CHARS = 2000
MAX_EVIDENCE_CHARS = 8000
MAX_TERMS = 12

# Retrieval topics are lexical cues, not predicates asserting truth about a user.
_TOPICS = {
    "allergy": ("过敏", "過敏", "allergic", "allergy", "allergies"),
    "intolerance": ("不耐受", "不耐", "intolerant", "intolerance"),
    "preference": ("喜欢", "喜歡", "讨厌", "討厭", "偏好", "爱喝", "爱吃", "like", "likes", "love", "enjoy", "prefer", "dislike", "hate"),
    "residence": ("住在", "居住", "住哪", "住哪里", "live", "lives", "lived", "reside"),
    "vehicle": ("使用", "座驾", "座駕", "drive", "drives", "use", "uses"),
    "employer": ("公司", "雇主", "工作单位", "work at", "work for", "employer"),
    "email": ("邮箱", "郵箱", "email"),
    "phone": ("电话", "電話", "phone"),
}
_PHRASES = {**{"concept:" + k: v for k, v in ALIASES.items()},
            **{"topic:" + k: v for k, v in _TOPICS.items()}}


def _pattern(alias):
    escaped = re.escape(alias)
    return r"(?<!\w)" + escaped + r"(?!\w)" if alias.isascii() else escaped


_ALIAS_TO_TERM = {alias: term for term, aliases in _PHRASES.items() for alias in aliases}
_RX = re.compile("|".join(_pattern(s) for s in sorted(_ALIAS_TO_TERM, key=lambda s: (-len(s), s))), re.I)
_LEFT_MODIFIER = re.compile(r"(?:\b(?:raw|roasted|salted|unsalted|refined|unrefined|crushed|ground)\s+|生|熟|烤|炒|精炼|未精炼)$", re.I)
_RIGHT_MODIFIER = re.compile(r"^(?:\s+(?:oil|butter|flour|powder|sauce|protein|extract|milk)\b|油|酱|醬|粉|蛋白|提取物|汁)", re.I)
_SHORT_LEFT = re.compile(r"(?:对|對|的|吃|喝|爱|愛|喜欢|喜歡|讨厌|討厭|买了|買了|吃了|吃过|吃過|提过|提過|提到|关于|關於|有|是)$")
_SHORT_RIGHT = re.compile(r"^(?:过敏|過敏|可以|能|会|會|有|的|吗|嗎|呢|不|也|和|与|與|是|在)")
_FAMILY = re.compile(r"(?:我(?:的)?|my\s+)(女儿|女兒|daughter|儿子|兒子|son|妈妈|母亲|mother|mom|爸爸|父亲|father|dad|妻子|wife|丈夫|husband)(?![a-z])", re.I)
_FAMILY_KEYS = {"女儿": "daughter", "女兒": "daughter", "儿子": "son", "兒子": "son",
                "妈妈": "mother", "母亲": "mother", "mom": "mother", "爸爸": "father", "父亲": "father", "dad": "father",
                "妻子": "wife", "丈夫": "husband"}


def _scope(text, *, query):
    family = _FAMILY.search(text)
    if family:
        # A comparison or a recollection about several people has no one owner.
        if re.search(r"我和|我与|\band\b|\bcompare\b|不同|区别", text, re.I):
            return None
        name = family.group(1).lower()
        return _FAMILY_KEYS.get(name, name)
    pattern = r"我(?:对|喜欢|不喜欢|住|使用)|\b(?:am i|do i|where do i|i (?:like|live|use))\b" if query else r"^\s*(?:我|i\b)"
    return "owner" if re.search(pattern, text, re.I) else None


def terms(text: str, *, query=False) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    text = text[:MAX_QUERY_CHARS if query else MAX_EVIDENCE_CHARS]
    out = []
    for match in _RX.finditer(text):
        term = _ALIAS_TO_TERM[match.group().casefold()]
        before, after = text[:match.start()], text[match.end():]
        single = len(match.group()) == 1 and not match.group().isascii()
        compound = single and ((before and "\u4e00" <= before[-1] <= "\u9fff" and not _SHORT_LEFT.search(before))
                               or (after and "\u4e00" <= after[0] <= "\u9fff" and not _SHORT_RIGHT.search(after)))
        if term.startswith("concept:") and (compound or
                _LEFT_MODIFIER.search(before) or _RIGHT_MODIFIER.search(after)):
            if "qualified:" + term not in out:
                out.append("qualified:" + term)
            continue
        if term not in out:
            out.append(term)
    return tuple(out)


@dataclass(frozen=True)
class QueryPlan:
    concepts: tuple[str, ...]
    topics: tuple[str, ...]
    subject: str | None = field(default=None, repr=False)
    qualified: tuple[str, ...] = ()

    @property
    def alternatives(self):
        return tuple(dict.fromkeys(alias for term in self.concepts + self.topics for alias in _PHRASES[term]))

    def matches(self, text: str) -> bool:
        if not (self.concepts or self.topics):
            return False
        present = set(terms(text))
        if self.concepts and not present.intersection(self.concepts):
            return False
        if self.topics and not present.intersection(self.topics):
            return False
        scope = _scope(str(text), query=False)
        return not (self.subject and scope and scope != self.subject)

    def compatible(self, text: str) -> bool:
        """Reject explicit typed conflicts; absence of a rule is not a denial."""
        present = set(terms(text))
        concepts = {t for t in present if t.startswith("concept:")}
        topics = {t for t in present if t.startswith("topic:")}
        if self.concepts and concepts and not concepts.intersection(self.concepts):
            return False
        if self.topics and topics and not topics.intersection(self.topics):
            return False
        if self.concepts and not concepts.intersection(self.concepts) and any("qualified:" + t in present for t in self.concepts):
            return False
        if self.qualified and any(t.removeprefix("qualified:") in concepts and t not in present for t in self.qualified):
            return False
        scope = _scope(str(text), query=False)
        return not (self.subject and scope and scope != self.subject)


def plan(query: str) -> QueryPlan:
    found = terms(query, query=True)
    # Oversized plans keep the old lexical route rather than discard constraints.
    if len(found) > MAX_TERMS or len(str(query)) > MAX_QUERY_CHARS:
        return QueryPlan((), ())
    qualified = tuple(t for t in found if t.startswith("qualified:"))
    if qualified:
        return QueryPlan((), (), _scope(str(query), query=True), qualified)
    return QueryPlan(tuple(t for t in found if t.startswith("concept:")),
                     tuple(t for t in found if t.startswith("topic:")), _scope(str(query), query=True))
