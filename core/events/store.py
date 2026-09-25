"""Event store port (owned by core.events).

Append-only history. Accepted events are never mutated; corrections are new
events. Implementations live in platform.database (SECTION 20 Phase 1).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterator

from core.events.contracts import Event
from core.events.repository import EventRepository


class EventStore(EventRepository, ABC):
    """Extended event storage: queries, sequence and replay-safe reads."""

    @abstractmethod
    def append(self, event: Event) -> int:  # pragma: no cover - port definition
        """Validate + append; returns the store sequence number."""
        ...

    @abstractmethod
    def query_by_time(self, start: datetime, end: datetime) -> Iterator[Event]:  # pragma: no cover
        ...

    @abstractmethod
    def query_by_type(self, event_type: str) -> Iterator[Event]:  # pragma: no cover
        ...

    @abstractmethod
    def query_by_source(self, source: str) -> Iterator[Event]:  # pragma: no cover
        ...

    @abstractmethod
    def query_by_causation(self, causation_id: str) -> Iterator[Event]:  # pragma: no cover
        ...

    @abstractmethod
    def find_by_dedup_key(self, dedup_key: str) -> Event | None:  # pragma: no cover
        """Deterministic deduplication lookup (SECTION 22)."""
        ...

    @abstractmethod
    def get_sequence(self, event_id: str) -> int:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...

    @abstractmethod
    def iter_all(self) -> Iterator[Event]:  # pragma: no cover
        ...
