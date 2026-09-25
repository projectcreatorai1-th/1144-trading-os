"""Event-to-state projector (owned by core.state).

Pure, deterministic projection rules: event -> (category, entity, status,
payload). The same rules run live and during rebuild, so identical event
history always produces identical state (SECTION 11/39). Timestamps derive
from the event itself - never from a clock.

Handlers shipped in Phase 2 (state infrastructure only, no trading logic):
- SYSTEM_STATE_CHANGED -> SYSTEM_STATE
- DATA_QUALITY_CHANGED -> DATA_STATE (per data source)
- LEDGER_POSTED -> ACCOUNT_STATE (per account, balance payload)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from architecture.contracts.errors import ContractValidationError
from core.events.contracts import Event, EventType
from core.state.contracts import StateCategory, StateRecord
from core.state.engine import StateApplication, StateEngine
from core.state.stores import StateStore

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class Projection:
    category: StateCategory
    entity_id: str
    new_status: str
    payload: Mapping[str, Any]
    reason: str


Handler = Callable[[Event], Projection | None]

DEFAULT_ACTOR = "core.state.projector"


def _system_state_handler(event: Event) -> Projection | None:
    state = event.payload.get("state")
    if not isinstance(state, str) or not state:
        raise ContractValidationError(
            "SYSTEM_STATE_CHANGED events must carry payload.state",
            location="projector.system_state",
        )
    entity = event.payload.get("system", "1144-os")
    return Projection(
        category=StateCategory.SYSTEM_STATE,
        entity_id=str(entity),
        new_status=state,
        payload={"last_event_type": event.event_type.value},
        reason=f"system state event {event.event_id}",
    )


def _data_quality_handler(event: Event) -> Projection | None:
    level = event.payload.get("level")
    if not isinstance(level, str) or not level:
        return None
    return Projection(
        category=StateCategory.DATA_STATE,
        entity_id=event.source_id,
        new_status=level,
        payload={"reasons": list(event.payload.get("reasons", []))},
        reason=f"data quality changed to {level}",
    )


def _ledger_posted_handler(event: Event) -> Projection | None:
    account_id = event.payload.get("account_id")
    currency = event.payload.get("currency")
    amount = event.payload.get("amount")
    if not isinstance(account_id, str) or not account_id:
        return None
    if not isinstance(currency, str) or not isinstance(amount, str):
        raise ContractValidationError(
            "LEDGER_POSTED events must carry account_id, currency and decimal-string amount",
            location="projector.ledger_posted",
        )
    return Projection(
        category=StateCategory.ACCOUNT_STATE,
        entity_id=account_id,
        new_status="POSTED",
        payload={"balance_delta": {currency: amount}},
        reason=f"ledger entry {event.payload.get('entry_type', 'UNKNOWN')} posted",
    )


DEFAULT_HANDLERS: dict[EventType, Handler] = {
    EventType.SYSTEM_STATE_CHANGED: _system_state_handler,
    EventType.DATA_QUALITY_CHANGED: _data_quality_handler,
    EventType.LEDGER_POSTED: _ledger_posted_handler,
}


class EventStateProjector:
    """Applies events to state. `project` is pure (rebuild-safe);
    `apply` additionally persists through the state store (idempotent)."""

    def __init__(self, engine: StateEngine | None = None,
                 handlers: Mapping[EventType, Handler] | None = None) -> None:
        self._engine = engine or StateEngine()
        self._handlers: dict[EventType, Handler] = dict(handlers or DEFAULT_HANDLERS)

    @property
    def handled_event_types(self) -> tuple[EventType, ...]:
        return tuple(self._handlers)

    def projection_for(self, event: Event) -> Projection | None:
        """Pure probe: which entity/status would this event project, if any?"""
        handler = self._handlers.get(event.event_type)
        if handler is None:
            return None
        return handler(event)

    def project(self, event: Event, current: StateRecord | None) -> StateApplication | None:
        """Pure projection: no store writes, no clock reads."""
        projection = self.projection_for(event)
        if projection is None:
            return None
        if current is not None and current.status == projection.new_status \
                and current.entity_type is projection.category \
                and current.entity_id == projection.entity_id:
            return None  # no state change - idempotent no-op (never a fabricated transition)
        payload = dict(projection.payload)
        if projection.category is StateCategory.ACCOUNT_STATE:
            from decimal import Decimal

            balances = dict(current.payload.get("balances", {})) if current is not None else {}
            for currency, amount in dict(projection.payload["balance_delta"]).items():
                balances[currency] = str(Decimal(balances.get(currency, "0")) + Decimal(amount))
            payload = {"balances": balances}
        return self._engine.apply_event(
            entity_type=projection.category,
            entity_id=projection.entity_id,
            current=current,
            new_status=projection.new_status,
            event=event,
            payload=payload,
            reason=projection.reason,
            actor=DEFAULT_ACTOR,
            effective_time=event.event_time,
            observed_time=event.received_time,
            processed_time=event.received_time,
        )

    def apply(self, event: Event, store: StateStore) -> StateApplication | None:
        """Store-backed application. Idempotent: events already applied to an
        entity are skipped (crash recovery replays safely)."""
        handler = self._handlers.get(event.event_type)
        if handler is None:
            return None
        probe = handler(event)
        if probe is None:
            return None
        if store.has_applied_event(probe.category.value, probe.entity_id, event.event_id):
            return None  # already applied - deterministic no-op
        current = store.get_current_state(probe.category.value, probe.entity_id)
        application = self.project(event, current)
        if application is None:
            return None
        store.save_state(application.state, application.transition)
        return application
