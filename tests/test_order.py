"""Order contract tests (SECTION 13) - including risk-gate failure tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from architecture.contracts.errors import (
    ContractValidationError,
    EnvironmentMismatchError,
    IdentifierValidationError,
    RiskGateError,
    StateTransitionError,
    TimeValidationError,
)
from core.execution.contracts import OrderSide, OrderStatus, OrderType, TimeInForce
from core.risk.contracts import MarketState, RiskResult
from core.validation.contracts import DataQualityLevel
from tests.factories import at, make_order, make_risk_decision


def passing_risk(**overrides):
    return make_risk_decision(environment="PAPER", **overrides)


class TestOrderContract:
    def test_valid_order(self):
        make_order().validate()

    def test_lifecycle_to_submission_with_valid_risk(self):
        order = make_order(status=OrderStatus.RISK_CHECK)
        submitted, record = order.transition_status(
            OrderStatus.SUBMITTED,
            reason="risk allowed",
            actor="core.execution",
            risk_decision=passing_risk(),
            at=at(12, 4),
        )
        assert submitted.status is OrderStatus.SUBMITTED
        assert submitted.risk_decision_id == order_risk_id(submitted)
        assert record.requirement == "VALID_RISK_DECISION"

    def test_lifecycle_after_submission(self):
        order = make_order(
            status=OrderStatus.SUBMITTED,
            risk_decision_id=passing_risk().risk_decision_id,
        )
        accepted, _ = order.transition_status(
            OrderStatus.ACCEPTED, reason="broker ack", actor="adapters.broker"
        )
        filled, _ = accepted.transition_status(
            OrderStatus.FILLED, reason="fill received", actor="adapters.broker"
        )
        closed, _ = filled.transition_status(
            OrderStatus.CLOSED, reason="lifecycle end", actor="core.execution"
        )
        assert closed.status is OrderStatus.CLOSED

    def test_quantity_normalized_to_decimal(self):
        order = make_order(quantity="0.10")
        assert isinstance(order.quantity, Decimal)

    def test_cancellation_allowed_from_created(self):
        order = make_order(status=OrderStatus.ORDER_CREATED)
        cancelled, _ = order.transition_status(
            OrderStatus.CANCELLED, reason="user cancel", actor="ui.desktop.user"
        )
        assert cancelled.status is OrderStatus.CANCELLED


def order_risk_id(order) -> str:
    return order.risk_decision_id


class TestRiskGateFailures:
    def test_submission_without_risk_decision_rejected(self):
        order = make_order(status=OrderStatus.RISK_CHECK)
        with pytest.raises(RiskGateError):
            order.transition_status(
                OrderStatus.SUBMITTED, reason="submit", actor="core.execution"
            )

    def test_submission_with_blocked_risk_rejected(self):
        order = make_order(status=OrderStatus.RISK_CHECK)
        blocked = passing_risk(decision=RiskResult.BLOCK)
        with pytest.raises(RiskGateError):
            order.transition_status(
                OrderStatus.SUBMITTED, reason="submit", actor="core.execution",
                risk_decision=blocked, at=at(12, 4),
            )

    def test_submission_with_expired_risk_rejected(self):
        order = make_order(status=OrderStatus.RISK_CHECK)
        expired = passing_risk(expires_at=at(12, 30))
        with pytest.raises(RiskGateError) as excinfo:
            order.transition_status(
                OrderStatus.SUBMITTED, reason="submit", actor="core.execution",
                risk_decision=expired, at=at(12, 45),
            )
        assert excinfo.value.rule_id == "RISK-EXPIRED"

    def test_submission_with_environment_mismatch_rejected(self):
        order = make_order(status=OrderStatus.RISK_CHECK, environment="PAPER")
        live_risk = make_risk_decision(environment="LIVE")
        with pytest.raises(EnvironmentMismatchError):
            order.transition_status(
                OrderStatus.SUBMITTED, reason="submit", actor="core.execution",
                risk_decision=live_risk, at=at(12, 4),
            )

    def test_simulation_order_never_executes_live(self):
        order = make_order(status=OrderStatus.RISK_CHECK, environment="SIMULATION")
        with pytest.raises(EnvironmentMismatchError):
            order.assert_execution_environment("LIVE")

    def test_unknown_market_state_risk_cannot_submit(self):
        order = make_order(status=OrderStatus.RISK_CHECK)
        with pytest.raises(ContractValidationError):
            make_risk_decision(market_state=MarketState.UNKNOWN).validate()

    def test_invalid_risk_decision_id_format_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_order(
                status=OrderStatus.SUBMITTED, risk_decision_id="rsk_bad"
            ).validate()


class TestOrderValidationFailures:
    def test_zero_quantity_rejected(self):
        with pytest.raises(ContractValidationError):
            make_order(quantity=Decimal("0")).validate()

    def test_negative_quantity_rejected(self):
        with pytest.raises(ContractValidationError):
            make_order(quantity=Decimal("-1")).validate()

    def test_limit_order_without_price_rejected(self):
        with pytest.raises(ContractValidationError):
            make_order(order_type=OrderType.LIMIT).validate()

    def test_limit_order_with_price_accepted(self):
        make_order(order_type=OrderType.LIMIT, price=Decimal("2655.00")).validate()

    def test_invalid_side_rejected(self):
        with pytest.raises(ContractValidationError):
            make_order(side="LONG").validate()

    def test_invalid_time_in_force_rejected(self):
        with pytest.raises(ContractValidationError):
            make_order(time_in_force="GTD").validate()

    def test_unknown_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_order(environment="REAL").validate()

    def test_invalid_status_rejected(self):
        with pytest.raises(ContractValidationError):
            make_order(status="SENT").validate()

    def test_submitted_without_risk_reference_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_order(status=OrderStatus.SUBMITTED).validate()
        assert excinfo.value.rule_id == "RISK-GATE"

    def test_updated_before_created_rejected(self):
        with pytest.raises(TimeValidationError):
            make_order(created_at=at(12, 3), updated_at=at(12, 2)).validate()

    def test_invalid_status_transition_rejected(self):
        order = make_order(status=OrderStatus.SIGNAL)
        with pytest.raises(StateTransitionError):
            order.transition_status(
                OrderStatus.FILLED, reason="skip", actor="core.execution"
            )

    def test_order_is_immutable(self):
        order = make_order()
        with pytest.raises(FrozenInstanceError):
            order.environment = "LIVE"
