"""Deterministic date/clock policy with explicit ambiguity and DST validation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import calendar
import re

from .observation import TemporalContext

TIME_POLICY_VERSION = "1"
_WD = {name: i for i, name in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
_CN = {x: i for i, x in enumerate("一二三四五六日")}
_CN["天"] = 6
_RULES = (
    ("iso", r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)"),
    ("chinese", r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]?"),
    ("numeric", r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)"),
    ("weekday_en", r"\b(next|this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"),
    ("weekday_zh", r"(下下|下|这|本)(?:个)?(?:周|星期|礼拜)([一二三四五六日天])"),
    ("relative", r"\bday after tomorrow\b|\btomorrow\b|\byesterday\b|\btoday\b|大后天|后天|明天|今天|昨天|前天"),
    ("delta_en", r"\b(?:in (\d+) (days?|weeks?|months?|years?)|(\d+) (days?|weeks?|months?|years?) ago)\b"),
    ("delta_zh", r"(\d+|[一二两三四五六七八九十]{1,3})(天|周|个月|年)(前|后)"),
)
_COMPILED = [(name, re.compile(pattern, re.I)) for name, pattern in _RULES]
_CLOCK = re.compile(r"(?:\s*(?:at|的|在)?\s*)(\d{1,2}):(\d{2})(?:\s*(am|pm))?\b", re.I)
_DMY = {"en-gb", "en-au", "en-nz", "de-de", "fr-fr", "es-es", "it-it"}
_MDY = {"en-us", "en-ph"}


@dataclass(frozen=True)
class TimeResolution:
    status: str
    span: tuple[int, int]
    basis: tuple[str, ...]
    start: date | None = None
    end: date | None = None
    instant: datetime | None = None
    candidates: tuple[str, ...] = ()

    def to_dict(self):
        return {"status": self.status, "span": list(self.span), "basis": list(self.basis),
                "start": self.start.isoformat() if self.start else None,
                "end": self.end.isoformat() if self.end else None,
                "instant_utc": self.instant.isoformat() if self.instant else None,
                "candidates": list(self.candidates), "policy_version": TIME_POLICY_VERSION}


def _day(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError, OverflowError):
        return None


def _delta(day, n, unit):
    if unit.startswith(("day", "天")):
        return day + timedelta(days=n)
    if unit.startswith(("week", "周")):
        return day + timedelta(days=7 * n)
    months = n * 12 if unit.startswith(("year", "年")) else n
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def scan_times(text: str, context: TemporalContext) -> tuple[TimeResolution, ...]:
    matches = sorted(((m.start(), -(m.end()-m.start()), name, m) for name, rx in _COMPILED for m in rx.finditer(text)),
                     key=lambda x: (x[0], x[1]))
    out, until = [], -1
    for start, _, name, m in matches:
        if start < until:
            continue
        until = m.end()
        basis = (name, context.basis, "zone:" + (context.timezone or "unspecified"))
        span = (start, m.end())
        day, candidates, status = None, (), "resolved"
        relative = name not in ("iso", "chinese", "numeric")
        if relative and not context.timezone and (context.reference.tzinfo is not None or context.basis != "legacy_wall"):
            out.append(TimeResolution("needs_timezone", span, basis))
            continue
        today = context.local.date()
        try:
            if name in ("iso", "chinese"):
                day = _day(*m.groups())
            elif name == "numeric":
                a, b, year = map(int, m.groups())
                mdy, dmy = _day(year, a, b), _day(year, b, a)
                options = sorted({d.isoformat() for d in (mdy, dmy) if d})
                if context.locale in _MDY:
                    day = mdy
                    basis += ("order:mdy",)
                elif context.locale in _DMY:
                    day = dmy
                    basis += ("order:dmy",)
                elif len(options) == 1:
                    day = date.fromisoformat(options[0])
                elif len(options) > 1:
                    status, candidates = "ambiguous", tuple(options)
            elif name.startswith("weekday"):
                token, wd = m.groups()
                index = _WD[wd.lower()] if name.endswith("en") else _CN[wd]
                weeks = 2 if token == "下下" else 1 if token.lower() in ("next", "下") else 0
                day = today - timedelta(days=today.weekday()) + timedelta(days=7*weeks+index)
                basis += ("next_means_next_iso_week",)
            elif name == "relative":
                offset = {"day after tomorrow": 2, "tomorrow": 1, "today": 0, "yesterday": -1,
                          "今天": 0, "明天": 1, "后天": 2, "大后天": 3, "昨天": -1, "前天": -2}[m.group().lower()]
                day = today + timedelta(days=offset)
            else:
                if name == "delta_en":
                    ahead, u1, ago, u2 = m.groups()
                    n, unit = (int(ahead), u1) if ahead else (-int(ago), u2)
                else:
                    n, unit, direction = m.groups()
                    if n.isdigit():
                        n = int(n)
                    else:
                        digits = dict(zip("一二两三四五六七八九", (1,2,2,3,4,5,6,7,8,9)))
                        if "十" in n:
                            head, tail = n.split("十")
                            n = 10 * (digits.get(head, 1) if head else 1) + digits.get(tail, 0)
                        else:
                            n = digits[n]
                    n *= -1 if direction == "前" else 1
                day = _delta(today, n, unit)
                basis += ("calendar_arithmetic",)
        except (OverflowError, ValueError):
            day = None
        if day is None and status == "resolved":
            status = "invalid_date"
        resolution = TimeResolution(status, span, basis, day, day, candidates=candidates)
        clock = _CLOCK.match(text, m.end())
        if clock and day and status == "resolved":
            resolution = _clock_resolution(resolution, clock, context)
            until = clock.end()
        out.append(resolution)
    return tuple(out)


def _clock_resolution(result, match, context):
    hour, minute = int(match[1]), int(match[2])
    if match[3]:
        if not 1 <= hour <= 12:
            return replace(result, status="invalid_time", start=None, end=None)
        hour = hour % 12 + (12 if match[3].lower() == "pm" else 0)
    try:
        wall = datetime.combine(result.start, datetime.min.time()).replace(hour=hour, minute=minute)
    except ValueError:
        return replace(result, status="invalid_time", start=None, end=None)
    span = (result.span[0], match.end())
    if not context.timezone:
        # Legacy APIs remain day-granular, without inventing an exact instant.
        if context.reference.tzinfo is None and context.basis == "legacy_wall":
            return replace(result, span=span, basis=result.basis + ("legacy_day_only",))
        return replace(result, status="needs_timezone", span=span, start=None, end=None)
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(context.timezone)
    instants = sorted({wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                       for fold in (0, 1)
                       if wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == wall})
    if len(instants) == 1:
        return replace(result, span=span, instant=instants[0], basis=result.basis + ("zoneinfo_roundtrip",))
    return replace(result, status="ambiguous_time" if instants else "nonexistent_time", span=span,
                   start=None, end=None, candidates=tuple(t.isoformat() for t in instants))


def resolve_time(text: str, *, reference: datetime, language=None) -> TimeResolution:
    ctx = TemporalContext(reference, language.locale if language else None,
                          language.timezone if language else None, "explicit" if language else "legacy_wall")
    results = scan_times(text, ctx)
    if not results:
        return TimeResolution("unknown", (0, 0), ("no_supported_expression",))
    if len(results) > 1:
        return TimeResolution("ambiguous", (results[0].span[0], results[-1].span[1]), ("multiple_expressions",),
                              candidates=tuple(r.start.isoformat() for r in results if r.start))
    return results[0]
