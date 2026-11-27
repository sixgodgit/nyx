"""Bounded event references for explicit cancellations/rescheduling.

Kind, subject, dates and exact named anchors provide evidence. Missing or
conflicting targets stay ambiguous; a category by itself is not an event ID.
"""
from __future__ import annotations
import re
from datetime import timedelta

from .assertions import assertion_state

_ALIASES = {
    "org.tencent": ("腾讯", "tencent"), "org.google": ("谷歌", "google"),
    "org.microsoft": ("微软", "microsoft"), "org.amazon": ("亚马逊", "amazon"),
    "place.shanghai": ("上海", "shanghai"), "place.seattle": ("西雅图", "seattle"),
    "place.london": ("伦敦", "london"), "place.berlin": ("柏林", "berlin"),
}
_STOP = set("i my we our the a an on in at next this last tomorrow today yesterday interview exam monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september october november december hr".split())
_STOP.update("taking dad father mom mum mother son daughter going heading returning planning having have checkup appointment interview exam dentist doctor surgery tomorrow today yesterday cancelled rescheduled postponed".split())
_NEG_ACTION = re.compile(r"(?:没有|没|并未|并没有|未|不)(?:被)?(?:取消|改期|延期|推迟)|"
                         r"\b(?:not|never|didn'?t|don'?t|haven'?t|hasn'?t|wasn'?t)\b[^,.;!?]{0,25}\b(?:cancel\w*|reschedul\w*|postpon\w*)", re.I)


def move_target_start(text):
    matches = list(re.finditer(r"推迟到|延期到|改期到|改到|挪到|提前到|\bto\s+", text, re.I))
    return matches[-1].end() if matches else len(text)


def identity(text, kinds):
    from nexsandglass.core import understand, followups
    spans = followups._event_spans(text, kinds)
    subject = understand._who(text, spans[0][0], text) if spans else None
    if subject is None and spans:
        kin = re.search(r"\b(dad|father|mom|mum|mother|son|daughter)'s\b", text[:spans[0][0]], re.I)
        if kin:
            role = {"dad": "father", "mom": "mother", "mum": "mother"}.get(kin[1].lower(), kin[1].lower())
            subject = "user's " + role
    anchors = set()
    for key, aliases in _ALIASES.items():
        if any((re.search(r"\b" + re.escape(a) + r"\b", text, re.I) if a.isascii() else a in text) for a in aliases):
            anchors.add(key)
    known_words = {a for aliases in _ALIASES.values() for a in aliases if a.isascii()}
    for word in re.findall(r"\b[A-Z][A-Za-z0-9&.-]{1,30}\b", text):
        low = word.lower().rstrip(".")
        if low not in _STOP and low not in known_words:
            anchors.add("named:" + low)
    return "unknown" if subject == "" else subject or "user", frozenset(anchors)


def same_event(a, b):
    return a["kinds"][0] == b["kinds"][0] and identity(a["sentence"], a["kinds"]) == identity(b["sentence"], b["kinds"])


def action(text):
    from nexsandglass.core import followups
    if assertion_state(text) != "affirmed" or _NEG_ACTION.search(text):
        return ""
    if followups._CANCEL.search(text):
        return "cancel"
    if followups._MOVE.search(text):
        return "reschedule"
    for kind in followups.kinds_in(text):
        span = followups._event_spans(text, (kind,))[0]
        before = text[:span[0]]
        if followups._NEG_ZH.search(before) or followups._NEG_EN.search(before):
            return "cancel"
    return ""


def targets(text, candidates, when):
    """Return source IDs only for uniquely resolved explicit event actions."""
    from nexsandglass.core import followups
    ids, states = set(), []
    for sent in followups._sentences(text):
        mode = action(sent)
        if not mode:
            continue
        kinds = followups.kinds_in(sent)
        if not kinds:
            states.append("missing_event_kind")
            continue
        subject, anchors = identity(sent, kinds)
        pool = [c for c in candidates if c["said"] < when and c["kinds"][0] in kinds
                and identity(c["sentence"], c["kinds"])[0] == subject
                and c["end"] >= when.date() - timedelta(days=followups.AWAITING_DAYS)]
        if anchors:
            pool = [c for c in pool if anchors <= identity(c["sentence"], c["kinds"])[1]]
        times = followups._times(sent, when.date())
        cutoff = move_target_start(sent) if mode == "reschedule" else len(sent)
        dates = [t[3] for t in times if t[3] and t[0] < cutoff]
        if dates:
            pool = [c for c in pool if any(c["start"] <= end and start <= c["end"] for start, end in dates)]
        # Multiple records can describe the same dated event; conflicting
        # named anchors, dates or subjects keep the binding ambiguous.
        identities = {(c["start"], c["end"], identity(c["sentence"], c["kinds"])) for c in pool}
        if len(identities) == 1:
            ids.update(c["mem_id"] for c in pool)
            states.append("resolved")
        else:
            states.append("ambiguous" if pool else "unmatched")
    return ids, tuple(states)
