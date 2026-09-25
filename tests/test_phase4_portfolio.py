"""Phase 4 portfolio tests: contract, membership, allocation, exposure,
capacity/liquidity, constraints, decision, conflicts, priority."""
from __future__ import annotations

from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractError, ContractValidationError
from architecture.contracts.identifiers import new_identifier
from core.portfolio.allocation import (
    AllocationError,
    CapitalAllocator,
    OverflowBehavior,
)
from core.portfolio.capacity import (
    CapacityAssessment,
    CapacityKind,
    CapacitySubject,
    LiquidityBudget,
    LiquidityState,
)
from core.portfolio.decision import ConflictType, PortfolioDecisionEngine
from core.portfolio.exposure import ExposureAggregator, ExposureLeg
from core.portfolio.portfolio_contract import (
    Portfolio,
    PortfolioMembership,
    PortfolioStatus,
)
from tests.phase1_factories import at


def make_portfolio(**overrides):
    defaults = dict(
        portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
        name="master", account_scope="ACC-1", environment="SIMULATION",
        status=PortfolioStatus.ACTIVE, base_currency="USD",
        strategy_members=(), allocation_policy_id="alloc-fixed",
        effective_from=at(0, 0), created_at=at(9, 0),
    )
    defaults.update(overrides)
    return Portfolio(**defaults)


def make_membership(strategy_id, priority, allocation="500", risk_budget="5", enabled=True):
    return PortfolioMembership(
        portfolio_id=new_identifier("portfolio_id"), strategy_id=strategy_id, strategy_version="1.0.0",
        allocation=allocation, risk_budget=risk_budget, priority=priority,
        enabled=enabled, effective_from=at(0, 0), environment="SIMULATION",
    )


class TestPortfolioContract:
    def test_valid(self):
        make_portfolio().validate()

    def test_membership_priorities_explicit(self):
        make_membership(new_identifier("strategy_id"), 1).validate()
        with pytest.raises(ContractValidationError):
            make_membership(new_identifier("strategy_id"), True).validate()  # bool is not a priority


class TestCapitalAllocation:
    def test_centralized_arithmetic(self):
        allocation = CapitalAllocator().allocate(
            portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
            total_capital="1000", reserved_capital="100",
            memberships=[make_membership("str_a", 1, allocation="400"),
                         make_membership("str_b", 2, allocation="300")],
            requested={}, environment="SIMULATION", computed_at=at(12, 0),
        )
        assert allocation.available_capital == "900"
        assert allocation.allocated_capital == "700"
        assert allocation.remaining_capital == "200"
        assert allocation.strategy_allocations == {"str_a": "400", "str_b": "300"}

    def test_overflow_rejected_not_clipped(self):
        with pytest.raises(AllocationError) as excinfo:
            CapitalAllocator().allocate(
                portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
                total_capital="1000", reserved_capital="100",
                memberships=[make_membership("str_a", 1, allocation="950")],
                requested={}, environment="SIMULATION", computed_at=at(12, 0),
            )
        assert "exceeds available" in excinfo.value.message

    def test_overflow_constrain_is_explicit(self):
        allocation = CapitalAllocator().allocate(
            portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
            total_capital="1000", reserved_capital="100",
            memberships=[make_membership("str_a", 1, allocation="950")],
            requested={}, environment="SIMULATION", computed_at=at(12, 0),
            overflow_behavior=OverflowBehavior.CONSTRAIN_TO_AVAILABLE,
        )
        assert allocation.strategy_allocations["str_a"] == "900"
        assert allocation.remaining_capital == "0"

    def test_priority_order_deterministic(self):
        allocation = CapitalAllocator().allocate(
            portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
            total_capital="1000", reserved_capital="0",
            memberships=[make_membership("str_b", 2, allocation="600"),
                         make_membership("str_a", 1, allocation="600")],
            requested={}, environment="SIMULATION", computed_at=at(12, 0),
            overflow_behavior=OverflowBehavior.CONSTRAIN_TO_AVAILABLE,
        )
        # priority 1 served first; priority 2 constrained to the remainder
        assert allocation.strategy_allocations == {"str_a": "600", "str_b": "400"}

    def test_ambiguous_priority_fails_closed(self):
        with pytest.raises(AllocationError) as excinfo:
            CapitalAllocator().allocate(
                portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
                total_capital="1000", reserved_capital="0",
                memberships=[make_membership("str_a", 1), make_membership("str_b", 1)],
                requested={}, environment="SIMULATION", computed_at=at(12, 0),
            )
        assert excinfo.value.rule_id == "PORTFOLIO-002"

    def test_disabled_members_excluded(self):
        allocation = CapitalAllocator().allocate(
            portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
            total_capital="1000", reserved_capital="0",
            memberships=[make_membership("str_a", 1, allocation="100"),
                         make_membership("str_b", 2, allocation="900", enabled=False)],
            requested={}, environment="SIMULATION", computed_at=at(12, 0),
        )
        assert "str_b" not in allocation.strategy_allocations

    def test_negative_request_rejected(self):
        with pytest.raises(AllocationError):
            CapitalAllocator().allocate(
                portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
                total_capital="1000", reserved_capital="0",
                memberships=[make_membership("str_a", 1)],
                requested={"str_a": "-5"}, environment="SIMULATION",
                computed_at=at(12, 0),
            )


class TestExposure:
    def test_aggregation(self):
        exposure = ExposureAggregator().aggregate(
            portfolio_id=new_identifier("portfolio_id"), environment="SIMULATION", computed_at=at(12, 0),
            legs=[
                ExposureLeg("str_a", "XAUUSD", "METALS", "LONG", "1000"),
                ExposureLeg("str_b", "XAUUSD", "METALS", "LONG", "500"),
                ExposureLeg("str_c", "EURUSD", "FX", "SHORT", "300"),
            ],
        )
        assert exposure.long_exposure == "1500"
        assert exposure.short_exposure == "-300"
        assert exposure.gross_exposure == "1800"
        assert exposure.net_exposure == "1200"
        assert exposure.by_symbol["XAUUSD"] == "1500"

    def test_correlation_group_boundary(self):
        exposure = ExposureAggregator().aggregate(
            portfolio_id=new_identifier("portfolio_id"), environment="SIMULATION", computed_at=at(12, 0),
            legs=[ExposureLeg("str_a", "XAUUSD", "METALS", "LONG", "1000"),
                  ExposureLeg("str_b", "XAUUSD", "METALS", "LONG", "500")],
        )
        assert "UNKNOWN" in exposure.correlation_groups  # no data -> explicit UNKNOWN
        combined = ExposureAggregator.correlated_exposure(exposure, ["XAUUSD"])
        assert combined == "1500"

    def test_cross_strategy_combined_visible(self):
        exposure = ExposureAggregator().aggregate(
            portfolio_id=new_identifier("portfolio_id"), environment="SIMULATION", computed_at=at(12, 0),
            legs=[ExposureLeg("str_a", "XAUUSD", "METALS", "LONG", "1000"),
                  ExposureLeg("str_b", "XAUUSD", "METALS", "LONG", "500")],
        )
        assert exposure.by_symbol["XAUUSD"] == "1500"  # combined, not isolated

    def test_wrong_sign_leg_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            ExposureAggregator().aggregate(
                portfolio_id=new_identifier("portfolio_id"), environment="SIMULATION", computed_at=at(12, 0),
                legs=[ExposureLeg("str_a", "XAUUSD", "METALS", "LONG", "-100")],
            )
        assert excinfo.value.rule_id == "EXPOSURE-001"


class TestCapacityLiquidity:
    def test_capacity_kinds(self):
        observed = CapacityAssessment(
            subject_type=CapacitySubject.SYMBOL, subject_id="XAUUSD",
            capacity_kind=CapacityKind.OBSERVED, unit="USD",
            provenance={"source": "observation"}, as_of=at(12, 0),
            environment="SIMULATION", value="5000",
        )
        observed.validate()
        assert observed.is_exceeded_by("6000") is True
        assert observed.is_exceeded_by("4000") is False

    def test_unknown_capacity_never_a_fact(self):
        unknown = CapacityAssessment(
            subject_type=CapacitySubject.SYMBOL, subject_id="XAUUSD",
            capacity_kind=CapacityKind.UNKNOWN, unit="USD",
            provenance={"source": "none"}, as_of=at(12, 0),
            environment="SIMULATION",
        )
        unknown.validate()
        assert unknown.is_exceeded_by("1") is None  # callers fail closed on None
        with pytest.raises(ContractValidationError):
            CapacityAssessment(
                subject_type=CapacitySubject.SYMBOL, subject_id="XAUUSD",
                capacity_kind=CapacityKind.UNKNOWN, unit="USD",
                provenance={"s": 1}, as_of=at(12, 0), environment="SIMULATION",
                value="100",  # UNKNOWN with a value is a contradiction
            ).validate()

    def test_estimated_needs_provenance(self):
        with pytest.raises(ContractValidationError):
            CapacityAssessment(
                subject_type=CapacitySubject.STRATEGY, subject_id="str_a",
                capacity_kind=CapacityKind.ESTIMATED, unit="USD",
                provenance={}, as_of=at(12, 0), environment="SIMULATION", value="100",
            ).validate()

    def test_liquidity_unknown(self):
        budget = LiquidityBudget.unknown("XAUUSD", at(12, 0), "SIMULATION")
        assert budget.covers("100") is None

    def test_liquidity_covers(self):
        budget = LiquidityBudget(
            symbol="XAUUSD", liquidity_state=LiquidityState.NORMAL, as_of=at(12, 0),
            environment="SIMULATION", remaining_liquidity="1000",
        )
        assert budget.covers("800") is True
        assert budget.covers("1200") is False


class TestPortfolioDecision:
    def _evaluate(self, **overrides):
        defaults = dict(
            portfolio=make_portfolio(),
            memberships=[make_membership("str_a", 1, allocation="400", risk_budget="10"),
                         make_membership("str_b", 2, allocation="300", risk_budget="5")],
            eligible_strategy_ids=["str_a", "str_b"],
            current_legs=[ExposureLeg("str_a", "XAUUSD", "METALS", "LONG", "1000")],
            intent_legs=[ExposureLeg("str_b", "XAUUSD", "METALS", "LONG", "500")],
            total_capital="1000", reserved_capital="100",
            requested_capital={},
            capacities=[CapacityAssessment(
                subject_type=CapacitySubject.SYMBOL, subject_id="XAUUSD",
                capacity_kind=CapacityKind.OBSERVED, unit="USD",
                provenance={"source": "obs"}, as_of=at(12, 0),
                environment="SIMULATION", value="5000")],
            liquidity={"XAUUSD": LiquidityBudget.unknown("XAUUSD", at(12, 0), "SIMULATION")},
            constraint_limits={"max_total_exposure": "2000"},
            at=at(12, 0), correlation_id=new_identifier("correlation_id"),
        )
        defaults.update(overrides)
        return PortfolioDecisionEngine().evaluate(**defaults)

    def test_decision_fields(self):
        decision = self._evaluate()
        assert set(decision.active_strategies) == {"str_a", "str_b"}
        assert decision.projected_exposure["gross"] == "1500"
        assert decision.risk_decision_id is None  # risk comes later, separately

    def test_inactive_portfolio_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            self._evaluate(portfolio=make_portfolio(status=PortfolioStatus.PAUSED))
        assert excinfo.value.rule_id == "PORTFOLIO-001"

    def test_shared_exposure_conflict_classified(self):
        decision = self._evaluate()
        types = {c.conflict_type for c in decision.conflicts}
        assert ConflictType.SHARED_EXPOSURE in types

    def test_direction_conflict_classified_never_netted(self):
        decision = self._evaluate(
            intent_legs=[ExposureLeg("str_b", "XAUUSD", "METALS", "SHORT", "500")],
        )
        conflicts = [c for c in decision.conflicts
                     if c.conflict_type is ConflictType.DIRECTION_CONFLICT]
        assert conflicts and "never auto-netted" in conflicts[0].detail

    def test_constraint_triggered(self):
        decision = self._evaluate(constraint_limits={"max_total_exposure": "1200"})
        assert "TOTAL_EXPOSURE_LIMIT" in decision.triggered_constraints

    def test_capacity_exceeded_reported(self):
        decision = self._evaluate(
            capacities=[CapacityAssessment(
                subject_type=CapacitySubject.SYMBOL, subject_id="XAUUSD",
                capacity_kind=CapacityKind.OBSERVED, unit="USD",
                provenance={"s": 1}, as_of=at(12, 0), environment="SIMULATION",
                value="1000")],
        )
        assert any("CAPACITY_EXCEEDED" in item for item in decision.exceeded_capacities)

    def test_risk_conflict_when_budget_exceeded(self):
        decision = self._evaluate(
            memberships=[make_membership("str_a", 1, allocation="400", risk_budget="1")],
            eligible_strategy_ids=["str_a"],
            requested_capital={"str_a": "400"},
            intent_legs=[ExposureLeg("str_a", "XAUUSD", "METALS", "LONG", "1000")],
        )
        types = {c.conflict_type for c in decision.conflicts}
        assert ConflictType.RISK_CONFLICT in types
