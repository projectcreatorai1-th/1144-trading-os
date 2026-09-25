"""Strategy intent contract (owned by core.strategy).

INTENT != SIGNAL != ORDER (SECTION 11): a signal is an observation, an
intent is a proposed action, risk grants permission, orders are Phase 5."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_any_identifier, validate_identifier
from architecture.contracts.time import ensure_not_before, ensure_utc
from architecture.contracts.versioning import SemVer
from core.ledger.money import parse_decimal

CONTRACT_VERSION = "1.0.0"


class IntentType(Enum):
    OPEN = "OPEN"
    INCREASE = "INCREASE"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    HOLD = "HOLD"
    CANCEL_INTENT = "CANCEL_INTENT"


class IntentDirection(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class Urgency(Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


RISK_INCREASING_INTENTS = (IntentType.OPEN, IntentType.INCREASE)


@dataclass(frozen=True)
class StrategyIntent:
    intent_id: str
    strategy_id: str
    strategy_version: str
    intent_type: IntentType
    symbol: str
    direction: IntentDirection
    requested_quantity: str
    entry_conditions: tuple
    exit_conditions: tuple
    urgency: Urgency
    rationale: str
    risk_context_hash: str
    source_event_id: str
    correlation_id: str
    environment: str
    created_at: datetime
    expires_at: datetime
    requested_notional: str | None = None
    confidence: str | None = None
    policy_id: str | None = None
    policy_version: str | None = None
    causation_id: str | None = None
    provenance: Any | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("intent_id", self.intent_id, location="intent.intent_id")
        validate_identifier("strategy_id", self.strategy_id, location="intent.strategy_id")
        SemVer.parse(self.strategy_version, location="intent.strategy_version")
        for name, enum_type in (("intent_type", IntentType), ("direction", IntentDirection),
                                ("urgency", Urgency)):
            if not isinstance(getattr(self, name), enum_type):
                raise ContractValidationError(
                    f"intent.{name} must be a {enum_type.__name__}",
                    location=f"intent.{name}", rule_id="SCHEMA-ENUM",
                )
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ContractValidationError(
                "intent.symbol must be a non-empty string", location="intent.symbol",
            )
        if self.direction is IntentDirection.FLAT and self.intent_type in RISK_INCREASING_INTENTS:
            raise ContractValidationError(
                "FLAT direction cannot carry risk-increasing intents (OPEN/INCREASE)",
                location="intent.direction", rule_id="INTENT-001",
            )
        quantity = parse_decimal(self.requested_quantity, location="intent.requested_quantity")
        if quantity < 0:
            raise ContractValidationError(
                "intent.requested_quantity must be >= 0", location="intent.requested_quantity",
            )
        if quantity == 0 and self.intent_type in RISK_INCREASING_INTENTS:
            raise ContractValidationError(
                "risk-increasing intents require a positive requested_quantity",
                location="intent.requested_quantity", rule_id="INTENT-001",
            )
        if self.requested_notional is not None:
            parse_decimal(self.requested_notional, location="intent.requested_notional")
        for name in ("entry_conditions", "exit_conditions"):
            if not isinstance(getattr(self, name), tuple):
                raise ContractValidationError(
                    f"intent.{name} must be a tuple", location=f"intent.{name}",
                )
        if not isinstance(self.rationale, str) or not self.rationale:
            raise ContractValidationError(
                "intent.rationale is required (explainability from evidence)",
                location="intent.rationale", rule_id="TRACE-001",
            )
        if not isinstance(self.risk_context_hash, str) or len(self.risk_context_hash) != 64:
            raise ContractValidationError(
                "intent.risk_context_hash must be a sha-256 hex string",
                location="intent.risk_context_hash",
            )
        validate_identifier("event_id", self.source_event_id, location="intent.source_event_id")
        validate_any_identifier(self.correlation_id, location="intent.correlation_id")
        parse_environment(self.environment, location="intent.environment")
        created = ensure_utc(self.created_at, location="intent.created_at")
        ensure_not_before(self.expires_at, not_before=created, location="intent.expires_at")
        if self.policy_id is not None:
            validate_identifier("policy_id", self.policy_id, location="intent.policy_id")
        if self.policy_version is not None:
            SemVer.parse(self.policy_version, location="intent.policy_version")
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="intent.causation_id")

    def is_expired(self, now: datetime) -> bool:
        moment = ensure_utc(now, location="intent.now")
        return ensure_utc(self.expires_at, location="intent.expires_at") <= moment

    def is_risk_increasing(self) -> bool:
        return self.intent_type in RISK_INCREASING_INTENTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id, "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "intent_type": self.intent_type.value, "symbol": self.symbol,
            "direction": self.direction.value,
            "requested_quantity": self.requested_quantity,
            "requested_notional": self.requested_notional,
            "entry_conditions": list(self.entry_conditions),
            "exit_conditions": list(self.exit_conditions),
            "urgency": self.urgency.value, "confidence": self.confidence,
            "rationale": self.rationale, "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "risk_context_hash": self.risk_context_hash,
            "source_event_id": self.source_event_id,
            "correlation_id": self.correlation_id, "causation_id": self.causation_id,
            "environment": self.environment,
            "created_at": ensure_utc(self.created_at).isoformat(),
            "expires_at": ensure_utc(self.expires_at).isoformat(),
        }
