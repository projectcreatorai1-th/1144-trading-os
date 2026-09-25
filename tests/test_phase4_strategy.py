"""Phase 4 strategy tests: contract, registry/lifecycle, capability, config,
evaluation, eligibility, kill criteria, health, intent, dependency graph."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractError, ContractValidationError, StateTransitionError
from architecture.contracts.identifiers import new_identifier
from core.risk.context import build_context
from core.strategy.contracts import (
    Directional,
    Strategy,
    StrategyConfig,
    StrategyLifecycle,
    StrategyType,
    config_hash,
)
from core.strategy.dependency import DependencyCycleError, DependencyGraph
from core.strategy.evaluation import (
    EligibilityResultValue,
    EvaluationResult,
    HealthStatus,
    KillResult,
    StrategyEligibilityEvaluator,
    StrategyEvaluator,
    KillCriteriaEvaluator,
    StrategyHealth,
)
from core.strategy.intent import IntentDirection, IntentType, StrategyIntent, Urgency
from core.strategy.registry import StrategyActor, StrategyRegistry, StrategyRegistryError
from platform.database.sqlite_stores import StorageSet
from platform.security.contracts import Role
from tests.phase1_factories import at


@pytest.fixture()
def env(tmp_path):
    storage = StorageSet(tmp_path / "p4-strategy.db")
    registry = StrategyRegistry(storage.strategies, storage.audit)
    yield {"storage": storage, "registry": registry}
    storage.close()


ADMIN = lambda uid: StrategyActor(user_id=uid, role=Role.ADMIN)  # noqa: E731


def make_strategy(**overrides):
    defaults = dict(
        strategy_id=new_identifier("strategy_id"), strategy_version="1.0.0",
        strategy_type=StrategyType.TREND, name="trend-alpha", owner="desk-1",
        lifecycle_status=StrategyLifecycle.IDEA, environment="SIMULATION",
        effective_from=at(0, 0), created_at=at(9, 0), updated_at=at(9, 0),
        configuration_version="1.0.0", capability_profile_id="cap-default",
    )
    defaults.update(overrides)
    return Strategy(**defaults)


def make_capability(**overrides):
    from core.strategy.contracts import CapabilityProfile

    defaults = dict(
        capability_profile_id="cap-default", strategy_type=StrategyType.TREND,
        supported_symbols=("XAUUSD", "EURUSD"), supported_markets=("FX", "METALS"),
        supported_environments=("SIMULATION", "PAPER", "DEMO"),
        partial_close_capability=True, basket_capability=False, hedge_capability=False,
        directional_capability=Directional.BOTH,
        news_sensitivity="MEDIUM", volatility_sensitivity="MEDIUM",
        spread_sensitivity="LOW", liquidity_requirement="NORMAL",
        provenance={"source": "desk-declaration", "observed": False},
    )
    defaults.update(overrides)
    return CapabilityProfile(**defaults)


def make_config(strategy, **overrides):
    defaults = dict(
        strategy_id=strategy.strategy_id, strategy_version=strategy.strategy_version,
        parameters={"entry_threshold": "1.5"}, units={"entry_threshold": "sigma"},
        constraints={
            "entry": ({"condition_id": "C-ENTRY", "field": "positions.gross",
                       "op": "<", "value": "2000", "purpose": "entry"},),
            "exit": (),
            "block": ({"condition_id": "C-BLOCK", "field": "market.volatility",
                       "op": "==", "value": "EXTREME", "purpose": "block"},),
        },
        environment=strategy.environment, effective_from=at(0, 0),
        provenance={"author": "desk-1"},
    )
    defaults.update(overrides)
    parameters = defaults["parameters"]
    units = defaults["units"]
    constraints = defaults["constraints"]
    defaults["config_hash"] = config_hash(parameters, units, constraints)
    defaults.update(overrides)
    return StrategyConfig(**defaults)


def full_context(**overrides):
    values = dict(
        account_equity="10000", account_margin_level="500", gross_exposure="1000",
        drawdown_pct="1", market_state="NORMAL", volatility_state="NORMAL",
        spread_state="NORMAL", liquidity_state="NORMAL", data_quality="VALIDATED",
        event_risk="NORMAL", system_state="RUNNING", execution_state="READY",
    )
    values.update(overrides)
    return build_context(as_of=at(12, 0), environment="SIMULATION", **values)


class TestStrategyContract:
    def test_valid_strategy(self):
        make_strategy().validate()

    def test_version_immutable(self):
        strategy = make_strategy()
        with pytest.raises(FrozenInstanceError):
            strategy.name = "changed"

    def test_live_requires_prerequisites(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_strategy(lifecycle_status=StrategyLifecycle.LIVE).validate()
        assert excinfo.value.rule_id == "STRATEGY-002"

    def test_lifecycle_chain_order(self):
        chain = [s.value for s in StrategyLifecycle]
        assert chain.index("APPROVED") < chain.index("LIVE")


class TestRegistryLifecycle:
    def test_register_and_promote_to_live(self, env):
        strategy = make_strategy()
        author = ADMIN(new_identifier("user_id"))
        env["registry"].register(strategy, author, at=at(9, 0))
        target_chain = [
            StrategyLifecycle.RESEARCH, StrategyLifecycle.BACKTEST,
            StrategyLifecycle.ROBUSTNESS, StrategyLifecycle.OUT_OF_SAMPLE,
            StrategyLifecycle.REPLAY, StrategyLifecycle.PAPER, StrategyLifecycle.DEMO,
            StrategyLifecycle.FORWARD, StrategyLifecycle.APPROVED,
        ]
        current = strategy
        for target in target_chain:
            current = env["registry"].transition(
                current.strategy_id, current.strategy_version, target,
                ADMIN(new_identifier("user_id")), at=at(9, 1), reason=f"promote to {target.value}",
            )
        # attach LIVE prerequisites then go LIVE
        from dataclasses import replace

        live_ready = replace(current, policy_id=new_identifier("policy_id"),
                             policy_version="1.0.0",
                             risk_budget_id=new_identifier("risk_budget_id"),
                             strategy_version="1.9.0")
        env["storage"].strategies.save(live_ready)
        live = env["registry"].transition(
            live_ready.strategy_id, live_ready.strategy_version, StrategyLifecycle.LIVE,
            ADMIN(new_identifier("user_id")), at=at(10, 0), reason="go live",
        )
        assert live.lifecycle_status is StrategyLifecycle.LIVE

    def test_skip_lifecycle_fails_closed(self, env):
        strategy = make_strategy()
        env["registry"].register(strategy, ADMIN(new_identifier("user_id")), at=at(9, 0))
        with pytest.raises(StateTransitionError):
            env["registry"].transition(
                strategy.strategy_id, strategy.strategy_version, StrategyLifecycle.LIVE,
                ADMIN(new_identifier("user_id")), at=at(9, 1), reason="skip",
            )

    def test_unauthorized_transition_rejected(self, env):
        strategy = make_strategy()
        env["registry"].register(strategy, ADMIN(new_identifier("user_id")), at=at(9, 0))
        viewer = StrategyActor(user_id=new_identifier("user_id"), role=Role.VIEWER)
        with pytest.raises(ContractError):
            env["registry"].transition(
                strategy.strategy_id, strategy.strategy_version, StrategyLifecycle.RESEARCH,
                viewer, at=at(9, 1), reason="unauthorized",
            )

    def test_missing_strategy_fails_closed(self, env):
        with pytest.raises(StrategyRegistryError) as excinfo:
            env["registry"].transition(
                "str_" + "0" * 32, "1.0.0", StrategyLifecycle.RESEARCH,
                ADMIN(new_identifier("user_id")), at=at(9, 0), reason="ghost",
            )
        assert excinfo.value.rule_id == "STRATEGY-001"

    def test_resolve_deterministic(self, env):
        strategy = make_strategy()
        env["registry"].register(strategy, ADMIN(new_identifier("user_id")), at=at(9, 0))
        first = env["registry"].resolve(strategy.strategy_id, environment="SIMULATION", at=at(12, 0))
        second = env["registry"].resolve(strategy.strategy_id, environment="SIMULATION", at=at(12, 0))
        assert first is not None and first.strategy_version == second.strategy_version

    def test_version_history_immutable(self, env):
        strategy = make_strategy()
        env["registry"].register(strategy, ADMIN(new_identifier("user_id")), at=at(9, 0))
        env["registry"].transition(
            strategy.strategy_id, strategy.strategy_version, StrategyLifecycle.RESEARCH,
            ADMIN(new_identifier("user_id")), at=at(9, 1), reason="research",
        )
        versions = [s.strategy_version for s in env["storage"].strategies.iter_versions(strategy.strategy_id)]
        assert len(versions) == 2  # original retained, new version appended


class TestCapabilityAndConfig:
    def test_capability_validation(self):
        make_capability().validate()

    def test_capability_support_checks(self):
        capability = make_capability()
        assert capability.supports_symbol("XAUUSD")
        assert not capability.supports_symbol("BTCUSD")
        assert capability.supports_environment("PAPER")
        assert not capability.supports_environment("LIVE")

    def test_config_hash_integrity(self):
        strategy = make_strategy()
        config = make_config(strategy)
        config.validate()
        from dataclasses import replace

        tampered = replace(config, parameters={"entry_threshold": "9.9"})
        with pytest.raises(ContractValidationError):
            tampered.validate()

    def test_empty_parameters_rejected(self):
        strategy = make_strategy()
        with pytest.raises(ContractValidationError):
            make_config(strategy, parameters={}, config_hash=config_hash({}, {"u": "v"}, {"c": {}}),
                        units={"u": "v"}, constraints={"c": {}}).validate()


class TestEvaluationAndEligibility:
    def test_conditions_deterministic(self):
        evaluator = StrategyEvaluator()
        conditions = ({"condition_id": "C-ENTRY", "field": "positions.gross",
                       "op": "<", "value": "2000", "purpose": "entry"},)
        context = full_context(gross_exposure="1500").flattened()
        first = evaluator.evaluate_conditions(conditions, context)
        second = evaluator.evaluate_conditions(conditions, context)
        assert [(o.satisfied, o.resolved) for o in first] == [(o.satisfied, o.resolved) for o in second]
        assert first[0].satisfied is True
        blocked = evaluator.evaluate_conditions(
            ({"condition_id": "C-BLOCK", "field": "market.volatility",
              "op": "==", "value": "EXTREME"},), full_context(volatility_state="EXTREME").flattened())
        assert blocked[0].satisfied is True

    def test_unknown_field_unresolved(self):
        evaluator = StrategyEvaluator()
        outcome = evaluator.evaluate_conditions(
            ({"condition_id": "C-X", "field": "risk.recovery_state", "op": "==", "value": "X"},),
            full_context().flattened(),
        )[0]
        assert outcome.resolved is False and outcome.satisfied is False

    def test_eligibility_happy_path(self, env):
        strategy = make_strategy(lifecycle_status=StrategyLifecycle.DEMO)
        capability = make_capability()
        config = make_config(strategy)
        result = StrategyEligibilityEvaluator().evaluate(
            strategy=strategy, capability=capability, config=config,
            context=full_context(), symbol="XAUUSD",
            correlation_id=new_identifier("correlation_id"), at=at(12, 0),
        )
        assert result.result is EligibilityResultValue.ELIGIBLE

    def test_suspended_strategy_ineligible(self, env):
        strategy = make_strategy(lifecycle_status=StrategyLifecycle.SUSPENDED)
        result = StrategyEligibilityEvaluator().evaluate(
            strategy=strategy, capability=make_capability(), config=make_config(strategy),
            context=full_context(), symbol="XAUUSD",
            correlation_id=new_identifier("correlation_id"), at=at(12, 0),
        )
        assert result.result is EligibilityResultValue.INELIGIBLE
        assert any("STRATEGY_LIFECYCLE_SUSPENDED" == r for r in result.reasons)

    def test_unknown_symbol_ineligible(self, env):
        result = StrategyEligibilityEvaluator().evaluate(
            strategy=make_strategy(lifecycle_status=StrategyLifecycle.DEMO),
            capability=make_capability(), config=make_config(make_strategy()),
            context=full_context(), symbol="BTCUSD",
            correlation_id=new_identifier("correlation_id"), at=at(12, 0),
        )
        assert result.result is EligibilityResultValue.INELIGIBLE
        assert "SYMBOL_NOT_SUPPORTED" in result.reasons

    def test_unknown_critical_not_eligible(self, env):
        strategy = make_strategy(lifecycle_status=StrategyLifecycle.DEMO)
        result = StrategyEligibilityEvaluator().evaluate(
            strategy=strategy, capability=make_capability(), config=make_config(strategy),
            context=full_context(market_state=None), symbol="XAUUSD",
            correlation_id=new_identifier("correlation_id"), at=at(12, 0),
        )
        assert result.result is EligibilityResultValue.UNKNOWN

    def test_invalid_data_ineligible(self, env):
        strategy = make_strategy(lifecycle_status=StrategyLifecycle.DEMO)
        result = StrategyEligibilityEvaluator().evaluate(
            strategy=strategy, capability=make_capability(), config=make_config(strategy),
            context=full_context(data_quality="INVALID"), symbol="XAUUSD",
            correlation_id=new_identifier("correlation_id"), at=at(12, 0),
        )
        assert result.result is EligibilityResultValue.INELIGIBLE


class TestKillAndHealth:
    def test_kill_triggered(self):
        evaluation = KillCriteriaEvaluator().evaluate(
            strategy_id=new_identifier("strategy_id"), strategy_version="1.0.0",
            criteria_version="1.0.0",
            rules=({"rule_id": "K-DD", "field": "risk.drawdown_pct", "op": "<=",
                    "value": "8", "on_trigger": "KILL_REQUIRED"},),
            evidence={"risk": {"drawdown_pct": "12"}}, environment="SIMULATION",
            at=at(12, 0),
        )
        assert evaluation.result is KillResult.KILL_REQUIRED
        assert evaluation.triggered

    def test_kill_unknown_evidence_warns(self):
        evaluation = KillCriteriaEvaluator().evaluate(
            strategy_id=new_identifier("strategy_id"), strategy_version="1.0.0",
            criteria_version="1.0.0",
            rules=({"rule_id": "K-DRIFT", "field": "model.drift", "op": "<=",
                    "value": "0.1", "on_trigger": "SUSPEND_REQUIRED"},),
            evidence={}, environment="SIMULATION", at=at(12, 0),
        )
        assert evaluation.result is KillResult.WARNING  # unknown drift boundary -> warning, not silent ACTIVE

    def test_health_contract(self):
        health = StrategyHealth(
            strategy_id=new_identifier("strategy_id"), operational_state="RUNNING",
            data_state="VALIDATED", risk_state="NORMAL",
            lifecycle_state=StrategyLifecycle.DEMO, health_status=HealthStatus.HEALTHY,
            capacity_status="OK", environment="SIMULATION",
        )
        health.validate()


class TestIntent:
    def _intent(self, **overrides):
        defaults = dict(
            intent_id=new_identifier("intent_id"),
            strategy_id=new_identifier("strategy_id"), strategy_version="1.0.0",
            intent_type=IntentType.OPEN, symbol="XAUUSD",
            direction=IntentDirection.LONG, requested_quantity="0.5",
            entry_conditions=({"condition_id": "C-ENTRY"},), exit_conditions=(),
            urgency=Urgency.NORMAL, rationale="trend continuation confirmed",
            risk_context_hash="a" * 64, source_event_id=new_identifier("event_id"),
            correlation_id=new_identifier("correlation_id"), environment="SIMULATION",
            created_at=at(12, 0), expires_at=at(12, 30),
        )
        defaults.update(overrides)
        return StrategyIntent(**defaults)

    def test_valid_intent(self):
        self._intent().validate()

    def test_expiry(self):
        intent = self._intent()
        assert intent.is_expired(at(12, 31)) is True
        assert intent.is_expired(at(12, 10)) is False

    def test_flat_direction_cannot_increase_risk(self):
        with pytest.raises(ContractValidationError) as excinfo:
            self._intent(direction=IntentDirection.FLAT).validate()
        assert excinfo.value.rule_id == "INTENT-001"

    def test_zero_quantity_open_rejected(self):
        with pytest.raises(ContractValidationError):
            self._intent(requested_quantity="0").validate()

    def test_float_quantity_rejected(self):
        with pytest.raises(ContractValidationError):
            self._intent(requested_quantity=0.5).validate()

    def test_rationale_required(self):
        with pytest.raises(ContractValidationError):
            self._intent(rationale="").validate()

    def test_intent_is_not_order(self):
        intent = self._intent()
        # intents carry no execution fields at all
        assert not hasattr(intent, "order_type")
        assert not hasattr(intent, "time_in_force")


class TestDependencyGraph:
    def test_dependencies_and_impact(self):
        graph = DependencyGraph()
        graph.add_dependency("STRATEGY", "str_a", "DATA_SOURCE", "feed-xauusd")
        graph.add_dependency("STRATEGY", "str_b", "DATA_SOURCE", "feed-xauusd")
        graph.add_dependency("STRATEGY", "str_a", "SYMBOL", "XAUUSD")
        impacted = graph.impacted_by("DATA_SOURCE", "feed-xauusd")
        assert ("STRATEGY", "str_a") in impacted and ("STRATEGY", "str_b") in impacted

    def test_cycle_rejected(self):
        graph = DependencyGraph()
        graph.add_dependency("STRATEGY", "str_a", "DATA_SOURCE", "feed-1")
        graph.add_dependency("DATA_SOURCE", "feed-1", "FEATURE", "feat-1")
        with pytest.raises(DependencyCycleError):
            graph.add_dependency("FEATURE", "feat-1", "STRATEGY", "str_a")
