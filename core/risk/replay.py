"""Risk decision replay (owned by core.risk).

Forensic capability: re-evaluate a stored decision against the historical
policy version and the stored context, then COMPARE. Mismatches are
reported - never auto-corrected (SECTION 25). Runs read-only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.time import ensure_utc
from core.policy.contracts import PolicyType
from core.policy.evaluation import PolicyEvaluator
from core.policy.registry import PolicyRegistry
from core.risk import config
from core.risk.contracts import RiskDecision
from core.risk.context import RiskContext
from core.risk.decision_store import RiskDecisionStore

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class ReplayComparison:
    risk_decision_id: str
    status: str  # MATCH | MISMATCH | UNKNOWN
    original_permission: str
    replayed_permission: str | None
    reasons: tuple[str, ...]


class RiskReplayService:
    def __init__(self, decisions: RiskDecisionStore, policies: PolicyRegistry,
                 evaluator: PolicyEvaluator | None = None) -> None:
        self._decisions = decisions
        self._policies = policies
        self._evaluator = evaluator or PolicyEvaluator()

    def replay(self, risk_decision_id: str, *, environment: str) -> ReplayComparison:
        decision = self._decisions.get_by_id(risk_decision_id)
        context = self._decisions.get_context(risk_decision_id)
        self._require_replay_environment(environment, decision)

        references = decision.policy_reference.split(",")
        replayed: list[str] = []
        reasons: list[str] = []
        for reference in references:
            policy_id, _, version = reference.partition("@")
            if not policy_id.startswith("pol_"):
                continue  # informational markers (e.g. missing-policy)
            try:
                policy = self._policies.get_version(policy_id, version)
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                reasons.append(f"policy_unavailable:{policy_id}@{version}:{exc}")
                continue
            evaluation = self._evaluator.evaluate(
                policy=policy,
                context=context.flattened(),
                environment=decision.environment,
                timestamp=decision.decision_time,
                correlation_id=decision.correlation_id,
            )
            replayed.append(evaluation.result)
            if evaluation.context_hash != decision.risk_context_hash:
                reasons.append("context_hash_mismatch")

        if not replayed:
            return ReplayComparison(
                risk_decision_id=decision.risk_decision_id,
                status="UNKNOWN",
                original_permission=decision.decision.value,
                replayed_permission=None,
                reasons=tuple(reasons) or ("no_replayable_policy",),
            )

        from core.risk.composition import compose as compose_permissions

        # re-compose with the same aggregator semantics
        class _Shim:
            def __init__(self, result: str) -> None:
                self.result = result
                self.triggered_rules: tuple = ()

        composition = compose_permissions([_Shim(result) for result in replayed], context)
        replayed_permission = composition.permission
        # re-apply the same safety gates the engine applied
        gating_permissions = {
            permission for permission in config.permission_precedence()
            if permission != "ALLOW"
        }
        if decision.decision.value in gating_permissions and replayed_permission == "ALLOW":
            # engine gates (hard policies, risk state, CLOSE-action semantics)
            # are recorded in reasons; strict comparison below decides
            reasons.append("engine_gate_differs")

        status = "MATCH" if replayed_permission == decision.decision.value else "MISMATCH"
        return ReplayComparison(
            risk_decision_id=decision.risk_decision_id,
            status=status,
            original_permission=decision.decision.value,
            replayed_permission=replayed_permission,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _require_replay_environment(environment: str, decision: RiskDecision) -> None:
        from architecture.contracts.environment import assert_same_environment

        assert_same_environment(environment, "REPLAY", context="risk.replay.environment")
        # the decision itself keeps its original environment; replay reads only
