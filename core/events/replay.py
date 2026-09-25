"""Event replay foundation (owned by core.events).

Phase 1 ships a replay-safe read API, not a full replay engine (SECTION 26).
Replay reads historical events WITHOUT mutating them, runs only in the
REPLAY environment, and cannot route replayed events into LIVE execution:
the replay runner is environment-gated and any attempt to run it in a
non-REPLAY environment fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from architecture.contracts.environment import (
    Environment,
    assert_same_environment,
    parse_environment,
)
from architecture.contracts.errors import EnvironmentMismatchError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc, utc_now
from core.events.contracts import Event, EventType, build_event
from core.events.store import EventStore

CONTRACT_VERSION = "1.0.0"
REPLAY_ENVIRONMENT = Environment.REPLAY


@dataclass(frozen=True)
class ReplayFilter:
    time_start: datetime | None = None
    time_end: datetime | None = None
    event_type: str | None = None
    correlation_id: str | None = None

    def validate(self) -> None:
        if self.time_start is not None:
            ensure_utc(self.time_start, location="replay.time_start")
        if self.time_end is not None:
            ensure_utc(self.time_end, location="replay.time_end")
        if (
            self.time_start is not None
            and self.time_end is not None
            and ensure_utc(self.time_end) < ensure_utc(self.time_start)
        ):
            raise EnvironmentMismatchError(
                "Replay time range end before start",
                location="replay.time_range",
                rule_id="TIME-001",
            )


@dataclass(frozen=True)
class ReplayResult:
    events: tuple[Event, ...]
    replay_started_event_id: str
    replay_completed_event_id: str
    environment: str


class ReplayRunner:
    """Read-only replay over the event store; REPLAY environment only."""

    def __init__(self, store: EventStore) -> None:
        self._store = store

    def run(self, replay_filter: ReplayFilter | None = None, *, environment: str | None = None) -> ReplayResult:
        declared = parse_environment(environment, location="replay.environment") if environment else REPLAY_ENVIRONMENT
        assert_same_environment(
            declared, REPLAY_ENVIRONMENT, context="replay.environment_gate"
        )
        replay_filter = replay_filter or ReplayFilter()
        replay_filter.validate()

        correlation = new_identifier("correlation_id")
        started = self._marker(
            EventType.REPLAY_STARTED,
            correlation,
            {"filter": self._filter_description(replay_filter)},
        )
        events = tuple(self._fetch(replay_filter))
        completed = self._marker(
            EventType.REPLAY_COMPLETED,
            correlation,
            {"filter": self._filter_description(replay_filter), "event_count": len(events)},
        )
        return ReplayResult(
            events=events,
            replay_started_event_id=started,
            replay_completed_event_id=completed,
            environment=declared.value,
        )

    def _fetch(self, replay_filter: ReplayFilter):
        if replay_filter.correlation_id is not None:
            yield from self._store.iter_by_correlation_id(replay_filter.correlation_id)
            return
        if replay_filter.event_type is not None:
            source = self._store.query_by_type(replay_filter.event_type)
        elif replay_filter.time_start is not None or replay_filter.time_end is not None:
            start = replay_filter.time_start or datetime(2000, 1, 1, tzinfo=ensure_utc(utc_now()).tzinfo)
            end = replay_filter.time_end or utc_now()
            source = self._store.query_by_time(start, end)
        else:
            source = self._store.iter_all()
        for event in source:
            if replay_filter.time_start is not None and ensure_utc(event.event_time) < ensure_utc(replay_filter.time_start):
                continue
            if replay_filter.time_end is not None and ensure_utc(event.event_time) > ensure_utc(replay_filter.time_end):
                continue
            yield event

    def _marker(self, event_type: EventType, correlation_id: str, payload: dict) -> str:
        event = build_event(
            event_id=new_identifier("event_id"),
            event_type=event_type,
            source="core.events.replay",
            source_id="replay-runner",
            environment=REPLAY_ENVIRONMENT.value,
            correlation_id=correlation_id,
            event_time=utc_now(),
            received_time=utc_now(),
            payload=payload,
        )
        self._store.append(event)
        return event.event_id

    @staticmethod
    def _filter_description(replay_filter: ReplayFilter) -> dict:
        return {
            "time_start": ensure_utc(replay_filter.time_start).isoformat()
            if replay_filter.time_start else None,
            "time_end": ensure_utc(replay_filter.time_end).isoformat()
            if replay_filter.time_end else None,
            "event_type": replay_filter.event_type,
            "correlation_id": replay_filter.correlation_id,
        }
