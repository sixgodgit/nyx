"""Exact language aliases over existing postings, with bounded candidate work."""
from __future__ import annotations
from itertools import islice

from nexsandglass.language.query import plan

MAX_POSTINGS = 2000
MAX_CANDIDATES = 120
MAX_TOKENS = 96
MAX_INDEX_TERMS = 50000


def current_records(lines, *, allow_unbound=False):
    """Hydrate unique live canonical sources; postings are only candidates."""
    if not lines:
        return {}, set()
    from nexsandglass.core import memid
    rows = memid.get_conn().execute(
        "SELECT line_start,ts,sender,text,deleted_at FROM memories WHERE line_start IN ("
        + ",".join("?" for _ in lines) + ")", list(lines)).fetchall()
    grouped = {}
    for line, ts, sender, text, deleted in rows:
        grouped.setdefault(line, []).append((ts, sender, text, deleted))
    live, blocked = {}, set()
    for line in lines:
        sources = grouped.get(line, [])
        if len(sources) == 1 and not sources[0][3]:
            live[line] = sources[0][:3]
        elif sources or not allow_unbound:
            blocked.add(line)
    return live, blocked


class AliasSearch:
    def __init__(self):
        self.status = "not_run"

    def search(self, query: str, limit: int = 10) -> list:
        self.status = "no_terms"
        query_plan = plan(query)
        if not query_plan.alternatives or limit <= 0:
            return []
        from nexsandglass.features.sandglass_vault import _sync_index, _query_tokens
        from nexsandglass.core.sandglass_sqlite import sync_incremental
        sync_incremental()
        index = _sync_index() or {}
        tokens = set().union(*(_query_tokens(s) for s in query_plan.alternatives))
        short = {s for s in query_plan.alternatives if len(s) == 1 and "\u4e00" <= s <= "\u9fff"}
        self.status = "ready"
        if short:
            # Legacy postings store Chinese bigrams, not isolated characters.
            # Enumerate a bounded vocabulary rather than scan raw records or
            # reinterpret an old persisted index under a new token policy.
            if len(index) > MAX_INDEX_TERMS:
                self.status = "bounded"
            tokens.update(t for t in tuple(islice(index, MAX_INDEX_TERMS))
                          if len(t) == 2 and all("\u4e00" <= c <= "\u9fff" for c in t)
                          and any(c in t for c in short))
        tokens = sorted(tokens)
        if len(tokens) > MAX_TOKENS:
            self.status = "bounded"
        postings = [(t, index[t]) for t in tokens[:MAX_TOKENS] if t in index]
        postings.sort(key=lambda p: (len(p[1]), p[0]))
        candidates = {}
        visited = 0
        for _, lines in postings:
            if visited + len(lines) > MAX_POSTINGS:
                self.status = "bounded"
                continue
            for line in lines:
                candidates[line] = candidates.get(line, 0) + 1
            visited += len(lines)
        if len(candidates) > MAX_CANDIDATES:
            self.status = "bounded"
        lines = sorted(candidates, key=lambda ln: (-candidates[ln], ln))[:MAX_CANDIDATES]
        records, _ = current_records(lines)
        return [(ln, records[ln][0], records[ln][2]) for ln in lines
                if ln in records and query_plan.matches(records[ln][2])][:min(limit, MAX_CANDIDATES)]
