"""Event -> State -> Ledger -> Audit processor (owned by core.state).

For each accepted event: project state (if a rule applies), post ledger
entries (if a registered ledger handler applies), emit the LEDGER_POSTED
event for event-sourced balance reconstruction, and audit every action.

Transactional boundary (SECTION 35): state write, ledger write and event
write are SEPARATE transactions in Phase 2 storage. Recovery is explicit and
idempotent: replaying events through this processor skips already-applied
state transitions (per-entity event idempotency) and duplicate ledger posts
(deterministic idempotency keys), so reprocessing converges without
duplicates. No cross-store atomicity is claimed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping

from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc
from core.events.contracts import Event, EventType, build_event
from core.events.store import EventStore
from core.ledger.posting import LedgerDraft, LedgerPostingService
from core.ledger.contracts import LedgerEntry
from core.state.projector import EventStateProjector
from core.state.stores import StateStore
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"

LedgerHandler = Callable[[Event], LedgerDraft | None]


@dataclass(frozen=True)
class ProcessingOutcome:
    event_id: str
    state_applied: bool
    ledger_posted: bool
    ledger_entry_id: str | None
    ledger_posted_event_id: str | None


class EventStateLedgerProcessor:
    def __init__(
        self,
        *,
        states: StateStore,
        events: EventStore,
        posting: LedgerPostingService,
        audit: AuditRepository,
        projector: EventStateProjector | None = None,
        ledger_handlers: Mapping[str, LedgerHandler] | None = None,
    ) -> None:
        self._states = states
        self._events = events
        self._posting = posting
        self._audit = audit
        self._projector = projector or EventStateProjector()
        self._ledger_handlers: dict[str, LedgerHandler] = dict(ledger_handlers or {})

    def register_ledger_handler(self, event_type: str, handler: LedgerHandler) -> None:
        self._ledger_handlers[event_type] = handler

    def process(self, event: Event, *, entry_time: datetime | None = None) -> ProcessingOutcome:
        event.validate()
        state_applied = False
        application = self._projector.apply(event, self._states)
        if application is not None:
            self._audit.append(AuditRecord(
                audit_id=new_identifier("audit_id"),
                actor_type=ActorType.SYSTEM,
                actor_id="core.state.processor",
                action="STATE_TRANSITION",
                entity_type="state",
                entity_id=f"{application.state.entity_type.value}:{application.state.entity_id}",
                event_time=application.transition.timestamp,
                before={"status": application.transition.previous_state,
                        "version": application.transition.previous_version},
                after={"status": application.state.status,
                       "version": application.state.state_version},
                reason=application.transition.reason,
                source="core.state.processor",
                environment=event.environment,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
            ))
            state_applied = True

        ledger_entry: LedgerEntry | None = None
        ledger_posted_event_id: str | None = None
        handler = self._ledger_handlers.get(event.event_type.value)
        if handler is not None:
            draft = handler(event)
            if draft is not None:
                result = self._posting.post(
                    draft, event=event,
                    entry_time=ensure_utc(entry_time, location="processor.entry_time")
                    if entry_time else event.received_time,
                )
                ledger_entry = result.entry
                if result.created:
                    # only NEW posts emit LEDGER_POSTED (idempotent replay stays silent)
                    ledger_posted_event_id = self._emit_ledger_posted_event(ledger_entry, event)

        return ProcessingOutcome(
            event_id=event.event_id,
            state_applied=state_applied,
            ledger_posted=ledger_entry is not None,
            ledger_entry_id=ledger_entry.ledger_entry_id if ledger_entry else None,
            ledger_posted_event_id=ledger_posted_event_id,
        )

    def process_all(self, events: Iterable[Event]) -> list[ProcessingOutcome]:
        return [self.process(event) for event in events]

    def recover(self) -> list[ProcessingOutcome]:
        """Idempotent recovery: reprocess every stored event; state skips
        applied events, ledger skips duplicate posts (SECTIONS 33-35)."""
        return self.process_all(self._events.iter_all())

    def _emit_ledger_posted_event(self, entry: LedgerEntry, cause: Event) -> str:
        event = build_event(
            event_id=new_identifier("event_id"),
            event_type=EventType.LEDGER_POSTED,
            source="core.ledger.posting",
            source_id=entry.ledger_entry_id,
            environment=entry.environment,
            correlation_id=entry.correlation_id,
            causation_id=cause.event_id,
            entity_id=entry.account_id,
            event_time=entry.entry_time,
            received_time=entry.entry_time,
            payload={
                "ledger_entry_id": entry.ledger_entry_id,
                "account_id": entry.account_id,
                "entry_type": entry.entry_type.value,
                "amount": str(entry.amount),
                "currency": entry.currency,
            },
            metadata={"ledger_entry_id": entry.ledger_entry_id},
        )
        self._events.append(event)
        return event.event_id
