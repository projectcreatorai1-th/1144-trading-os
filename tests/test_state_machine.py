"""State machine contract tests (SECTION 10) - including failure tests."""
from __future__ import annotations

import pytest

from architecture.contracts.errors import (
    RequirementNotMetError,
    StateTransitionError,
    UnknownStateError,
)
from architecture.contracts.state_machine import build_state_machine_registry

REGISTRY = build_state_machine_registry()


class TestMachinesRegistered:
    @pytest.mark.parametrize("machine", [
        "system_state", "market_state", "risk_state", "execution_state",
        "order_state", "position_status", "environment", "policy_status",
        "decision_status",
    ])
    def test_machine_exists(self, machine):
        assert REGISTRY.is_known_state(machine, REGISTRY.machine(machine).initial)

    def test_system_states(self):
        assert set(REGISTRY.machine("system_state").states) == {
            "STARTING", "READY", "RUNNING", "DEGRADED", "SAFE_MODE", "STOPPED"
        }

    def test_market_states(self):
        assert set(REGISTRY.machine("market_state").states) == {
            "CALM", "NORMAL", "VOLATILE", "EXTREME", "UNKNOWN"
        }

    def test_risk_states(self):
        assert set(REGISTRY.machine("risk_state").states) == {
            "NORMAL", "CAUTION", "LIMITED", "PAUSE", "EMERGENCY"
        }

    def test_execution_states(self):
        assert set(REGISTRY.machine("execution_state").states) == {
            "READY", "DEGRADED", "BLOCKED", "CLOSE_ONLY", "DISCONNECTED"
        }


class TestValidTransitions:
    @pytest.mark.parametrize("machine,frm,to", [
        ("system_state", "STARTING", "READY"),
        ("system_state", "RUNNING", "SAFE_MODE"),
        ("system_state", "DEGRADED", "RUNNING"),
        ("market_state", "UNKNOWN", "VOLATILE"),
        ("market_state", "VOLATILE", "UNKNOWN"),
        ("risk_state", "NORMAL", "EMERGENCY"),
        ("risk_state", "EMERGENCY", "PAUSE"),
        ("execution_state", "READY", "CLOSE_ONLY"),
        ("execution_state", "DISCONNECTED", "READY"),
        ("order_state", "SIGNAL", "DECISION"),
        ("order_state", "ACCEPTED", "PARTIAL_FILL"),
        ("position_status", "OPEN", "PARTIALLY_CLOSED"),
        ("environment", "PAPER", "DEMO"),
    ])
    def test_transition_applies(self, machine, frm, to):
        record = REGISTRY.apply(machine, frm, to, reason="test", actor="tester")
        assert record.previous_state == frm
        assert record.new_state == to
        assert record.timestamp.tzinfo is not None


class TestInvalidTransitions:
    def test_unknown_state_rejected(self):
        with pytest.raises(UnknownStateError):
            REGISTRY.apply("system_state", "RUNNING", "HIBERNATING", reason="x", actor="t")

    def test_unknown_machine_rejected(self):
        with pytest.raises(StateTransitionError):
            REGISTRY.apply("coffee_machine", "OFF", "ON", reason="x", actor="t")

    def test_invalid_system_transition(self):
        with pytest.raises(StateTransitionError):
            REGISTRY.apply("system_state", "STOPPED", "RUNNING", reason="x", actor="t")

    def test_risk_deescalation_must_be_stepwise(self):
        with pytest.raises(StateTransitionError):
            REGISTRY.apply("risk_state", "EMERGENCY", "NORMAL", reason="x", actor="t")

    def test_market_jump_more_than_one_step(self):
        with pytest.raises(StateTransitionError):
            REGISTRY.apply("market_state", "CALM", "EXTREME", reason="x", actor="t")

    def test_order_cannot_skip_risk_check(self):
        with pytest.raises(StateTransitionError):
            REGISTRY.apply("order_state", "ORDER_CREATED", "SUBMITTED", reason="x", actor="t")

    def test_requirement_not_met_fails_closed(self):
        with pytest.raises(RequirementNotMetError):
            REGISTRY.apply(
                "order_state", "RISK_CHECK", "SUBMITTED",
                reason="submit", actor="trader", context={},
            )

    def test_requirement_met_allows_submission(self):
        record = REGISTRY.apply(
            "order_state", "RISK_CHECK", "SUBMITTED",
            reason="submit", actor="trader",
            context={"risk_decision_validated": True},
        )
        assert record.requirement == "VALID_RISK_DECISION"

    def test_false_flag_is_not_truthy_enough(self):
        with pytest.raises(RequirementNotMetError):
            REGISTRY.apply(
                "order_state", "RISK_CHECK", "SUBMITTED",
                reason="submit", actor="trader",
                context={"risk_decision_validated": "yes"},
            )

    def test_reason_required(self):
        with pytest.raises(StateTransitionError):
            REGISTRY.apply("system_state", "STARTING", "READY", reason="", actor="t")

    def test_actor_required(self):
        with pytest.raises(StateTransitionError):
            REGISTRY.apply("system_state", "STARTING", "READY", reason="ready", actor="")

    def test_arbitrary_string_states_never_valid(self):
        with pytest.raises(UnknownStateError):
            REGISTRY.validate_states("system_state", "kinda_running")
