"""Capital + risk budget allocation (owned by core.portfolio).

Centralized arithmetic: available = total - reserved; allocated = sum(strategy
allocations); remaining = available - allocated. Allocation beyond available
capital is REJECTED or constrained per explicit policy behavior - never
silently clipped. Risk budgets reuse Phase 3 RiskBudget (never duplicated)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Mapping

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc
from core.ledger.money import parse_decimal
from core.portfolio.portfolio_contract import PortfolioMembership
from core.risk.budget import RiskBudget, build_budget

CONTRACT_VERSION = "1.0.0"


class OverflowBehavior(Enum):
    REJECT = "REJECT"          # allocation fails closed
    CONSTRAIN_TO_AVAILABLE = "CONSTRAIN_TO_AVAILABLE"  # explicit, auditable constraint


class AllocationError(ContractValidationError):
    rule_id = "ALLOCATION-001"


@dataclass(frozen=True)
class CapitalAllocation:
    capital_allocation_id: str
    portfolio_id: str
    portfolio_version: str
    total_capital: str
    reserved_capital: str
    strategy_allocations: Mapping[str, str]
    symbol_allocations: Mapping[str, str]
    risk_allocations: Mapping[str, str]
    available_capital: str
    remaining_capital: str
    allocated_capital: str
    environment: str
    computed_at: str
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        for name in ("total_capital", "reserved_capital", "available_capital",
                     "remaining_capital", "allocated_capital"):
            parse_decimal(getattr(self, name), location=f"allocation.{name}")
        for group in ("strategy_allocations", "symbol_allocations", "risk_allocations"):
            if not isinstance(getattr(self, group), Mapping):
                raise ContractValidationError(
                    f"allocation.{group} must be a mapping", location=f"allocation.{group}",
                )
            for key, value in getattr(self, group).items():
                parse_decimal(value, location=f"allocation.{group}.{key}")
        # centralized arithmetic checks
        total = parse_decimal(self.total_capital, location="allocation.total")
        reserved = parse_decimal(self.reserved_capital, location="allocation.reserved")
        available = parse_decimal(self.available_capital, location="allocation.available")
        allocated = parse_decimal(self.allocated_capital, location="allocation.allocated")
        remaining = parse_decimal(self.remaining_capital, location="allocation.remaining")
        if available != total - reserved:
            raise AllocationError(
                "available_capital != total - reserved (centralized arithmetic violated)",
                location="allocation.arithmetic",
            )
        if remaining != available - allocated:
            raise AllocationError(
                "remaining_capital != available - allocated",
                location="allocation.arithmetic",
            )
        strategy_sum = sum(
            (parse_decimal(v, location="allocation.sum") for v in self.strategy_allocations.values()),
            Decimal("0"),
        )
        if strategy_sum != allocated:
            raise AllocationError(
                "allocated_capital != sum(strategy_allocations)",
                location="allocation.arithmetic",
            )


class CapitalAllocator:
    """Deterministic allocator. Priority ordering is EXPLICIT (membership
    priority); ambiguous priorities fail closed."""

    def allocate(
        self,
        *,
        portfolio_id: str,
        portfolio_version: str,
        total_capital: Any,
        reserved_capital: Any,
        memberships: Iterable[PortfolioMembership],
        requested: Mapping[str, Any],
        environment: str,
        computed_at,
        overflow_behavior: OverflowBehavior = OverflowBehavior.REJECT,
    ) -> CapitalAllocation:
        total = parse_decimal(total_capital, location="allocation.total")
        reserved = parse_decimal(reserved_capital, location="allocation.reserved")
        available = total - reserved
        members = [m for m in memberships if m.enabled]
        self._check_priorities(members)
        # deterministic order: priority ascending (lower number = higher priority), then id
        ordered = sorted(members, key=lambda m: (m.priority, m.strategy_id))
        granted: dict[str, str] = {}
        allocated = Decimal("0")
        constrained: list[str] = []
        for member in ordered:
            want = parse_decimal(requested.get(member.strategy_id, member.allocation),
                                 location=f"allocation.request.{member.strategy_id}")
            if want < 0:
                raise AllocationError(
                    f"Negative allocation requested for {member.strategy_id}",
                    location="allocation.request",
                )
            room = available - allocated
            if allocated + want > available:
                if overflow_behavior is OverflowBehavior.REJECT:
                    raise AllocationError(
                        f"Allocation for {member.strategy_id} exceeds available capital "
                        f"(requested {want}, room {room}) - rejected, never silently clipped",
                        location="allocation.overflow",
                        details={"strategy_id": member.strategy_id,
                                 "requested": str(want), "room": str(room)},
                    )
                want = room
                constrained.append(member.strategy_id)
            if want < 0:
                want = Decimal("0")
            granted[member.strategy_id] = str(want)
            allocated += want
        allocation = CapitalAllocation(
            capital_allocation_id=new_identifier("capital_allocation_id"),
            portfolio_id=portfolio_id, portfolio_version=portfolio_version,
            total_capital=str(total), reserved_capital=str(reserved),
            strategy_allocations=dict(granted),
            symbol_allocations={}, risk_allocations={},
            available_capital=str(available), remaining_capital=str(available - allocated),
            allocated_capital=str(allocated), environment=environment,
            computed_at=ensure_utc(computed_at, location="allocation.computed_at").isoformat(),
        )
        allocation.validate()
        return allocation

    @staticmethod
    def _check_priorities(members: list[PortfolioMembership]) -> None:
        seen: dict[int, str] = {}
        for member in members:
            if member.priority in seen:
                raise AllocationError(
                    f"Ambiguous priority {member.priority} between {seen[member.priority]} "
                    f"and {member.strategy_id} (explicit priority required - fail closed)",
                    location="allocation.priority", rule_id="PORTFOLIO-002",
                    details={"priority": member.priority},
                )
            seen[member.priority] = member.strategy_id

    @staticmethod
    def risk_budget_for(membership: PortfolioMembership, *, used: Any, requested: Any,
                        environment: str, risk_budget_id: str | None = None) -> RiskBudget:
        """Strategy risk budgets REUSE the Phase 3 RiskBudget contract."""
        from architecture.contracts.identifiers import new_identifier

        return build_budget(
            scope_type=__import__("core.risk.budget", fromlist=["BudgetScope"]).BudgetScope.STRATEGY,
            scope_id=membership.strategy_id,
            allocated=membership.risk_budget, used=used, requested=requested,
            environment=environment,
            risk_budget_id=risk_budget_id or new_identifier("risk_budget_id"),
        )
