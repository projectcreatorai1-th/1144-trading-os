"""Deterministic state rebuilder (owned by core.state).

Same events + same contract versions + same projection rules = same state
(SECTION 11). The rebuilder replays events through the SAME projector used
live (pure path, no store writes), optionally starting from a verified
snapshot. It never reads a clock and never mutates stored state."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from architecture.contracts.errors import ContractValidationError
from core.events.contracts import Event
from core.events.store import EventStore
from core.state.contracts import StateCategory, StateRecord, StateSnapshot
from core.state.projector import EventStateProjector
from core.state.stores import SnapshotStore

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class RebuildReport:
    entity_type: StateCategory
    entity_id: str
    state: StateRecord | None
    from_event_sequence: int
    applied_events: int


class StateRebuilder:
    def __init__(self, events: EventStore, projector: EventStateProjector,
                 snapshots: SnapshotStore | None = None) -> None:
        self._events = events
        self._projector = projector
        self._snapshots = snapshots

    def rebuild(
        self,
        *,
        entity_type: StateCategory,
        entity_id: str,
        upto_sequence: int | None = None,
        from_snapshot: StateSnapshot | None = None,
    ) -> RebuildReport:
        current: StateRecord | None = None
        start_sequence = 1
        if from_snapshot is not None:
            from_snapshot.verify_hash()  # corrupted snapshots rejected (STATE-003)
            if from_snapshot.entity_type is not entity_type or from_snapshot.entity_id != entity_id:
                raise ContractValidationError(
                    "Snapshot does not belong to the rebuilt entity",
                    location="rebuild.snapshot",
                )
            start_sequence = from_snapshot.event_sequence + 1
            current = StateRecord.from_snapshot_seed(from_snapshot)
        applied = 0
        for event in self._relevant_events(entity_type, entity_id, start_sequence, upto_sequence):
            application = self._projector.project(event, current)
            if application is not None:
                current = application.state
                applied += 1
        return RebuildReport(
            entity_type=entity_type,
            entity_id=entity_id,
            state=current,
            from_event_sequence=start_sequence,
            applied_events=applied,
        )

    def _relevant_events(
        self,
        entity_type: StateCategory,
        entity_id: str,
        start_sequence: int,
        upto_sequence: int | None,
    ) -> Iterator[Event]:
        for event in self._events.iter_all():
            sequence = self._events.get_sequence(event.event_id)
            if sequence < start_sequence:
                continue
            if upto_sequence is not None and sequence > upto_sequence:
                continue
            probe = self._projector.projection_for(event)
            if probe is None or probe.category is not entity_type or probe.entity_id != entity_id:
                continue  # event does not project onto the rebuilt entity
            yield event
