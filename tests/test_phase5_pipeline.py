"""Phase 5 pipeline tests: execution -> position state + ledger (Phase 2)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from core.execution.contracts import OrderSide, OrderStatus, OrderType, TimeInForce
from core.ledger.contracts import LedgerType
from core.ledger.posting import LedgerPostingService
from core.oms.contracts import (
    ExecutionReport,
    ExecutionStatus,
    ExecutionType,
    execution_idempotency_key,
)
from core.oms.projection import ExecutionProjector
from core.state.contracts import StateCategory
from platform.database.sqlite_stores import StorageSet
from tests.factories import make_risk_decision
from tests.phase1_factories import at
from core.risk.contracts import RiskResult
from tests.test_phase5_boundary_oms import aligned, make_order


@pytest.fixture()
def env(tmp_path):
    storage = StorageSet(tmp_path / "p5-proj.db")
    posting = LedgerPostingService(storage.ledger, storage.audit)
    projector = ExecutionProjector(states=storage.states, posting=posting,
                                   events=storage.events, audit=storage.audit)
    yield {"storage": storage, "posting": posting, "projector": projector}
    storage.close()


def _order(env, side=OrderSide.BUY, quantity="1.00"):
    risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                              decision=RiskResult.ALLOW, decision_time=at(11, 59),
                              expires_at=at(12, 30))
    order = aligned(make_order(side=side, quantity=Decimal(quantity)), risk)
    return order


def _report(order, quantity="1.00", price="2650.00", ref=None):
    return ExecutionReport(
        execution_id=new_identifier("execution_id"), order_id=order.order_id,
        environment=order.environment, execution_type=ExecutionType.FILL,
        status=ExecutionStatus.CONFIRMED, symbol=order.symbol, side=order.side,
        executed_quantity=quantity, execution_price=price, currency="USD",
        broker_order_id="2001",
        broker_timestamp=at(12, 1), received_at=at(12, 1),
        provenance={"source": "simulation", "adapter": "simulation-executor",
                    "raw": {"synthetic": True}},
        raw_reference=ref or f"SIM-{new_identifier('correlation_id')[:12]}",
        idempotency_key=execution_idempotency_key(
            order_id=order.order_id, broker_order_id="2001",
            broker_execution_reference=ref or new_identifier("correlation_id")[:12]),
    )


class TestPositionProjection:
    def test_fill_opens_position(self, env):
        order = _order(env)
        result = env["projector"].project(_report(order), order, at=at(12, 2))
        assert result.position_state.status == "OPEN"
        assert result.position_state.payload["quantity"] == "1.00"
        assert result.position_state.entity_type is StateCategory.POSITION_STATE
        assert not result.duplicate

    def test_reduce_closes_position(self, env):
        buy = _order(env, OrderSide.BUY, "1.00")
        env["projector"].project(_report(buy), buy, at=at(12, 2))
        sell = _order(env, OrderSide.SELL, "1.00")
        result = env["projector"].project(_report(sell), sell, at=at(12, 3))
        # SELL 1.00 against LONG 1.00 => flat => CLOSED
        assert Decimal(result.position_state.payload["quantity"]) == Decimal("0")

    def test_position_chain_traces_to_execution(self, env):
        order = _order(env)
        result = env["projector"].project(_report(order), order, at=at(12, 2))
        event = env["storage"].events.get_by_id(result.position_state.source_event_id)
        assert event.payload["execution_id"]
        assert event.entity_id == order.order_id  # order linkage via entity
        # ledger event causes from the execution event (event-only chain)
        from core.events.contracts import EventType as ET
        ledger_events = list(env["storage"].events.query_by_type(ET.LEDGER_POSTED.value))
        assert any(e.causation_id == event.event_id for e in ledger_events)

    def test_duplicate_execution_no_duplicate_effect(self, env):
        order = _order(env)
        report = _report(order, ref="SAME-REF")
        first = env["projector"].project(report, order, at=at(12, 2))
        assert not first.duplicate
        second = env["projector"].project(report, order, at=at(12, 3))
        assert second.duplicate
        assert env["storage"].ledger.count() == 1  # no duplicate ledger effect

    def test_unconfirmed_never_projects(self, env):
        from dataclasses import replace

        order = _order(env)
        report = replace(_report(order), status=ExecutionStatus.UNKNOWN)
        with pytest.raises(ContractError):
            env["projector"].project(report, order, at=at(12, 2))

    def test_environment_mismatch_rejected(self, env):
        from dataclasses import replace

        order = _order(env)
        report = replace(_report(order), environment="LIVE")
        with pytest.raises(ContractError) as excinfo:
            env["projector"].project(report, order, at=at(12, 2))
        assert excinfo.value.rule_id == "ENV-004"


class TestLedgerIntegration:
    def test_fill_posts_execution_entry(self, env):
        order = _order(env)
        result = env["projector"].project(_report(order), order, at=at(12, 2))
        assert result.ledger_entry_id is not None
        entry = env["storage"].ledger.get_by_id(result.ledger_entry_id)
        assert entry.entry_type is LedgerType.EXECUTION
        assert entry.symbol == "XAUUSD"
        assert entry.order_id == order.order_id
        assert env["storage"].ledger.verify_account_chain("ACCOUNT") == []

    def test_ledger_uses_phase2_idempotency(self, env):
        order = _order(env)
        report = _report(order, ref="IDEM-1")
        env["projector"].project(report, order, at=at(12, 2))
        # re-projecting the same report hits the ledger idempotency key
        result = env["projector"].project(report, order, at=at(12, 3))
        assert result.duplicate
        assert env["storage"].ledger.count() == 1

    def test_partial_fills_post_per_fill(self, env):
        order = _order(env, quantity="1.00")
        for ref, quantity in (("F1", "0.30"), ("F2", "0.30"), ("F3", "0.40")):
            report = _report(order, quantity=quantity, ref=ref)
            env["projector"].project(report, order, at=at(12, 2))
        assert env["storage"].ledger.count() == 3
