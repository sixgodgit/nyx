"""Explicit rule adapter boundary. Selection does not change store or trust."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Any

from .ontology import SemanticKey, fact_key


class RuleAdapter(Protocol):
    name: str
    languages: tuple[str, ...]

    def extract_facts(self, text: str, *, now: datetime, subject: str) -> list: ...
    def classify_profile(self, text: str) -> tuple | None: ...
    def profile_slots(self, kind: str, clause: str) -> set: ...


class BilingualRules:
    name = "zh-en-rules-v1"
    languages = ("zh", "en")

    def extract_facts(self, text, *, now, subject):
        # Reuse the established parser and temporal policy, rather than add a
        # parallel extraction engine. Optional LLM extraction stays upstream.
        from nexsandglass.core.understand import _extract_rules_impl
        return _extract_rules_impl(text, now=now, subject=subject)

    def classify_profile(self, text):
        from .bilingual import classify
        return classify(text)

    def profile_slots(self, kind, clause):
        from .bilingual import slots
        return slots(kind, clause)


DEFAULT_ADAPTER = BilingualRules()


@dataclass(frozen=True)
class LanguageAnalysis:
    adapter: str
    status: str
    facts: tuple[Any, ...] = field(default=(), repr=False)
    keys: tuple[SemanticKey, ...] = ()


def analyze(text: str, *, now: datetime | None = None, subject: str = "user",
            language: str | None = None, adapter: RuleAdapter | None = None) -> LanguageAnalysis:
    """A bounded capability report; unknown declared languages keep raw evidence.

    The caller owns source recording. 'no_match' is not a guessed language.
    An explicitly supplied adapter is trusted application code, not user text.
    Legacy Fact instances keep their existing mutability/export semantics.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if language is not None and not isinstance(language, str):
        raise TypeError("language must be a string or None")
    selected = adapter or DEFAULT_ADAPTER
    primary = language.casefold().split("-", 1)[0] if language else None
    if primary not in (None, "mixed", "unknown", *selected.languages):
        return LanguageAnalysis(selected.name, "unsupported_language")
    if now is None:
        from nexsandglass.core import clock
        now = clock.now()
    facts = tuple(selected.extract_facts(text, now=now, subject=subject))
    return LanguageAnalysis(selected.name, "matched" if facts else "no_match", facts,
                            tuple(fact_key(f.subject, f.relation, f.object) for f in facts))
