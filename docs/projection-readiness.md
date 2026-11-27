# Projection readiness and recovery decision (M1-05)

Status: implemented for profile and follow-up views in 7.25.0. No authority
migration is performed in this release.

## Authority retained

`memories` in `nyx.db` retains canonical IDs, monotonic sequence numbers, source
bindings and tombstones. `sandglass.txt` retains the existing raw journal and
line-address compatibility. Allocation coordinates the two under the journal
lock and compensates handled append/commit errors. A lock is not a cross-file
transaction: process death or power loss between file append and SQLite commit
still requires existing verify/repair procedures. Projection jobs neither turn
that dual write into a transaction ledger nor erase that recovery boundary.

Quarantine retains recoverable originals until expiry. Tombstones and source
IDs keep their existing semantics. Profile and follow-up tables are disposable
views, not the authority for a user's statement. FTS, shadow, graph and semantic
indexes keep their existing rebuild/health mechanisms; they are not all migrated
to the new job scheduler.

## Durable view protocol

`projection_jobs` lives in the same database as the source records and views.
Its idempotency key is the view name within one store; it records processor
version, target/checkpoint sequence, pending/running/ready/failed state, reset
requirement, attempt count and a fenced expiring worker lease. No source text or
raw exception is persisted in job diagnostics.

A processor version change invalidates the old lease and requests a full view
rebuild. Legacy checkpoints are adopted once only when their rule version
matches. Repeated enqueue coalesces the target; it cannot replace newer progress
with an older request. Projection writes, legacy metadata and checkpoint
completion share one SQLite savepoint. A failed batch rolls back its view
changes and leaves `failed/processor_error`; later reads can retry. Nested
savepoints do not commit an unrelated caller transaction.

Canonical source insert/update/delete and provenance changes invalidate jobs in
SQLite triggers, in the same transaction as the source change. Earlier affected
sequences rewind the checkpoint and fence in-flight work. This closes the crash
window between restoring an old record and calling a host's `reconsider()`
callback. Replayed ranges delete prior projection rows before reclassification,
so changes that stop matching a rule cannot leave stale classifications.

A killed process can leave a running lease; another worker can reclaim after
expiry. Uncommitted batch writes roll back on restart. A superseded worker cannot
publish a checkpoint or overwrite the new worker's status. This is at-least-once
processing with idempotent view updates, not exactly-once execution of arbitrary
external side effects.

Voice startup waits for each view through a fixed canonical source watermark,
under its existing deadline. It reports pending/timeout rather than claiming an
incomplete initial scan is current. Ordinary rendering retains its best-effort
contract. Ready through a source watermark does not mean a downstream voice
client received or applied the generated facts.

The protocol is inspectable in Python:

```python
from nexsandglass.core import memid, projection_jobs
job = projection_jobs.get(memid.get_conn(), 'profile')
# None means no job has been scheduled, not that the view is ready.
```

## Transaction ledger migration deferred

A future authority switch needs an append-only committed event design, stable
IDs and source/tombstone import, shadow replay comparison, fault injection at
every write boundary, and a rollback procedure retaining writes made after
cutover. An old database/journal backup alone cannot roll back new events safely.
M1 keeps current authority and adds durable recovery only to rebuildable views.

Evidence: `tests/test_projection_jobs.py` covers retry, nested rollback, processor
version changes, competing leases, stale-worker fencing, real process death
inside a batch, old-record restore without callbacks, source reclassification,
and atomic source/trust invalidation. Existing fault tests continue to cover
handled allocation failures and source-aware deletion/restore.
