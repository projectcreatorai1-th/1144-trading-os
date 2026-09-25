"""Routing: events, commands, correlation, idempotency, ordering (§10-§14).

The gateway routes WITHOUT interpreting strategy/risk/research semantics.
Every rejected or anomalous message is classified, audited and NOT routed.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from architecture.contracts.errors import ContractError

from platform.gateway.contracts import (
    ANALYZER_COMMANDS,
    EVENT_CLASSES,
    EventEnvelope,
    GatewayCommand,
    ROUTED_COMMANDS,
    new_id,
    utc_now_iso,
)


class RoutingError(ContractError):
    rule_id = "GWS-002"


# --------------------------------------------------------------------- #
# Ordering (§14): duplicate / out-of-order / missing detection, original
# ordering is preserved as evidence - never silently rewritten.
# --------------------------------------------------------------------- #
@dataclass
class OrderingDecision:
    status: str                     # OK | DUPLICATE | OUT_OF_ORDER | MISSING
    original_sequence: int
    observed_sequence: int


class OrderingTracker:
    def __init__(self) -> None:
        self._last: dict[str, int] = {}
        self._seen: dict[str, set[int]] = {}

    def check(self, source: str, sequence: int) -> OrderingDecision:
        last = self._last.get(source, 0)
        seen = self._seen.setdefault(source, set())
        if sequence in seen:
            return OrderingDecision("DUPLICATE", sequence, sequence)
        seen.add(sequence)
        status = "OK" if sequence > last else "OUT_OF_ORDER"
        if status == "OK" and last and sequence > last + 1:
            status = "MISSING"      # gap detected; event still routed
        if sequence > last:
            self._last[source] = sequence
        return OrderingDecision(status, sequence, sequence)


# --------------------------------------------------------------------- #
# Idempotency (§13): same client + idempotency key + operation = ONE op
# --------------------------------------------------------------------- #
class IdempotencyManager:
    def __init__(self, max_entries: int = 10_000) -> None:
        self._max = max_entries
        self._keys: dict[tuple[str, str, str], str] = {}
        self._order: deque = deque()

    def check(self, client_id: str, idempotency_key: str,
              operation: str) -> str:
        """Returns NEW (first sight) or the recorded state of the logical
        operation; retry never creates a second operation."""
        if not idempotency_key:
            return "NEW"
        key = (client_id, idempotency_key, operation)
        if key in self._keys:
            return self._keys[key]
        self._keys[key] = "PROCESSING"
        self._order.append(key)
        while len(self._order) > self._max:
            old = self._order.popleft()
            self._keys.pop(old, None)
        return "NEW"

    def settle(self, client_id: str, idempotency_key: str,
               operation: str, state: str) -> None:
        if idempotency_key:
            self._keys[(client_id, idempotency_key, operation)] = state


# --------------------------------------------------------------------- #
# Correlation (§12): response must reference its request
# --------------------------------------------------------------------- #
@dataclass
class RequestRecord:
    request_id: str
    correlation_id: str
    trace_id: str
    client_id: str
    target: str
    state: str = "REQUESTED"
    created_at: float = field(default_factory=time.time)


class CorrelationManager:
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout = timeout_seconds
        self._requests: dict[str, RequestRecord] = {}

    def open(self, *, request_id: str, correlation_id: str, trace_id: str,
             client_id: str, target: str) -> RequestRecord:
        if not request_id:
            raise RoutingError("request_id required (§12)",
                               location="gateway.correlation")
        if request_id in self._requests:
            raise RoutingError(
                f"request_id {request_id!r} already in flight",
                location="gateway.correlation")
        record = RequestRecord(request_id=request_id,
                               correlation_id=correlation_id or new_id("COR"),
                               trace_id=trace_id or new_id("TRC"),
                               client_id=client_id, target=target)
        self._requests[request_id] = record
        return record

    def complete(self, request_id: str, state: str) -> RequestRecord:
        record = self._requests.get(request_id)
        if record is None:
            raise RoutingError(
                f"response references unknown request {request_id!r} "
                "(request A -> response B is forbidden, §12)",
                location="gateway.correlation")
        record.state = state
        return record

    def expired(self) -> list[RequestRecord]:
        now = time.time()
        expired = [r for r in self._requests.values()
                   if now - r.created_at > self.timeout
                   and r.state in ("REQUESTED", "ACCEPTED", "PROCESSING")]
        for record in expired:
            record.state = "TIMEOUT"
        return expired


# --------------------------------------------------------------------- #
# Event routing (§10): source -> destination by event class
# --------------------------------------------------------------------- #
class EventRouter:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[EventEnvelope], None]]] = {}

    def subscribe(self, event_class: str,
                  handler: Callable[[EventEnvelope], None]) -> None:
        if event_class not in EVENT_CLASSES:
            raise RoutingError(
                f"unknown event class {event_class!r}",
                location="gateway.event_router")
        self._subscribers.setdefault(event_class, []).append(handler)

    def route(self, envelope: EventEnvelope) -> int:
        if envelope.event_type not in EVENT_CLASSES:
            raise RoutingError(
                f"event_type {envelope.event_type!r} is not routable",
                location="gateway.event_router")
        handlers = self._subscribers.get(envelope.event_type, [])
        for handler in handlers:
            handler(envelope)
        return len(handlers)


# --------------------------------------------------------------------- #
# Command routing (§11): validate -> authorize boundary -> correlate ->
# route -> audit -> result. Targets are session senders; the gateway adds
# no strategy interpretation.
# --------------------------------------------------------------------- #
class CommandRouter:
    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[GatewayCommand], dict]] = {}

    def register_target(self, command_type: str,
                        handler: Callable[[GatewayCommand], dict]) -> None:
        legal = ROUTED_COMMANDS + ANALYZER_COMMANDS
        if command_type not in legal:
            raise RoutingError(
                f"cannot register non-contract command {command_type!r}",
                location="gateway.command_router")
        self._handlers[command_type] = handler

    def route(self, command: GatewayCommand) -> dict:
        errors = command.validate()
        if errors:
            raise RoutingError(
                "invalid command: " + "; ".join(errors),
                location="gateway.command_router")
        handler = self._handlers.get(command.command_type)
        if handler is None:
            raise RoutingError(
                f"no route registered for {command.command_type!r} "
                f"-> target {command.target!r}",
                location="gateway.command_router")
        return handler(command)


# --------------------------------------------------------------------- #
# Backpressure (§17): WARN -> THROTTLE -> REJECT -> HALT, bounded memory
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class BackpressurePolicy:
    max_queue_size: int = 1000
    max_events_per_second: int = 500
    max_commands_per_second: int = 100
    max_pending_requests: int = 200


class BackpressureManager:
    def __init__(self, policy: BackpressurePolicy | None = None) -> None:
        self.policy = policy or BackpressurePolicy()
        self._events_window: deque = deque()
        self._commands_window: deque = deque()
        self.queue_depth = 0
        self.dropped = 0
        self.throttled = 0
        self.halted = False

    def _rate(self, window: deque, limit: int) -> bool:
        now = time.time()
        window.append(now)
        while window and now - window[0] > 1.0:
            window.popleft()
        return len(window) > limit

    def admit_event(self) -> str:
        if self.halted:
            return "HALT"
        if self.queue_depth >= self.policy.max_queue_size:
            self.dropped += 1
            return "REJECT"
        if self._rate(self._events_window,
                      self.policy.max_events_per_second):
            self.throttled += 1
            return "THROTTLE"
        self.queue_depth += 1
        return "WARN" if self.queue_depth > self.policy.max_queue_size * 0.8 \
            else "OK"

    def admit_command(self) -> str:
        if self.halted:
            return "HALT"
        if self._rate(self._commands_window,
                      self.policy.max_commands_per_second):
            self.throttled += 1
            return "THROTTLE"
        return "OK"

    def release(self) -> None:
        self.queue_depth = max(0, self.queue_depth - 1)

    def set_halted(self, halted: bool) -> None:
        self.halted = halted
