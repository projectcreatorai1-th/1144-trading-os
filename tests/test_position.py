"""Position contract tests (SECTION 14) - including failure tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from architecture.contracts.errors import (
    ContractValidationError,
    IdentifierValidationError,
    StateTransitionError,
    TimeValidationError,
)
from core.portfolio.contracts import PositionSide, PositionStatus
from tests.factories import make_position


class TestPositionContract:
    def test_valid_position(self):
        make_position().validate()

    def test_multi_strategy_account_supported(self):
        from architecture.contracts.identifiers import new_identifier

        first = make_position(strategy_id=new_identifier("strategy_id"))
        second = make_position(
            position_id=new_identifier("position_id"),
            strategy_id=new_identifier("strategy_id"),
        )
        assert first.account_id == second.account_id
        assert first.strategy_id != second.strategy_id
        first.validate()
        second.validate()

    def test_partial_close_transition(self):
        position = make_position()
        partially, _ = position.transition_status(
            PositionStatus.PARTIALLY_CLOSED, reason="partial fill close", actor="core.execution"
        )
        assert partially.status is PositionStatus.PARTIALLY_CLOSED
        assert partially.quantity > 0

    def test_close_sets_quantity_zero(self):
        position = make_position()
        closed, _ = position.transition_status(
            PositionStatus.CLOSED, reason="position closed", actor="core.execution"
        )
        assert closed.quantity == 0
        closed.validate()


class TestPositionFailures:
    def test_zero_quantity_while_open_rejected(self):
        with pytest.raises(ContractValidationError):
            make_position(quantity=Decimal("0")).validate()

    def test_nonzero_quantity_when_closed_rejected(self):
        with pytest.raises(ContractValidationError):
            make_position(
                status=PositionStatus.CLOSED, quantity=Decimal("0.1")
            ).validate()

    def test_invalid_average_price_rejected(self):
        with pytest.raises(ContractValidationError):
            make_position(average_price=Decimal("0")).validate()

    def test_negative_margin_rejected(self):
        with pytest.raises(ContractValidationError):
            make_position(margin=Decimal("-1")).validate()

    def test_invalid_side_rejected(self):
        with pytest.raises(ContractValidationError):
            make_position(side="BUYISH").validate()

    def test_invalid_strategy_id_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_position(strategy_id="grid-master").validate()

    def test_unknown_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_position(environment="TESTNET").validate()

    def test_updated_before_opened_rejected(self):
        from tests.factories import at

        with pytest.raises(TimeValidationError):
            make_position(opened_at=at(12, 5), updated_at=at(12, 4)).validate()

    def test_invalid_transition_rejected(self):
        position = make_position(status=PositionStatus.PARTIALLY_CLOSED)
        with pytest.raises(StateTransitionError):
            # PARTIALLY_CLOSED -> OPEN remains invalid (machine 1.3.0 added
            # quantity self-transitions and CLOSED reopen only)
            position.transition_status(PositionStatus.OPEN, reason="bad", actor="x")

    def test_position_is_immutable(self):
        position = make_position()
        with pytest.raises(FrozenInstanceError):
            position.status = PositionStatus.CLOSED
