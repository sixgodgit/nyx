**Reliability contracts**

This change preserves the local, dependency-free core and the existing journal
format. It hardens existing behavior before adding new memory representations.

**Recording and derived identities**

Formation requires a real canonical raw record before it writes derived stores.
Hermes passes the ID returned by the raw write instead of associating a turn by
content alone. Session-end fallback records raw messages when the host has not
called `sync_turn`.

For handled allocation failures, uncommitted database rows and this call's journal
append are rolled back while holding the journal lock. Existing bytes are kept;
an unexpected file replacement or external append is reported rather than
truncated. A commit that completed before an exception is not undone. This is
process-error recovery, not a claim of atomic transactions across SQLite and a
text file under power loss. The existing journal/repair model remains in place.

New engram rows have a unique `engram:<uuid>` identity and retain `source_mem_id`.
Legacy rows are not rewritten by reading them. Timestamp aliases are accepted
only when unambiguous; ambiguous feedback cannot change multiple records.
Bridge appends, relative updates, deletion and restoration share a store lock.

`mem_id` selectors and restoration use canonical raw `m_*` IDs. The public
`memory_id` selector can resolve a live engram ID through its source binding.
After forgetting, use the canonical IDs returned by the forget report to restore
from quarantine; do not assume an erased derived alias remains resolvable.

**Deletion and snapshots**

Managed consolidation snapshots participate in source-aware cleanup and erasure
verification. Quarantining removes their matching rows too; recoverable content
remains in the tracked quarantine. Unrelated rows are retained. Invalid or
unreadable snapshots prevent a claim of complete purge. This contract covers
Nyx-managed storage, not copies exported elsewhere by an application.

Repeated snapshot tags produce distinct files, and a tag cannot select a parent
directory. Source metadata is preserved so a snapshot does not bypass erasure.

**Recall context, deadlines and budgets**

`runtime.recall` forwards `context` and `session_id`. Bounded, secret-filtered
context contributes to retrieval for anaphoric queries. A session ID is metadata,
not an invented persistent-session filter; existing stores do not provide such
a filter. Trace metadata records context use.

Sources run in a shared bounded executor. A source deadline no longer waits for
an executor context manager to join a late task. Python cannot forcibly stop an
already-running thread, so downstream I/O must still use timeouts. Late work
retains its capacity slot until it actually finishes.

The normal recall result fits complete evidence records into the requested
estimated token budget. An individually oversized record may be omitted instead
of silently cutting off its correction or negation. Internal `candidate_mode`
returns broad candidates for consumers that apply their own rendering budget.
Hermes uses that mode and then budgets its final display lines, including dates,
the memory header and notices. An explicit zero prefetch budget yields no text.
The existing Chinese-character/other-character estimator is used; it is not an
exact tokenizer for every model. The separately generated persistent system
profile is not counted against the per-turn `prefetch_tokens` setting.

**Consolidation and time isolation**

Engine reports distinguish validated proposals, in-memory changes, deferred
actions and persisted changes. The public facade persists supported relative
decay updates, reports the number actually saved, and reloads access counts on
later runs. Abstract/conflict proposals are deferred rather than presented as
durably activated knowledge. Semantic/procedural memories retain their static
type policy.

Historical media writes use a thread-local clock scope. They preserve an existing
simulation clock and do not set the time seen by another concurrent request.

**Verification**

Regression tests cover injected commit and partial-write failures, identity
ambiguity, concurrent mutation, snapshot cleanup and malformed files, restoration,
source deadlines, final display budgets, contextual queries, durable decay and
media clock isolation. Full pytest and the existing offline personal/LongMemEval
mini harnesses are run against the changed checkout. Small fixture results are
regression evidence, not a new full-benchmark or robotics performance claim.
