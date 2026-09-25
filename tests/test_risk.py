"""Risk contract tests (SECTION 12) - including failure tests.

Covers: explicit results (never boolean), UNKNOWN fail-closed behaviour,
expiry, environment gates, confidence != risk permission.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from architecture.contracts.errors import (
    ContractValidationError,
    EnvironmentMismatchError,
    RiskGateError,
    TimeValidationError,
)
from core.risk.contracts import MarketState, RiskResult, RiskState
from core.validation.contracts import DataQualityLevel
from tests.factories import at, make_risk_decision

BLOCKING = {RiskResult.BLOCK, RiskResult.CLOSE_ONLY, RiskResult.EMERGENCY}


class TestRiskResults:
    def test_explicit_results_registered(self):
        assert {r.value for r in RiskResult} == {
            "ALLOW", "LIMITED", "BLOCK", "CLOSE_ONLY", "EMERGENCY"
        }

    def test_boolean_is_not_a_risk_result(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_risk_decision(decision=True).validate()
        assert "booleans are not valid" in excinfo.value.message


class TestValidRiskDecisions:
    @pytest.mark.parametrize("result", ["ALLOW", "LIMITED", "BLOCK", "CLOSE_ONLY", "EMERGENCY"])
    def test_every_explicit_result_constructible(self, result):
        make_risk_decision(decision=RiskResult(result)).validate()

    def test_executable_results(self):
        assert RiskResult.ALLOW.executable and RiskResult.LIMITED.executable
        assert not RiskResult.BLOCK.executable
        assert not RiskResult.EMERGENCY.executable


class TestUnknownFailsClosed:
    def test_unknown_market_state_cannot_allow(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_risk_decision(market_state=MarketState.UNKNOWN).validate()
        assert excinfo.value.rule_id == "RISK-GATE"

    def test_unknown_data_quality_cannot_allow(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_risk_decision(data_quality=DataQualityLevel.UNKNOWN).validate()
        assert excinfo.value.rule_id == "RISK-GATE"

    def test_degraded_data_quality_cannot_allow(self):
        with pytest.raises(ContractValidationError):
            make_risk_decision(data_quality=DataQualityLevel.DEGRADED).validate()

    def test_unknown_market_can_block(self):
        make_risk_decision(
            decision=RiskResult.BLOCK, market_state=MarketState.UNKNOWN
        ).validate()

    def test_unknown_data_can_close_only(self):
        make_risk_decision(
            decision=RiskResult.CLOSE_ONLY, data_quality=DataQualityLevel.UNKNOWN
        ).validate()

    def test_confidence_never_overrides_unknown(self):
        with pytest.raises(ContractValidationError):
            make_risk_decision(
                market_state=MarketState.UNKNOWN, confidence=1.0
            ).validate()


class TestExpiry:
    def test_not_expired_before_expiry(self):
        decision = make_risk_decision()
        assert decision.is_expired(now=at(12, 30)) is False

    def test_expired_at_expiry(self):
        decision = make_risk_decision()
        assert decision.is_expired(now=at(13, 0)) is True

    def test_expired_after_expiry(self):
        decision = make_risk_decision()
        assert decision.is_expired(now=at(14, 0)) is True

    def test_expiry_must_follow_decision_time(self):
        with pytest.raises(TimeValidationError):
            make_risk_decision(decision_time=at(12, 2), expires_at=at(12, 1)).validate()


class TestExecutability:
    def test_allow_is_executable_in_same_environment(self):
        decision = make_risk_decision(environment="PAPER")
        assert decision.is_executable("PAPER", now=at(12, 30)) is True
        decision.assert_executable("PAPER", now=at(12, 30))

    def test_environment_mismatch_not_executable(self):
        decision = make_risk_decision(environment="SIMULATION")
        assert decision.is_executable("LIVE", now=at(12, 30)) is False

    def test_environment_mismatch_raises(self):
        decision = make_risk_decision(environment="SIMULATION")
        with pytest.raises(RiskGateError) as excinfo:
            decision.assert_executable("LIVE", now=at(12, 30))
        assert excinfo.value.rule_id == "ENV-001"

    def test_block_not_executable(self):
        decision = make_risk_decision(decision=RiskResult.BLOCK)
        assert decision.is_executable("PAPER", now=at(12, 30)) is False
        with pytest.raises(RiskGateError):
            decision.assert_executable("PAPER", now=at(12, 30))

    def test_expired_decision_not_executable(self):
        decision = make_risk_decision()
        assert decision.is_executable("PAPER", now=at(13, 30)) is False
        with pytest.raises(RiskGateError) as excinfo:
            decision.assert_executable("PAPER", now=at(13, 30))
        assert excinfo.value.rule_id == "RISK-EXPIRED"


class TestRiskValidationFailures:
    def test_missing_reasons_rejected(self):
        with pytest.raises(ContractValidationError):
            make_risk_decision(reasons=()).validate()

    def test_empty_limits_rejected(self):
        with pytest.raises(ContractValidationError):
            make_risk_decision(limits={}).validate()

    def test_negative_exposure_rejected(self):
        with pytest.raises(ContractValidationError):
            make_risk_decision(current_exposure=-1.0).validate()

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ContractValidationError):
            make_risk_decision(confidence=1.2).validate()

    def test_invalid_risk_state_rejected(self):
        with pytest.raises(ContractValidationError):
            make_risk_decision(risk_state="SPICY").validate()

    def test_invalid_market_state_rejected(self):
        with pytest.raises(ContractValidationError):
            make_risk_decision(market_state="CRASHED").validate()

    def test_missing_policy_reference_rejected(self):
        with pytest.raises(ContractValidationError):
            make_risk_decision(policy_reference="").validate()

    def test_missing_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_risk_decision(environment=None).validate()

    def test_risk_decision_is_immutable(self):
        decision = make_risk_decision()
        with pytest.raises(FrozenInstanceError):
            decision.decision = RiskResult.ALLOW
