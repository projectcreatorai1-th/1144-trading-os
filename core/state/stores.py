"""State store ports (owned by core.state).

Historical state and transitions are append-only; current state is a derived
view. Idempotency: re-applying the same event to the same entity is detected
so crash recovery can replay safely (SECTIONS 8/9/33).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterator

from core.state.contracts import StateRecord, StateSnapshot, StateTransitionRecord


class StateStore(ABC):
    @abstractmethod
    def save_state(self, state: StateRecord, transition: StateTransitionRecord) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def get_current_state(self, entity_type: str, entity_id: str) -> StateRecord | None:  # pragma: no cover
        ...

    @abstractmethod
    def get_state_at_time(self, entity_type: str, entity_id: str, at: datetime) -> StateRecord | None:  # pragma: no cover
        ...

    @abstractmethod
    def get_state_history(self, entity_type: str, entity_id: str) -> Iterator[StateRecord]:  # pragma: no cover
        ...

    @abstractmethod
    def has_applied_event(self, entity_type: str, entity_id: str, event_id: str) -> bool:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...


class SnapshotStore(ABC):
    @abstractmethod
    def append(self, snapshot: StateSnapshot) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def get_by_id(self, snapshot_id: str) -> StateSnapshot:  # pragma: no cover
        ...

    @abstractmethod
    def latest_for(self, entity_type: str, entity_id: str) -> StateSnapshot | None:  # pragma: no cover
        ...
