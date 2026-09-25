"""Risk decision validator (owned by core.risk).

The downstream gate: a RiskDecision is usable ONLY when schema, environment,
policy reference, effective window, expiration, context/policy hash integrity,
permission semantics and hard limits all hold. Critical UNKNOWN fails closed
(SECTION 14/16). Expired decisions are never valid."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from architecture.contracts.environment import assert_same_environment
from architecture.contracts.errors import EnvironmentMismatchError, RiskGateError
from architecture.contracts.time import ensure_utc
from core.risk.config import severity_rank
from core.risk.contracts import RiskDecision

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class DecisionValidation:
    valid: bool
    reasons: tuple[str, ...]


class RiskDecisionValidator:
    def __init__(self, expected_policy_reference: str | None = None,
                 expected_policy_hash: str | None = None) -> None:
        self._expected_policy_reference = expected_policy_reference
        self._expected_policy_hash = expected_policy_hash

    def validate(
        self,
        decision: RiskDecision,
        *,
        environment: str,
        now: datetime,
        expected_context_hash: str | None = None,
    ) -> DecisionValidation:
        reasons: list[str] = []

        try:
            decision.validate()  # schema + fail-closed semantic contract checks
        except Exception as exc:  # noqa: BLE001 - collected, never swallowed silently
            return DecisionValidation(False, (f"schema:{exc}",))

        try:
            assert_same_environment(
                decision.environment, environment, context="decision.environment_gate"
            )
        except RiskGateError as exc:
            reasons.append(f"environment_mismatch:{exc.message}")
        except EnvironmentMismatchError as exc:
            reasons.append(f"environment_mismatch:{exc.message}")

        moment = ensure_utc(now, location="decision.now")
        if decision.is_expired(moment):
            reasons.append(
                f"expired:{ensure_utc(decision.expires_at).isoformat()}"
            )

        if expected_context_hash is not None and decision.risk_context_hash is not None \
                and decision.risk_context_hash != expected_context_hash:
            reasons.append("risk_context_mismatch")

        if self._expected_policy_reference is not None:
            decision_ref = decision.policy_reference.split(",")[0] if decision.policy_reference else ""
            if "missing-policy" in decision.policy_reference:
                reasons.append("missing_policy")
            elif self._expected_policy_reference not in decision.policy_reference:
                reasons.append(f"policy_reference_mismatch:{decision_ref}")

        if self._expected_policy_hash is not None and decision.policy_hash is not None \
                and decision.policy_hash != self._expected_policy_hash:
            reasons.append("policy_hash_mismatch")

        # hard-limit violations can never validate as ALLOW/LIMITED
        if decision.decision.value in ("ALLOW", "LIMITED"):
            for rule in decision.blocked_rules:
                outcome = rule.get("outcome") if isinstance(rule, dict) else None
                if outcome in ("BLOCK", "CLOSE_ONLY", "EMERGENCY"):
                    reasons.append(f"hard_limit_violation:{rule.get('rule_id')}")
                    break

        # LIMITED decisions must carry their constraints (never silently unconstrained)
        if decision.decision.value == "LIMITED" and not decision.permission_constraints:
            reasons.append("limited_without_constraints")

        return DecisionValidation(len(reasons) == 0, tuple(reasons))

    @staticmethod
    def assert_usable(
        decision: RiskDecision,
        *,
        environment: str,
        now: datetime,
        expected_context_hash: str | None = None,
    ) -> None:
        result = RiskDecisionValidator().validate(
            decision, environment=environment, now=now,
            expected_context_hash=expected_context_hash,
        )
        if not result.valid:
            raise RiskGateError(
                f"RiskDecision {decision.risk_decision_id} is not usable: "
                + "; ".join(result.reasons),
                location="risk.decision_validator",
                details={"reasons": list(result.reasons)},
            )
