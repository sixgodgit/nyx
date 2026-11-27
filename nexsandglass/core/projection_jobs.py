"""Durable, fenced checkpoints for rebuildable views in one canonical store.

The caller supplies the store's SQLite connection. Jobs contain only progress
metadata; raw records, identities and tombstones keep their existing authority.
Projection writes and checkpoint completion share a transaction. A process that
dies after claiming work leaves a lease that another worker can recover.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import sqlite3
import time
from uuid import uuid4


_SCHEMA = """CREATE TABLE IF NOT EXISTS projection_jobs (
    name TEXT PRIMARY KEY,
    processor_version TEXT NOT NULL,
    target_seq INTEGER NOT NULL,
    checkpoint_seq INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending', 'running', 'ready', 'failed')),
    reset_required INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_until REAL NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
)"""
_COLUMNS = ("name, processor_version, target_seq, checkpoint_seq, state, "
            "reset_required, attempts, lease_owner, lease_until, last_error, updated_at")


class LeaseLost(RuntimeError):
    """A newer worker/version owns this projection; discard this batch."""


@dataclass(frozen=True)
class ProjectionJob:
    name: str
    processor_version: str
    target_seq: int
    checkpoint_seq: int
    state: str
    reset_required: bool
    attempts: int
    lease_owner: str
    lease_until: float
    last_error: str
    updated_at: float


def ensure(conn: sqlite3.Connection) -> None:
    # Unlike executescript(), execute() does not commit a caller's transaction.
    conn.execute(_SCHEMA)
    # Installed with the canonical schema. Source changes and invalidation are
    # one SQLite transaction, including crashes before host refresh callbacks.
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "memories" not in tables:
        return
    def invalidate(seq):
        return ("UPDATE projection_jobs SET checkpoint_seq=MIN(checkpoint_seq,MAX(0,(" + seq + ")-1)), "
                "target_seq=MAX(target_seq,(" + seq + ")), state='pending', "
                "lease_owner='',lease_until=0,last_error='',updated_at=CAST(strftime('%s','now') AS REAL); ")
    for event, seq, columns in (
            ("INSERT", "NEW.seq", ""),
            ("UPDATE", "MIN(OLD.seq,NEW.seq)", " OF seq,ts,sender,text,line_start,line_end,deleted_at"),
            ("DELETE", "OLD.seq", "")):
        conn.execute("CREATE TRIGGER IF NOT EXISTS projection_source_" + event.lower() +
                     " AFTER " + event + columns + " ON memories BEGIN " + invalidate(seq) + "END")
    if "provenance" in tables:
        for event, ref in (("INSERT", "NEW.mem_id"), ("UPDATE", "NEW.mem_id"), ("DELETE", "OLD.mem_id")):
            seq = "SELECT seq FROM memories WHERE mem_id=" + ref
            conn.execute("CREATE TRIGGER IF NOT EXISTS projection_trust_" + event.lower() +
                         " AFTER " + event + " ON provenance WHEN EXISTS(" + seq + ") BEGIN " +
                         invalidate("(" + seq + ")") + "END")


@contextmanager
def transaction(conn: sqlite3.Connection):
    """A private savepoint: atomic alone, or nested in a caller transaction."""
    key = "projection_" + uuid4().hex
    conn.execute("SAVEPOINT " + key)
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO " + key)
        conn.execute("RELEASE " + key)
        raise
    else:
        conn.execute("RELEASE " + key)


def _identifier(value: str, label: str) -> None:
    if (not isinstance(value, str) or not value.strip() or len(value) > 128
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(label + " must be a nonempty identifier")


def _sequence(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("sequence must be a nonnegative integer")


def _now(now):
    value = time.time() if now is None else float(now)
    if not math.isfinite(value):
        raise ValueError("time must be finite")
    return value


def get(conn: sqlite3.Connection, name: str) -> ProjectionJob | None:
    ensure(conn)
    row = conn.execute("SELECT " + _COLUMNS + " FROM projection_jobs WHERE name=?", (name,)).fetchone()
    if row is None:
        return None
    values = list(row)
    values[5] = bool(values[5])
    return ProjectionJob(*values)


def enqueue(conn: sqlite3.Connection, name: str, processor_version: str, target_seq: int,
            *, initial_checkpoint: int = 0, initial_reset: bool = False, now=None) -> ProjectionJob:
    """Coalesce to one idempotent job; a processor-version change fences old work.

    The initial checkpoint is used only when adopting a legacy projection for
    the first time. Existing job metadata is authoritative for later progress.
    """
    _identifier(name, "name")
    _identifier(processor_version, "processor_version")
    _sequence(target_seq)
    _sequence(initial_checkpoint)
    clock = _now(now)
    ensure(conn)
    with transaction(conn):
        old = get(conn, name)
        if old is None:
            checkpoint = min(initial_checkpoint, target_seq) if not initial_reset else 0
            state = "pending" if initial_reset or checkpoint < target_seq else "ready"
            conn.execute(
                "INSERT INTO projection_jobs(" + _COLUMNS + ") VALUES (?,?,?,?,?,?,0,'',0,'',?)",
                (name, processor_version, target_seq, checkpoint, state, int(initial_reset), clock))
        elif old.processor_version != processor_version:
            conn.execute(
                "UPDATE projection_jobs SET processor_version=?, target_seq=?, checkpoint_seq=0, "
                "state='pending', reset_required=1, attempts=0, lease_owner='', lease_until=0, "
                "last_error='', updated_at=? WHERE name=?",
                (processor_version, target_seq, clock, name))
        elif target_seq > old.target_seq:
            state = "pending" if old.state == "ready" else old.state
            conn.execute("UPDATE projection_jobs SET target_seq=?,state=?,updated_at=? WHERE name=?",
                         (target_seq, state, clock, name))
    return get(conn, name)


def claim(conn: sqlite3.Connection, name: str, processor_version: str, *, lease_seconds: float = 30,
          now=None) -> ProjectionJob | None:
    _identifier(name, "name")
    _identifier(processor_version, "processor_version")
    duration = float(lease_seconds)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("lease_seconds must be positive and finite")
    clock = _now(now)
    ensure(conn)
    with transaction(conn):
        owner = uuid4().hex
        changed = conn.execute(
            "UPDATE projection_jobs SET state='running',lease_owner=?,lease_until=?, "
            "attempts=attempts+1,last_error='',updated_at=? WHERE name=? AND processor_version=? "
            "AND (checkpoint_seq < target_seq OR reset_required=1) "
            "AND (state!='running' OR lease_until<=?)",
            (owner, clock + duration, clock, name, processor_version, clock)).rowcount
        return get(conn, name) if changed else None


def _owned(conn, lease, clock):
    current = get(conn, lease.name)
    if (current is None or current.processor_version != lease.processor_version
            or current.lease_owner != lease.lease_owner or current.state != "running"
            or current.lease_until <= clock):
        raise LeaseLost("projection lease is no longer current")
    return current


def finish(conn: sqlite3.Connection, lease: ProjectionJob, checkpoint_seq: int, *, now=None) -> ProjectionJob:
    """Finish inside the same transaction as the projection batch writes."""
    _sequence(checkpoint_seq)
    clock = _now(now)
    current = _owned(conn, lease, clock)
    if not current.checkpoint_seq <= checkpoint_seq <= lease.target_seq:
        raise ValueError("checkpoint must stay within the claimed source range")
    state = "ready" if checkpoint_seq >= current.target_seq else "pending"
    changed = conn.execute(
        "UPDATE projection_jobs SET checkpoint_seq=?,state=?,reset_required=0,lease_owner='',"
        "lease_until=0,last_error='',updated_at=? WHERE name=? AND processor_version=? "
        "AND lease_owner=? AND state='running' AND lease_until>?",
        (checkpoint_seq, state, clock, lease.name, lease.processor_version, lease.lease_owner, clock)).rowcount
    if not changed:
        raise LeaseLost("projection completion was superseded")
    return get(conn, lease.name)


def failed(conn: sqlite3.Connection, lease: ProjectionJob, *, now=None) -> None:
    # Exception text may contain source data or credentials. Persist a stable
    # code only, and never overwrite the status of a newer worker or version.
    with transaction(conn):
        conn.execute("UPDATE projection_jobs SET state='failed',lease_owner='',lease_until=0,"
                     "last_error='processor_error',updated_at=? WHERE name=? AND processor_version=? "
                     "AND lease_owner=? AND state='running'",
                     (_now(now), lease.name, lease.processor_version, lease.lease_owner))


def run(conn: sqlite3.Connection, name: str, processor_version: str, target_seq: int, processor,
        *, initial_checkpoint: int = 0, initial_reset: bool = False, lease_seconds: float = 30):
    """Persist scheduling, run one bounded batch, atomically publish its progress.

    processor(conn, lease) returns (checkpoint_seq, caller_result). It must not
    commit, execute scripts, or open another store connection. On failure the
    batch is rolled back, failed metadata is persisted, and the exception rises.
    """
    enqueue(conn, name, processor_version, target_seq, initial_checkpoint=initial_checkpoint,
            initial_reset=initial_reset)
    lease = claim(conn, name, processor_version, lease_seconds=lease_seconds)
    if lease is None:
        return 0
    try:
        with transaction(conn):
            _owned(conn, lease, _now(None))
            checkpoint, result = processor(conn, lease)
            finish(conn, lease, checkpoint)
        return result
    except BaseException:
        failed(conn, lease)
        raise


def ready_through(conn: sqlite3.Connection, name: str, processor_version: str, target_seq: int) -> bool:
    """Readiness is a fixed source watermark, not merely 'worker returned'."""
    job = get(conn, name)
    return bool(job and job.processor_version == processor_version and not job.reset_required
                and job.checkpoint_seq >= target_seq and job.state != "failed")
