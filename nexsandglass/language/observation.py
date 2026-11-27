"""Utterance-time context, committed with a canonical record, never from IDs.

Old records have a legacy wall clock; current profile settings cannot rewrite
their interpretation. Explicit aware references are UTC instants; locale and
timezone remain independent and can be absent.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class TemporalContext:
    reference: datetime
    locale: str | None = None
    timezone: str | None = None
    basis: str = "legacy_wall"
    input_language: str = "unknown"

    def __post_init__(self):
        if not isinstance(self.reference, datetime):
            raise TypeError("reference must be a datetime")
        from nexsandglass.runtime.contracts import LanguageContext
        checked = LanguageContext(input_language=self.input_language, locale=self.locale, timezone=self.timezone)
        object.__setattr__(self, "locale", checked.locale)
        object.__setattr__(self, "input_language", checked.input_language)
        if self.timezone and (self.reference.tzinfo is None or self.reference.utcoffset() is None):
            raise ValueError("a timezone requires an aware reference instant")

    @property
    def local(self):
        if self.timezone:
            from zoneinfo import ZoneInfo
            return self.reference.astimezone(ZoneInfo(self.timezone))
        return self.reference


_CURRENT: ContextVar[TemporalContext | None] = ContextVar("nyx_observation_time", default=None)


def current():
    return _CURRENT.get()


@contextmanager
def bind(context: TemporalContext | None):
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


@contextmanager
def scope(*, language=None, observed_at=None):
    from nexsandglass.runtime.contracts import LanguageContext
    if language is not None and not isinstance(language, LanguageContext):
        raise TypeError("language must be a LanguageContext or None")
    if observed_at is not None and (not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None):
        raise ValueError("observed_at must be an aware datetime")
    if language is None and observed_at is None:
        yield current()
        return
    from nexsandglass.core import clock
    ref = observed_at or clock.utcnow()
    ctx = TemporalContext(ref.astimezone(timezone.utc),
                          language.locale if language else None,
                          language.timezone if language else None, "explicit", language.input_language if language else "unknown")
    with bind(ctx):
        if observed_at is None:
            yield ctx
        else:
            # Only a new explicit observation changes its canonical timestamp.
            # Existing records/IDs are never restamped or reinterpreted.
            with clock.frozen(ref):
                yield ctx


def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS observation_context (
        mem_id TEXT PRIMARY KEY REFERENCES memories(mem_id) ON DELETE CASCADE,
        reference TEXT NOT NULL, locale TEXT, timezone TEXT, basis TEXT NOT NULL, input_language TEXT NOT NULL DEFAULT 'unknown'
    )""")
    if "input_language" not in {r[1] for r in conn.execute("PRAGMA table_info(observation_context)")}:
        conn.execute("ALTER TABLE observation_context ADD COLUMN input_language TEXT NOT NULL DEFAULT 'unknown'")


def capture(conn, mid):
    context = current()
    if context is None:
        from nexsandglass.core import settings, clock
        loc, zone = settings.get("NYX_LOCALE") or None, settings.get("NYX_TIMEZONE") or None
        if not loc and not zone:
            return
        context = TemporalContext(clock.utcnow(), loc, zone, "profile_configuration")
    if context.basis == "legacy_wall":
        return
    conn.execute("INSERT OR IGNORE INTO observation_context VALUES (?,?,?,?,?,?)",
                 (mid, context.reference.isoformat(), context.locale, context.timezone, context.basis, context.input_language))


def for_record(conn, mid, ts=None):
    row = conn.execute("SELECT reference,locale,timezone,basis,input_language FROM observation_context WHERE mem_id=?", (mid,)).fetchone()
    if row:
        return TemporalContext(datetime.fromisoformat(row[0]), row[1], row[2], row[3], row[4])
    if ts is None:
        source = conn.execute("SELECT ts FROM memories WHERE mem_id=?", (mid,)).fetchone()
        ts = source[0] if source else "1970-01-01 00:00:00"
    return TemporalContext(datetime.fromisoformat(str(ts).replace("Z", "+00:00")))
