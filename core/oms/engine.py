"""OMS - Order Management System (owned by core.oms).

Owns the order lifecycle: receive canonical order, validate (single
OrderValidator from core.execution), persist immutable versions, idempotency,
cancel/replace with risk revalidation, execution-report reconciliation,
recovery. OMS never calls MT5, never invents fills, never overrides risk
(SECTION 7/16)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Mapping

from architecture.contracts.errors import ContractError, RiskGateError, StorageError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.state_machine import StateMachineRegistry, build_state_machine_registry
from architecture.contracts.time import ensure_utc
from core.execution.boundary import (
    BoundaryResult,
    ExecutionContext,
    ExecutionDecision,
    can_execute,
    validate_order,
)
from core.execution.contracts import Order, OrderSide, OrderStatus, OrderType
from core.execution.store import OrderStore
from core.oms.contracts import ExecutionReport, ExecutionStatus, ExecutionType
from core.oms.stores import ExecutionReportStore
from core.risk.contracts import RiskDecision
from core.risk.validator import RiskDecisionValidator
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"
ORDER_MACHINE = "order_state"

TERMINAL_STATES = frozenset({
    OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED,
    OrderStatus.EXPIRED, OrderStatus.REPLACED, OrderStatus.CLOSED, OrderStatus.FAILED,
})


class OMSError(ContractError):
    rule_id = "OMS-001"


def order_idempotency_key(*, intent_id: str, strategy_id: str, symbol: str,
                          side: str, order_type: str, quantity: str,
                          order_version: int) -> str:
    """Deterministic semantic identity (SECTION 8): same semantic request ->
    same key -> no duplicate external submission."""
    material = json.dumps({
        "intent_id": intent_id, "strategy_id": strategy_id, "symbol": symbol,
        "side": side, "order_type": order_type, "quantity": quantity,
        "order_version": order_version,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AcceptanceResult:
    order: Order
    accepted: bool
    reasons: tuple[str, ...]
    duplicate_of: str | None = None


class OrderManagementSystem:
    def __init__(self, *, orders: OrderStore, reports: ExecutionReportStore,
                 audit: AuditRepository | None = None) -> None:
        self._orders = orders
        self._reports = reports
        self._audit = audit
        self._machines = build_state_machine_registry()

    # ------------------------------------------------------------------ #
    # Acceptance                                                          #
    # ------------------------------------------------------------------ #
    def submit(self, order: Order, *, risk_decision: RiskDecision,
               execution_context: ExecutionContext) -> AcceptanceResult:
        """Validate + accept. Duplicate semantic requests return the EXISTING
        order (no duplicate canonical order, no duplicate submission)."""
        moment = ensure_utc(execution_context.now, location="oms.now") if execution_context.now else None

        key = order.idempotency_key or order_idempotency_key(
            intent_id=order.intent_id or "", strategy_id=order.strategy_id,
            symbol=order.symbol, side=order.side.value,
            order_type=order.order_type.value, quantity=str(order.quantity),
            order_version=order.order_version,
        )
        existing = self._orders.find_by_idempotency_key(key)
        if existing is not None:
            return AcceptanceResult(existing, accepted=False,
                                    reasons=("duplicate_semantic_request",),
                                    duplicate_of=existing.order_id)

        reasons = validate_order(order, risk_decision=risk_decision,
                                 expected_context_hash=execution_context.expected_risk_context_hash,
                                 now=moment)
        if reasons:
            rejected = replace(order, status=OrderStatus.REJECTED)
            self._audit_order(rejected, "ORDER_REJECTED", before=None,
                              after={"reasons": list(reasons)}, reason=";".join(reasons),
                              at=moment or order.created_at)
            return AcceptanceResult(rejected, accepted=False, reasons=reasons)

        accepted = replace(order, status=OrderStatus.ACCEPTED, idempotency_key=key)
        self._orders.save(accepted)
        self._audit_order(accepted, "ORDER_ACCEPTED", before={"status": order.status.value},
                          after={"status": "ACCEPTED", "idempotency_key": key},
                          reason="validated + risk-authorized", at=moment or order.created_at)
        return AcceptanceResult(accepted, accepted=True, reasons=())

    # ------------------------------------------------------------------ #
    # Lifecycle transitions (append-only versions)                        #
    # ------------------------------------------------------------------ #
    def transition(self, order: Order, target: OrderStatus, *, reason: str,
                   actor: str, at: datetime | None = None) -> Order:
        latest = self._orders.get_by_id(order.order_id)
        self._machines.apply(ORDER_MACHINE, latest.status.value, target.value,
                             reason=reason, actor=actor,
                             timestamp=ensure_utc(at, location="oms.at") if at else None)
        updated = replace(latest, status=target,
                          order_version=latest.order_version + 1,
                          idempotency_key=None)  # uniqueness belongs to v1 only
        self._orders.save(updated)
        self._audit_order(updated, f"ORDER_{target.value}",
                          before={"status": latest.status.value, "version": latest.order_version},
                          after={"status": target.value, "version": updated.order_version},
                          reason=reason, at=at or latest.updated_at)
        return updated

    # ------------------------------------------------------------------ #
    # Execution reports (fills)                                           #
    # ------------------------------------------------------------------ #
    def ingest_execution_report(self, report: ExecutionReport) -> tuple[Order, bool]:
        """Idempotent ingestion. Returns (updated order, created?) - a
        duplicate report creates nothing (INV-009/013)."""
        report.validate()
        created = self._reports.append(report)  # False on duplicate key
        if not created:
            order = self._orders.get_by_id(report.order_id)
            return order, False
        order = self._orders.get_by_id(report.order_id)
        if report.status is ExecutionStatus.REJECTED:
            updated = self.transition(
                order, OrderStatus.REJECTED,
                reason=f"broker reject: {report.normalized_error.value if report.normalized_error else 'UNKNOWN'}",
                actor="core.oms", at=report.received_at)
            return updated, True
        if report.execution_type in (ExecutionType.FILL, ExecutionType.PARTIAL_FILL) \
                and report.status is ExecutionStatus.CONFIRMED:
            filled = self.filled_quantity(report.order_id)  # append already included it
            requested = order.quantity
            if filled > requested:
                # Overfill is corruption: state records it, never clips (SECTION 13/14)
                updated = self.transition(
                    order, OrderStatus.UNKNOWN,
                    reason=f"OVERFILL: cumulative {filled} > requested {requested}",
                    actor="core.oms", at=report.received_at)
                return updated, True
            target = OrderStatus.FILLED if filled == requested else OrderStatus.PARTIALLY_FILLED
            if order.status is target and target is OrderStatus.PARTIALLY_FILLED:
                updated = self.transition(order, OrderStatus.PARTIALLY_FILLED,
                                          reason=f"additional fill {report.executed_quantity}",
                                          actor="core.oms", at=report.received_at)
            else:
                updated = self.transition(order, target,
                                          reason=f"fill {report.executed_quantity} (cumulative {filled})",
                                          actor="core.oms", at=report.received_at)
            return updated, True
        if report.execution_type is ExecutionType.CANCEL and report.status is ExecutionStatus.CONFIRMED:
            updated = self.transition(order, OrderStatus.CANCELLED,
                                      reason="broker cancellation confirmed",
                                      actor="core.oms", at=report.received_at)
            return updated, True
        return order, True

    def filled_quantity(self, order_id: str) -> Decimal:
        total = Decimal("0")
        for report in self._reports.iter_by_order(order_id):
            if report.execution_type in (ExecutionType.FILL, ExecutionType.PARTIAL_FILL) \
                    and report.status is ExecutionStatus.CONFIRMED:
                total += report.quantity_decimal()
        return total

    # ------------------------------------------------------------------ #
    # Cancel / replace                                                    #
    # ------------------------------------------------------------------ #
    def request_cancel(self, order: Order, *, reason: str, actor: str,
                       at: datetime) -> Order:
        return self.transition(order, OrderStatus.CANCEL_REQUESTED,
                               reason=reason, actor=actor, at=at)

    def confirm_cancel(self, order: Order, *, evidence: ExecutionReport,
                       at: datetime) -> Order:
        """CANCELLED requires broker evidence (SECTION 10 / INV-038)."""
        if evidence.execution_type is not ExecutionType.CANCEL or \
                evidence.status is not ExecutionStatus.CONFIRMED:
            raise OMSError(
                "CANCELLED requires a confirmed broker CANCEL report (no evidence -> UNKNOWN)",
                location="oms.confirm_cancel",
            )
        return self.transition(order, OrderStatus.CANCELLED,
                               reason="broker cancellation evidence received",
                               actor="core.oms", at=at)

    def build_replacement(self, order: Order, *, quantity, price,
                          risk_decision: RiskDecision,
                          execution_context: ExecutionContext,
                          reason: str) -> Order:
        """Immutable replacement version v(n+1). Material changes REQUIRE a
        fresh risk decision covering the new request (SECTION 11 / INV-021)."""
        from core.ledger.money import parse_decimal

        new_quantity = parse_decimal(quantity, location="oms.replace.quantity")
        if new_quantity == order.quantity and (price is None or
                                               (order.price is not None and parse_decimal(price, location="oms.replace.price") == order.price)):
            raise OMSError(
                "Replacement is materially identical - no new version needed",
                location="oms.build_replacement",
            )
        boundary = can_execute(order, risk_decision, execution_context)
        if boundary.decision is not ExecutionDecision.ALLOW:
            raise RiskGateError(
                f"Replacement requires risk revalidation: {boundary.reasons}",
                location="oms.build_replacement", rule_id="EXEC-001",
                details={"reasons": list(boundary.reasons)},
            )
        # risk decision must cover the NEW quantity under LIMITED constraints
        if risk_decision.decision.value == "LIMITED":
            constraints = risk_decision.permission_constraints or {}
            max_q = constraints.get("max_quantity")
            if max_q is not None and new_quantity > parse_decimal(max_q, location="oms.replace.max"):
                raise RiskGateError(
                    "Replacement quantity exceeds the LIMITED risk constraint",
                    location="oms.build_replacement", rule_id="EXEC-001",
                )
        requested = self.transition(order, OrderStatus.REPLACEMENT_REQUESTED,
                                    reason=reason, actor="core.oms")
        replacement = Order(
            order_id=new_identifier("order_id"),
            client_order_id=f"{order.client_order_id}-r{order.order_version + 1}",
            strategy_id=order.strategy_id, symbol=order.symbol, side=order.side,
            order_type=order.order_type, quantity=new_quantity,
            price=parse_decimal(price, location="oms.replace.price") if price is not None else order.price,
            time_in_force=order.time_in_force, environment=order.environment,
            policy_id=order.policy_id, status=OrderStatus.CREATED,
            source=order.source, created_at=order.created_at,
            updated_at=order.updated_at, correlation_id=order.correlation_id,
            risk_decision_id=risk_decision.risk_decision_id,
            causation_id=order.order_id, account_id=order.account_id,
            intent_id=order.intent_id, portfolio_decision_id=order.portfolio_decision_id,
            risk_decision_hash=order.risk_decision_hash,
            policy_version=order.policy_version,
            risk_context_hash=order.risk_context_hash,
            idempotency_key=order_idempotency_key(
                intent_id=order.intent_id or "", strategy_id=order.strategy_id,
                symbol=order.symbol, side=order.side.value,
                order_type=order.order_type.value, quantity=str(new_quantity),
                order_version=1,
            ),
            order_version=1, replaces_order_id=order.order_id,
        )
        replacement.validate()
        self._orders.save(replacement)
        self.transition(requested, OrderStatus.REPLACED,
                        reason=f"replaced by {replacement.order_id}", actor="core.oms")
        return replacement

    # ------------------------------------------------------------------ #
    # Recovery                                                            #
    # ------------------------------------------------------------------ #
    def recover(self) -> list[Order]:
        """Restart recovery: return orders in non-terminal states (drivers
        reconcile via poll; UNKNOWN never silently becomes FAILED)."""
        return [order for order in self._orders.iter_active()]

    def iter_active_orders(self) -> Iterable[Order]:
        return self._orders.iter_active()

    def _audit_order(self, order: Order, action: str, *, before, after, reason: str,
                     at: datetime) -> None:
        if self._audit is None:
            return
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"), actor_type=ActorType.SYSTEM,
            actor_id="core.oms", action=action, entity_type="order",
            entity_id=order.order_id, event_time=ensure_utc(at, location="oms.audit"),
            before=before, after=after, reason=reason, source="core.oms",
            environment=order.environment, correlation_id=order.correlation_id,
        ))


# forward-declared typing alias to avoid circular import at module scope
AuditRegistry = platform.audit.repository.AuditRepository if False else None  # noqa
