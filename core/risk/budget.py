"""Risk budget (owned by core.risk).

Centralized budget arithmetic: remaining = allocated - used; projected =
used + requested. Computed HERE only - subsystems never reimplement it
(SECTION 18). Decimal strings throughout."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_identifier
from core.ledger.money import parse_decimal

CONTRACT_VERSION = "1.0.0"


class BudgetScope(Enum):
    ACCOUNT = "ACCOUNT"
    STRATEGY = "STRATEGY"
    SYMBOL = "SYMBOL"
    DIRECTION = "DIRECTION"
    PORTFOLIO = "PORTFOLIO"


@dataclass(frozen=True)
class RiskBudget:
    risk_budget_id: str
    scope_type: BudgetScope
    scope_id: str
    allocated_risk: str
    used_risk: str
    requested_risk: str
    environment: str
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("risk_budget_id", self.risk_budget_id, location="budget.risk_budget_id")
        if not isinstance(self.scope_type, BudgetScope):
            raise ContractValidationError(
                f"budget.scope_type must be a BudgetScope, got {self.scope_type!r}",
                location="budget.scope_type",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.scope_id, str) or not self.scope_id:
            raise ContractValidationError(
                "budget.scope_id must be a non-empty string",
                location="budget.scope_id",
            )
        for name in ("allocated_risk", "used_risk", "requested_risk"):
            value = parse_decimal(getattr(self, name), location=f"budget.{name}")
            if value < 0:
                raise ContractValidationError(
                    f"budget.{name} must be >= 0",
                    location=f"budget.{name}",
                )
        parse_environment(self.environment, location="budget.environment")

    # --- centralized arithmetic (single source; nothing else computes these) ---
    def remaining_risk(self) -> Decimal:
        return parse_decimal(self.allocated_risk, location="budget.allocated") - \
            parse_decimal(self.used_risk, location="budget.used")

    def projected_risk(self) -> Decimal:
        return parse_decimal(self.used_risk, location="budget.used") + \
            parse_decimal(self.requested_risk, location="budget.requested")

    def requested_within_budget(self) -> bool:
        return self.projected_risk() <= parse_decimal(self.allocated_risk, location="budget.allocated")


def build_budget(*, scope_type: BudgetScope, scope_id: str, allocated: Any,
                 used: Any, requested: Any, environment: str,
                 risk_budget_id: str) -> RiskBudget:
    from architecture.contracts.identifiers import new_identifier

    budget = RiskBudget(
        risk_budget_id=risk_budget_id or new_identifier("risk_budget_id"),
        scope_type=scope_type,
        scope_id=scope_id,
        allocated_risk=str(parse_decimal(allocated, location="budget.allocated")),
        used_risk=str(parse_decimal(used, location="budget.used")),
        requested_risk=str(parse_decimal(requested, location="budget.requested")),
        environment=environment,
    )
    budget.validate()
    return budget
