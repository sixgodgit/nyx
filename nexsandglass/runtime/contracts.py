"""Additive, dependency-free recall contracts for in-process host adapters.

Request identifiers correlate work; they do not authorize access or filter the
store. A monotonic deadline belongs to this process and is not a wire timestamp.
Packets contain selected evidence, never pre-gate debug trace payloads.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from numbers import Real
import re
import time
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _identifier(name: str, value, *, optional: bool = False):
    if value is None and optional:
        return
    if (not isinstance(value, str) or not value.strip() or len(value) > 128
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f"{name} must be a nonempty identifier of at most 128 characters")


_LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*\Z")
_INPUT_BASES = frozenset(("unspecified", "detected", "user", "configuration"))
_RESPONSE_BASES = frozenset(("unspecified", "user", "configuration"))


def _language_tag(name: str, value, *, optional: bool = False, input_tag: bool = False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError(f"{name} must be a bounded language tag")
    value = value.lower()
    if input_tag and value in ("mixed", "unknown"):
        return value
    if value in ("mixed", "unknown", "auto") or not _LANGUAGE_TAG.fullmatch(value):
        raise ValueError(f"{name} must be a BCP-47-style tag")
    return value


@dataclass(frozen=True)
class LanguageContext:
    """Request metadata, independent of storage, trust and persisted preferences.

    Input detection never chooses a response language. A response preference
    needs an explicitly declared user/configuration basis; this declaration is
    still caller metadata, not proof of permission or instruction authority.
    """
    input_language: str = "unknown"
    input_basis: str = "unspecified"
    response_language: str | None = None
    response_basis: str = "unspecified"
    locale: str | None = None
    timezone: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "input_language",
                           _language_tag("input_language", self.input_language, input_tag=True))
        object.__setattr__(self, "response_language",
                           _language_tag("response_language", self.response_language, optional=True))
        object.__setattr__(self, "locale", _language_tag("locale", self.locale, optional=True))
        if not isinstance(self.input_basis, str) or self.input_basis not in _INPUT_BASES:
            raise ValueError("invalid input language basis")
        if not isinstance(self.response_basis, str) or self.response_basis not in _RESPONSE_BASES:
            raise ValueError("invalid response language basis")
        if (self.response_language is None) != (self.response_basis == "unspecified"):
            raise ValueError("response language requires a user or configuration basis")
        if self.timezone is not None:
            _identifier("timezone", self.timezone)
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("timezone must identify an available IANA zone") from None

    def to_dict(self) -> dict:
        return asdict(self)


def _language_context(value):
    if value is not None and not isinstance(value, LanguageContext):
        raise TypeError("language must be a LanguageContext or None")


@dataclass(frozen=True)
class RecallRequest:
    query: str = field(repr=False)
    context: str | None = field(default=None, repr=False)
    token_budget: int = 1500
    session_id: str | None = None
    turn_id: str | None = None
    revision: int = 0
    task_id: str | None = None
    scene_id: str | None = None
    channel: str = "text"
    request_id: str = field(default_factory=lambda: uuid4().hex)
    deadline: float | None = None
    language: LanguageContext | None = None

    def __post_init__(self):
        _language_context(self.language)
        if not isinstance(self.query, str):
            raise TypeError("query must be text")
        if self.context is not None and not isinstance(self.context, str):
            raise TypeError("context must be text or None")
        for name in ("token_budget", "revision"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 2**31 - 1:
                raise ValueError(f"{name} must be a nonnegative 32-bit integer")
        for name in ("session_id", "turn_id", "task_id", "scene_id", "channel", "request_id"):
            _identifier(name, getattr(self, name), optional=name not in ("channel", "request_id"))
        if self.deadline is not None:
            if (isinstance(self.deadline, bool) or not isinstance(self.deadline, Real)
                    or not math.isfinite(self.deadline) or self.deadline < 0):
                raise ValueError("deadline must be a finite nonnegative monotonic timestamp")


@dataclass(frozen=True)
class EvidenceRef:
    memory_id: str
    source_mem_id: str | None = None
    source_line: int | None = None
    retrieval_source: str = ""
    origin: str = ""
    trust_signal: str = "unknown"
    valid_from: str = ""
    valid_until: str = ""

    def __post_init__(self):
        for name in ("memory_id", "retrieval_source", "origin", "trust_signal", "valid_from", "valid_until"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be text")
        if self.source_mem_id is not None and not isinstance(self.source_mem_id, str):
            raise TypeError("source_mem_id must be text or None")
        if self.source_line is not None and (type(self.source_line) is not int or self.source_line <= 0):
            raise ValueError("source_line must be a positive integer or None")


@dataclass(frozen=True)
class ContextPacket:
    request_id: str
    session_id: str | None = None
    turn_id: str | None = None
    revision: int = 0
    task_id: str | None = None
    scene_id: str | None = None
    channel: str = "text"
    text: str = field(default="", repr=False)
    evidence: tuple[EvidenceRef, ...] = ()
    token_budget: int = 1500
    est_tokens: int = 0
    status: str = "empty"  # ready / empty / partial / expired / error
    stage: str = "context"  # internal candidates are NOT a final injection
    degraded: bool = False
    deadline_exceeded: bool = False
    reason_codes: tuple[str, ...] = ()
    language: LanguageContext | None = None
    schema_version: int = field(default=1, init=False)

    def __post_init__(self):
        _language_context(self.language)
        for name in ("session_id", "turn_id", "task_id", "scene_id", "channel", "request_id"):
            _identifier(name, getattr(self, name), optional=name not in ("channel", "request_id"))
        if self.status not in ("ready", "empty", "partial", "expired", "error"):
            raise ValueError("invalid packet status")
        if self.stage not in ("context", "candidates"):
            raise ValueError("invalid packet stage")
        if isinstance(self.evidence, (str, bytes)) or isinstance(self.reason_codes, (str, bytes)):
            raise TypeError("evidence and reason_codes must be collections")
        evidence, reasons = tuple(self.evidence), tuple(self.reason_codes)
        if not all(isinstance(e, EvidenceRef) for e in evidence):
            raise TypeError("evidence must contain EvidenceRef values")
        if not all(isinstance(code, str) for code in reasons):
            raise TypeError("reason_codes must contain text")
        if not isinstance(self.text, str):
            raise TypeError("packet text must be text")
        for name in ("degraded", "deadline_exceeded"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be boolean")
        for name in ("token_budget", "est_tokens", "revision"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 2**31 - 1:
                raise ValueError(f"{name} must be a nonnegative 32-bit integer")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "reason_codes", reasons)

    def matches_request(self, request: RecallRequest) -> bool:
        """Correlation only: the caller supplies its latest request/revision."""
        return isinstance(request, RecallRequest) and self.language == request.language and all(
            getattr(self, name) == getattr(request, name) for name in (
                "request_id", "session_id", "turn_id", "revision", "task_id", "scene_id", "channel", "token_budget"))

    def can_apply_to(self, request: RecallRequest) -> bool:
        """Check correlation, stage and deadline; not a storage-freshness check.

        No global 'latest turn' registry is implied. Hosts must pass their
        current request and handle later forgetting/context invalidation.
        """
        return (self.matches_request(request) and self.stage == "context"
                and self.status not in ("expired", "error") and not self.deadline_exceeded
                and self.est_tokens <= request.token_budget
                and (request.deadline is None or time.monotonic() < request.deadline))

    def to_dict(self) -> dict:
        """Safe export boundary: no query, task text, deadline or debug payload."""
        result = asdict(self)
        if self.language is None:
            # Preserve the original export shape for callers that opt out.
            result.pop("language")
        return result


def packet_for(request: RecallRequest, **payload) -> ContextPacket:
    """Copy correlation fields consistently, without copying request contents."""
    metadata = {name: getattr(request, name) for name in (
        "request_id", "session_id", "turn_id", "revision", "task_id", "scene_id", "channel", "language")}
    return ContextPacket(**metadata, token_budget=request.token_budget, **payload)
