"""Phase 13 - chaos framework + built-in scenarios (SIMULATION only)."""
from __future__ import annotations

import pytest

from architecture.contracts.errors import ContractError
from platform.chaos.framework import CHAOS_ENVIRONMENTS, ChaosRunner, ChaosScenario
from tests.phase13_scenarios import BUILTIN_SCENARIOS


def _ok_scenario():
    return ChaosScenario(
        scenario_id="chaos_probe_ok", description="always fine",
        environment="SIMULATION",
        inject=lambda: {"value": 1},
        detect=lambda a: {"ok": a["value"] == 1},
        contain=lambda a: {"ok": True},
        recover=lambda a: {"ok": True},
        verify=lambda a: {"ok": True})


class TestFramework:
    def test_live_refused(self):
        for env in ("LIVE", "DEMO", "PRODUCTION"):
            with pytest.raises(ContractError):
                ChaosRunner(environment=env)

    def test_scenario_environment_mismatch_refused(self):
        runner = ChaosRunner(environment="SIMULATION")
        scenario = ChaosScenario(
            scenario_id="chaos_env_mismatch", description="x",
            environment="REPLAY", inject=lambda: None,
            detect=lambda a: {"ok": True}, contain=lambda a: {"ok": True},
            recover=lambda a: {"ok": True}, verify=lambda a: {"ok": True})
        with pytest.raises(ContractError):
            runner.run(scenario)

    def test_phase_failure_fails_scenario(self):
        runner = ChaosRunner(environment="SIMULATION")
        scenario = ChaosScenario(
            scenario_id="chaos_probe_fail", description="verify fails",
            environment="SIMULATION",
            inject=lambda: {"value": 1},
            detect=lambda a: {"ok": True},
            contain=lambda a: {"ok": True},
            recover=lambda a: {"ok": True},
            verify=lambda a: {"ok": False, "why": "post-state wrong"})
        result = runner.run(scenario)
        assert result.outcome == "FAIL"
        assert result.phases["verify"]["ok"] is False

    def test_happy_path_passes_with_evidence(self):
        result = ChaosRunner(environment="SIMULATION").run(_ok_scenario())
        assert result.outcome == "PASS"
        assert set(result.phases) == {"inject", "detect", "contain",
                                      "recover", "verify"}
        for phase in result.phases.values():
            assert phase["ok"] is True


class TestBuiltInScenarios:
    @pytest.mark.parametrize("factory", BUILTIN_SCENARIOS,
                             ids=lambda f: f.__name__)
    def test_scenario_passes(self, factory):
        result = ChaosRunner(environment="SIMULATION").run(factory())
        assert result.outcome == "PASS", result.as_dict()

    def test_all_scenarios_simulation_only(self):
        for factory in BUILTIN_SCENARIOS:
            assert factory().environment in CHAOS_ENVIRONMENTS
