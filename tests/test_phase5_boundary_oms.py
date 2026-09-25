"""Phase 5 tests: execution boundary (can_execute), OMS lifecycle/idempotency/
cancel/replace, fill accumulation. E2E and recovery live in dedicated files."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractError, RiskGateError, StorageError
from architecture.contracts.identifiers import new_identifier
from core.execution.boundary import (
    ExecutionDecision,
    ExecutionContext,
    can_execute,
    validate_order,
)
from core.execution.contracts import Order, OrderSide, OrderStatus, OrderType, TimeInForce
from core.oms.contracts import (
    ExecutionReport,
    ExecutionStatus,
    ExecutionType,
    execution_idempotency_key,
)
from core.oms.engine import (
    OrderManagementSystem,
    order_idempotency_key,
)
from core.risk.contracts import RiskResult
from core.execution.boundary import _risk_decision_hash
from platform.database.sqlite_stores import StorageSet
from platform.security.contracts import Role
from tests.factories import make_risk_decision
from tests.phase1_factories import at
from tests.test_phase3_risk_engine import full_context


@pytest.fixture()
def env(tmp_path):
    storage = StorageSet(tmp_path / "p5.db")
    oms = OrderManagementSystem(orders=storage.orders,
                                reports=storage.execution_reports,
                                audit=storage.audit)
    yield {"storage": storage, "oms": oms}
    storage.close()


def make_order(**overrides):
    defaults = dict(
        order_id=new_identifier("order_id"),
        client_order_id="CO-0001",
        strategy_id=new_identifier("strategy_id"),
        symbol="XAUUSD", side=OrderSide.BUY, order_type=OrderType.MARKET,
        quantity=Decimal("1.00"), time_in_force=TimeInForce.GTC,
        environment="SIMULATION", policy_id=new_identifier("policy_id"),
        status=OrderStatus.CREATED, source="core.oms.test",
        created_at=at(12, 0), updated_at=at(12, 0),
        correlation_id=new_identifier("correlation_id"),
        risk_decision_id=None, intent_id=new_identifier("intent_id"),
        account_id="ACC-1",
    )
    defaults.update(overrides)
    return Order(**defaults)


def make_risk(order, decision=RiskResult.ALLOW, **overrides):
    defaults = dict(
        environment=order.environment, scope=order.symbol,
        decision=decision, correlation_id=order.correlation_id,
        decision_time=at(11, 59), expires_at=at(12, 30),
    )
    defaults.update(overrides)
    risk = make_risk_decision(**defaults)
    return risk


def aligned(order, risk):
    """Attach hashes so order<->risk chain is consistent."""
    return replace(order, risk_decision_id=risk.risk_decision_id,
                   risk_decision_hash=_risk_decision_hash(risk),
                   risk_context_hash=risk.risk_context_hash)


def ctx(**overrides):
    defaults = dict(market_state="OPEN", safety_control="NORMAL",
                    actor_role=Role.ADMIN, now=at(12, 1))
    defaults.update(overrides)
    return ExecutionContext(**defaults)


class TestBoundary:
    def test_valid_order_allows(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        result = can_execute(order, risk, ctx())
        assert result.permitted, result.reasons
        assert result.decision is ExecutionDecision.ALLOW

    def test_missing_risk_decision_rejects(self):
        order = make_order()
        reasons = validate_order(order)
        assert "missing_risk_decision" in reasons

    def test_expired_risk_rejects(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 0), expires_at=at(11, 30))
        order = aligned(make_order(), risk)
        result = can_execute(order, risk, ctx(now=at(12, 1)))
        assert result.decision is ExecutionDecision.REJECT
        assert "risk_decision_expired" in result.reasons

    def test_environment_mismatch_rejects(self):
        risk = make_risk_decision(environment="LIVE", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(environment="SIMULATION"), risk)
        result = can_execute(order, risk, ctx())
        assert "environment_mismatch" in result.reasons

    def test_unknown_market_state_blocks(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        result = can_execute(order, risk, ctx(market_state="UNKNOWN"))
        assert result.decision is ExecutionDecision.UNKNOWN  # UNKNOWN = BLOCK
        assert not result.permitted

    def test_none_market_state_blocks(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        assert can_execute(order, risk, ctx(market_state=None)).decision is ExecutionDecision.UNKNOWN

    def test_global_pause_blocks_new_risk(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        result = can_execute(order, risk, ctx(safety_control="PAUSE"))
        assert result.decision is ExecutionDecision.REJECT
        assert "global_pause_blocks_new_risk" in result.reasons

    def test_close_only_blocks_new_exposure(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        result = can_execute(order, risk, ctx(safety_control="CLOSE_ONLY"))
        assert "close_only_blocks_new_exposure" in result.reasons

    def test_emergency_blocks_everything(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        result = can_execute(order, risk, ctx(safety_control="EMERGENCY"))
        assert "emergency_stop" in result.reasons

    def test_live_requires_permission(self):
        risk = make_risk_decision(environment="LIVE", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(environment="LIVE"), risk)
        denied = can_execute(order, risk, ctx(actor_role=Role.TRADER))
        assert denied.decision is ExecutionDecision.REJECT
        assert any("permission_denied" in r for r in denied.reasons)
        allowed = can_execute(order, risk, ctx(actor_role=Role.ADMIN))
        assert allowed.permitted

    def test_live_without_actor_rejects(self):
        risk = make_risk_decision(environment="LIVE", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(environment="LIVE"), risk)
        result = can_execute(order, risk, ctx(actor_role=None))
        assert "live_requires_actor" in result.reasons

    def test_block_risk_decision_never_executes(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.BLOCK,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        result = can_execute(order, risk, ctx())
        assert "risk_decision_BLOCK" in result.reasons

    def test_limited_constraint_violation_detected(self):
        risk = make_risk_decision(
            environment="SIMULATION", scope="XAUUSD", decision=RiskResult.LIMITED,
            decision_time=at(11, 59), expires_at=at(12, 30),
            permission_constraints={"max_quantity": "0.50"},
        )
        order = aligned(make_order(quantity=Decimal("1.00")), risk)
        result = can_execute(order, risk, ctx())
        assert "limited_constraint_violated" in result.reasons

    def test_risk_hash_mismatch_detected(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = replace(aligned(make_order(), risk), risk_decision_hash="f" * 64)
        result = can_execute(order, risk, ctx())
        assert "risk_decision_hash_mismatch" in result.reasons

    def test_context_hash_mismatch_detected(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        result = can_execute(order, risk, ctx(expected_risk_context_hash="a" * 64))
        assert "stale_risk_context" in result.reasons

    def test_expired_order_never_submits(self):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 0), expires_at=at(13, 0))
        order = aligned(make_order(expires_at=at(12, 0)), risk)
        result = can_execute(order, risk, ctx(now=at(12, 1)))
        assert "order_expired" in result.reasons


class TestIdempotency:
    def test_deterministic_key(self):
        key1 = order_idempotency_key(intent_id="itt_1", strategy_id="str_1",
                                     symbol="XAUUSD", side="BUY", order_type="MARKET",
                                     quantity="1.00", order_version=1)
        key2 = order_idempotency_key(intent_id="itt_1", strategy_id="str_1",
                                     symbol="XAUUSD", side="BUY", order_type="MARKET",
                                     quantity="1.00", order_version=1)
        key3 = order_idempotency_key(intent_id="itt_1", strategy_id="str_1",
                                     symbol="XAUUSD", side="BUY", order_type="MARKET",
                                     quantity="2.00", order_version=1)
        assert key1 == key2
        assert key1 != key3

    def test_duplicate_submit_returns_existing(self, env):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        first = env["oms"].submit(order, risk_decision=risk, execution_context=ctx())
        assert first.accepted
        second = env["oms"].submit(order, risk_decision=risk, execution_context=ctx())
        assert not second.accepted
        assert second.duplicate_of == first.order.order_id
        assert env["storage"].orders.count() == 1  # ONE canonical order

    def test_invalid_order_rejected_with_reasons(self, env):
        risk = make_risk_decision(environment="LIVE", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(environment="SIMULATION"), risk)
        result = env["oms"].submit(order, risk_decision=risk, execution_context=ctx())
        assert not result.accepted
        assert "environment_mismatch" in result.reasons


class TestLifecycleAndFills:
    def _accepted(self, env, quantity="1.00"):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(quantity=Decimal(quantity)), risk)
        return env["oms"].submit(order, risk_decision=risk, execution_context=ctx()).order, risk

    def test_partial_fills_accumulate_to_filled(self, env):
        order, _ = self._accepted(env, "1.00")
        for quantity in ("0.30", "0.20", "0.50"):
            order = self._fill(env, order, quantity)
        assert order.status is OrderStatus.FILLED
        assert env["oms"].filled_quantity(order.order_id) == Decimal("1.00")

    def test_overfill_goes_unknown_not_clipped(self, env):
        order, _ = self._accepted(env, "1.00")
        order = self._fill(env, order, "0.70")
        assert order.status is OrderStatus.PARTIALLY_FILLED
        order = self._fill(env, order, "0.50")  # cumulative 1.20 > 1.00
        assert order.status is OrderStatus.UNKNOWN  # corruption: never clipped

    def test_duplicate_fill_report_no_duplicate_effect(self, env):
        order, _ = self._accepted(env, "1.00")
        report = self._fill_report(order, "0.50")
        _, created_first = env["oms"].ingest_execution_report(report)
        _, created_second = env["oms"].ingest_execution_report(report)
        assert created_first and not created_second
        assert env["oms"].filled_quantity(order.order_id) == Decimal("0.50")

    def test_broker_reject_transitions_rejected(self, env):
        order, _ = self._accepted(env)
        report = ExecutionReport(
            execution_id=new_identifier("execution_id"), order_id=order.order_id,
            environment="SIMULATION", execution_type=ExecutionType.REJECT,
            status=ExecutionStatus.REJECTED, symbol=order.symbol, side=order.side,
            executed_quantity="0", execution_price="0", currency="USD",
            broker_timestamp=at(12, 1), received_at=at(12, 1),
            provenance={"source": "mt5", "adapter": "mt5-demo", "raw": {"retcode": 10016}},
            raw_reference="MT5-REJ-1",
            idempotency_key=execution_idempotency_key(
                order_id=order.order_id, broker_order_id=None,
                broker_execution_reference="REJ-1"),
            normalized_error=__import__("core.oms.contracts", fromlist=["BrokerError"]).BrokerError.INVALID_PRICE,
        )
        updated, _ = env["oms"].ingest_execution_report(report)
        assert updated.status is OrderStatus.REJECTED

    def test_cancel_requires_evidence(self, env):
        order, _ = self._accepted(env)
        requested = env["oms"].request_cancel(order, reason="operator",
                                              actor="operator", at=at(12, 2))
        assert requested.status is OrderStatus.CANCEL_REQUESTED
        wrong_evidence = self._fill_report(requested, "0.10")
        with pytest.raises(ContractError):
            env["oms"].confirm_cancel(requested, evidence=wrong_evidence, at=at(12, 3))

    def test_cancel_confirmed_with_evidence(self, env):
        order, _ = self._accepted(env)
        requested = env["oms"].request_cancel(order, reason="operator",
                                              actor="operator", at=at(12, 2))
        evidence = ExecutionReport(
            execution_id=new_identifier("execution_id"), order_id=order.order_id,
            environment="SIMULATION", execution_type=ExecutionType.CANCEL,
            status=ExecutionStatus.CONFIRMED, symbol=order.symbol, side=order.side,
            executed_quantity="0", execution_price="0", currency="USD",
            broker_timestamp=at(12, 3), received_at=at(12, 3),
            provenance={"source": "mt5", "adapter": "mt5-demo", "raw": {"cancel": True}},
            raw_reference="MT5-CXL-1",
            idempotency_key=execution_idempotency_key(
                order_id=order.order_id, broker_order_id=None,
                broker_execution_reference="CXL-1"),
        )
        cancelled = env["oms"].confirm_cancel(requested, evidence=evidence, at=at(12, 3))
        assert cancelled.status is OrderStatus.CANCELLED

    def test_replacement_requires_risk_revalidation(self, env):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        accepted = env["oms"].submit(order, risk_decision=risk, execution_context=ctx()).order
        blocked_risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                          decision=RiskResult.BLOCK,
                                          decision_time=at(12, 2), expires_at=at(12, 30))
        with pytest.raises(RiskGateError):
            env["oms"].build_replacement(accepted, quantity="2.00", price=None,
                                         risk_decision=blocked_risk,
                                         execution_context=ctx(), reason="increase size")
        replacement = env["oms"].build_replacement(accepted, quantity="2.00", price=None,
                                                   risk_decision=risk,
                                                   execution_context=ctx(),
                                                   reason="increase size")
        assert replacement.replaces_order_id == accepted.order_id
        assert replacement.order_version == 1
        # original chain shows REPLACED
        latest_original = env["storage"].orders.get_by_id(accepted.order_id)
        assert latest_original.status is OrderStatus.REPLACED

    def test_historical_order_version_immutable(self, env):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        accepted = env["oms"].submit(order, risk_decision=risk, execution_context=ctx()).order
        with pytest.raises(StorageError):
            env["storage"].orders.save(accepted)  # same version id

    def test_recovery_returns_active(self, env):
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        accepted = env["oms"].submit(order, risk_decision=risk, execution_context=ctx()).order
        active = env["oms"].recover()
        assert any(o.order_id == accepted.order_id for o in active)

    # --------------------------- helpers --------------------------- #
    def _fill(self, env, order, quantity):
        report = self._fill_report(order, quantity)
        updated, _ = env["oms"].ingest_execution_report(report)
        return updated

    @staticmethod
    def _fill_report(order, quantity):
        return ExecutionReport(
            execution_id=new_identifier("execution_id"), order_id=order.order_id,
            environment=order.environment,
            execution_type=ExecutionType.FILL if quantity == "1.00" else ExecutionType.PARTIAL_FILL,
            status=ExecutionStatus.CONFIRMED, symbol=order.symbol, side=order.side,
            executed_quantity=quantity, execution_price="2650.00", currency="USD",
            broker_order_id="1001",
            broker_timestamp=at(12, 1), received_at=at(12, 1),
            provenance={"source": "simulation", "adapter": "simulation-executor",
                        "raw": {"synthetic": True}},
            raw_reference=f"SIM-FILL-{quantity}",
            idempotency_key=execution_idempotency_key(
                order_id=order.order_id, broker_order_id="1001",
                broker_execution_reference=f"FILL-{quantity}-{new_identifier('correlation_id')[:8]}"),
        )
