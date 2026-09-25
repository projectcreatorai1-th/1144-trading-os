"""In-process event bus implementation (owned by platform.event_bus).

Synchronous, deterministic, single-threaded (documented limitation - no
thread/process safety claimed). Satisfies the core.events.EventBus port, so
a future Redis/Kafka/NATS/RabbitMQ implementation can replace it without
touching core (SECTION 27, 44).
"""
from __future__ import annotations

import uuid
from typing import Callable

from architecture.contracts.environment import assert_same_environment
from architecture.contracts.errors import BusError
from core.events.bus import EventBus
from core.events.contracts import Event

CONTRACT_VERSION = "1.0.0"


class InProcessEventBus(EventBus):
    def __init__(self, *, declared_environment: str | None = None) -> None:
        self._declared_environment = declared_environment
        self._subscriptions: dict[str, tuple[Callable[[Event], None], str | None]] = {}
        self._order: list[str] = []

    @property
    def declared_environment(self) -> str | None:
        return self._declared_environment

    def publish(self, event: Event) -> None:
        event.validate()
        if self._declared_environment is not None:
            assert_same_environment(
                event.environment,
                self._declared_environment,
                context="event_bus.environment_gate",
            )
        failures: list[dict[str, str]] = []
        for subscription_id in list(self._order):
            handler, event_type = self._subscriptions[subscription_id]
            if event_type is not None and event_type != event.event_type.value:
                continue
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001 - collected, never swallowed silently
                failures.append({"subscription_id": subscription_id, "error": str(exc)})
        if failures:
            raise BusError(
                "Event bus subscriber failures (all subscribers were attempted)",
                location="event_bus.publish",
                details={"failures": failures, "event_id": event.event_id},
            )

    def subscribe(self, handler: Callable[[Event], None], *, event_type: str | None = None) -> str:
        if not callable(handler):
            raise BusError("subscribe requires a callable handler", location="event_bus.subscribe")
        subscription_id = f"sub_{uuid.uuid4().hex}"
        self._subscriptions[subscription_id] = (handler, event_type)
        self._order.append(subscription_id)
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        if subscription_id not in self._subscriptions:
            raise BusError(
                f"Unknown subscription id '{subscription_id}'",
                location="event_bus.unsubscribe",
            )
        del self._subscriptions[subscription_id]
        self._order.remove(subscription_id)
