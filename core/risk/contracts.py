"""Risk contract (owned by core.risk).

Risk decisions are explicit (ALLOW/LIMITED/BLOCK/CLOSE_ONLY/EMERGENCY),
never booleans. UNKNOWN market/data states fail closed: they can only lead
to blocking decisions (RULE 010/011/019). AI confidence is never a risk
permission (RULE 008).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import Environment, parse_environment
from architecture.contracts.errors import ContractValidationError, RiskGateError
from architecture.contracts.identifiers import validate_any_identifier, validate_identifier
from architecture.contracts.time import ensure_not_before, ensure_utc, utc_now
from architecture.contracts.versioning import SemVer
from core.validation.contracts import DataQualityLevel

CONTRACT_VERSION = "1.2.0"

EXECUTABLE_RESULTS = ("ALLOW", "LIMITED")
BLOCKING_RESULTS = ("BLOCK", "CLOSE_ONLY", "EMERGENCY")


class RiskResult(Enum):
    ALLOW = "ALLOW"
    LIMITED = "LIMITED"
    BLOCK = "BLOCK"
    CLOSE_ONLY = "CLOSE_ONLY"
    EMERGENCY = "EMERGENCY"

    @property
    def executable(self) -> bool:
        return self.value in EXECUTABLE_RESULTS


class RiskState(Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    LIMITED = "LIMITED"
    PAUSE = "PAUSE"
    EMERGENCY = "EMERGENCY"


class MarketState(Enum):
    CALM = "CALM"
    NORMAL = "NORMAL"
    VOLATILE = "VOLATILE"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RiskDecision:
    risk_decision_id: str
    environment: str
    scope: str
    decision: RiskResult
    reasons: tuple[str, ...]
    limits: Mapping[str, Any]
    current_exposure: float
    requested_exposure: float
    risk_metrics: Mapping[str, Any]
    risk_state: RiskState
    market_state: MarketState
    data_quality: DataQualityLevel
    confidence: float
    policy_reference: str
    correlation_id: str
    decision_time: datetime
    expires_at: datetime
    causation_id: str | None = None
    action_type: str | None = None
    subject: str | None = None
    account_id: str | None = None
    symbol: str | None = None
    strategy_id: str | None = None
    evaluated_rules: tuple = ()
    triggered_rules: tuple = ()
    blocked_rules: tuple = ()
    risk_context_hash: str | None = None
    policy_hash: str | None = None
    provenance: object | None = None
    permission_constraints: Mapping[str, Any] | None = None
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier(
            "risk_decision_id", self.risk_decision_id, location="risk.risk_decision_id"
        )
        parse_environment(self.environment, location="risk.environment")
        if not isinstance(self.scope, str) or not self.scope:
            raise ContractValidationError(
                "risk.scope must be a non-empty string",
                location="risk.scope",
            )
        if not isinstance(self.decision, RiskResult):
            raise ContractValidationError(
                f"risk.decision must be a RiskResult, got {self.decision!r} "
                "(booleans are not valid risk decisions)",
                location="risk.decision",
                rule_id="SCHEMA-ENUM",
            )
        if not self.reasons or not all(isinstance(r, str) and r for r in self.reasons):
            raise ContractValidationError(
                "risk.reasons must be a non-empty sequence of strings (traceability)",
                location="risk.reasons",
                rule_id="TRACE-001",
            )
        if not isinstance(self.limits, Mapping) or not self.limits:
            raise ContractValidationError(
                "risk.limits must be a non-empty mapping",
                location="risk.limits",
            )
        for name in ("current_exposure", "requested_exposure"):
            value = getattr(self, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
            ):
                raise ContractValidationError(
                    f"risk.{name} must be a non-negative number",
                    location=f"risk.{name}",
                )
        if not isinstance(self.risk_metrics, Mapping):
            raise ContractValidationError(
                "risk.risk_metrics must be a mapping",
                location="risk.risk_metrics",
            )
        if not isinstance(self.risk_state, RiskState):
            raise ContractValidationError(
                f"risk.risk_state must be a RiskState, got {self.risk_state!r}",
                location="risk.risk_state",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.market_state, MarketState):
            raise ContractValidationError(
                f"risk.market_state must be a MarketState, got {self.market_state!r}",
                location="risk.market_state",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.data_quality, DataQualityLevel):
            raise ContractValidationError(
                f"risk.data_quality must be a DataQualityLevel, got {self.data_quality!r}",
                location="risk.data_quality",
                rule_id="SCHEMA-ENUM",
            )
        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise ContractValidationError(
                "risk.confidence must be within [0.0, 1.0]; confidence never substitutes risk permission",
                location="risk.confidence",
            )
        if not isinstance(self.policy_reference, str) or not self.policy_reference:
            raise ContractValidationError(
                "risk.policy_reference must be a non-empty string (policy_id@version)",
                location="risk.policy_reference",
            )
        validate_any_identifier(self.correlation_id, location="risk.correlation_id")
        decision_time = ensure_utc(self.decision_time, location="risk.decision_time")
        ensure_not_before(
            self.expires_at, not_before=decision_time, location="risk.expires_at"
        )
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="risk.causation_id")
        for name in ("action_type", "subject", "account_id", "symbol", "strategy_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ContractValidationError(
                    f"risk.{name} must be a non-empty string or None",
                    location=f"risk.{name}",
                )
        for name in ("evaluated_rules", "triggered_rules", "blocked_rules"):
            if not isinstance(getattr(self, name), tuple):
                raise ContractValidationError(
                    f"risk.{name} must be a tuple",
                    location=f"risk.{name}",
                )
        for name in ("risk_context_hash", "policy_hash"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or len(value) != 64):
                raise ContractValidationError(
                    f"risk.{name} must be a sha-256 hex string or None",
                    location=f"risk.{name}",
                )
        if self.permission_constraints is not None and not isinstance(self.permission_constraints, Mapping):
            raise ContractValidationError(
                "risk.permission_constraints must be a mapping or None",
                location="risk.permission_constraints",
            )
        SemVer.parse(self.contract_version, location="risk.contract_version")

        # Fail-closed rules (RULE 010/011): UNKNOWN can never produce SAFE results.
        if (
            self.market_state is MarketState.UNKNOWN
            or self.data_quality.blocks_risk_allowance
        ) and self.decision.value not in BLOCKING_RESULTS:
            raise ContractValidationError(
                "UNKNOWN market/data state must lead to a blocking decision "
                f"({'/'.join(BLOCKING_RESULTS)}); got {self.decision.value}",
                location="risk.decision",
                rule_id="RISK-GATE",
                details={
                    "market_state": self.market_state.value,
                    "data_quality": self.data_quality.value,
                    "decision": self.decision.value,
                },
            )


    def to_dict_full(self) -> dict[str, Any]:
        """Full serialization for storage (all contract 1.2.0 fields)."""
        data = self.to_dict()
        data.update({
            "action_type": self.action_type,
            "subject": self.subject,
            "account_id": self.account_id,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "evaluated_rules": list(self.evaluated_rules),
            "triggered_rules": [dict(r) if isinstance(r, Mapping) else r for r in self.triggered_rules],
            "blocked_rules": [dict(r) if isinstance(r, Mapping) else r for r in self.blocked_rules],
            "risk_context_hash": self.risk_context_hash,
            "policy_hash": self.policy_hash,
            "permission_constraints": dict(self.permission_constraints)
            if self.permission_constraints else None,
            "contract_version": self.contract_version,
        })
        return data

    @classmethod
    def from_storage(cls, data: Mapping[str, Any]) -> "RiskDecision":
        from architecture.contracts.time import parse_canonical

        decision = cls(
            risk_decision_id=data["risk_decision_id"],
            environment=data["environment"],
            scope=data["scope"],
            decision=RiskResult(data["decision"]),
            reasons=tuple(data["reasons"]),
            limits=dict(data["limits"]),
            current_exposure=data["current_exposure"],
            requested_exposure=data["requested_exposure"],
            risk_metrics=dict(data["risk_metrics"]),
            risk_state=RiskState(data["risk_state"]),
            market_state=MarketState(data["market_state"]),
            data_quality=DataQualityLevel(data["data_quality"]),
            confidence=data["confidence"],
            policy_reference=data["policy_reference"],
            correlation_id=data["correlation_id"],
            decision_time=parse_canonical(data["decision_time"]),
            expires_at=parse_canonical(data["expires_at"]),
            causation_id=data.get("causation_id"),
            action_type=data.get("action_type"),
            subject=data.get("subject"),
            account_id=data.get("account_id"),
            symbol=data.get("symbol"),
            strategy_id=data.get("strategy_id"),
            evaluated_rules=tuple(data.get("evaluated_rules", ())),
            triggered_rules=tuple(data.get("triggered_rules", ())),
            blocked_rules=tuple(data.get("blocked_rules", ())),
            risk_context_hash=data.get("risk_context_hash"),
            policy_hash=data.get("policy_hash"),
            provenance=data.get("provenance"),
            permission_constraints=data.get("permission_constraints"),
        )
        decision.validate()
        return decision

    def is_expired(self, now: datetime | None = None) -> bool:
        moment = ensure_utc(now, location="risk.now") if now else utc_now()
        return ensure_utc(self.expires_at, location="risk.expires_at") <= moment

    def is_executable(self, order_environment: Environment | str, *, now: datetime | None = None) -> bool:
        """True only when: executable result + not expired + same environment."""
        environment = parse_environment(order_environment, location="risk.order_environment")
        if parse_environment(self.environment, location="risk.environment") is not environment:
            return False
        if not self.decision.executable:
            return False
        return not self.is_expired(now)

    def assert_executable(self, order_environment: Environment | str, *, now: datetime | None = None) -> None:
        """Raise a structured RiskGateError explaining why execution is blocked."""
        environment = parse_environment(order_environment, location="risk.order_environment")
        if not self.decision.executable:
            raise RiskGateError(
                f"Risk decision {self.risk_decision_id} result {self.decision.value} "
                "does not permit execution (RULE 009)",
                location="risk.gate",
                details={"risk_decision_id": self.risk_decision_id, "decision": self.decision.value},
            )
        if parse_environment(self.environment, location="risk.environment") is not environment:
            raise RiskGateError(
                f"Risk decision {self.risk_decision_id} environment {self.environment} "
                f"does not match order environment {environment.value} (fail closed)",
                location="risk.gate",
                rule_id="ENV-001",
                details={"risk_environment": self.environment, "order_environment": environment.value},
            )
        if self.is_expired(now):
            raise RiskGateError(
                f"Risk decision {self.risk_decision_id} expired at "
                f"{ensure_utc(self.expires_at).isoformat()}",
                location="risk.gate",
                rule_id="RISK-EXPIRED",
                details={"expires_at": ensure_utc(self.expires_at).isoformat()},
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_decision_id": self.risk_decision_id,
            "environment": self.environment,
            "scope": self.scope,
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "limits": dict(self.limits),
            "current_exposure": self.current_exposure,
            "requested_exposure": self.requested_exposure,
            "risk_metrics": dict(self.risk_metrics),
            "risk_state": self.risk_state.value,
            "market_state": self.market_state.value,
            "data_quality": self.data_quality.value,
            "confidence": self.confidence,
            "policy_reference": self.policy_reference,
            "correlation_id": self.correlation_id,
            "decision_time": ensure_utc(self.decision_time).isoformat(),
            "expires_at": ensure_utc(self.expires_at).isoformat(),
        }
