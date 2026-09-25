"""Decision contract tests - including failure tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from architecture.contracts.errors import (
    ContractValidationError,
    IdentifierValidationError,
    ProvenanceError,
    StateTransitionError,
)
from core.decision.contracts import DecisionStatus, DecisionType
from tests.factories import make_decision


class TestDecisionContract:
    def test_valid_decision(self):
        make_decision().validate()

    def test_status_transition(self):
        decision = make_decision()
        validated, record = decision.transition_status(
            DecisionStatus.VALIDATED, reason="policy evaluated", actor="core.policy"
        )
        assert validated.status is DecisionStatus.VALIDATED
        assert record.previous_state == "PROPOSED"

    def test_confidence_boundary_values(self):
        make_decision(confidence=0.0).validate()
        make_decision(confidence=1.0).validate()


class TestDecisionFailures:
    def test_missing_provenance_rejected(self):
        with pytest.raises(ProvenanceError):
            make_decision(provenance=None).validate()

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ContractValidationError):
            make_decision(confidence=1.5).validate()

    def test_confidence_boolean_rejected(self):
        with pytest.raises(ContractValidationError):
            make_decision(confidence=True).validate()

    def test_empty_reasons_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_decision(reasons=()).validate()
        assert excinfo.value.rule_id == "TRACE-001"

    def test_invalid_decision_type_rejected(self):
        with pytest.raises(ContractValidationError):
            make_decision(decision_type="HODL").validate()

    def test_invalid_strategy_id_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_decision(strategy_id="strategy-1").validate()

    def test_invalid_status_rejected(self):
        with pytest.raises(ContractValidationError):
            make_decision(status="MAYBE").validate()

    def test_expiry_before_decision_rejected(self):
        from architecture.contracts.errors import TimeValidationError
        from tests.factories import at

        with pytest.raises(TimeValidationError):
            make_decision(decision_time=at(12, 1), expires_at=at(12, 0)).validate()

    def test_open_ended_decision_without_expiry_valid(self):
        make_decision(expires_at=None).validate()

    def test_invalid_status_transition_rejected(self):
        decision = make_decision(status=DecisionStatus.PROPOSED)
        with pytest.raises(StateTransitionError):
            decision.transition_status(
                DecisionStatus.EXECUTED, reason="skip", actor="core.execution"
            )

    def test_decision_is_immutable(self):
        decision = make_decision()
        with pytest.raises(FrozenInstanceError):
            decision.confidence = 0.99
