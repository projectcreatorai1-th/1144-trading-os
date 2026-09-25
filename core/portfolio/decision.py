"""Portfolio decision engine (owned by core.portfolio).

Deterministic portfolio evaluation: eligibility, allocation, projected
exposure, constraints, capacities, liquidity, conflict classification and
priority. Produces a PortfolioDecision - NOT an order, and NEVER a risk
verdict: projected exposure feeds the SAME Phase 3 RiskEngine for the final
permission (SECTION 24/28/29/62)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Mapping

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import new_identifier, validate_identifier
from architecture.contracts.time import ensure_utc
from core.ledger.money import parse_decimal
from core.portfolio.allocation import CapitalAllocation, CapitalAllocator
from core.portfolio.capacity import CapacityAssessment, LiquidityBudget
from core.portfolio.exposure import ExposureAggregator, ExposureLeg, PortfolioExposure
from core.portfolio.portfolio_contract import Portfolio, PortfolioMembership, PortfolioStatus
from core.risk.budget import RiskBudget

CONTRACT_VERSION = "1.0.0"


class ConflictType(Enum):
    NO_CONFLICT = "NO_CONFLICT"
    SHARED_EXPOSURE = "SHARED_EXPOSURE"
    CAPITAL_CONFLICT = "CAPITAL_CONFLICT"
    RISK_CONFLICT = "RISK_CONFLICT"
    DIRECTION_CONFLICT = "DIRECTION_CONFLICT"
    CORRELATION_CONFLICT = "CORRELATION_CONFLICT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PortfolioConflict:
    conflict_type: ConflictType
    strategies: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class PortfolioDecision:
    portfolio_decision_id: str
    portfolio_id: str
    portfolio_version: str
    environment: str
    active_strategies: tuple
    eligible_strategies: tuple
    allocations: Mapping[str, Any]
    risk_budgets: Mapping[str, Any]
    projected_exposure: Mapping[str, Any]
    triggered_constraints: tuple
    exceeded_capacities: tuple
    liquidity_status: Mapping[str, Any]
    conflicts: tuple
    resulting_state: Mapping[str, Any]
    created_at: datetime
    correlation_id: str
    risk_decision_id: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("portfolio_decision_id", self.portfolio_decision_id,
                            location="pdecision.portfolio_decision_id")
        validate_identifier("portfolio_id", self.portfolio_id, location="pdecision.portfolio_id")
        ensure_utc(self.created_at, location="pdecision.created_at")
        for conflict in self.conflicts:
            if not isinstance(conflict, PortfolioConflict):
                raise ContractValidationError(
                    "pdecision.conflicts entries must be PortfolioConflict objects",
                    location="pdecision.conflicts",
                )


class PortfolioDecisionEngine:
    """Deterministic composition. Inputs arrive explicitly (no hidden state).
    Conflict classification never auto-nets, auto-closes or executes."""

    def __init__(self, allocator: CapitalAllocator | None = None,
                 aggregator: ExposureAggregator | None = None) -> None:
        self._allocator = allocator or CapitalAllocator()
        self._aggregator = aggregator or ExposureAggregator()

    def evaluate(
        self,
        *,
        portfolio: Portfolio,
        memberships: Iterable[PortfolioMembership],
        eligible_strategy_ids: Iterable[str],
        current_legs: Iterable[ExposureLeg],
        intent_legs: Iterable[ExposureLeg],
        total_capital: Any,
        reserved_capital: Any,
        requested_capital: Mapping[str, Any],
        capacities: Iterable[CapacityAssessment],
        liquidity: Mapping[str, LiquidityBudget],
        constraint_limits: Mapping[str, Any],
        at: datetime,
        correlation_id: str,
    ) -> PortfolioDecision:
        portfolio.validate()
        if portfolio.status is not PortfolioStatus.ACTIVE:
            raise ContractValidationError(
                f"Portfolio {portfolio.portfolio_id} is {portfolio.status.value}, not ACTIVE",
                location="pdecision.portfolio_status", rule_id="PORTFOLIO-001",
            )
        moment = ensure_utc(at, location="pdecision.at")
        eligible = frozenset(eligible_strategy_ids)
        members = [m for m in memberships if m.enabled and m.strategy_id in eligible]

        allocation = self._allocator.allocate(
            portfolio_id=portfolio.portfolio_id,
            portfolio_version=portfolio.portfolio_version,
            total_capital=total_capital, reserved_capital=reserved_capital,
            memberships=members, requested=requested_capital,
            environment=portfolio.environment, computed_at=moment,
        )

        risk_budgets = {
            m.strategy_id: self._allocator.risk_budget_for(
                m, used="0",
                requested=str(requested_capital.get(m.strategy_id, "0")),
                environment=portfolio.environment,
            )
            for m in members
        }

        exposure = self._aggregator.aggregate(
            portfolio_id=portfolio.portfolio_id,
            legs=list(current_legs) + list(intent_legs),
            environment=portfolio.environment, computed_at=moment,
        )

        constraints = self._check_constraints(exposure, constraint_limits)
        exceeded = self._check_capacities(capacities, exposure)
        conflicts = self._classify_conflicts(members, list(current_legs), list(intent_legs),
                                             allocation, risk_budgets)

        decision = PortfolioDecision(
            portfolio_decision_id=new_identifier("portfolio_decision_id"),
            portfolio_id=portfolio.portfolio_id,
            portfolio_version=portfolio.portfolio_version,
            environment=portfolio.environment,
            active_strategies=tuple(m.strategy_id for m in members),
            eligible_strategies=tuple(sorted(eligible)),
            allocations=dict(allocation.strategy_allocations),
            risk_budgets={key: {"allocated": b.allocated_risk, "used": b.used_risk,
                                "requested": b.requested_risk,
                                "within": b.requested_within_budget()}
                          for key, b in risk_budgets.items()},
            projected_exposure={
                "gross": exposure.gross_exposure, "net": exposure.net_exposure,
                "by_symbol": dict(exposure.by_symbol),
            },
            triggered_constraints=tuple(constraints),
            exceeded_capacities=tuple(exceeded),
            liquidity_status={symbol: budget.liquidity_state.value
                              for symbol, budget in liquidity.items()},
            conflicts=tuple(conflicts),
            resulting_state={
                "remaining_capital": allocation.remaining_capital,
                "allocated_capital": allocation.allocated_capital,
            },
            created_at=moment,
            correlation_id=correlation_id,
        )
        decision.validate()
        return decision

    @staticmethod
    def _check_constraints(exposure: PortfolioExposure,
                           limits: Mapping[str, Any]) -> list[str]:
        triggered: list[str] = []
        checks = (
            ("max_total_exposure", exposure.gross_exposure, "TOTAL_EXPOSURE_LIMIT"),
            ("max_long_exposure", exposure.long_exposure, "LONG_EXPOSURE_LIMIT"),
            ("max_short_exposure", abs(Decimal(exposure.short_exposure)), "SHORT_EXPOSURE_LIMIT"),
        )
        for limit_name, value, label in checks:
            if limit_name in limits:
                if parse_decimal(value, location="constraint.value") > \
                        parse_decimal(limits[limit_name], location=f"constraint.{limit_name}"):
                    triggered.append(label)
        for symbol, value in exposure.by_symbol.items():
            key = f"max_symbol_exposure"
            if key in limits and parse_decimal(value, location="constraint.symbol") > \
                    parse_decimal(limits[key], location=f"constraint.{key}"):
                triggered.append(f"SYMBOL_EXPOSURE_LIMIT:{symbol}")
        return triggered

    @staticmethod
    def _check_capacities(capacities: Iterable[CapacityAssessment],
                          exposure: PortfolioExposure) -> list[str]:
        exceeded: list[str] = []
        for capacity in capacities:
            if capacity.subject_type.value == "SYMBOL":
                relevant = exposure.by_symbol.get(capacity.subject_id)
                if relevant is None:
                    continue
                result = capacity.is_exceeded_by(relevant)
                if result is True:
                    exceeded.append(f"CAPACITY_EXCEEDED:{capacity.subject_id}")
                elif result is None:
                    exceeded.append(f"CAPACITY_UNKNOWN:{capacity.subject_id}")
        return exceeded

    @staticmethod
    def _classify_conflicts(
        members: list[PortfolioMembership],
        current_legs: list[ExposureLeg],
        intent_legs: list[ExposureLeg],
        allocation: CapitalAllocation,
        risk_budgets: Mapping[str, RiskBudget],
    ) -> list[PortfolioConflict]:
        conflicts: list[PortfolioConflict] = []
        legs = current_legs + intent_legs
        symbols: dict[str, list[ExposureLeg]] = {}
        for leg in legs:
            symbols.setdefault(leg.symbol, []).append(leg)
        for symbol, symbol_legs in sorted(symbols.items()):
            strategies = {leg.strategy_id for leg in symbol_legs}
            if len(strategies) < 2:
                continue
            directions = {leg.direction for leg in symbol_legs}
            if "LONG" in directions and "SHORT" in directions:
                conflicts.append(PortfolioConflict(
                    ConflictType.DIRECTION_CONFLICT, tuple(sorted(strategies)),
                    f"opposite directions on {symbol} (reported, never auto-netted)",
                ))
            else:
                conflicts.append(PortfolioConflict(
                    ConflictType.SHARED_EXPOSURE, tuple(sorted(strategies)),
                    f"shared same-direction exposure on {symbol}",
                ))
        for strategy_id, budget in risk_budgets.items():
            if not budget.requested_within_budget():
                conflicts.append(PortfolioConflict(
                    ConflictType.RISK_CONFLICT, (strategy_id,),
                    f"risk budget exceeded: projected {budget.projected_risk()} "
                    f"> allocated {budget.allocated_risk}",
                ))
        conflicts.append(PortfolioConflict(
            ConflictType.NO_CONFLICT, (), "baseline",
        ) if not conflicts else None)
        return [c for c in conflicts if c]
