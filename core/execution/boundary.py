"""Execution safety boundary (owned by core.execution).

`can_execute(order, risk_decision, execution_context)` is THE single
deterministic gate before any external submission (SECTION 36). It reuses
the Phase 3 RiskDecisionValidator, the Phase 0 permission model and the
Phase 0 environment contract - there is no second risk engine here."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.errors import ContractValidationError, RiskGateError
from architecture.contracts.time import ensure_utc
from core.execution.contracts import Order, OrderType, OrderStatus
from core.risk.contracts import RiskDecision, RiskResult
from core.risk.validator import RiskDecisionValidator
from platform.security.contracts import Permission, Role, build_role_permission_registry

CONTRACT_VERSION = "1.0.0"

PRICED_TYPES = frozenset({"LIMIT", "STOP", "STOP_LIMIT"})
STATES_REQUIRING_RISK = frozenset({
    OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.PARTIAL_FILL,
    OrderStatus.FILLED, OrderStatus.CLOSED, OrderStatus.SENT,
    OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED,
})


class ExecutionDecision(Enum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"
    UNKNOWN = "UNKNOWN"  # UNKNOWN = BLOCK (SECTION 36)


#: Market states that permit new exposure; anything else (incl. UNKNOWN) blocks.
EXECUTABLE_MARKET_STATES = frozenset({"OPEN"})


@dataclass(frozen=True)
class ExecutionContext:
    """Inputs the boundary needs beyond the order itself. Everything explicit;
    no hidden clocks or mutable globals."""

    market_state: str | None          # OPEN / CLOSED / HALTED / UNKNOWN
    safety_control: str | None        # Phase 3 risk-state/safety: NORMAL/PAUSE/CLOSE_ONLY/EMERGENCY
    actor_role: Role | None = None    # permission for LIVE submission etc.
    now: datetime | None = None
    expected_risk_context_hash: str | None = None

    def validate(self) -> None:
        if self.now is not None:
            ensure_utc(self.now, location="boundary.now")


@dataclass(frozen=True)
class BoundaryResult:
    decision: ExecutionDecision
    reasons: tuple[str, ...]

    @property
    def permitted(self) -> bool:
        return self.decision is ExecutionDecision.ALLOW


def _risk_decision_hash(decision: RiskDecision) -> str:
    import hashlib
    import json

    material = json.dumps(decision.to_dict(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_order(order: Order, *, risk_decision: RiskDecision | None = None,
                   expected_context_hash: str | None = None,
                   now: datetime | None = None) -> tuple[str, ...]:
    """THE canonical order validation (SECTION 6) - used by OMS and EMS alike;
    never duplicated. Returns the list of violation reasons (empty = valid)."""
    reasons: list[str] = []
    try:
        order.validate()
    except ContractValidationError as exc:
        reasons.append(f"schema:{exc.message}")
        return tuple(reasons)

    if order.order_type.value in PRICED_TYPES and order.price is None:
        reasons.append("price_required")
    if order.expires_at is not None and now is not None:
        moment = ensure_utc(now, location="boundary.now")
        if ensure_utc(order.expires_at, location="order.expires_at") <= moment:
            reasons.append("order_expired")
    if risk_decision is None:
        reasons.append("missing_risk_decision")
        return tuple(reasons)

    try:
        risk_decision.validate()
    except ContractValidationError as exc:
        reasons.append(f"risk_decision_invalid:{exc.message}")
        return tuple(reasons)

    if order.risk_decision_id != risk_decision.risk_decision_id:
        reasons.append("risk_decision_mismatch")
    if order.environment != risk_decision.environment:
        reasons.append("environment_mismatch")
    if risk_decision.is_expired(ensure_utc(now, location="boundary.now") if now else None):
        reasons.append("risk_decision_expired")
    if order.risk_decision_hash is not None and \
            order.risk_decision_hash != _risk_decision_hash(risk_decision):
        reasons.append("risk_decision_hash_mismatch")
    if order.risk_context_hash is not None and \
            order.risk_context_hash != risk_decision.risk_context_hash:
        reasons.append("risk_context_hash_mismatch")
    if expected_context_hash is not None and \
            risk_decision.risk_context_hash != expected_context_hash:
        reasons.append("stale_risk_context")
    if risk_decision.symbol is not None and order.symbol != risk_decision.symbol:
        reasons.append("subject_mismatch")
    if not risk_decision.decision.executable:
        reasons.append(f"risk_decision_{risk_decision.decision.value}")
    if risk_decision.decision is RiskResult.LIMITED:
        constraints = risk_decision.permission_constraints or {}
        max_quantity = constraints.get("max_quantity") or constraints.get("max_requested_quantity")
        if max_quantity is not None:
            from core.ledger.money import parse_decimal

            if order.quantity > parse_decimal(max_quantity, location="boundary.max_quantity"):
                reasons.append("limited_constraint_violated")
        elif not constraints:
            reasons.append("limited_without_constraints")
    return tuple(reasons)


def can_execute(order: Order, risk_decision: RiskDecision,
                execution_context: ExecutionContext) -> BoundaryResult:
    """Deterministic execution gate: ALLOW / REJECT / UNKNOWN(=BLOCK).

    Checks (SECTION 36): schema, environment, risk decision validity+expiry,
    hashes, subject/constraints, market state, safety controls, LIVE
    permission, order expiry. UNKNOWN is never SAFE."""
    execution_context.validate()
    reasons = validate_order(order, risk_decision=risk_decision,
                             expected_context_hash=execution_context.expected_risk_context_hash,
                             now=execution_context.now)
    if reasons:
        return BoundaryResult(ExecutionDecision.REJECT, reasons)

    unknowns: list[str] = []
    if execution_context.market_state is None or execution_context.market_state == "UNKNOWN":
        unknowns.append("market_state_unknown")
    if execution_context.safety_control is None or execution_context.safety_control == "UNKNOWN":
        unknowns.append("safety_control_unknown")
    if unknowns:
        return BoundaryResult(ExecutionDecision.UNKNOWN, tuple(unknowns))

    if execution_context.market_state not in EXECUTABLE_MARKET_STATES:
        return BoundaryResult(ExecutionDecision.REJECT, (f"market_{execution_context.market_state.lower()}",))

    control = execution_context.safety_control
    risk_increasing = order.order_type is not None and order.quantity > 0 and \
        order.status in (OrderStatus.CREATED, OrderStatus.VALATING if False else OrderStatus.VALIDATING,
                         OrderStatus.ACCEPTED, OrderStatus.ROUTING)
    if control == "EMERGENCY":
        return BoundaryResult(ExecutionDecision.REJECT, ("emergency_stop",))
    if control == "PAUSE" and risk_increasing:
        return BoundaryResult(ExecutionDecision.REJECT, ("global_pause_blocks_new_risk",))
    if control == "CLOSE_ONLY":
        # Only risk-REDUCING actions pass; a fresh order is by definition new exposure
        if risk_increasing:
            return BoundaryResult(ExecutionDecision.REJECT, ("close_only_blocks_new_exposure",))

    if order.environment == "LIVE":
        role = execution_context.actor_role
        if role is None:
            return BoundaryResult(ExecutionDecision.REJECT, ("live_requires_actor",))
        registry = build_role_permission_registry()
        if not registry.has_permission(role, Permission.LIVE_TRADE):
            return BoundaryResult(
                ExecutionDecision.REJECT, ("permission_denied:LIVE_TRADE",
                                           f"role:{role.value}"))

    return BoundaryResult(ExecutionDecision.ALLOW, ())
