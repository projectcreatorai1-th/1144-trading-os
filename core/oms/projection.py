"""Execution projection: fills -> position state + ledger effects
(owned by core.oms, integrating Phase 2 subsystems).

Positions derive from CONFIRMED executions through the Phase 2 State Engine
(POSITION_STATE) - never a competing position store. Financial effects post
to the Phase 2 Ledger with its existing idempotency + hash chain. Duplicate
reports create no duplicate ledger effect (SECTION 28/29)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc, utc_now
from core.events.contracts import Event, EventType, build_event
from core.execution.contracts import Order, OrderSide
from core.ledger.contracts import LedgerType
from core.ledger.posting import LedgerDraft, LedgerPostingService
from core.oms.contracts import ExecutionReport, ExecutionStatus, ExecutionType
from core.state.contracts import StateCategory
from core.state.engine import StateEngine
from core.state.stores import StateStore
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class ProjectionResult:
    position_state: Any            # Phase 2 StateRecord (POSITION_STATE)
    ledger_entry_id: str | None    # Phase 2 posted entry (None = duplicate)
    event_id: str                  # EXECUTION_RECEIVED event in the event store
    duplicate: bool                # True when the execution was already projected


class ExecutionProjector:
    """Projects confirmed fills into position state + ledger via Phase 2."""

    def __init__(self, *, states: StateStore, posting: LedgerPostingService,
                 events, audit: AuditRepository,
                 engine: StateEngine | None = None) -> None:
        self._states = states
        self._posting = posting
        self._events = events
        self._audit = audit
        self._engine = engine or StateEngine()

    def project(self, report: ExecutionReport, order: Order,
                *, at: datetime | None = None) -> ProjectionResult:
        report.validate()
        order.validate()
        moment = ensure_utc(at, location="projection.at") if at else utc_now()
        if report.execution_type not in (ExecutionType.FILL, ExecutionType.PARTIAL_FILL):
            raise ContractError(
                "Only fills project into positions/ledger",
                location="projection.type",
            )
        if report.status is not ExecutionStatus.CONFIRMED:
            raise ContractError(
                "Only CONFIRMED executions project (UNKNOWN never projects)",
                location="projection.status",
            )
        if report.environment != order.environment:
            raise ContractError(
                f"Execution environment {report.environment} != order environment "
                f"{order.environment} (fail closed)",
                location="projection.environment", rule_id="ENV-004",
            )

        # 1. canonical event (event-sourced evidence, Phase 1 infra)
        event = build_event(
            event_id=new_identifier("event_id"),
            event_type=EventType.ORDER_FILLED
            if report.execution_type is ExecutionType.FILL
            else EventType.ORDER_PARTIALLY_FILLED,
            source="core.oms.projection",
            source_id=report.execution_id,
            environment=report.environment,
            correlation_id=order.correlation_id,
            causation_id=None,  # first execution event is the chain root
            entity_id=order.order_id,
            event_time=report.broker_timestamp,
            received_time=report.received_at,
            payload={
                "execution_id": report.execution_id, "order_id": order.order_id,
                "symbol": report.symbol, "side": report.side.value,
                "executed_quantity": report.executed_quantity,
                "execution_price": report.execution_price,
                "broker_order_id": report.broker_order_id,
            },
            metadata={"execution_id": report.execution_id,
                      "idempotency_key": report.idempotency_key},
        )

        # 2. position state through the Phase 2 state engine
        entity_id = f"{order.account_id or 'ACCOUNT'}:{report.symbol}"
        current = self._states.get_current_state(StateCategory.POSITION_STATE.value, entity_id)
        if current is not None and current.environment != report.environment:
            raise ContractError(
                "Position environment mismatch - cross-environment position update",
                location="projection.position_environment", rule_id="ENV-004",
            )
        signed = self._signed_quantity(report)
        new_quantity = self._new_quantity(current, signed, order.side)
        status = "OPEN" if new_quantity > 0 else "CLOSED"
        payload = {
            "quantity": str(abs(new_quantity)),
            "side": "LONG" if new_quantity > 0 else "SHORT" if new_quantity < 0 else "FLAT",
            "average_price": report.execution_price,
            "symbol": report.symbol,
            "account_id": order.account_id,
            "strategy_id": order.strategy_id,
            "last_execution_id": report.execution_id,
        }
        if self._already_projected(report):
            # replaying the same execution: idempotent no-op, state unchanged
            current_after = self._states.get_current_state(
                StateCategory.POSITION_STATE.value, entity_id)
            return ProjectionResult(current_after, None, event.event_id, True)
        application = self._engine.apply_event(
            entity_type=StateCategory.POSITION_STATE,
            entity_id=entity_id,
            current=current,
            new_status=status,
            event=event,
            payload=payload,
            reason=f"execution {report.execution_id}",
            actor="core.oms.projection",
            effective_time=report.broker_timestamp,
            observed_time=report.received_at,
            processed_time=moment,
        )
        self._states.save_state(application.state, application.transition)
        self._events.append(event)

        # 3. ledger effect (Phase 2 posting: idempotent, hash-chained)
        notional = report.quantity_decimal() * Decimal(report.execution_price)
        signed_notional = notional if order.side is OrderSide.BUY else -notional
        ledger_event = build_event(
            event_id=new_identifier("event_id"),
            event_type=EventType.LEDGER_POSTED,
            source="core.oms.projection",
            source_id=report.execution_id,
            environment=report.environment,
            correlation_id=order.correlation_id,
            causation_id=event.event_id,
            entity_id=order.order_id,
            event_time=report.broker_timestamp,
            received_time=report.received_at,
            payload={"execution_id": report.execution_id, "order_id": order.order_id},
            metadata={"execution_id": report.execution_id},
        )
        self._events.append(ledger_event)
        posted = self._posting.post(
            LedgerDraft(
                entry_type=LedgerType.EXECUTION,
                account_id=order.account_id or "ACCOUNT",
                amount=str(signed_notional),
                currency=report.currency,
                reason=f"execution {report.execution_id}",
                quantity=report.executed_quantity,
                symbol=report.symbol,
                order_id=order.order_id,
                execution_id=report.execution_id,
                strategy_id=order.strategy_id,
                semantic_ref=report.idempotency_key,
            ),
            event=ledger_event,
            entry_time=moment,
        )
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"), actor_type=ActorType.SYSTEM,
            actor_id="core.oms.projection", action="EXECUTION_PROJECTED",
            entity_type="execution", entity_id=report.execution_id,
            event_time=moment, before=None,
            after={"position": entity_id, "quantity": str(new_quantity),
                   "ledger_entry": posted.entry.ledger_entry_id,
                   "ledger_created": posted.created},
            reason="fill projected to position + ledger",
            source="core.oms.projection", environment=report.environment,
            correlation_id=order.correlation_id, causation_id=event.event_id,
        ))
        return ProjectionResult(application.state, posted.entry.ledger_entry_id,
                                event.event_id, not posted.created)

    def _already_projected(self, report: ExecutionReport) -> bool:
        """True when this exact execution (by execution_id) already produced a
        canonical projection event - deterministic duplicate guard."""
        for event in self._events.query_by_source("core.oms.projection"):
            if event.source_id == report.execution_id:
                return True
        return False

    @staticmethod
    def _signed_quantity(report: ExecutionReport) -> Decimal:
        quantity = report.quantity_decimal()
        return quantity if report.side is OrderSide.BUY else -quantity

    @staticmethod
    def _new_quantity(current, signed: Decimal, side: OrderSide) -> Decimal:
        if current is None:
            return signed
        existing = Decimal(str(current.payload.get("quantity", "0")))
        existing_side = current.payload.get("side", "FLAT")
        signed_existing = existing if existing_side != "SHORT" else -existing
        return signed_existing + signed
