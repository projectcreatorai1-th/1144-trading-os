"""Event repository port (owned by core.events).

Core depends on this port; platform.database provides technology-specific
implementations in later phases (SECTION 33). No implementation exists here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.events.contracts import Event


class EventRepository(ABC):
    """Persistence port for immutable events (append-only)."""

    @abstractmethod
    def append(self, event: Event) -> None:  # pragma: no cover - port definition
        ...

    @abstractmethod
    def get_by_id(self, event_id: str) -> Event:  # pragma: no cover - port definition
        ...

    @abstractmethod
    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[Event]:  # pragma: no cover
        ...
