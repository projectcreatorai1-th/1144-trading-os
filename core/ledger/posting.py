"""Ledger posting service (owned by core.ledger).

Flow (SECTION 17): EVENT -> VALIDATE -> CREATE ENTRY -> POST (hash) -> STORE
-> AUDIT. Deterministic and idempotent: the idempotency key is derived from
the source event + entry type + semantic identity - never from timestamps or
random values (SECTION 18). Corrections are REVERSAL/ADJUSTMENT entries that
reference the original; originals are never edited or deleted (SECTION 16).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping

from architecture.contracts.errors import ContractValidationError, StorageError
from architecture.contracts.identifiers import new_identifier, validate_identifier
from architecture.contracts.observability import LogRecord, LogLevel
from architecture.contracts.time import ensure_utc
from core.events.contracts import Event, EventType
from core.ledger.contracts import LedgerEntry, LedgerType
from core.ledger.money import parse_decimal, precision_for
from core.ledger.store import LedgerStore
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"


def compute_idempotency_key(
    *,
    source_event_id: str,
    entry_type: str,
    account_id: str,
    currency: str,
    amount: Decimal,
    semantic_ref: str | None = None,
) -> str:
    """Deterministic key: same event + same semantics = same key (SECTION 18)."""
    material = "|".join(
        [
            source_event_id,
            entry_type,
            account_id,
            currency,
            str(amount),
            semantic_ref or "",
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerDraft:
    """Input for posting: what to book, derived from an event by a handler."""

    entry_type: LedgerType
    account_id: str
    amount: Any  # Decimal / int / decimal string (floats rejected)
    currency: str
    reason: str
    quantity: Any | None = None
    symbol: str | None = None
    order_id: str | None = None
    execution_id: str | None = None
    position_id: str | None = None
    strategy_id: str | None = None
    semantic_ref: str | None = None
    reverses_entry_id: str | None = None
    adjusts_entry_id: str | None = None


@dataclass(frozen=True)
class PostingResult:
    entry: LedgerEntry
    created: bool  # False when an idempotent replay returned the existing entry


class LedgerPostingService:
    def __init__(
        self,
        store: LedgerStore,
        audit: AuditRepository,
    ) -> None:
        self._store = store
        self._audit = audit

    def post(self, draft: LedgerDraft, *, event: Event, entry_time: datetime) -> PostingResult:
        """Post one entry derived from an event. Idempotent: re-posting the
        same draft from the same event returns the existing entry (created
        False) - no duplicate canonical ledger."""
        amount = parse_decimal(draft.amount, location="posting.amount")
        precision_for(draft.currency)  # currency must be registered
        if not isinstance(draft.entry_type, LedgerType):
            raise ContractValidationError(
                f"posting.entry_type must be a LedgerType, got {draft.entry_type!r}",
                location="posting.entry_type",
            )
        idempotency_key = compute_idempotency_key(
            source_event_id=event.event_id,
            entry_type=draft.entry_type.value,
            account_id=draft.account_id,
            currency=draft.currency,
            amount=amount,
            semantic_ref=draft.semantic_ref,
        )
        existing = self._store.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return PostingResult(entry=existing, created=False)

        # corrections must reference the original entry (LEDGER-002), checked
        # before construction so the structured rule id surfaces
        if draft.entry_type is LedgerType.ADJUSTMENT and draft.adjusts_entry_id is None:
            raise ContractValidationError(
                "ADJUSTMENT drafts must set adjusts_entry_id "
                "(corrections reference the original, LEDGER-002)",
                location="posting.adjustment",
                rule_id="LEDGER-002",
            )
        if (draft.adjusts_entry_id is not None or draft.reverses_entry_id is not None) \
                and draft.entry_type is LedgerType.TRANSFER:
            raise ContractValidationError(
                "TRANSFER entries must not be correction entries",
                location="posting.correction",
            )

        previous_hash = self._store.last_entry_hash(draft.account_id)
        entry = LedgerEntry(
            ledger_entry_id=new_identifier("ledger_entry_id"),
            entry_type=draft.entry_type,
            account_id=draft.account_id,
            amount=amount,
            currency=draft.currency,
            environment=event.environment,
            entry_time=ensure_utc(entry_time, location="posting.entry_time"),
            correlation_id=event.correlation_id,
            source_event_id=event.event_id,
            order_id=draft.order_id,
            execution_id=draft.execution_id,
            position_id=draft.position_id,
            strategy_id=draft.strategy_id,
            causation_id=event.causation_id,
            quantity=parse_decimal(draft.quantity, location="posting.quantity")
            if draft.quantity is not None else None,
            symbol=draft.symbol,
            reverses_entry_id=draft.reverses_entry_id,
            adjusts_entry_id=draft.adjusts_entry_id,
        )
        object.__setattr__(entry, "idempotency_key", idempotency_key)
        object.__setattr__(entry, "previous_entry_hash", previous_hash)
        object.__setattr__(entry, "entry_hash", entry.compute_hash())
        entry.validate()

        try:
            self._store.append(entry)
        except StorageError as exc:
            if exc.rule_id == "LEDGER-003" or "idempotency" in exc.message:
                raced = self._store.find_by_idempotency_key(idempotency_key)
                if raced is not None:
                    return PostingResult(entry=raced, created=False)
            raise
        self._audit.append(self._audit_record(entry, before=None, after=entry.to_dict()))
        return PostingResult(entry=entry, created=True)

    def reverse(self, entry: LedgerEntry, *, event: Event, entry_time: datetime,
                reason: str) -> LedgerEntry:
        """Post a REVERSAL that negates the original entry. The original is
        never deleted (invariant 7)."""
        if entry.reverses_entry_id is not None:
            raise ContractValidationError(
                "Cannot reverse a reversal entry",
                location="posting.reverse",
            )
        reversal_draft = LedgerDraft(
            entry_type=entry.entry_type,
            account_id=entry.account_id,
            amount=-entry.amount,
            currency=entry.currency,
            reason=reason,
            quantity=-entry.quantity if entry.quantity is not None else None,
            symbol=entry.symbol,
            order_id=entry.order_id,
            execution_id=entry.execution_id,
            position_id=entry.position_id,
            strategy_id=entry.strategy_id,
            semantic_ref=entry.ledger_entry_id,
            reverses_entry_id=entry.ledger_entry_id,
        )
        result = self.post(reversal_draft, event=event, entry_time=entry_time)
        reversal = result.entry
        if result.created:
            self._audit.append(AuditRecord(
                audit_id=new_identifier("audit_id"),
                actor_type=ActorType.SYSTEM,
                actor_id="core.ledger.posting",
                action="LEDGER_REVERSED",
                entity_type="ledger_entry",
                entity_id=entry.ledger_entry_id,
                event_time=ensure_utc(entry_time, location="posting.reverse"),
                before={"amount": str(entry.amount)},
                after={"reversal_entry_id": reversal.ledger_entry_id,
                       "amount": str(reversal.amount)},
                reason=reason,
                source="core.ledger.posting",
                environment=entry.environment,
                correlation_id=entry.correlation_id,
            ))
        return reversal

    @staticmethod
    def _audit_record(entry: LedgerEntry, *, before: Mapping[str, Any] | None,
                      after: Mapping[str, Any]) -> AuditRecord:
        return AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.SYSTEM,
            actor_id="core.ledger.posting",
            action="LEDGER_POSTED",
            entity_type="ledger_entry",
            entity_id=entry.ledger_entry_id,
            event_time=entry.entry_time,
            before=dict(before) if before is not None else None,
            after=dict(after),
            reason=f"{entry.entry_type.value} posting",
            source="core.ledger.posting",
            environment=entry.environment,
            correlation_id=entry.correlation_id,
            risk_version=None,
        )
