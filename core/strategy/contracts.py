"""Strategy contracts (owned by core.strategy).

Strategy proposes trading INTENT - never orders, never risk verdicts.
Versions are immutable; semantic changes create new versions (SECTION 4)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Tuple

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.time import ensure_not_before, ensure_utc
from architecture.contracts.versioning import SemVer

CONTRACT_VERSION = "1.0.0"


class StrategyType(Enum):
    TREND = "TREND"
    MEAN_REVERSION = "MEAN_REVERSION"
    BREAKOUT = "BREAKOUT"
    MOMENTUM = "MOMENTUM"
    GRID = "GRID"
    SCALPING = "SCALPING"
    NEWS_REACTION = "NEWS_REACTION"
    ARBITRAGE_BOUNDARY = "ARBITRAGE_BOUNDARY"
    CUSTOM = "CUSTOM"


class StrategyLifecycle(Enum):
    IDEA = "IDEA"
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    ROBUSTNESS = "ROBUSTNESS"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    REPLAY = "REPLAY"
    PAPER = "PAPER"
    DEMO = "DEMO"
    FORWARD = "FORWARD"
    APPROVED = "APPROVED"
    LIVE = "LIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


#: Lifecycle statuses permitted to propose intents (environment-gated separately).
INTENT_ELIGIBLE_LIFECYCLE = (StrategyLifecycle.LIVE, StrategyLifecycle.FORWARD,
                             StrategyLifecycle.DEMO, StrategyLifecycle.PAPER,
                             StrategyLifecycle.REPLAY)


class Directional(Enum):
    LONG_ONLY = "LONG_ONLY"
    SHORT_ONLY = "SHORT_ONLY"
    BOTH = "BOTH"


def config_hash(parameters: Mapping[str, Any], units: Mapping[str, Any],
                constraints: Mapping[str, Any]) -> str:
    material = json.dumps(
        {"parameters": dict(parameters), "units": dict(units), "constraints": dict(constraints)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CapabilityProfile:
    capability_profile_id: str
    strategy_type: StrategyType
    supported_symbols: Tuple[str, ...]
    supported_markets: Tuple[str, ...]
    supported_environments: Tuple[str, ...]
    partial_close_capability: bool
    basket_capability: bool
    hedge_capability: bool
    directional_capability: Directional
    news_sensitivity: str
    volatility_sensitivity: str
    spread_sensitivity: str
    liquidity_requirement: str
    provenance: Mapping[str, Any]
    max_positions: int | None = None
    max_grid_depth: int | None = None
    max_observed_lot: str | None = None
    max_exposure: str | None = None
    max_risk: str | None = None
    recovery_behavior: str | None = None
    margin_requirement: str | None = None
    capacity_estimate: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.capability_profile_id, str) or not self.capability_profile_id:
            raise ContractValidationError(
                "capability.capability_profile_id must be a non-empty string",
                location="capability.capability_profile_id",
            )
        if not isinstance(self.strategy_type, StrategyType):
            raise ContractValidationError(
                f"capability.strategy_type must be a StrategyType, got {self.strategy_type!r}",
                location="capability.strategy_type", rule_id="SCHEMA-ENUM",
            )
        for name in ("supported_symbols", "supported_markets", "supported_environments"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or not value or \
                    not all(isinstance(item, str) and item for item in value):
                raise ContractValidationError(
                    f"capability.{name} must be a non-empty tuple of strings",
                    location=f"capability.{name}",
                )
        for name in ("partial_close_capability", "basket_capability", "hedge_capability"):
            if not isinstance(getattr(self, name), bool):
                raise ContractValidationError(
                    f"capability.{name} must be a boolean",
                    location=f"capability.{name}",
                )
        if not isinstance(self.directional_capability, Directional):
            raise ContractValidationError(
                "capability.directional_capability must be a Directional",
                location="capability.directional_capability", rule_id="SCHEMA-ENUM",
            )
        for name in ("news_sensitivity", "volatility_sensitivity", "spread_sensitivity",
                     "liquidity_requirement"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"capability.{name} must be a non-empty string",
                    location=f"capability.{name}",
                )
        if not isinstance(self.provenance, Mapping) or not self.provenance:
            raise ContractValidationError(
                "capability.provenance is required (observations must be traceable)",
                location="capability.provenance", rule_id="PROV-001",
            )
        for name in ("max_positions", "max_grid_depth"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ContractValidationError(
                    f"capability.{name} must be a non-negative integer or None",
                    location=f"capability.{name}",
                )
        for env_name in self.supported_environments:
            parse_environment(env_name, location="capability.supported_environments")
        from core.ledger.money import parse_decimal

        for name in ("max_observed_lot", "max_exposure", "max_risk", "margin_requirement"):
            value = getattr(self, name)
            if value is not None:
                parse_decimal(value, location=f"capability.{name}")

    def supports_symbol(self, symbol: str) -> bool:
        return "*" in self.supported_symbols or symbol in self.supported_symbols

    def supports_environment(self, environment: str) -> bool:
        return environment in self.supported_environments


@dataclass(frozen=True)
class StrategyConfig:
    strategy_id: str
    strategy_version: str
    parameters: Mapping[str, Any]
    units: Mapping[str, Any]
    constraints: Mapping[str, Any]
    environment: str
    effective_from: datetime
    provenance: Mapping[str, Any]
    config_hash: str
    effective_to: datetime | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("strategy_id", self.strategy_id, location="config.strategy_id")
        SemVer.parse(self.strategy_version, location="config.strategy_version")
        for name in ("parameters", "units", "constraints", "provenance"):
            if not isinstance(getattr(self, name), Mapping):
                raise ContractValidationError(
                    f"config.{name} must be a mapping", location=f"config.{name}",
                )
        if not self.parameters:
            raise ContractValidationError(
                "config.parameters must not be empty", location="config.parameters",
            )
        parse_environment(self.environment, location="config.environment")
        ensure_utc(self.effective_from, location="config.effective_from")
        if self.effective_to is not None:
            ensure_not_before(self.effective_to, not_before=self.effective_from,
                              location="config.effective_to")
        if not isinstance(self.provenance, Mapping) or not self.provenance:
            raise ContractValidationError(
                "config.provenance is required", location="config.provenance",
            )
        expected = config_hash(self.parameters, self.units, self.constraints)
        if self.config_hash != expected:
            raise ContractValidationError(
                "config.config_hash mismatch (integrity violation)",
                location="config.config_hash", details={"expected": expected},
            )


@dataclass(frozen=True)
class Strategy:
    strategy_id: str
    strategy_version: str
    strategy_type: StrategyType
    name: str
    owner: str
    lifecycle_status: StrategyLifecycle
    environment: str
    effective_from: datetime
    created_at: datetime
    updated_at: datetime
    configuration_version: str
    capability_profile_id: str
    description: str | None = None
    effective_to: datetime | None = None
    risk_budget_id: str | None = None
    policy_id: str | None = None
    policy_version: str | None = None
    provenance: Any | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("strategy_id", self.strategy_id, location="strategy.strategy_id")
        SemVer.parse(self.strategy_version, location="strategy.strategy_version")
        if not isinstance(self.strategy_type, StrategyType):
            raise ContractValidationError(
                "strategy.strategy_type must be a StrategyType",
                location="strategy.strategy_type", rule_id="SCHEMA-ENUM",
            )
        for name in ("name", "owner"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"strategy.{name} must be a non-empty string", location=f"strategy.{name}",
                )
        if not isinstance(self.lifecycle_status, StrategyLifecycle):
            raise ContractValidationError(
                "strategy.lifecycle_status must be a StrategyLifecycle",
                location="strategy.lifecycle_status", rule_id="SCHEMA-ENUM",
            )
        parse_environment(self.environment, location="strategy.environment")
        ensure_utc(self.effective_from, location="strategy.effective_from")
        created = ensure_utc(self.created_at, location="strategy.created_at")
        ensure_not_before(self.updated_at, not_before=created, location="strategy.updated_at")
        if self.effective_to is not None:
            ensure_not_before(self.effective_to, not_before=self.effective_from,
                              location="strategy.effective_to")
        SemVer.parse(self.configuration_version, location="strategy.configuration_version")
        if not isinstance(self.capability_profile_id, str) or not self.capability_profile_id:
            raise ContractValidationError(
                "strategy.capability_profile_id must be a non-empty string",
                location="strategy.capability_profile_id",
            )
        if self.lifecycle_status is StrategyLifecycle.LIVE:
            if self.policy_id is None or self.policy_version is None or self.risk_budget_id is None:
                raise ContractValidationError(
                    "LIVE strategies require policy and risk budget references",
                    location="strategy.live_prerequisites", rule_id="STRATEGY-002",
                )
        if self.risk_budget_id is not None:
            validate_identifier("risk_budget_id", self.risk_budget_id,
                                location="strategy.risk_budget_id")
        if self.policy_id is not None:
            validate_identifier("policy_id", self.policy_id, location="strategy.policy_id")
        if self.policy_version is not None:
            SemVer.parse(self.policy_version, location="strategy.policy_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id, "strategy_version": self.strategy_version,
            "strategy_type": self.strategy_type.value, "name": self.name,
            "description": self.description, "owner": self.owner,
            "lifecycle_status": self.lifecycle_status.value, "environment": self.environment,
            "effective_from": ensure_utc(self.effective_from).isoformat(),
            "effective_to": ensure_utc(self.effective_to).isoformat() if self.effective_to else None,
            "configuration_version": self.configuration_version,
            "capability_profile_id": self.capability_profile_id,
            "risk_budget_id": self.risk_budget_id, "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "created_at": ensure_utc(self.created_at).isoformat(),
            "updated_at": ensure_utc(self.updated_at).isoformat(),
        }

    @classmethod
    def from_storage(cls, data: Mapping[str, Any]) -> "Strategy":
        from architecture.contracts.time import parse_canonical

        strategy = cls(
            strategy_id=data["strategy_id"], strategy_version=data["strategy_version"],
            strategy_type=StrategyType(data["strategy_type"]), name=data["name"],
            description=data.get("description"), owner=data["owner"],
            lifecycle_status=StrategyLifecycle(data["lifecycle_status"]),
            environment=data["environment"],
            effective_from=parse_canonical(data["effective_from"]),
            effective_to=parse_canonical(data["effective_to"]) if data.get("effective_to") else None,
            configuration_version=data["configuration_version"],
            capability_profile_id=data["capability_profile_id"],
            risk_budget_id=data.get("risk_budget_id"), policy_id=data.get("policy_id"),
            policy_version=data.get("policy_version"),
            created_at=parse_canonical(data["created_at"]),
            updated_at=parse_canonical(data["updated_at"]),
        )
        strategy.validate()
        return strategy
