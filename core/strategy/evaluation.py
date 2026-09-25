"""Strategy evaluation, eligibility, kill criteria and health (owned by core.strategy).

The generic StrategyEvaluator evaluates conditions declared in the strategy
CONFIG against a risk context - deterministic, no clock/random/network. It
does NOT implement strategy algorithms (Phase 6) and never judges hard risk
(that belongs to core.risk)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import new_identifier, validate_identifier
from architecture.contracts.time import ensure_utc
from architecture.contracts.versioning import SemVer
from core.policy.evaluation import OPERATORS  # single comparison-semantics source
from core.risk.context import RiskContext
from core.strategy.contracts import (
    INTENT_ELIGIBLE_LIFECYCLE,
    Strategy,
    StrategyConfig,
    StrategyLifecycle,
)

CONTRACT_VERSION = "1.0.0"


class EvaluationResult(Enum):
    PROPOSED = "PROPOSED"
    NO_ACTION = "NO_ACTION"
    BLOCKED = "BLOCKED"


class EligibilityResultValue(Enum):
    ELIGIBLE = "ELIGIBLE"
    CONDITIONALLY_ELIGIBLE = "CONDITIONALLY_ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNKNOWN = "UNKNOWN"


class KillResult(Enum):
    ACTIVE = "ACTIVE"
    WARNING = "WARNING"
    SUSPEND_REQUIRED = "SUSPEND_REQUIRED"
    KILL_REQUIRED = "KILL_REQUIRED"


class HealthStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


def compare(left: Any, right: Any, operator: str) -> bool:
    """Shared comparison semantics (reused from the policy interpreter)."""
    if operator == "<=":
        return left <= right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == ">":
        return left > right
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    raise ContractValidationError(
        f"Unknown operator '{operator}'", location="strategy.compare",
    )


def _resolve(context: Mapping[str, Any], dotted: str) -> tuple[bool, Any]:
    node: Any = context
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _typed(value: Any, operator: str):
    """Numeric comparison for numeric strings; string equality otherwise."""
    from core.ledger.money import parse_decimal

    if isinstance(value, str) and operator not in ("==", "!="):
        return parse_decimal(value, location="strategy.condition")
    return value


@dataclass(frozen=True)
class ConditionOutcome:
    condition: Mapping[str, Any]
    satisfied: bool
    resolved: bool
    value: Any = None


class StrategyEvaluator:
    """Deterministic condition evaluator. Conditions live in config.constraints
    as mappings: {"condition_id", "field", "op", "value", "purpose" ("entry"/"exit"/"block")}.
    Signals = satisfied entry/exit conditions; blocked = satisfied block conditions."""

    def evaluate_conditions(
        self, conditions: Iterable[Mapping[str, Any]], context: Mapping[str, Any]
    ) -> list[ConditionOutcome]:
        outcomes: list[ConditionOutcome] = []
        for condition in conditions:
            data = dict(condition)
            operator = str(data.get("op", "=="))
            if operator not in OPERATORS:
                raise ContractValidationError(
                    f"Condition {data.get('condition_id', '?')} uses unknown operator '{operator}'",
                    location="strategy.conditions",
                )
            present, raw = _resolve(context, str(data.get("field", "")))
            if not present or raw is None:
                outcomes.append(ConditionOutcome(data, False, False))
                continue
            try:
                left = _typed(raw, operator)
                right = _typed(data.get("value"), operator)
                satisfied = compare(left, right, operator)
            except ContractValidationError:
                outcomes.append(ConditionOutcome(data, False, False))
                continue
            outcomes.append(ConditionOutcome(data, satisfied, True, value=raw))
        return outcomes


@dataclass(frozen=True)
class StrategyEvaluation:
    evaluation_id: str
    strategy_id: str
    strategy_version: str
    result: EvaluationResult
    signals: tuple
    intents: tuple
    conditions: tuple
    blocked_conditions: tuple
    evidence: Mapping[str, Any]
    state_hash: str
    config_hash: str
    risk_context_hash: str
    environment: str
    timestamp: datetime
    correlation_id: str
    policy_hash: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("strategy_evaluation_id", self.evaluation_id,
                            location="evaluation.evaluation_id")
        validate_identifier("strategy_id", self.strategy_id, location="evaluation.strategy_id")
        SemVer.parse(self.strategy_version, location="evaluation.strategy_version")
        if not isinstance(self.result, EvaluationResult):
            raise ContractValidationError(
                "evaluation.result must be an EvaluationResult",
                location="evaluation.result", rule_id="SCHEMA-ENUM",
            )
        for name in ("state_hash", "config_hash", "risk_context_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64:
                raise ContractValidationError(
                    f"evaluation.{name} must be a sha-256 hex string",
                    location=f"evaluation.{name}",
                )
        ensure_utc(self.timestamp, location="evaluation.timestamp")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id, "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version, "result": self.result.value,
            "signals": list(self.signals), "intents": [dict(i) for i in self.intents],
            "conditions": [dict(c) for c in self.conditions],
            "blocked_conditions": [dict(c) for c in self.blocked_conditions],
            "evidence": dict(self.evidence), "state_hash": self.state_hash,
            "config_hash": self.config_hash, "policy_hash": self.policy_hash,
            "risk_context_hash": self.risk_context_hash,
            "environment": self.environment,
            "timestamp": ensure_utc(self.timestamp).isoformat(),
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class EligibilityResult:
    strategy_id: str
    strategy_version: str
    environment: str
    result: EligibilityResultValue
    reasons: tuple
    evaluated_at: datetime
    correlation_id: str
    symbol: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("strategy_id", self.strategy_id, location="eligibility.strategy_id")
        if not isinstance(self.result, EligibilityResultValue):
            raise ContractValidationError(
                "eligibility.result must be an EligibilityResultValue",
                location="eligibility.result", rule_id="SCHEMA-ENUM",
            )
        ensure_utc(self.evaluated_at, location="eligibility.evaluated_at")

    @property
    def eligible(self) -> bool:
        return self.result in (EligibilityResultValue.ELIGIBLE,
                               EligibilityResultValue.CONDITIONALLY_ELIGIBLE)


class StrategyEligibilityEvaluator:
    """Deterministic eligibility: lifecycle, environment, capability support,
    config validity, data quality, market state. UNKNOWN critical inputs never
    become automatically eligible (fail closed)."""

    CRITICAL_UNKNOWN = ("data.quality", "market.state", "system.state")

    def evaluate(
        self, *, strategy: Strategy, capability, config: StrategyConfig,
        context: RiskContext, symbol: str, correlation_id: str, at: datetime,
    ) -> EligibilityResult:
        strategy.validate()
        capability.validate()
        config.validate()
        flattened = context.flattened()
        reasons: list[str] = []
        result = EligibilityResultValue.ELIGIBLE

        if strategy.lifecycle_status not in INTENT_ELIGIBLE_LIFECYCLE:
            reasons.append(f"STRATEGY_LIFECYCLE_{strategy.lifecycle_status.value}")
            result = EligibilityResultValue.INELIGIBLE
        if strategy.environment != context.environment:
            reasons.append("STRATEGY_ENVIRONMENT_MISMATCH")
            result = EligibilityResultValue.INELIGIBLE
        if not capability.supports_environment(context.environment):
            reasons.append("ENVIRONMENT_NOT_SUPPORTED")
            result = EligibilityResultValue.INELIGIBLE
        if not capability.supports_symbol(symbol):
            reasons.append("SYMBOL_NOT_SUPPORTED")
            result = EligibilityResultValue.INELIGIBLE
        moment = ensure_utc(at, location="eligibility.at")
        if moment < strategy.effective_from or (
            strategy.effective_to is not None and moment > strategy.effective_to
        ):
            reasons.append("STRATEGY_NOT_EFFECTIVE")
            result = EligibilityResultValue.INELIGIBLE
        if config.strategy_id != strategy.strategy_id or \
                config.strategy_version != strategy.strategy_version:
            reasons.append("CONFIG_STRATEGY_MISMATCH")
            result = EligibilityResultValue.INELIGIBLE
        if config.environment != context.environment:
            reasons.append("CONFIG_ENVIRONMENT_MISMATCH")
            result = EligibilityResultValue.INELIGIBLE

        critical_unknown: list[str] = []
        for dotted in self.CRITICAL_UNKNOWN:
            present, value = _resolve(flattened, dotted)
            if not present or value is None or value == "UNKNOWN":
                critical_unknown.append(dotted)
        if critical_unknown:
            reasons.extend(f"UNKNOWN_CRITICAL:{field}" for field in critical_unknown)
            result = EligibilityResultValue.UNKNOWN
        elif context.data_quality in ("INVALID", "STALE"):
            reasons.append(f"DATA_QUALITY_{context.data_quality}")
            result = EligibilityResultValue.INELIGIBLE
        elif context.data_quality == "DEGRADED" and result is EligibilityResultValue.ELIGIBLE:
            reasons.append("DATA_QUALITY_DEGRADED")
            result = EligibilityResultValue.CONDITIONALLY_ELIGIBLE

        eligibility = EligibilityResult(
            strategy_id=strategy.strategy_id, strategy_version=strategy.strategy_version,
            environment=context.environment, result=result, reasons=tuple(reasons),
            evaluated_at=moment, correlation_id=correlation_id, symbol=symbol,
        )
        eligibility.validate()
        return eligibility


@dataclass(frozen=True)
class KillCriteriaEvaluation:
    strategy_id: str
    strategy_version: str
    criteria_version: str
    result: KillResult
    triggered: tuple
    evidence: Mapping[str, Any]
    evaluated_at: datetime
    environment: str
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("strategy_id", self.strategy_id, location="kill.strategy_id")
        if not isinstance(self.result, KillResult):
            raise ContractValidationError(
                "kill.result must be a KillResult", location="kill.result",
                rule_id="SCHEMA-ENUM",
            )
        SemVer.parse(self.criteria_version, location="kill.criteria_version")
        ensure_utc(self.evaluated_at, location="kill.evaluated_at")


class KillCriteriaEvaluator:
    """Evaluates versioned kill criteria rules against supplied evidence
    (context + strategy health + capacity). Evidence arrives via contract;
    drift/execution-quality boundaries are INPUTS computed elsewhere.
    Severity mapping: kill -> KILL_REQUIRED, suspend -> SUSPEND_REQUIRED,
    warn -> WARNING. Strategies cannot self-override (no override path)."""

    SEVERITY_RANK = {KillResult.ACTIVE: 0, KillResult.WARNING: 1,
                     KillResult.SUSPEND_REQUIRED: 2, KillResult.KILL_REQUIRED: 3}

    def evaluate(
        self, *, strategy_id: str, strategy_version: str, criteria_version: str,
        rules: Iterable[Mapping[str, Any]], evidence: Mapping[str, Any],
        environment: str, at: datetime,
    ) -> KillCriteriaEvaluation:
        from core.strategy.evaluation import compare as cmp

        worst = KillResult.ACTIVE
        triggered: list[Mapping[str, Any]] = []
        for rule in rules:
            data = dict(rule)
            operator = str(data.get("op", ">="))
            if operator not in OPERATORS:
                raise ContractValidationError(
                    f"Kill rule {data.get('rule_id', '?')} uses unknown operator",
                    location="kill.rules",
                )
            present, raw = _resolve(evidence, str(data.get("field", "")))
            if not present or raw is None:
                # unknown evidence for a kill criterion is treated as WARNING
                # (observable, never silently ACTIVE when watching for failures)
                if worst is KillResult.ACTIVE:
                    worst = KillResult.WARNING
                triggered.append({**data, "state": "unknown_evidence"})
                continue
            try:
                left = _typed(raw, operator)
                right = _typed(data.get("value"), operator)
                violated = not cmp(left, right, operator)
            except ContractValidationError:
                triggered.append({**data, "state": "uncomparable"})
                continue
            if violated:
                severity = KillResult(str(data.get("on_trigger", "KILL_REQUIRED")))
                triggered.append({**data, "state": "triggered"})
                if self.SEVERITY_RANK[severity] > self.SEVERITY_RANK[worst]:
                    worst = severity
        evaluation = KillCriteriaEvaluation(
            strategy_id=strategy_id, strategy_version=strategy_version,
            criteria_version=criteria_version, result=worst,
            triggered=tuple(triggered), evidence=dict(evidence),
            evaluated_at=ensure_utc(at, location="kill.at"), environment=environment,
        )
        evaluation.validate()
        return evaluation


@dataclass(frozen=True)
class StrategyHealth:
    strategy_id: str
    operational_state: str
    data_state: str
    risk_state: str
    lifecycle_state: StrategyLifecycle
    health_status: HealthStatus
    capacity_status: str
    environment: str
    failure_count: int = 0
    anomaly_count: int = 0
    last_evaluation: datetime | None = None
    stale_since: datetime | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("strategy_id", self.strategy_id, location="health.strategy_id")
        for name in ("operational_state", "data_state", "risk_state", "capacity_status"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"health.{name} must be a non-empty string", location=f"health.{name}",
                )
        if not isinstance(self.lifecycle_state, StrategyLifecycle):
            raise ContractValidationError(
                "health.lifecycle_state must be a StrategyLifecycle",
                location="health.lifecycle_state", rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.health_status, HealthStatus):
            raise ContractValidationError(
                "health.health_status must be a HealthStatus",
                location="health.health_status", rule_id="SCHEMA-ENUM",
            )
        for name in ("failure_count", "anomaly_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ContractValidationError(
                    f"health.{name} must be a non-negative integer",
                    location=f"health.{name}",
                )
        if self.last_evaluation is not None:
            ensure_utc(self.last_evaluation, location="health.last_evaluation")
        if self.stale_since is not None:
            ensure_utc(self.stale_since, location="health.stale_since")
