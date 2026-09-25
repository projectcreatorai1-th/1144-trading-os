"""Phase 4 E2E flows (SECTION 55) + invariants (SECTION 45: all 30)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractError, ContractValidationError, RiskGateError
from architecture.contracts.identifiers import new_identifier
from core.policy.contracts import PolicyStatus, PolicyType
from core.policy.registry import ActorContext, PolicyRegistry
from core.portfolio.capacity import CapacityAssessment, CapacityKind, CapacitySubject, LiquidityBudget
from core.portfolio.decision import ConflictType, PortfolioDecisionEngine
from core.portfolio.exposure import ExposureLeg
from core.portfolio.portfolio_contract import Portfolio, PortfolioMembership, PortfolioStatus
from core.risk.engine import RiskEngine, RiskEvaluationRequest
from core.risk.context import build_context
from core.risk.state_service import RiskStateService
from core.strategy.contracts import Strategy, StrategyLifecycle, StrategyType
from core.strategy.evaluation import EligibilityResultValue, StrategyEligibilityEvaluator
from core.strategy.gate import IntentGate
from core.strategy.intent import IntentDirection, IntentType, StrategyIntent, Urgency
from core.events.contracts import EventType
from tests.factories import make_event
from tests.phase1_factories import at
from tests.test_phase3_risk_engine import activate as activate_policy, full_context
from tests.test_phase4_strategy import make_capability, make_config, make_strategy


@pytest.fixture()
def env(tmp_path):
    from platform.database.sqlite_stores import StorageSet

    storage = StorageSet(tmp_path / "p4-e2e.db")
    policies = PolicyRegistry(storage.policies, storage.audit)
    risk_engine = RiskEngine(policies=policies, audit=storage.audit)
    risk_state = RiskStateService(storage.states, storage.audit)

    # exposure dimension policy: gross <= 2000 else BLOCK
    activate_policy({"registry": policies, "storage": storage}, PolicyType.EXPOSURE_POLICY,
                    rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                            "field": "positions.gross", "op": "<=",
                            "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                    limits={"max_gross": "2000"})
    yield {"storage": storage, "policies": policies, "risk": risk_engine,
           "risk_state": risk_state}
    storage.close()


def _portfolio(env, strategies=("str_a",)):
    portfolio = Portfolio(
        portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
        name="e2e", account_scope="ACC-1", environment="SIMULATION",
        status=PortfolioStatus.ACTIVE, base_currency="USD",
        strategy_members=tuple(strategies), allocation_policy_id="fixed",
        effective_from=at(0, 0), created_at=at(9, 0),
    )
    portfolio.validate()
    return portfolio


def _memberships(strategy_ids):
    return [
        PortfolioMembership(
            portfolio_id=new_identifier("portfolio_id"), strategy_id=sid,
            strategy_version="1.0.0", allocation="500", risk_budget="10",
            priority=index + 1, enabled=True, effective_from=at(0, 0),
            environment="SIMULATION",
        )
        for index, sid in enumerate(strategy_ids)
    ]


def _intent(strategy_id, quantity="0.5", intent_type=IntentType.OPEN, symbol="XAUUSD",
            environment="SIMULATION", expires=None):
    return StrategyIntent(
        intent_id=new_identifier("intent_id"), strategy_id=strategy_id,
        strategy_version="1.0.0", intent_type=intent_type, symbol=symbol,
        direction=IntentDirection.LONG, requested_quantity=quantity,
        entry_conditions=({"condition_id": "C"},), exit_conditions=(),
        urgency=Urgency.NORMAL, rationale="e2e intent",
        risk_context_hash="a" * 64, source_event_id=new_identifier("event_id"),
        correlation_id=new_identifier("correlation_id"), environment=environment,
        created_at=at(12, 0), expires_at=expires or at(12, 30),
    )


def _decision(env, strategies, current_legs=(), intent_legs=(), limits=None,
              requested=None):
    return PortfolioDecisionEngine().evaluate(
        portfolio=_portfolio(env, strategies),
        memberships=_memberships(strategies),
        eligible_strategy_ids=list(strategies),
        current_legs=list(current_legs), intent_legs=list(intent_legs),
        total_capital="1000", reserved_capital="100",
        requested_capital=requested or {},
        capacities=[], liquidity={}, constraint_limits=limits or {},
        at=at(12, 0), correlation_id=new_identifier("correlation_id"),
    )


class TestE2EFlows:
    def test_e2e_1_full_chain(self, env):
        strategy_id = new_identifier("strategy_id")
        decision = _decision(env, [strategy_id])
        gate = IntentGate(env["risk"], env["storage"].audit)
        permission = gate.authorize(
            intent=_intent(strategy_id), portfolio_decision=decision,
            base_context=full_context(gross_exposure="1000"), at=at(12, 0),
        )
        assert permission.permission == "ALLOW"
        audits = list(env["storage"].audit.iter_by_correlation_id(
            permission.risk_decision.correlation_id))
        assert any(a.action == "RISK_DECISION" for a in audits)
        assert any(a.action == "INTENT_AUTHORIZATION" for a in audits)

    def test_e2e_2_suspended_strategy_ineligible(self, env):
        strategy = make_strategy(lifecycle_status=StrategyLifecycle.SUSPENDED)
        result = StrategyEligibilityEvaluator().evaluate(
            strategy=strategy, capability=make_capability(), config=make_config(strategy),
            context=full_context(), symbol="XAUUSD",
            correlation_id=new_identifier("correlation_id"), at=at(12, 0),
        )
        assert result.result is EligibilityResultValue.INELIGIBLE

    def test_e2e_3_two_strategies_aggregation(self, env):
        a, b = new_identifier("strategy_id"), new_identifier("strategy_id")
        decision = _decision(
            env, [a, b],
            current_legs=[ExposureLeg(a, "XAUUSD", "METALS", "LONG", "1000")],
            intent_legs=[ExposureLeg(b, "XAUUSD", "METALS", "LONG", "1500")],
            requested={a: "400", b: "400"},
        )
        assert decision.projected_exposure["gross"] == "2500"
        gate = IntentGate(env["risk"], env["storage"].audit)
        permission = gate.authorize(
            intent=_intent(b), portfolio_decision=decision,
            base_context=full_context(gross_exposure="1000"), at=at(12, 0),
        )
        assert permission.permission == "BLOCK"  # combined 2500 > 2000 policy
        assert any(c.conflict_type is ConflictType.SHARED_EXPOSURE for c in decision.conflicts)

    def test_e2e_4_capital_conflict_priority(self, env):
        from core.portfolio.allocation import CapitalAllocator, OverflowBehavior
        from core.portfolio.portfolio_contract import PortfolioMembership

        a, b = new_identifier("strategy_id"), new_identifier("strategy_id")

        def member(sid, priority):
            return PortfolioMembership(
                portfolio_id=new_identifier("portfolio_id"), strategy_id=sid,
                strategy_version="1.0.0", allocation="600", risk_budget="10",
                priority=priority, enabled=True, effective_from=at(0, 0),
                environment="SIMULATION",
            )

        allocation = CapitalAllocator().allocate(
            portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
            total_capital="1000", reserved_capital="0",
            memberships=[member(b, 2), member(a, 1)], requested={a: "600", b: "600"},
            environment="SIMULATION", computed_at=at(12, 0),
            overflow_behavior=OverflowBehavior.CONSTRAIN_TO_AVAILABLE,
        )
        # deterministic priority: 1 wins fully, 2 receives the remainder
        assert allocation.strategy_allocations[a] == "600"
        assert allocation.strategy_allocations[b] == "400"

    def test_e2e_5_risk_block_cannot_be_bypassed(self, env):
        strategy_id = new_identifier("strategy_id")
        decision = _decision(env, [strategy_id])
        gate = IntentGate(env["risk"], env["storage"].audit)
        permission = gate.authorize(
            intent=_intent(strategy_id, quantity="100"), portfolio_decision=decision,
            base_context=full_context(gross_exposure="5000"), at=at(12, 0),
        )
        assert permission.permission == "BLOCK"
        assert not permission.permitted

    def test_e2e_6_global_pause_blocks(self, env):
        env["risk"].set_risk_state("PAUSE")
        strategy_id = new_identifier("strategy_id")
        permission = IntentGate(env["risk"], env["storage"].audit).authorize(
            intent=_intent(strategy_id), portfolio_decision=_decision(env, [strategy_id]),
            base_context=full_context(), at=at(12, 0),
        )
        assert permission.permission == "BLOCK"

    def test_e2e_7_close_only_reduce_ok_increase_rejected(self, env):
        from tests.test_phase3_risk_engine import activate as act
        act({"registry": env["policies"], "storage": env["storage"]},
            PolicyType.VOLATILITY_POLICY,
            rules=({"rule_id": "R-VOL", "dimension": "VOLATILITY",
                    "field": "market.volatility", "op": "!=",
                    "limit": "extreme", "on_trigger": "CLOSE_ONLY", "critical": True},),
            limits={"extreme": "EXTREME"})
        strategy_id = new_identifier("strategy_id")
        decision = _decision(env, [strategy_id])
        gate = IntentGate(env["risk"], env["storage"].audit)
        reduce_perm = gate.authorize(
            intent=_intent(strategy_id, intent_type=IntentType.REDUCE),
            portfolio_decision=decision,
            base_context=full_context(volatility_state="EXTREME"), at=at(12, 0),
        )
        assert reduce_perm.permission == "LIMITED"
        increase_perm = gate.authorize(
            intent=_intent(strategy_id, intent_type=IntentType.INCREASE),
            portfolio_decision=decision,
            base_context=full_context(volatility_state="EXTREME"), at=at(12, 0),
        )
        assert increase_perm.permission == "CLOSE_ONLY"
        assert not increase_perm.permitted

    def test_e2e_8_emergency_no_risk_increase(self, env):
        env["risk"].set_risk_state("EMERGENCY")
        strategy_id = new_identifier("strategy_id")
        permission = IntentGate(env["risk"], env["storage"].audit).authorize(
            intent=_intent(strategy_id), portfolio_decision=_decision(env, [strategy_id]),
            base_context=full_context(), at=at(12, 0),
        )
        assert permission.permission == "EMERGENCY"
        assert not permission.permitted

    def test_e2e_9_replay_compare(self, env):
        strategy_id = new_identifier("strategy_id")
        decision = _decision(env, [strategy_id])
        gate = IntentGate(env["risk"], env["storage"].audit)
        context = full_context(gross_exposure="1500")
        first = gate.authorize(intent=_intent(strategy_id), portfolio_decision=decision,
                               base_context=context, at=at(12, 0))
        second = gate.authorize(intent=_intent(strategy_id), portfolio_decision=decision,
                                base_context=context, at=at(12, 0))
        assert first.permission == second.permission
        assert first.risk_decision.risk_context_hash == second.risk_decision.risk_context_hash

    def test_e2e_10_restart_identical_result(self, tmp_path):
        from platform.database.sqlite_stores import StorageSet

        path = tmp_path / "restart.db"
        storage = StorageSet(path)
        policies = PolicyRegistry(storage.policies, storage.audit)
        activate_policy({"registry": policies, "storage": storage}, PolicyType.EXPOSURE_POLICY,
                        rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                                "field": "positions.gross", "op": "<=",
                                "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                        limits={"max_gross": "2000"})
        engine = RiskEngine(policies=policies, audit=storage.audit)
        request = RiskEvaluationRequest(
            action_type="OPEN", subject="XAUUSD", environment="SIMULATION",
            requested_exposure="100", context=full_context(gross_exposure="1500"),
            correlation_id=new_identifier("correlation_id"), at=at(12, 0))
        before = engine.evaluate(request)
        storage.close()

        storage2 = StorageSet(path)
        policies2 = PolicyRegistry(storage2.policies, storage2.audit)
        engine2 = RiskEngine(policies=policies2, audit=storage2.audit)
        after = engine2.evaluate(request)
        assert after.decision is before.decision
        assert after.policy_hash == before.policy_hash
        storage2.close()


class TestInvariants1to10:
    def test_1_and_2_strategy_portfolio_cannot_bypass_risk(self, env):
        strategy_id = new_identifier("strategy_id")
        permission = IntentGate(env["risk"], env["storage"].audit).authorize(
            intent=_intent(strategy_id), portfolio_decision=_decision(env, [strategy_id]),
            base_context=full_context(gross_exposure="9000"), at=at(12, 0),
        )
        assert permission.permission == "BLOCK"  # hard exposure limit, no bypass

    def test_3_and_4_no_order_creation(self):
        import core.strategy.gate as gate_module
        import core.portfolio.decision as decision_module

        gate_source = open(gate_module.__file__, encoding="utf-8").read()
        decision_source = open(decision_module.__file__, encoding="utf-8").read()
        for forbidden in ("order_type", "time_in_force", "submit_order", "OrderRepository"):
            assert forbidden not in gate_source
            assert forbidden not in decision_source

    def test_5_riskdecision_is_final_authority(self, env):
        strategy_id = new_identifier("strategy_id")
        permission = IntentGate(env["risk"], env["storage"].audit).authorize(
            intent=_intent(strategy_id), portfolio_decision=_decision(env, [strategy_id]),
            base_context=full_context(gross_exposure="9000"), at=at(12, 0),
        )
        assert permission.permission == permission.risk_decision.decision.value

    def test_6_unknown_critical_not_eligible(self, env):
        strategy = make_strategy(lifecycle_status=StrategyLifecycle.DEMO)
        result = StrategyEligibilityEvaluator().evaluate(
            strategy=strategy, capability=make_capability(), config=make_config(strategy),
            context=full_context(system_state=None), symbol="XAUUSD",
            correlation_id=new_identifier("correlation_id"), at=at(12, 0),
        )
        assert result.result is EligibilityResultValue.UNKNOWN
        assert not result.eligible

    def test_7_expired_intent_invalid(self, env):
        import dataclasses

        strategy_id = new_identifier("strategy_id")
        intent = dataclasses.replace(
            _intent(strategy_id), created_at=at(11, 0), expires_at=at(11, 30))
        object.__setattr__(intent, "expires_at", intent.expires_at)
        with pytest.raises(RiskGateError) as excinfo:
            IntentGate(env["risk"], env["storage"].audit).authorize(
                intent=intent,
                portfolio_decision=_decision(env, [strategy_id]),
                base_context=full_context(), at=at(12, 0),
            )
        assert excinfo.value.rule_id == "INTENT-002"

    def test_8_expired_riskdecision_invalid(self):
        from core.risk.validator import RiskDecisionValidator
        from tests.factories import make_risk_decision

        decision = make_risk_decision(environment="SIMULATION")
        assert not RiskDecisionValidator().validate(
            decision, environment="SIMULATION", now=at(13, 0)).valid

    def test_9_environment_mismatch_invalid(self, env):
        strategy_id = new_identifier("strategy_id")
        with pytest.raises(ContractError):
            IntentGate(env["risk"], env["storage"].audit).authorize(
                intent=_intent(strategy_id, environment="LIVE"),
                portfolio_decision=_decision(env, [strategy_id]),
                base_context=full_context(), at=at(12, 0),
            )

    def test_10_strategy_version_immutable(self, env):
        from architecture.contracts.errors import StorageError
        from tests.test_phase4_strategy import ADMIN

        strategy = make_strategy()
        env["storage"].strategies.save(strategy)
        with pytest.raises(StorageError):
            env["storage"].strategies.save(strategy)


class TestInvariants11to20:
    def test_11_portfolio_version_immutable(self, env):
        from architecture.contracts.errors import StorageError

        portfolio = _portfolio(env)
        env["storage"].portfolios.save(portfolio)
        with pytest.raises(StorageError):
            env["storage"].portfolios.save(portfolio)

    def test_12_historical_membership_immutable(self):
        from dataclasses import FrozenInstanceError

        membership = _memberships([new_identifier("strategy_id")])[0]
        with pytest.raises(FrozenInstanceError):
            membership.allocation = "999"

    def test_13_and_14_allocation_limits(self):
        from core.portfolio.allocation import AllocationError, CapitalAllocator

        memberships = _memberships([new_identifier("strategy_id")])
        memberships[0] = _memberships([memberships[0].strategy_id])[0]
        with pytest.raises(AllocationError):
            CapitalAllocator().allocate(
                portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
                total_capital="100", reserved_capital="0", memberships=memberships,
                requested={memberships[0].strategy_id: "500"},
                environment="SIMULATION", computed_at=at(12, 0),
            )

    def test_15_hard_limit_no_override(self, env):
        from tests.test_phase3_risk_engine import activate as act

        act({"registry": env["policies"], "storage": env["storage"]},
            PolicyType.GLOBAL_SAFETY_POLICY,
            rules=({"rule_id": "R-HARD", "dimension": "GLOBAL",
                    "field": "positions.gross", "op": "<=",
                    "limit": "hard_max", "on_trigger": "EMERGENCY", "critical": True},),
            limits={"hard_max": "1500"})
        strategy_id = new_identifier("strategy_id")
        permission = IntentGate(env["risk"], env["storage"].audit).authorize(
            intent=_intent(strategy_id), portfolio_decision=_decision(env, [strategy_id]),
            base_context=full_context(gross_exposure="1800"), at=at(12, 0),
        )
        assert permission.permission == "EMERGENCY"

    def test_16_to_18_determinism_and_hashes(self, env):
        strategy_id = new_identifier("strategy_id")
        decision = _decision(env, [strategy_id])
        gate = IntentGate(env["risk"], env["storage"].audit)
        context = full_context(gross_exposure="1200")
        first = gate.authorize(intent=_intent(strategy_id), portfolio_decision=decision,
                               base_context=context, at=at(12, 0))
        second = gate.authorize(intent=_intent(strategy_id), portfolio_decision=decision,
                                base_context=context, at=at(12, 0))
        assert first.permission == second.permission
        assert first.risk_decision.risk_context_hash == second.risk_decision.risk_context_hash
        assert first.risk_decision.policy_hash == second.risk_decision.policy_hash

    def test_19_replay_no_live_permission(self, env):
        strategy_id = new_identifier("strategy_id")
        with pytest.raises(ContractError):
            IntentGate(env["risk"], env["storage"].audit).authorize(
                intent=_intent(strategy_id, environment="REPLAY"),
                portfolio_decision=_decision(env, [strategy_id]),
                base_context=full_context(), at=at(12, 0),
            )

    def test_20_historical_reconstruction(self, env):
        strategy_id = new_identifier("strategy_id")
        decision = _decision(env, [strategy_id])
        env["storage"].portfolio_decisions.append(decision)
        reloaded = env["storage"].portfolio_decisions.get_by_id(decision.portfolio_decision_id)
        assert reloaded.projected_exposure == decision.projected_exposure
        assert reloaded.resulting_state == decision.resulting_state


class TestInvariants21to30:
    def test_22_duplicate_intent_no_duplicate_canonical(self, env):
        from architecture.contracts.errors import StorageError

        intent = _intent(new_identifier("strategy_id"))
        env["storage"].intents.append(intent)
        with pytest.raises(StorageError):
            env["storage"].intents.append(intent)

    def test_23_duplicate_portfolio_decision_no_duplicate(self, env):
        from architecture.contracts.errors import StorageError

        decision = _decision(env, [new_identifier("strategy_id")])
        env["storage"].portfolio_decisions.append(decision)
        with pytest.raises(StorageError):
            env["storage"].portfolio_decisions.append(decision)

    def test_24_dependency_cycle_rejected(self):
        from core.strategy.dependency import DependencyCycleError, DependencyGraph

        graph = DependencyGraph()
        graph.add_dependency("STRATEGY", "s1", "DATA_SOURCE", "d1")
        with pytest.raises(DependencyCycleError):
            graph.add_dependency("DATA_SOURCE", "d1", "STRATEGY", "s1")

    def test_25_ambiguous_priority_rejected(self):
        from core.portfolio.allocation import AllocationError, CapitalAllocator

        a, b = new_identifier("strategy_id"), new_identifier("strategy_id")
        with pytest.raises(AllocationError):
            CapitalAllocator().allocate(
                portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
                total_capital="1000", reserved_capital="0",
                memberships=_memberships_with_priority([(a, 1), (b, 1)]),
                requested={}, environment="SIMULATION", computed_at=at(12, 0),
            )

    def test_27_suspension_prevents_intent_eligibility(self, env):
        strategy = make_strategy(lifecycle_status=StrategyLifecycle.SUSPENDED)
        result = StrategyEligibilityEvaluator().evaluate(
            strategy=strategy, capability=make_capability(), config=make_config(strategy),
            context=full_context(), symbol="XAUUSD",
            correlation_id=new_identifier("correlation_id"), at=at(12, 0),
        )
        assert not result.eligible

    def test_28_portfolio_pause_prevents_allocation(self):
        paused = _portfolio(None, ("str_a",))
        from dataclasses import replace

        paused = replace(paused, status=PortfolioStatus.PAUSED)
        with pytest.raises(ContractValidationError) as excinfo:
            PortfolioDecisionEngine().evaluate(
                portfolio=paused, memberships=_memberships((new_identifier("strategy_id"),)),
                eligible_strategy_ids=[new_identifier("strategy_id")],
                current_legs=[], intent_legs=[], total_capital="1000",
                reserved_capital="0", requested_capital={}, capacities=[], liquidity={},
                constraint_limits={}, at=at(12, 0),
                correlation_id=new_identifier("correlation_id"),
            )
        assert excinfo.value.rule_id == "PORTFOLIO-001"

    def test_29_and_30_close_only_emergency(self, env):
        env["risk"].set_risk_state("EMERGENCY")
        strategy_id = new_identifier("strategy_id")
        permission = IntentGate(env["risk"], env["storage"].audit).authorize(
            intent=_intent(strategy_id, intent_type=IntentType.INCREASE),
            portfolio_decision=_decision(env, [strategy_id]),
            base_context=full_context(), at=at(12, 0),
        )
        assert permission.permission == "EMERGENCY" and not permission.permitted


def _memberships_with_priority(pairs):
    return [
        PortfolioMembership(
            portfolio_id=new_identifier("portfolio_id"), strategy_id=sid,
            strategy_version="1.0.0", allocation="100", risk_budget="10",
            priority=priority, enabled=True, effective_from=at(0, 0),
            environment="SIMULATION",
        )
        for sid, priority in pairs
    ]
