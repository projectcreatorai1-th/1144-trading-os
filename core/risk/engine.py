"""Risk engine (owned by core.risk).

The deterministic safety authority (SECTION 51 boundary):

STATE -> POLICY -> RISK CONTEXT -> DIMENSION POLICIES (rule interpreter)
-> COMPOSITION -> RISK DECISION -> ALLOW/LIMITED/BLOCK/CLOSE_ONLY/EMERGENCY.

Rules live in policies; interpretation lives in the single PolicyEvaluator;
precedence lives in risk-config.yaml; composition lives in core.risk.composition.
Hard limits evaluate FIRST and can never be softened (no AI/strategy/user
override - risk-config hard_limit_override_allowed=false). The engine never
mutates state, ledger or reconciliation (read-only inputs; audit output only)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from architecture.contracts.errors import ContractValidationError, RiskGateError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc
from core.policy.contracts import Policy, PolicyType
from core.policy.evaluation import PolicyEvaluator
from core.policy.registry import PolicyRegistry
from core.risk import config
from core.risk.composition import CompositionResult, compose
from core.risk.config import severity_rank
from core.risk.context import RiskContext
from core.state.contracts import StateCategory
from core.state.engine import StateEngine
from core.state.stores import StateStore
from core.validation.contracts import DataQualityLevel
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"

#: Dimension policy types evaluated by the engine, in deterministic order.
DIMENSION_POLICY_TYPES = (
    PolicyType.ACCOUNT_RISK_POLICY,
    PolicyType.POSITION_RISK_POLICY,
    PolicyType.EXPOSURE_POLICY,
    PolicyType.DRAWDOWN_POLICY,
    PolicyType.MARGIN_POLICY,
    PolicyType.VOLATILITY_POLICY,
    PolicyType.SPREAD_POLICY,
    PolicyType.LIQUIDITY_POLICY,
    PolicyType.CORRELATION_POLICY,
    PolicyType.NEWS_RISK_POLICY,
    PolicyType.EXECUTION_PERMISSION_POLICY,
)
#: Highest authority: evaluated first, never soften-able.
HARD_POLICY_TYPES = (PolicyType.GLOBAL_SAFETY_POLICY,)


@dataclass(frozen=True)
class RiskEvaluationRequest:
    action_type: str
    subject: str
    environment: str
    requested_exposure: str
    context: RiskContext
    correlation_id: str
    at: datetime
    account_id: str | None = None
    symbol: str | None = None
    strategy_id: str | None = None
    causation_id: str | None = None
    decision_ttl_seconds: int | None = None


class RiskEngine:
    def __init__(
        self,
        *,
        policies: PolicyRegistry,
        evaluator: PolicyEvaluator | None = None,
        audit: AuditRepository | None = None,
        risk_state: str | None = None,
    ) -> None:
        self._policies = policies
        self._evaluator = evaluator or PolicyEvaluator(
            precedence=config.permission_precedence(),
        )
        self._audit = audit
        self._risk_state = risk_state

    def evaluate(self, request: RiskEvaluationRequest) -> Any:
        """Returns an extended RiskDecision (contract 1.2.0). Fail closed."""
        from core.risk.contracts import RiskDecision, RiskResult

        at = ensure_utc(request.at, location="risk.evaluate.at")
        context = request.context

        # 1. HARD SAFETY POLICIES FIRST - their BLOCK/EMERGENCY cannot be softened
        hard_evaluations = [
            self._evaluate_policy(policy, request, at)
            for policy in self._resolve(HARD_POLICY_TYPES, request, at)
        ]
        hard_result = compose(hard_evaluations, context) if hard_evaluations else None

        # 2. environment gate: LIVE/DEMO/PAPER/SIMULATION decisions stay inside
        #    their environment (fail closed on mismatch is structural: the
        #    decision carries request.environment and validates against it)

        # 3. dimension policies (rules come from policy content)
        dimension_evaluations = []
        for policy_type in DIMENSION_POLICY_TYPES:
            policy = self._resolve_one(policy_type, request, at)
            if policy is not None:
                dimension_evaluations.append(self._evaluate_policy(policy, request, at))

        # 4. composition (critical unknowns gate inside)
        composition = compose(dimension_evaluations, context)
        permission = composition.permission
        if not dimension_evaluations and not hard_evaluations:
            permission = "BLOCK"  # missing policy -> fail closed (SECTION 16)

        # 5. hard result cannot be softened (AI/strategy/soft rules have no path)
        if hard_result is not None and severity_rank(hard_result.permission) < severity_rank(permission):
            permission = hard_result.permission

        # 6. safety-state gates: RISK_STATE EMERGENCY/PAUSE (from state store
        #    injected at construction via set_risk_state)
        if self._risk_state in ("EMERGENCY",):
            permission = "EMERGENCY"
        elif self._risk_state == "PAUSE" and severity_rank("BLOCK") < severity_rank(permission):
            permission = "BLOCK"

        # 7. action-direction semantics: risk-REDUCING actions stay allowed
        #    under CLOSE_ONLY (no new exposure)
        if permission == "CLOSE_ONLY" and request.action_type in ("CLOSE", "REDUCE"):
            permission = "LIMITED"

        triggered = composition.triggered_rules + (
            hard_result.triggered_rules if hard_result else ()
        )
        ttl = request.decision_ttl_seconds if request.decision_ttl_seconds is not None \
            else config.decision_ttl_seconds()

        decision = RiskDecision(
            risk_decision_id=new_identifier("risk_decision_id"),
            environment=request.environment,
            scope=request.subject,
            decision=RiskResult(permission),
            reasons=tuple(
                f"{rule.get('rule_id', 'UNKNOWN')}:{rule.get('outcome', permission)}"
                for rule in triggered
            ) or (f"composition:{permission}",),
            limits={
                "decision_ttl_seconds": str(ttl),
                "risk_rule_version": config.risk_rule_version(),
            },
            current_exposure=_current_exposure(context),
            requested_exposure=_to_float(request.requested_exposure),
            risk_metrics={"risk_rule_version": config.risk_rule_version()},
            risk_state=_risk_state_enum(self._risk_state),
            market_state=_market_state_enum(context.market_state),
            data_quality=_data_quality_enum(context.data_quality),
            confidence=1.0,  # deterministic engine: rules fired or not - no guessing
            policy_reference=_policy_reference(dimension_evaluations, hard_evaluations),
            correlation_id=request.correlation_id,
            decision_time=at,
            expires_at=at + timedelta(seconds=ttl),
            causation_id=request.causation_id,
            action_type=request.action_type,
            subject=request.subject,
            account_id=request.account_id,
            symbol=request.symbol,
            strategy_id=request.strategy_id,
            evaluated_rules=tuple(
                dict(rule) for evaluation in dimension_evaluations + hard_evaluations
                for rule in evaluation.triggered_rules
            ),
            triggered_rules=tuple(dict(rule) for rule in triggered),
            blocked_rules=tuple(
                dict(rule) for rule in triggered
                if rule.get("outcome") in ("BLOCK", "CLOSE_ONLY", "EMERGENCY")
            ),
            risk_context_hash=context.context_hash,
            policy_hash=_combined_policy_hash(dimension_evaluations, hard_evaluations),
            provenance=None,
            permission_constraints=dict(composition.constraints) if composition.constraints else None,
        )
        decision.validate()
        if composition.unknown_criticals:
            # the decision is BLOCK with the unknowns recorded as reasons
            object.__setattr__(decision, "reasons", tuple(decision.reasons) + tuple(
                f"UNKNOWN_CRITICAL:{field}" for field in composition.unknown_criticals
            ))
        if self._audit is not None:
            self._audit.append(AuditRecord(
                audit_id=new_identifier("audit_id"),
                actor_type=ActorType.SYSTEM,
                actor_id="core.risk.engine",
                action="RISK_DECISION",
                entity_type="risk_decision",
                entity_id=decision.risk_decision_id,
                event_time=at,
                before=None,
                after={"permission": decision.decision.value,
                       "action_type": request.action_type},
                reason=f"risk evaluation -> {decision.decision.value}",
                source="core.risk.engine",
                environment=request.environment,
                correlation_id=request.correlation_id,
                causation_id=request.causation_id,
                risk_version=config.risk_rule_version(),
            ))
        return decision

    def set_risk_state(self, status: str | None) -> None:
        """The runtime risk state (RISK_STATE category current status) is
        injected, never read from mutable globals behind the engine's back."""
        self._risk_state = status

    def _resolve(self, policy_types, request, at) -> list[Policy]:
        resolved = []
        for policy_type in policy_types:
            policy = self._policies.resolve_active(
                policy_type, environment=request.environment, at=at
            )
            if policy is not None:
                resolved.append(policy)
        return resolved

    def _resolve_one(self, policy_type, request, at) -> Policy | None:
        return self._policies.resolve_active(
            policy_type, environment=request.environment, at=at
        )

    def _evaluate_policy(self, policy: Policy, request: RiskEvaluationRequest, at: datetime):
        return self._evaluator.evaluate(
            policy=policy,
            context=request.context.flattened(),
            environment=request.environment,
            timestamp=at,
            correlation_id=request.correlation_id,
            causation_id=request.causation_id,
        )


def _policy_reference(dimension_evaluations, hard_evaluations) -> str:
    references = [
        f"{evaluation.policy_id}@{evaluation.policy_version}"
        for evaluation in list(hard_evaluations) + list(dimension_evaluations)
    ]
    if not references:
        return "none:missing-policy(fail-closed)"
    return ",".join(references)


def _combined_policy_hash(dimension_evaluations, hard_evaluations) -> str:
    import hashlib

    material = ",".join(sorted(
        f"{evaluation.policy_id}@{evaluation.policy_version}:{evaluation.policy_hash}"
        for evaluation in list(hard_evaluations) + list(dimension_evaluations)
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _current_exposure(context: RiskContext) -> float:
    if context.gross_exposure is None:
        return 0.0
    from core.ledger.money import parse_decimal

    return float(parse_decimal(context.gross_exposure, location="risk.current_exposure"))


def _to_float(value: str) -> float:
    from core.ledger.money import parse_decimal

    return float(parse_decimal(value, location="risk.requested_exposure"))


def _risk_state_enum(status: str | None):
    from core.risk.contracts import RiskState

    if status is None:
        return RiskState.NORMAL  # no injected state = engine-local default; the
        # runtime composition root always injects the actual RISK_STATE
    return RiskState(status)


def _market_state_enum(status: str | None):
    from core.risk.contracts import MarketState

    return MarketState(status) if status else MarketState.UNKNOWN


def _data_quality_enum(level: str | None):
    if level is None:
        return DataQualityLevel.UNKNOWN
    return DataQualityLevel(level)
