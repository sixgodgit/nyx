"""Shared host rendering and content-free generation traces.

Hosts establish store scope and source eligibility. A returned packet records
generation, not transport delivery or proof that the model used the memory.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import time

from .composition import ContextItem, ContextSection, compose_context, estimate_tokens
from .contracts import ContextPacket, EvidenceRef, RecallRequest, packet_for


@dataclass(frozen=True)
class HostTrace:
    request_id: str
    channel: str
    session_id: str | None
    turn_id: str | None
    revision: int
    status: str
    token_budget: int
    rendered_tokens: int
    reserved_tokens: int
    selected_count: int
    omitted_count: int
    required_omitted: tuple[tuple[str, str], ...]
    evidence_ids: tuple[str, ...]
    text_sha256: str
    reason_codes: tuple[str, ...]
    counter_name: str = "nyx_estimate_v1"
    stage: str = "generated"
    sent: bool | None = None
    applied: bool | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def render_host_context(request: RecallRequest, sections, *, reserved_tokens: int = 0,
                        max_chars: int | None = None, reason_codes=(),
                        deadline_exceeded: bool = False) -> tuple[ContextPacket, HostTrace]:
    """Use the common token budget, optionally also enforcing a voice char cap."""
    if max_chars is not None and (type(max_chars) is not int or max_chars < 0):
        raise ValueError("max_chars must be a nonnegative integer")

    def bounded_estimate(text):
        if max_chars is not None and len(text) > max_chars:
            return request.token_budget + 1
        return estimate_tokens(text)

    expired = deadline_exceeded or (request.deadline is not None and time.monotonic() >= request.deadline)
    result = compose_context(sections, 0 if expired else request.token_budget,
                             counter=bounded_estimate if max_chars is not None else None,
                             separator="\n\n")
    expired = expired or (request.deadline is not None and time.monotonic() >= request.deadline)
    codes = list(reason_codes)
    if result.omitted:
        codes.append("context_omitted")
    if result.required_omitted:
        codes.append("required_context_unavailable")
    if expired:
        codes.append("deadline_exceeded")
    status = ("expired" if expired else "error" if result.required_omitted else
              "partial" if result.omitted or reason_codes else "ready" if result.text else "empty")
    packet = packet_for(request, text="" if expired else result.text,
                        evidence=() if expired else result.evidence,
                        est_tokens=0 if expired else estimate_tokens(result.text),
                        status=status, degraded=bool(codes), deadline_exceeded=expired,
                        reason_codes=tuple(dict.fromkeys(codes)))
    trace = HostTrace(
        request_id=request.request_id, channel=request.channel, session_id=request.session_id,
        turn_id=request.turn_id, revision=request.revision, status=status,
        token_budget=request.token_budget, rendered_tokens=packet.est_tokens,
        reserved_tokens=reserved_tokens, selected_count=0 if expired else len(result.selected),
        omitted_count=len(result.omitted), required_omitted=result.required_omitted,
        evidence_ids=tuple(dict.fromkeys(e.source_mem_id or e.memory_id for e in packet.evidence)),
        text_sha256=sha256(packet.text.encode()).hexdigest(), reason_codes=packet.reason_codes)
    return packet, trace


def source_reference(mem_id: str, source: str) -> EvidenceRef | None:
    """Bind an active canonical record; never guess from excerpt text."""
    from nexsandglass.core import memid, provenance
    row = memid.get(mem_id)
    if row is None:
        return None
    prov = provenance.get(mem_id)
    if prov is None or prov.signal != provenance.TRUSTED:
        return None
    return EvidenceRef(memory_id=mem_id, source_mem_id=mem_id, source_line=row.get("line_start"),
                       retrieval_source=source, origin=prov.origin if prov else "",
                       trust_signal=prov.signal if prov else "unknown")


def personal_sections(*, voice: bool = False, deadline: float | None = None, source: str | None = None):
    """Read trusted current projections as complete, source-bound context units.

    The finite projection item limits still apply; final admission is shared
    with other host sections rather than an upstream character-prefix cut.
    """
    from nexsandglass.core import profile, followups
    if source not in (None, "profile", "followups"):
        raise ValueError("invalid personal context source")
    got = {kind: [] for kind in profile.KINDS}
    if source != "followups":
        if deadline is None:
            profile.refresh(max_rows=profile.BATCH, wait=False)
        else:
            profile.refresh_ready(deadline=deadline)
        got = profile.items()
    sections = []
    entries = []
    for kind in profile.KINDS:
        for date, text, mid in got[kind]:
            ref = source_reference(mid, "profile")
            if ref is not None:
                dated = f"[{date}] {text}" if date else text
                entries.append(ContextItem(mid, profile.HEADERS[kind] + ": " + dated,
                                           required=kind == "health", evidence=(ref,)))
    if entries:
        sections.append(ContextSection(
            "profile", "[About the user: their own words; [date] = when they said it]",
            tuple(entries), priority=90))
    if not voice:
        old = []
        for date, text, mid, when in got.get("changed", []):
            ref = source_reference(mid, "profile_history")
            if ref is not None:
                old.append(ContextItem(mid, f"[{date}] {text} (changed {when})", evidence=(ref,)))
        if old:
            sections.append(ContextSection(
                "profile.history", "Changed since (their earlier words, replaced by what is above):",
                tuple(old), priority=-20))
    if source != "profile" and followups.enabled():
        if deadline is None:
            followups.refresh(max_rows=followups.BATCH, wait=False)
        else:
            followups.refresh_ready(deadline=deadline)
        due = followups.due()
        entries = []
        for i, (item, line) in enumerate(zip(due, followups.lines(due))):
            ref = source_reference(item["mem_id"], "followups")
            if ref is not None:
                entries.append(ContextItem(str(i), line.removeprefix("- "), evidence=(ref,)))
        if entries:
            sections.append(ContextSection("followups", followups.HEADER, tuple(entries), priority=70,
                                           suffix="" if voice else followups.GUIDANCE))
    return tuple(sections)


def retrieval_sections(objects, references, *, dates=None, heading="", limit=None, prefix="- "):
    """Retain only objects represented in the planner's post-gate packet."""
    refs = {ref.memory_id: ref for ref in references}
    entries = []
    if limit is not None and limit <= 0:
        return (ContextSection("retrieval", heading, prefix=prefix),)
    for i, obj in enumerate(objects):
        ref = refs.get(obj.memory_id)
        if ref is None or not obj.content:
            continue
        text = obj.content
        if dates and i in dates:
            text = f"[{dates[i]}] {text}"
        entries.append(ContextItem(str(i), text, evidence=(ref,)))
        if limit is not None and len(entries) >= limit:
            break
    return (ContextSection("retrieval", heading, tuple(entries), prefix=prefix),)


def recall_host_context(request: RecallRequest, *, limit=None, heading="", include_dates=False,
                        filter_relevance=False, prefix="- "):
    """One candidate/gate/render path; internal debug context is never exported."""
    from .orchestrator import get_orchestrator, _context_packet
    if request.token_budget == 0 or limit == 0:
        packet, trace = render_host_context(request, ())
        return packet, trace, None
    mc = get_orchestrator().recall_planner.recall(
        request.query, request.token_budget, context=request.context,
        session_id=request.session_id, request=request, candidate_mode=True)
    from copy import copy
    from nexsandglass.core import memid, provenance
    by_id = {ref.memory_id: ref for ref in mc.packet.evidence}
    objects = []
    seen_ids = set()
    for original in mc.objects:
        obj = original
        ref = by_id.get(obj.memory_id)
        if not ref or obj.memory_id in seen_ids:
            continue
        if ref.source_mem_id and obj.provenance in ("search_router", "shadow", "sandglass"):
            row = memid.get(ref.source_mem_id)
            if row is None:
                continue
            prov = provenance.get(ref.source_mem_id)
            if prov and prov.signal == "tainted":
                continue
            obj = copy(original)
            obj.content = (prov.label() if prov else "(unverified · source: unknown) ") + row["text"]
        if obj.content and obj.content.strip():
            objects.append(obj)
            seen_ids.add(obj.memory_id)
    # Keep source objects untouched while exposing the exact hydrated data to
    # the adapter. Derived temporal beliefs are not replaced by raw history.
    mc = copy(mc)
    mc.objects = objects
    mc.strings = [obj.content for obj in objects]
    mc.text = "\n".join(mc.strings)
    mc.est_tokens = estimate_tokens(mc.text)
    mc.packet = _context_packet(mc, request, mc.packet.deadline_exceeded)
    if filter_relevance:
        from nexsandglass.core import relevance
        objects = relevance.keep(request.query, objects)
    refs = mc.packet.evidence
    dates = {}
    if include_dates:
        by_id = {ref.memory_id: ref for ref in refs}
        for i, obj in enumerate(objects):
            ref = by_id.get(obj.memory_id)
            row = memid.get(ref.source_mem_id) if ref and ref.source_mem_id else None
            if row:
                dates[i] = str(row.get("ts", ""))[:10]
    sections = retrieval_sections(objects, refs, dates=dates, heading=heading, limit=limit, prefix=prefix)
    packet, trace = render_host_context(request, sections, reason_codes=mc.packet.reason_codes,
                                        deadline_exceeded=mc.packet.deadline_exceeded)
    return packet, trace, mc
