"""Portfolio contracts (owned by core.portfolio).

Portfolio manages strategy/capital/exposure ALLOCATION - it never overrides
RiskDecisions and never sends orders (SECTION 3/17-19)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.time import ensure_not_before, ensure_utc
from architecture.contracts.versioning import SemVer
from core.ledger.money import parse_decimal

CONTRACT_VERSION = "1.0.0"


class PortfolioStatus(Enum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class AllocationPolicyType(Enum):
    EQUAL = "EQUAL"
    FIXED = "FIXED"
    RISK_BASED = "RISK_BASED"  # boundary only: consumes declared risk budgets
    PRIORITY = "PRIORITY"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class Portfolio:
    portfolio_id: str
    portfolio_version: str
    name: str
    account_scope: str
    environment: str
    status: PortfolioStatus
    base_currency: str
    strategy_members: tuple
    allocation_policy_id: str
    effective_from: datetime
    created_at: datetime
    effective_to: datetime | None = None
    risk_policy_id: str | None = None
    provenance: Any | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("portfolio_id", self.portfolio_id, location="portfolio.portfolio_id")
        SemVer.parse(self.portfolio_version, location="portfolio.portfolio_version")
        for name in ("name", "account_scope", "allocation_policy_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"portfolio.{name} must be a non-empty string", location=f"portfolio.{name}",
                )
        parse_environment(self.environment, location="portfolio.environment")
        if not isinstance(self.status, PortfolioStatus):
            raise ContractValidationError(
                "portfolio.status must be a PortfolioStatus",
                location="portfolio.status", rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.base_currency, str) or not self.base_currency:
            raise ContractValidationError(
                "portfolio.base_currency must be a non-empty currency code",
                location="portfolio.base_currency",
            )
        if not isinstance(self.strategy_members, tuple):
            raise ContractValidationError(
                "portfolio.strategy_members must be a tuple", location="portfolio.strategy_members",
            )
        ensure_utc(self.effective_from, location="portfolio.effective_from")
        ensure_utc(self.created_at, location="portfolio.created_at")
        if self.effective_to is not None:
            ensure_not_before(self.effective_to, not_before=self.effective_from,
                              location="portfolio.effective_to")
        if self.risk_policy_id is not None:
            validate_identifier("policy_id", self.risk_policy_id, location="portfolio.risk_policy_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id, "portfolio_version": self.portfolio_version,
            "name": self.name, "account_scope": self.account_scope,
            "environment": self.environment, "status": self.status.value,
            "base_currency": self.base_currency, "strategy_members": list(self.strategy_members),
            "allocation_policy_id": self.allocation_policy_id,
            "risk_policy_id": self.risk_policy_id,
            "effective_from": ensure_utc(self.effective_from).isoformat(),
            "effective_to": ensure_utc(self.effective_to).isoformat() if self.effective_to else None,
            "created_at": ensure_utc(self.created_at).isoformat(),
        }

    @classmethod
    def from_storage(cls, data: Mapping[str, Any]) -> "Portfolio":
        from architecture.contracts.time import parse_canonical

        portfolio = cls(
            portfolio_id=data["portfolio_id"], portfolio_version=data["portfolio_version"],
            name=data["name"], account_scope=data["account_scope"],
            environment=data["environment"], status=PortfolioStatus(data["status"]),
            base_currency=data["base_currency"],
            strategy_members=tuple(data["strategy_members"]),
            allocation_policy_id=data["allocation_policy_id"],
            risk_policy_id=data.get("risk_policy_id"),
            effective_from=parse_canonical(data["effective_from"]),
            effective_to=parse_canonical(data["effective_to"]) if data.get("effective_to") else None,
            created_at=parse_canonical(data["created_at"]),
        )
        portfolio.validate()
        return portfolio


@dataclass(frozen=True)
class PortfolioMembership:
    portfolio_id: str
    strategy_id: str
    strategy_version: str
    allocation: str
    risk_budget: str
    priority: int
    enabled: bool
    effective_from: datetime
    environment: str
    effective_to: datetime | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("portfolio_id", self.portfolio_id, location="membership.portfolio_id")
        validate_identifier("strategy_id", self.strategy_id, location="membership.strategy_id")
        SemVer.parse(self.strategy_version, location="membership.strategy_version")
        for name in ("allocation", "risk_budget"):
            value = parse_decimal(getattr(self, name), location=f"membership.{name}")
            if value < 0:
                raise ContractValidationError(
                    f"membership.{name} must be >= 0", location=f"membership.{name}",
                )
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise ContractValidationError(
                "membership.priority must be an explicit integer (no implicit ordering)",
                location="membership.priority", rule_id="PORTFOLIO-002",
            )
        if not isinstance(self.enabled, bool):
            raise ContractValidationError(
                "membership.enabled must be a boolean", location="membership.enabled",
            )
        parse_environment(self.environment, location="membership.environment")
        ensure_utc(self.effective_from, location="membership.effective_from")
        if self.effective_to is not None:
            ensure_not_before(self.effective_to, not_before=self.effective_from,
                              location="membership.effective_to")

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id, "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version, "allocation": self.allocation,
            "risk_budget": self.risk_budget, "priority": self.priority,
            "enabled": self.enabled,
            "effective_from": ensure_utc(self.effective_from).isoformat(),
            "effective_to": ensure_utc(self.effective_to).isoformat() if self.effective_to else None,
            "environment": self.environment,
        }
