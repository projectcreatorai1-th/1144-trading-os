"""Event bus port (owned by core.events).

Technology-neutral: publish/subscribe/unsubscribe with validation. Phase 1
ships an in-process implementation (platform.event_bus); future Redis/Kafka/
NATS/RabbitMQ implementations must satisfy this port without breaking core
(SECTION 27).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from core.events.contracts import Event

EventHandler = Callable[[Event], None]


class EventBus(ABC):
    @abstractmethod
    def publish(self, event: Event) -> None:  # pragma: no cover - port definition
        """Validate the event and dispatch to subscribers (fail closed)."""
        ...

    @abstractmethod
    def subscribe(self, handler: EventHandler, *, event_type: str | None = None) -> str:  # pragma: no cover
        """Returns a subscription id."""
        ...

    @abstractmethod
    def unsubscribe(self, subscription_id: str) -> None:  # pragma: no cover
        ...
