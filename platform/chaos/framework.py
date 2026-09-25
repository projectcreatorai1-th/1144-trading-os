"""Deterministic failure-injection framework (platform.chaos, Phase 13).

Every scenario proves UNKNOWN -> SAFE STATE (never UNKNOWN -> ASSUMED
SAFE): each phase (INJECT/DETECT/CONTAIN/RECOVER/VERIFY) must produce real
evidence from the component under test. The runner refuses environments
outside SIMULATION/REPLAY/controlled-test (LIVE is never chaotic; DEMO only
with components that cannot reach a broker).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.time import utc_now

CONTRACT_VERSION = "1.0.0"
CHAOS_ENVIRONMENTS = ("SIMULATION", "REPLAY", "CONTROLLED_TEST")


@dataclass(frozen=True)
class ChaosScenario:
    scenario_id: str
    description: str
    environment: str
    inject: Callable[[], Any]
    detect: Callable[[Any], Mapping[str, Any]]      # must observe failure
    contain: Callable[[Any], Mapping[str, Any]]     # safe state reached
    recover: Callable[[Any], Mapping[str, Any]]
    verify: Callable[[Any], Mapping[str, Any]]      # post-recovery proof

    def validate(self) -> None:
        for name in ("scenario_id", "description"):
            if not isinstance(getattr(self, name), str) \
                    or not getattr(self, name):
                raise ContractError(f"chaos.{name} must be non-empty",
                                    location="chaos.scenario")
        if self.environment not in CHAOS_ENVIRONMENTS:
            raise ContractError(
                f"chaos environment {self.environment!r} not allowed "
                f"(allowed: {CHAOS_ENVIRONMENTS}); LIVE is never chaotic",
                location="chaos.environment", rule_id="CHS-001")
        for phase in ("inject", "detect", "contain", "recover", "verify"):
            if not callable(getattr(self, phase)):
                raise ContractError(f"chaos.{phase} must be callable",
                                    location="chaos.scenario")


@dataclass
class ChaosResult:
    scenario_id: str
    started_at: str
    phases: dict = field(default_factory=dict)
    outcome: str = "NOT_RUN"

    def as_dict(self) -> dict:
        return {"scenario_id": self.scenario_id,
                "started_at": self.started_at,
                "outcome": self.outcome, "phases": self.phases}


class ChaosRunner:
    def __init__(self, *, environment: str) -> None:
        if environment not in CHAOS_ENVIRONMENTS:
            raise ContractError(
                f"ChaosRunner refuses environment {environment!r} "
                f"(allowed: {CHAOS_ENVIRONMENTS})",
                location="chaos.runner", rule_id="CHS-001")
        self.environment = environment

    def run(self, scenario: ChaosScenario) -> ChaosResult:
        scenario.validate()
        if scenario.environment != self.environment:
            raise ContractError(
                "scenario environment must match the runner environment",
                location="chaos.runner", rule_id="CHS-002")
        result = ChaosResult(scenario.scenario_id,
                             utc_now().isoformat())
        try:
            artifact = scenario.inject()
            result.phases["inject"] = {"ok": True}
            for phase in ("detect", "contain", "recover", "verify"):
                evidence = getattr(scenario, phase)(artifact)
                if not isinstance(evidence, Mapping) \
                        or not evidence.get("ok", False):
                    result.phases[phase] = {
                        "ok": False, "evidence": dict(evidence or {})}
                    result.outcome = "FAIL"
                    return result
                result.phases[phase] = {"ok": True,
                                        "evidence": dict(evidence)}
            result.outcome = "PASS"
        except ContractError as error:
            # fail-closed behavior inside a phase is a VALID detection when
            # the scenario expects it (the phase evidence carries it); an
            # unexpected error fails the scenario
            result.phases["error"] = {"ok": False, "detail": str(error)}
            result.outcome = "FAIL"
        return result
