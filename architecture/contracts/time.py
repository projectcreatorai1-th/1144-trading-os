"""Time contract primitives (SECTION 8).

Canonical system time is timezone-aware UTC. Naive datetimes are invalid as
canonical system time. Timestamp types support latency analysis.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from architecture.contracts.errors import TimeValidationError

UTC = timezone.utc


class TimestampType(Enum):
    """Declares what a timestamp measures (supports latency analysis)."""

    EVENT_TIME = "EVENT_TIME"
    RECEIVED_TIME = "RECEIVED_TIME"
    PROCESSED_TIME = "PROCESSED_TIME"
    DECISION_TIME = "DECISION_TIME"
    EXECUTION_TIME = "EXECUTION_TIME"


def utc_now() -> datetime:
    """Current canonical system time (timezone-aware UTC)."""
    return datetime.now(UTC)


def ensure_utc(value: object, *, location: str = "timestamp") -> datetime:
    """Validate that value is a timezone-aware datetime; return it in UTC.

    Naive datetimes are rejected (fail closed).
    """
    if not isinstance(value, datetime):
        raise TimeValidationError(
            f"{location} must be a datetime, got {type(value).__name__}",
            location=location,
            details={"expected": "timezone-aware datetime", "actual_type": type(value).__name__},
        )
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise TimeValidationError(
            f"{location} is a naive datetime; canonical system time must be timezone-aware",
            location=location,
            details={"value": str(value)},
        )
    return value.astimezone(UTC)


def canonical(value: datetime) -> str:
    """Canonical UTC ISO-8601 representation (with offset)."""
    return ensure_utc(value, location="canonical").isoformat()


def ensure_not_before(
    value: datetime, *, not_before: datetime, location: str
) -> datetime:
    """Validate ordering: value must be >= not_before (both aware)."""
    v = ensure_utc(value, location=location)
    nb = ensure_utc(not_before, location=f"{location}.not_before")
    if v < nb:
        raise TimeValidationError(
            f"{location} ({v.isoformat()}) is before {nb.isoformat()}",
            location=location,
            details={"value": v.isoformat(), "not_before": nb.isoformat()},
        )
    return v


def parse_canonical(value: str, *, location: str = "timestamp") -> datetime:
    """Parse an ISO-8601 string; naive strings are rejected."""
    if not isinstance(value, str):
        raise TimeValidationError(
            f"{location} must be an ISO-8601 string", location=location
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TimeValidationError(
            f"{location} is not a valid ISO-8601 timestamp: {value!r}",
            location=location,
            details={"value": value},
        ) from exc
    return ensure_utc(parsed, location=location)


def latency_ms(start: datetime, end: datetime, *, location: str = "latency") -> float:
    """Latency between two aware timestamps in milliseconds (non-negative)."""
    s = ensure_utc(start, location=f"{location}.start")
    e = ensure_utc(end, location=f"{location}.end")
    delta = (e - s).total_seconds() * 1000.0
    if delta < 0:
        raise TimeValidationError(
            f"{location}: end before start ({s.isoformat()} -> {e.isoformat()})",
            location=location,
        )
    return delta
