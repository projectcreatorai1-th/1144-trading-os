"""Time contract domain layer (owned by core.time).

Primitives (timezone-aware UTC, canonical representation, timestamp types)
live in the kernel. This module owns the domain time service: the Clock port
(production clock, deterministic clock for simulation/replay) and typed
Timestamp / LatencyMeasurement value objects.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from architecture.contracts.errors import TimeValidationError
from architecture.contracts.time import (
    TimestampType,
    ensure_utc,
    latency_ms,
    utc_now,
)

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class Timestamp:
    """A typed, timezone-aware point in time."""

    value: datetime
    type: TimestampType

    def validate(self) -> None:
        ensure_utc(self.value, location=f"timestamp[{self.type.value}]")
        if not isinstance(self.type, TimestampType):
            raise TimeValidationError(
                f"timestamp.type must be a TimestampType, got {self.type!r}",
                location="timestamp.type",
            )

    @property
    def canonical(self) -> str:
        self.validate()
        return ensure_utc(self.value).isoformat()


class Clock(ABC):
    """Time source port. Implementations: SystemClock (production),
    DeterministicClock (simulation/replay). Naive times are impossible by
    construction: implementations must return timezone-aware UTC."""

    @abstractmethod
    def now(self) -> datetime:  # pragma: no cover - port definition
        ...


class SystemClock(Clock):
    """Real system clock (production time source)."""

    def now(self) -> datetime:
        return utc_now()


class DeterministicClock(Clock):
    """Controlled time source for SIMULATION/REPLAY environments and tests.
    Not a substitute time source for LIVE; it is an explicit, declared clock."""

    def __init__(self, initial: datetime) -> None:
        self._current = ensure_utc(initial, location="deterministic_clock.initial")

    @property
    def now_value(self) -> datetime:
        return self._current

    def advance_to(self, moment: datetime) -> None:
        moment = ensure_utc(moment, location="deterministic_clock.advance_to")
        if moment < self._current:
            raise TimeValidationError(
                "DeterministicClock cannot move backwards",
                location="deterministic_clock.advance_to",
                details={"from": self._current.isoformat(), "to": moment.isoformat()},
            )
        self._current = moment

    def now(self) -> datetime:
        return self._current


@dataclass(frozen=True)
class LatencyMeasurement:
    """Latency between two typed timestamps (supports latency analysis)."""

    start: Timestamp
    end: Timestamp
    label: str

    def validate(self) -> None:
        self.start.validate()
        self.end.validate()

    @property
    def milliseconds(self) -> float:
        self.validate()
        return latency_ms(self.start.value, self.end.value, location=f"latency[{self.label}]")
