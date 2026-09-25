"""Policy contract tests (SECTION 11) - including failure tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from architecture.contracts.errors import (
    ContractValidationError,
    IdentifierValidationError,
    RequirementNotMetError,
    StateTransitionError,
    TimeValidationError,
)
from core.policy.contracts import PolicyStatus, PolicyType
from tests.factories import at, make_decision, make_policy

ALL_POLICY_TYPES = {
    "ENTRY", "DIRECTION", "GRID", "POSITION_SIZING", "EXPOSURE", "FREQUENCY",
    "RECOVERY", "PROFIT", "NEWS", "VOLATILITY", "SESSION", "PORTFOLIO", "EXECUTION",
    # Phase 3 risk policy types (policy contract 1.1.0)
    "ACCOUNT_RISK_POLICY", "POSITION_RISK_POLICY", "EXPOSURE_POLICY", "DRAWDOWN_POLICY",
    "MARGIN_POLICY", "VOLATILITY_POLICY", "SPREAD_POLICY", "LIQUIDITY_POLICY",
    "CORRELATION_POLICY", "NEWS_RISK_POLICY", "EXECUTION_PERMISSION_POLICY",
    "GLOBAL_SAFETY_POLICY",
}


class TestPolicyContract:
    def test_all_policy_types_registered(self):
        assert {t.value for t in PolicyType} == ALL_POLICY_TYPES

    def test_valid_draft_policy(self):
        make_policy().validate()

    def test_active_policy_with_approver(self):
        from architecture.contracts.identifiers import new_identifier

        make_policy(
            status=PolicyStatus.ACTIVE, approved_by=new_identifier("user_id")
        ).validate()

    def test_activate_with_approval(self):
        from architecture.contracts.identifiers import new_identifier

        approver = new_identifier("user_id")
        policy = make_policy(approved_by=approver)
        approved = policy.transition_status(
            PolicyStatus.ACTIVE,
            reason="approved for paper environment",
            actor="platform.security",
            approved=True,
        )[0]
        assert approved.status is PolicyStatus.ACTIVE


class TestPolicyFailures:
    def test_active_without_approver_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_policy(status=PolicyStatus.ACTIVE).validate()
        assert excinfo.value.rule_id == "POLICY-001"

    def test_activate_without_approval_flag_fails(self):
        policy = make_policy()
        with pytest.raises(RequirementNotMetError):
            policy.transition_status(
                PolicyStatus.ACTIVE, reason="self approval attempt", actor="core.policy"
            )

    def test_activate_without_approver_fails_closed(self):
        policy = make_policy()
        with pytest.raises(ContractValidationError) as excinfo:
            policy.transition_status(
                PolicyStatus.ACTIVE, reason="activate", actor="ops", approved=True
            )
        assert excinfo.value.rule_id == "POLICY-001"

    def test_invalid_policy_version_rejected(self):
        with pytest.raises(ContractValidationError):
            make_policy(policy_version="v2").validate()

    def test_invalid_policy_type_rejected(self):
        with pytest.raises(ContractValidationError):
            make_policy(policy_type="MARTINGALE").validate()

    def test_empty_limits_rejected(self):
        with pytest.raises(ContractValidationError):
            make_policy(limits={}).validate()

    def test_empty_conditions_rejected(self):
        with pytest.raises(ContractValidationError):
            make_policy(conditions=()).validate()

    def test_invalid_effective_window_rejected(self):
        with pytest.raises(TimeValidationError):
            make_policy(effective_from=at(10), effective_to=at(9)).validate()

    def test_invalid_creator_id_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_policy(created_by="admin").validate()

    def test_invalid_status_transition_rejected(self):
        policy = make_policy()
        with pytest.raises(StateTransitionError):
            policy.transition_status(PolicyStatus.SUSPENDED, reason="skip", actor="ops")

    def test_policy_is_immutable(self):
        policy = make_policy()
        with pytest.raises(FrozenInstanceError):
            policy.priority = 0
