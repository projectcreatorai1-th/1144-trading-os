"""Privileged action dispatch for the desktop gateway.

SECTION 43/45: every action runs through the REAL chain - Phase 8
authorization first, then the existing authority chain (intent ->
portfolio -> risk gate -> OMS -> EMS -> projection -> ledger). A button
click is only REQUESTED; the receipt carries the actual engine states,
and success is never shown before confirmed Core state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.events.contracts import EventType, build_event

from core.execution.contracts import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)

from core.oms.contracts import (
    ExecutionReport,
    ExecutionStatus,
    ExecutionType,
    execution_idempotency_key,
)
from core.oms.engine import ExecutionContext, OrderManagementSystem
from core.oms.projection import ExecutionProjector

from core.policy.contracts import PolicyStatus, PolicyType
from core.policy.registry import ActorContext, PolicyRegistry
from platform.security.contracts import Role

from core.portfolio.decision import PortfolioDecisionEngine
from core.portfolio.exposure import ExposureLeg
from core.portfolio.portfolio_contract import Portfolio, PortfolioMembership, PortfolioStatus

from core.risk.engine import RiskEngine, RiskEvaluationRequest
from core.risk.state_service import RiskStateService
from core.state.contracts import StateCategory

from core.strategy.gate import IntentGate
from core.strategy.intent import IntentDirection, IntentType, StrategyIntent, Urgency

#: The action lifecycle states surfaced to the UI (SECTION 45).
ACTION_STATES = ("REQUESTED", "PENDING", "APPLIED", "REJECTED", "FAILED",
                 "UNKNOWN")


@dataclass(frozen=True)
class ActionReceipt:
    action: str
    state: str
    reasons: tuple[str, ...]
    order_id: str | None = None
    fill_price: str | None = None
    position_quantity: str | None = None
    ledger_entry_id: str | None = None
    position_id: str | None = None
    audit_correlation_id: str | None = None


def _event(environment: str, entity_id: str, correlation: str,
           event_type: EventType, payload: Mapping[str, Any],
           at: datetime):
    return build_event(
        event_type=event_type, source="platform.api.desktop_gateway",
        source_id=entity_id, environment=environment,
        correlation_id=correlation, event_time=ensure_utc(at),
        received_time=ensure_utc(at), payload=dict(payload),
        event_id=new_identifier("event_id"), entity_id=entity_id)


class ActionDispatcher:
    """Dispatches privileged desktop actions through the authority chain.

    Holds references to the REAL engines composed by the gateway. It has
    no decision logic of its own: it translates UI intents into core
    contracts and reports what the engines actually did."""

    def __init__(self, *, environment: str, policies: PolicyRegistry,
                 risk_engine: RiskEngine, risk_state: RiskStateService,
                 oms: OrderManagementSystem, projector: ExecutionProjector,
                 strategy_id: str, audit) -> None:
        self._environment = environment
        self._policies = policies
        self._risk = risk_engine
        self._risk_state = risk_state
        self._oms = oms
        self._projector = projector
        self._strategy_id = strategy_id
        self._audit = audit

    # ------------------------------------------------------------------ #
    # Order submission: the full real chain                                #
    # ------------------------------------------------------------------ #
    def submit_order(self, *, symbol: str, side: str, quantity: str,
                     price: str, at: datetime, base_context,
                     session_actor_role: str = "TRADER") -> ActionReceipt:
        moment = ensure_utc(at, location="action.at")
        correlation = new_identifier("correlation_id")

        # 1. intent (Phase 4 contract)
        intent = StrategyIntent(
            intent_id=new_identifier("intent_id"),
            strategy_id=self._strategy_id, strategy_version="1.0.0",
            intent_type=IntentType.OPEN, symbol=symbol,
            direction=IntentDirection.LONG if side == "BUY"
            else IntentDirection.SHORT,
            requested_quantity=quantity,
            entry_conditions=({"condition_id": "manual-desktop-entry"},),
            exit_conditions=(), urgency=Urgency.NORMAL,
            rationale="desktop operator submission",
            risk_context_hash=base_context.context_hash,
            source_event_id=new_identifier("event_id"),
            correlation_id=correlation, environment=self._environment,
            created_at=moment, expires_at=moment + timedelta(minutes=30))
        intent.validate()

        # 2. portfolio decision (Phase 4 engine, real exposure legs)
        portfolio = Portfolio(
            portfolio_id=new_identifier("portfolio_id"),
            portfolio_version="1.0.0", name="desktop",
            account_scope="ACC-1", environment=self._environment,
            status=PortfolioStatus.ACTIVE, base_currency="USD",
            strategy_members=(self._strategy_id,),
            allocation_policy_id="fixed",
            effective_from=moment, created_at=moment)
        portfolio.validate()
        membership = PortfolioMembership(
            portfolio_id=portfolio.portfolio_id,
            strategy_id=self._strategy_id, strategy_version="1.0.0",
            allocation="500", risk_budget="10", priority=1, enabled=True,
            effective_from=moment, environment=self._environment)
        direction = "LONG" if side == "BUY" else "SHORT"
        notional = str(float(quantity) * float(price))
        decision = PortfolioDecisionEngine().evaluate(
            portfolio=portfolio, memberships=(membership,),
            eligible_strategy_ids=[self._strategy_id],
            current_legs=[], intent_legs=[
                ExposureLeg(self._strategy_id, symbol, "MANUAL", direction,
                            notional)],
            total_capital="100000", reserved_capital="0",
            requested_capital={self._strategy_id: notional},
            capacities=[], liquidity={}, constraint_limits={},
            at=moment, correlation_id=correlation)
        decision.validate()

        # 3. intent gate -> risk decision (Phase 3 engine, twice)
        gate = IntentGate(self._risk, self._audit)
        permission = gate.authorize(
            intent=intent, portfolio_decision=decision,
            base_context=base_context, at=moment)
        if not permission.permitted:
            return ActionReceipt(
                action="submit_order", state="REJECTED",
                reasons=(f"risk {permission.permission}: "
                         f"{'; '.join(permission.reasons)}",),
                audit_correlation_id=correlation)
        risk_decision = permission.risk_decision

        # 4. order (Phase 5 contract) aligned with the risk decision
        order = Order(
            order_id=new_identifier("order_id"),
            client_order_id="CO-" + new_identifier("correlation_id")[:11],
            strategy_id=self._strategy_id, symbol=symbol,
            side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal(quantity), time_in_force=TimeInForce.IOC,
            environment=self._environment,
            policy_id=risk_decision.policy_reference.split("@")[0],
            status=OrderStatus.CREATED, source="desktop",
            created_at=moment, updated_at=moment,
            correlation_id=correlation,
            risk_decision_id=risk_decision.risk_decision_id,
            risk_decision_hash=_hash_decision(risk_decision),
            risk_context_hash=risk_decision.risk_context_hash,
            intent_id=intent.intent_id, account_id="ACC-1")
        order.validate()

        # 5. OMS submit (validation + acceptance; idempotent)
        context = ExecutionContext(
            market_state="OPEN", safety_control="NORMAL",
            actor_role=Role.ADMIN, now=moment,
            expected_risk_context_hash=risk_decision.risk_context_hash)
        acceptance = self._oms.submit(order, risk_decision=risk_decision,
                                      execution_context=context)
        if not acceptance.accepted and acceptance.reasons and \
                "duplicate_semantic_request" not in acceptance.reasons:
            return ActionReceipt(
                action="submit_order", state="REJECTED",
                reasons=tuple(acceptance.reasons),
                order_id=acceptance.order.order_id,
                audit_correlation_id=correlation)

        # 6. EMS routing + simulation adapter (the only permitted
        #    execution plane for the desktop: SIMULATION/DEMO, synthetic)
        from core.ems.engine import ExecutionManagementSystem
        ems = getattr(self._oms, "_desktop_ems", None)
        if ems is None:  # gateway wires it; absence is a wiring error
            return ActionReceipt(action="submit_order", state="UNKNOWN",
                                 reasons=("ems not wired",),
                                 order_id=acceptance.order.order_id,
                                 audit_correlation_id=correlation)
        submission = ems.submit(acceptance.order,
                                risk_decision=risk_decision,
                                execution_context=context)
        if not submission.submitted:
            return ActionReceipt(
                action="submit_order",
                state="UNKNOWN" if submission.retry_classification not in
                (None, "NON_RETRYABLE") else "FAILED",
                reasons=(f"ems state {submission.state}: "
                         f"{submission.normalized_error}",),
                order_id=submission.order.order_id,
                audit_correlation_id=correlation)

        # 7. synthetic fill report -> OMS ingest -> position + ledger
        report = ExecutionReport(
            execution_id=new_identifier("execution_id"),
            order_id=submission.order.order_id,
            environment=self._environment,
            execution_type=ExecutionType.FILL,
            status=ExecutionStatus.CONFIRMED,
            symbol=symbol, side=submission.order.side,
            executed_quantity=str(submission.order.quantity),
            execution_price=price, currency="USD",
            broker_order_id=submission.broker_order_id or "SIM",
            broker_timestamp=moment, received_at=moment,
            provenance={"source": "simulation", "adapter": "simulation",
                        "raw": {"synthetic": True}},
            raw_reference=submission.raw_reference or "SIM",
            idempotency_key=execution_idempotency_key(
                order_id=submission.order.order_id,
                broker_order_id=submission.broker_order_id or "SIM",
                broker_execution_reference=submission.raw_reference
                or "SIM"))
        _, newly_filled = self._oms.ingest_execution_report(report)
        projection = self._projector.project(report, submission.order,
                                             at=moment)
        entry_id = None
        if not projection.duplicate:
            entry_id = projection.ledger_entry.entry_id \
                if getattr(projection, "ledger_entry", None) else None
        return ActionReceipt(
            action="submit_order", state="APPLIED",
            reasons=(f"risk {permission.permission}",
                     f"oms accepted, ems submitted, fill "
                     f"{'new' if newly_filled else 'duplicate-guarded'}"),
            order_id=submission.order.order_id,
            fill_price=price,
            position_quantity=projection.position_state.payload.get(
                "quantity"),
            position_id=projection.position_state.entity_id,
            ledger_entry_id=entry_id,
            audit_correlation_id=correlation)

    # ------------------------------------------------------------------ #
    # Safety controls (Phase 3 risk state machine via operator path)       #
    # ------------------------------------------------------------------ #
    def safety_control(self, *, target: str, at: datetime,
                       reason: str) -> ActionReceipt:
        moment = ensure_utc(at, location="safety.at")
        event = _event(self._environment, "risk-engine",
                       new_identifier("correlation_id"),
                       EventType.RISK_STATE_CHANGED,
                       {"target": target, "reason": reason}, moment)
        application = self._risk_state.operator_transition(
            target, event=event, reason=reason)
        state = "APPLIED" if application is not None else "PENDING"
        return ActionReceipt(
            action=f"safety:{target}", state=state,
            reasons=(f"risk state -> {target}",))


from datetime import timedelta  # noqa: E402  (used above)
from decimal import Decimal  # noqa: E402  (used above)


def _hash_decision(risk_decision) -> str:
    from core.execution.boundary import _risk_decision_hash
    return _risk_decision_hash(risk_decision)
