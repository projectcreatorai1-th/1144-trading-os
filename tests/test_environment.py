"""Environment contract tests (SECTION 18) - including failure tests."""
from __future__ import annotations

import pytest

from architecture.contracts.environment import (
    Environment,
    EnvironmentDeclaration,
    assert_same_environment,
    declare_environment,
    parse_environment,
    supported_environments,
    validate_environment_transition,
)
from architecture.contracts.errors import (
    ContractValidationError,
    EnvironmentMismatchError,
    EnvironmentTransitionError,
)
from tests.factories import at


class TestEnvironmentEnum:
    def test_supported_environments_match_contract(self):
        assert supported_environments() == [
            "RESEARCH", "SIMULATION", "REPLAY", "PAPER", "DEMO", "LIVE", "BACKTEST"
        ]

    def test_parse_accepts_enum_and_string(self):
        assert parse_environment("LIVE") is Environment.LIVE
        assert parse_environment(Environment.PAPER) is Environment.PAPER


class TestEnvironmentFailClosed:
    def test_missing_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            parse_environment(None)

    def test_unknown_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            parse_environment("PRODUCTION")

    def test_lowercase_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            parse_environment("live")

    def test_mismatch_fails_closed(self):
        with pytest.raises(EnvironmentMismatchError):
            assert_same_environment("SIMULATION", "LIVE", context="order.execution")

    def test_simulation_live_mismatch_is_hard_failure(self):
        with pytest.raises(EnvironmentMismatchError):
            assert_same_environment(Environment.SIMULATION, Environment.LIVE, context="broker")

    def test_matching_environments_pass(self):
        assert_same_environment("PAPER", Environment.PAPER, context="ok")


class TestEnvironmentTransitions:
    @pytest.mark.parametrize("current,target", [
        ("RESEARCH", "SIMULATION"),
        ("SIMULATION", "REPLAY"),
        ("REPLAY", "PAPER"),
        ("PAPER", "DEMO"),
        ("DEMO", "LIVE"),
        ("LIVE", "DEMO"),
    ])
    def test_adjacent_transitions_valid(self, current, target):
        validate_environment_transition(Environment(current), Environment(target))

    @pytest.mark.parametrize("current,target", [
        ("RESEARCH", "LIVE"),
        ("RESEARCH", "PAPER"),
        ("SIMULATION", "LIVE"),
        ("PAPER", "LIVE"),
        ("REPLAY", "DEMO"),
    ])
    def test_skipping_environments_fails_closed(self, current, target):
        with pytest.raises(EnvironmentTransitionError):
            validate_environment_transition(Environment(current), Environment(target))


class TestEnvironmentDeclaration:
    def test_valid_declaration(self):
        declaration = declare_environment(
            "DEMO", declared_by="platform.runtime", declared_at=at(0, 0)
        )
        declaration.validate()
        assert declaration.environment is Environment.DEMO

    def test_declaration_matches(self):
        declaration = make_declaration("PAPER")
        declaration.matches("PAPER")
        with pytest.raises(EnvironmentMismatchError):
            declaration.matches("LIVE", context="adapter")

    def test_declaration_rejects_unknown_environment(self):
        declaration = EnvironmentDeclaration(
            environment="STAGING", declared_by="x", declared_at=at(0, 0)
        )
        with pytest.raises(ContractValidationError):
            declaration.validate()


def make_declaration(env: str) -> EnvironmentDeclaration:
    return declare_environment(env, declared_by="test.environment", declared_at=at(0, 0))
