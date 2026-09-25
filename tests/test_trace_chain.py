"""End-to-end causal trace test (SECTION 24).

Proves the full chain NEWS -> EVENT -> AI -> STRATEGY(decision) -> POLICY ->
RISK -> ORDER -> POSITION -> LEDGER stays linked via correlation_id and
causation_id, and that audit records join the same chain (RULE 017/018).
"""
from __future__ import annotations

from decimal import Decimal

from architecture.contracts.causality import validate_causal_chain
from architecture.contracts.identifiers import new_identifier
from core.events.contracts import EventType
from core.execution.contracts import OrderStatus
from core.ledger.contracts import LedgerType
from core.risk.contracts import RiskResult
from platform.audit.contracts import ActorType
from tests.factories import (
    at,
    make_audit_record,
    make_decision,
    make_event,
    make_ledger_entry,
    make_order,
    make_position,
    make_risk_decision,
)


def test_full_causal_chain_is_traceable():
    correlation = new_identifier("correlation_id")

    news = make_event(
        event_type=EventType.NEWS_RECEIVED,
        correlation_id=correlation,
        causation_id=None,
        payload={"headline": "CPI surprise"},
    )
    news.validate()

    analysis = make_event(
        event_type=EventType.AI_ANALYSIS_COMPLETED,
        correlation_id=correlation,
        causation_id=news.event_id,
        payload={"regime": "VOLATILE"},
    )
    analysis.validate()

    signal = make_event(
        event_type=EventType.STRATEGY_SIGNAL_CREATED,
        correlation_id=correlation,
        causation_id=analysis.event_id,
        payload={"strategy": "grid-alpha", "action": "BUY"},
    )
    signal.validate()

    decision = make_decision(correlation_id=correlation, causation_id=signal.event_id)
    decision.validate()

    risk = make_risk_decision(
        decision=RiskResult.ALLOW,
        correlation_id=correlation,
        causation_id=decision.decision_id,
    )
    risk.validate()

    order = make_order(
        status=OrderStatus.RISK_CHECK,
        causation_id=decision.decision_id,
        correlation_id=correlation,
    )
    submitted, _ = order.transition_status(
        OrderStatus.SUBMITTED,
        reason="risk allowed",
        actor="core.execution",
        risk_decision=risk,
        at=at(12, 4),
    )

    position = make_position(
        causation_id=submitted.order_id, correlation_id=correlation
    )
    position.validate()

    ledger = make_ledger_entry(
        entry_type=LedgerType.EXECUTION,
        order_id=submitted.order_id,
        position_id=position.position_id,
        correlation_id=correlation,
        causation_id=submitted.order_id,
        amount=Decimal("-2.40"),
    )
    ledger.validate()

    audit = make_audit_record(
        correlation_id=correlation,
        causation_id=submitted.order_id,
        entity_id=submitted.order_id,
        actor_type=ActorType.SYSTEM,
    )
    audit.validate()

    chain_nodes = [news, analysis, signal, decision, risk, submitted, position, ledger]
    validate_causal_chain(chain_nodes)

    assert submitted.risk_decision_id == risk.risk_decision_id
    assert ledger.correlation_id == news.correlation_id
    assert audit.correlation_id == correlation
    assert position.environment == submitted.environment == risk.environment


def test_chain_with_broken_causation_is_rejected():
    correlation = new_identifier("correlation_id")
    root = make_event(correlation_id=correlation)
    orphan = make_event(
        correlation_id=correlation,
        causation_id="evt_" + "9" * 32,
    )
    try:
        validate_causal_chain([root, orphan])
        raised = False
    except Exception:
        raised = True
    assert raised
