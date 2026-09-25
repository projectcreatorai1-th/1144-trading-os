"""Event timeline service (owned by core.events).

Reconstructs the chronological chain for one correlation_id. Historical
timestamps are NEVER changed for prettier ordering: the timeline exposes
event-time order and ingestion order as separate views, and flags
out-of-order entries instead of fixing them (SECTION 25).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from architecture.contracts.errors import StorageError
from architecture.contracts.time import ensure_utc
from core.events.contracts import Event
from core.events.store import EventStore

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class TimelineEntry:
    event_id: str
    event_type: str
    event_time: datetime
    received_time: datetime
    source: str
    causation_id: str | None
    store_sequence: int
    sequence_number: int | None
    quality: str | None
    out_of_order: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_time": ensure_utc(self.event_time).isoformat(),
            "received_time": ensure_utc(self.received_time).isoformat(),
            "source": self.source,
            "causation_id": self.causation_id,
            "sequence": self.sequence_number,
            "quality": self.quality,
            "out_of_order": self.out_of_order,
            "store_sequence": self.store_sequence,
        }


@dataclass(frozen=True)
class EventTimeline:
    correlation_id: str
    entries: tuple[TimelineEntry, ...]

    @property
    def in_event_time_order(self) -> tuple[TimelineEntry, ...]:
        return tuple(sorted(self.entries, key=lambda e: (e.event_time, e.store_sequence)))

    @property
    def in_ingestion_order(self) -> tuple[TimelineEntry, ...]:
        return tuple(sorted(self.entries, key=lambda e: e.store_sequence))

    @property
    def out_of_order_event_ids(self) -> tuple[str, ...]:
        return tuple(e.event_id for e in self.entries if e.out_of_order)


class EventTimelineService:
    def __init__(self, store: EventStore) -> None:
        self._store = store

    def build(self, correlation_id: str) -> EventTimeline:
        events = list(self._store.iter_by_correlation_id(correlation_id))
        entries: list[TimelineEntry] = []
        seen_event_times: list[datetime] = []
        for event in events:
            entries.append(self._entry(event, seen_event_times))
        return EventTimeline(correlation_id=correlation_id, entries=tuple(entries))

    def _entry(self, event: Event, seen_event_times: list[datetime]) -> TimelineEntry:
        event_time = ensure_utc(event.event_time, location="timeline.event_time")
        out_of_order = any(event_time < seen for seen in seen_event_times)
        seen_event_times.append(event_time)
        return TimelineEntry(
            event_id=event.event_id,
            event_type=event.event_type.value,
            event_time=event_time,
            received_time=ensure_utc(event.received_time, location="timeline.received_time"),
            source=event.source,
            causation_id=event.causation_id,
            store_sequence=self._store.get_sequence(event.event_id),
            sequence_number=self._metadata_int(event, "sequence_number"),
            quality=self._metadata_str(event, "data_quality"),
            out_of_order=out_of_order,
        )

    @staticmethod
    def _metadata_int(event: Event, key: str) -> int | None:
        value = event.metadata.get(key) if event.metadata else None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _metadata_str(event: Event, key: str) -> str | None:
        value = event.metadata.get(key) if event.metadata else None
        return value if isinstance(value, str) else None
