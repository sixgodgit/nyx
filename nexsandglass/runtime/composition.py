"""Deterministic composition of indivisible context items under one budget.

Callers establish source eligibility and assign explicit priority/requirements.
This module performs no retrieval, trust inference, truncation or persistence.
The default counter preserves Nyx's existing estimate, not model-exact tokens.
Custom counters must be deterministic and count the empty string as zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from nexsandglass.runtime.contracts import EvidenceRef


def estimate_tokens(text: str) -> int:
    """Existing Nyx estimate: one per CJK character, others four per token."""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


def _key(value, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")


def _integer(value, name: str, *, nonnegative: bool = False) -> None:
    if type(value) is not int or (nonnegative and value < 0):
        raise ValueError(f"{name} must be {'a nonnegative ' if nonnegative else 'an '}integer")


def _evidence(values) -> tuple[EvidenceRef, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("evidence must be a collection of EvidenceRef values")
    values = tuple(values)
    if not all(isinstance(value, EvidenceRef) for value in values):
        raise TypeError("evidence must contain EvidenceRef values")
    return values


@dataclass(frozen=True)
class ContextItem:
    key: str
    text: str
    priority: int = 0
    required: bool = False
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self):
        _key(self.key, "item key")
        if not isinstance(self.text, str):
            raise TypeError("item text must be text")
        _integer(self.priority, "item priority")
        if type(self.required) is not bool:
            raise TypeError("required must be boolean")
        object.__setattr__(self, "evidence", _evidence(self.evidence))


@dataclass(frozen=True)
class ContextSection:
    key: str
    heading: str = ""
    items: tuple[ContextItem, ...] = ()
    priority: int = 0
    prefix: str = "- "
    suffix: str = ""

    def __post_init__(self):
        _key(self.key, "section key")
        for name in ("heading", "prefix", "suffix"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"section {name} must be text")
        _integer(self.priority, "section priority")
        if isinstance(self.items, (str, bytes)):
            raise TypeError("section items must contain ContextItem values")
        items = tuple(self.items)
        if not all(isinstance(item, ContextItem) for item in items):
            raise TypeError("section items must contain ContextItem values")
        if len({item.key for item in items}) != len(items):
            raise ValueError("item keys must be unique within each section")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True)
class CompositionResult:
    text: str
    token_budget: int
    est_tokens: int
    selected: tuple[tuple[str, str], ...] = ()
    omitted: tuple[tuple[str, str], ...] = ()
    required_omitted: tuple[tuple[str, str], ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    counter_name: str = "nyx_estimate_v1"

    def __post_init__(self):
        if not isinstance(self.text, str):
            raise TypeError("result text must be text")
        _integer(self.token_budget, "token budget", nonnegative=True)
        _integer(self.est_tokens, "estimated tokens", nonnegative=True)
        if self.est_tokens > self.token_budget:
            raise ValueError("estimated tokens exceed the token budget")
        for name in ("selected", "omitted", "required_omitted"):
            values = getattr(self, name)
            if isinstance(values, (str, bytes)):
                raise TypeError(f"{name} must contain section/item key pairs")
            pairs = []
            for pair in values:
                if isinstance(pair, (str, bytes)):
                    raise TypeError(f"{name} must contain section/item key pairs")
                pair = tuple(pair)
                if len(pair) != 2:
                    raise ValueError(f"{name} must contain section/item key pairs")
                _key(pair[0], "section key")
                _key(pair[1], "item key")
                pairs.append(pair)
            object.__setattr__(self, name, tuple(pairs))
        object.__setattr__(self, "evidence", _evidence(self.evidence))
        _key(self.counter_name, "counter name")


def compose_context(
    sections: Iterable[ContextSection],
    token_budget: int,
    *,
    counter: Callable[[str], int] | None = None,
    separator: str = "\n",
) -> CompositionResult:
    """Select whole evidence with all final framing charged to one budget.

    All required nonblank items must fit together before optional admission.
    A required blank item is also unavailable, so required failure returns an
    empty result and reports every required item as omitted. Zero budget always
    returns empty, including when the counter rounds short nonempty text to zero.

    Selection order is required first, then section priority, item priority and
    stable input position. Display order remains the original section/item order.
    Optional greedy admission is deterministic; it is not a knapsack optimizer.
    """
    _integer(token_budget, "token budget", nonnegative=True)
    if not isinstance(separator, str):
        raise TypeError("separator must be text")
    if isinstance(sections, (str, bytes)):
        raise TypeError("sections must contain ContextSection values")
    sections = tuple(sections)
    if not all(isinstance(section, ContextSection) for section in sections):
        raise TypeError("sections must contain ContextSection values")
    if len({section.key for section in sections}) != len(sections):
        raise ValueError("section keys must be unique")
    if counter is not None and not callable(counter):
        raise TypeError("counter must be callable")
    count_fn = estimate_tokens if counter is None else counter
    counter_name = ("nyx_estimate_v1" if counter is None else
                    str(getattr(count_fn, "__name__", type(count_fn).__name__)))

    def count(text: str) -> int:
        value = count_fn(text)
        if type(value) is not int or value < 0:
            raise ValueError("counter must return a nonnegative integer")
        return value

    if count("") != 0:
        raise ValueError("counter must count empty text as zero")
    entries = [(si, ii, section, item) for si, section in enumerate(sections)
               for ii, item in enumerate(section.items)]
    required = {(si, ii) for si, ii, _, item in entries if item.required}

    def render(picked: set[tuple[int, int]]) -> str:
        blocks = []
        grouped = {}
        # Iterate retained positions, not every rejected input item on every
        # trial. Ordering by indexes restores the original display order.
        for si, ii in sorted(picked):
            section = sections[si]
            grouped.setdefault(si, []).append(section.prefix + section.items[ii].text)
        for si, lines in grouped.items():
            section = sections[si]
            if section.heading:
                lines.insert(0, section.heading)
            if section.suffix:
                lines.append(section.suffix)
            blocks.append("\n".join(lines))
        return separator.join(blocks)

    def result(picked: set[tuple[int, int]], text: str, cost: int) -> CompositionResult:
        selected, omitted, required_omitted, evidence = [], [], [], []
        for si, ii, section, item in entries:
            pair = (section.key, item.key)
            if (si, ii) in picked:
                selected.append(pair)
                evidence.extend(item.evidence)
            else:
                omitted.append(pair)
                if item.required:
                    required_omitted.append(pair)
        return CompositionResult(text=text, token_budget=token_budget, est_tokens=cost,
                                 selected=tuple(selected), omitted=tuple(omitted),
                                 required_omitted=tuple(required_omitted), evidence=tuple(evidence),
                                 counter_name=counter_name)

    if not token_budget:
        return result(set(), "", 0)
    if any(item.required and not item.text.strip() for _, _, _, item in entries):
        return result(set(), "", 0)
    picked = set(required)
    text = render(picked)
    cost = count(text)
    if cost > token_budget:
        return result(set(), "", 0)
    optional = [entry for entry in entries if not entry[3].required and entry[3].text.strip()]
    optional.sort(key=lambda entry: (-entry[2].priority, -entry[3].priority, entry[0], entry[1]))
    for si, ii, _, _ in optional:
        proposed = picked | {(si, ii)}
        candidate = render(proposed)
        candidate_cost = count(candidate)
        if candidate_cost <= token_budget:
            picked, text, cost = proposed, candidate, candidate_cost
    return result(picked, text, cost)
