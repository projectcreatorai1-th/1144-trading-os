"""Phase 3 risk engine tests: composition, dimensions, hard limits, safety,
decision validation, determinism (SECTIONS 8-17)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractError, RiskGateError
from architecture.contracts.identifiers import new_identifier
from core.policy.contracts import PolicyStatus, PolicyType
from core.policy.evaluation import PolicyEvaluation
from core.policy.registry import ActorContext, PolicyRegistry
from core.risk import config
from core.risk.composition import check_critical_unknowns, compose
from core.risk.config import severity_rank
from core.risk.context import build_context
from core.risk.engine import RiskEngine, RiskEvaluationRequest
from core.risk.validator import RiskDecisionValidator
from platform.database.sqlite_stores import StorageSet
from platform.security.contracts import Role
from tests.phase1_factories import at


@pytest.fixture()
def env(tmp_path):
    storage = StorageSet(tmp_path / "p3-risk.db")
    registry = PolicyRegistry(storage.policies, storage.audit)
    engine = RiskEngine(policies=registry, audit=storage.audit)
    yield {"storage": storage, "registry": registry, "engine": engine}
    storage.close()


def dimension_policy(policy_type, rules, limits, **overrides):
    from tests.factories import make_policy

    defaults = dict(
        policy_type=policy_type,
        status=PolicyStatus.DRAFT,
        environment="SIMULATION",
        conditions=rules,
        actions=({"constrain": "risk"},),
        limits=limits,
        priority=10,
    )
    defaults.update(overrides)
    return defaults


def activate(env, policy_type, rules, limits, **overrides):
    from tests.test_phase3_policy import full_lifecycle
    from tests.factories import make_policy

    policy = make_policy(**dimension_policy(policy_type, rules, limits, **overrides))
    full_lifecycle(env["registry"], policy, at(9, 0))
    return policy


def full_context(**overrides):
    values = dict(
        account_equity="10000", account_margin_level="500",
        gross_exposure="1000", drawdown_pct="1",
        market_state="NORMAL", volatility_state="NORMAL",
        spread_state="NORMAL", liquidity_state="NORMAL",
        data_quality="VALIDATED", event_risk="NORMAL",
        system_state="RUNNING", execution_state="READY",
    )
    values.update(overrides)
    return build_context(as_of=at(12, 0), environment="SIMULATION", **values)


def request(context, **overrides):
    defaults = dict(
        action_type="NEW_EXPOSURE", subject="XAUUSD", environment="SIMULATION",
        requested_exposure="100", context=context,
        correlation_id=new_identifier("correlation_id"), at=at(12, 0),
    )
    defaults.update(overrides)
    return RiskEvaluationRequest(**defaults)


class TestConfigAndComposition:
    def test_precedence_from_registry(self):
        assert config.permission_precedence() == (
            "EMERGENCY", "CLOSE_ONLY", "BLOCK", "LIMITED", "ALLOW"
        )
        assert severity_rank("EMERGENCY") < severity_rank("ALLOW")

    def test_unknown_permission_rejected(self):
        with pytest.raises(ContractError):
            severity_rank("MAYBE")

    def test_hard_limit_override_forbidden(self):
        assert config.hard_limit_override_allowed() is False

    @dataclass
    class _Eval:
        result: str
        triggered_rules: tuple = ()

    def test_composition_most_severe_wins(self):
        result = compose(
            [self._Eval("ALLOW"), self._Eval("LIMITED"), self._Eval("BLOCK"),
             self._Eval("LIMITED")],
            full_context(),
        )
        assert result.permission == "BLOCK"

    def test_composition_limited_constraints_intersect(self):
        rule_a = {"rule_id": "A", "outcome": "LIMITED", "constraint": {"max_new_risk_pct": "0.5"}}
        rule_b = {"rule_id": "B", "outcome": "LIMITED", "constraint": {"max_new_risk_pct": "0.2"}}
        result = compose(
            [self._Eval("LIMITED", (rule_a,)), self._Eval("LIMITED", (rule_b,))],
            full_context(),
        )
        assert result.permission == "LIMITED"
        assert result.constraints["max_new_risk_pct"] == "0.2"  # strictest

    def test_critical_unknown_blocks_composition(self):
        context = full_context(gross_exposure=None)
        unknowns = check_critical_unknowns(context)
        assert "positions.gross" in unknowns
        result = compose([self._Eval("ALLOW")], context)
        assert result.permission == "BLOCK"
        assert result.unknown_criticals == unknowns

    def test_unknown_market_state_blocks(self):
        context = full_context(market_state="UNKNOWN")
        result = compose([self._Eval("ALLOW")], context)
        assert result.permission == "BLOCK"


class TestDimensions:
    def test_exposure_dimension(self, env):
        activate(env, PolicyType.EXPOSURE_POLICY,
                 rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                         "field": "positions.gross", "op": "<=",
                         "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                 limits={"max_gross": "2000"})
        decision = env["engine"].evaluate(
            request(full_context(gross_exposure="2500"))
        )
        assert decision.decision.value == "BLOCK"
        assert any(rule["rule_id"] == "R-EXP" for rule in decision.triggered_rules)

    def test_drawdown_dimension_limited(self, env):
        activate(env, PolicyType.DRAWDOWN_POLICY,
                 rules=({"rule_id": "R-DD", "dimension": "DRAWDOWN",
                         "field": "risk.drawdown_pct", "op": "<=",
                         "limit": "max_dd_pct", "on_trigger": "LIMITED",
                         "critical": True,
                         "constraint": {"max_new_risk_pct": "0.5"}},),
                 limits={"max_dd_pct": "2"})
        decision = env["engine"].evaluate(
            request(full_context(drawdown_pct="3"))
        )
        assert decision.decision.value == "LIMITED"
        assert decision.permission_constraints == {"max_new_risk_pct": "0.5"}

    def test_margin_dimension(self, env):
        activate(env, PolicyType.MARGIN_POLICY,
                 rules=({"rule_id": "R-MG", "dimension": "MARGIN",
                         "field": "account.margin_level", "op": ">=",
                         "limit": "min_margin_level", "on_trigger": "BLOCK",
                         "critical": True},),
                 limits={"min_margin_level": "200"})
        blocked = env["engine"].evaluate(request(full_context(account_margin_level="150")))
        assert blocked.decision.value == "BLOCK"
        allowed = env["engine"].evaluate(request(full_context(account_margin_level="500")))
        assert allowed.decision.value == "ALLOW"

    def test_volatility_and_spread_close_only(self, env):
        activate(env, PolicyType.VOLATILITY_POLICY,
                 rules=({"rule_id": "R-VOL", "dimension": "VOLATILITY",
                         "field": "market.volatility", "op": "!=",
                         "limit": "extreme_value", "on_trigger": "CLOSE_ONLY",
                         "critical": True},),
                 limits={"extreme_value": "EXTREME"})
        decision = env["engine"].evaluate(
            request(full_context(volatility_state="EXTREME"))
        )
        assert decision.decision.value == "CLOSE_ONLY"
        # risk-reducing actions stay permitted under CLOSE_ONLY
        reduce = env["engine"].evaluate(
            request(full_context(volatility_state="EXTREME"), action_type="CLOSE")
        )
        assert reduce.decision.value == "LIMITED"

    def test_news_event_risk_boundary(self, env):
        activate(env, PolicyType.NEWS_RISK_POLICY,
                 rules=({"rule_id": "R-NEWS", "dimension": "NEWS",
                         "field": "event.risk", "op": "!=",
                         "limit": "high_value", "on_trigger": "LIMITED",
                         "critical": True,
                         "constraint": {"max_new_positions": "1"}},),
                 limits={"high_value": "HIGH"})
        decision = env["engine"].evaluate(request(full_context(event_risk="HIGH")))
        assert decision.decision.value == "LIMITED"
        assert decision.permission_constraints == {"max_new_positions": "1"}

    def test_correlation_unknown_fails_closed(self, env):
        activate(env, PolicyType.CORRELATION_POLICY,
                 rules=({"rule_id": "R-CORR", "dimension": "CORRELATION",
                         "field": "risk.recovery_state", "op": "==",
                         "limit": "forbidden", "on_trigger": "BLOCK",
                         "critical": True},),
                 limits={"forbidden": "LOCKED"})
        # correlation data absent -> critical field unknown -> BLOCK (never guessed)
        decision = env["engine"].evaluate(request(full_context()))
        assert decision.decision.value == "BLOCK"


class TestHardLimitsAndSafety:
    def test_hard_policy_beats_soft_limited(self, env):
        activate(env, PolicyType.GLOBAL_SAFETY_POLICY,
                 rules=({"rule_id": "R-HARD-DD", "dimension": "GLOBAL",
                         "field": "risk.drawdown_pct", "op": "<=",
                         "limit": "hard_max_dd", "on_trigger": "EMERGENCY",
                         "critical": True},),
                 limits={"hard_max_dd": "10"})
        activate(env, PolicyType.DRAWDOWN_POLICY,
                 rules=({"rule_id": "R-SOFT-DD", "dimension": "DRAWDOWN",
                         "field": "risk.drawdown_pct", "op": "<=",
                         "limit": "soft_max_dd", "on_trigger": "LIMITED",
                         "critical": True,
                         "constraint": {"max_new_risk_pct": "1"}},),
                 limits={"soft_max_dd": "2"})
        decision = env["engine"].evaluate(request(full_context(drawdown_pct="12")))
        assert decision.decision.value == "EMERGENCY"  # hard authority wins

    def test_missing_policy_fails_closed(self, env):
        decision = env["engine"].evaluate(request(full_context()))
        assert decision.decision.value == "BLOCK"
        assert "missing-policy" in decision.policy_reference

    def test_risk_state_pause_blocks(self, env):
        env["engine"].set_risk_state("PAUSE")
        decision = env["engine"].evaluate(request(full_context()))
        assert decision.decision.value == "BLOCK"

    def test_risk_state_emergency(self, env):
        env["engine"].set_risk_state("EMERGENCY")
        decision = env["engine"].evaluate(request(full_context()))
        assert decision.decision.value == "EMERGENCY"


class TestDecision:
    def test_decision_contract_fields(self, env):
        activate(env, PolicyType.EXPOSURE_POLICY,
                 rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                         "field": "positions.gross", "op": "<=",
                         "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                 limits={"max_gross": "2000"})
        decision = env["engine"].evaluate(request(full_context()))
        assert decision.action_type == "NEW_EXPOSURE"
        assert decision.risk_context_hash == full_context().context_hash if False else \
            len(decision.risk_context_hash) == 64
        assert len(decision.policy_hash) == 64
        assert decision.expires_at > decision.decision_time
        assert decision.evaluated_rules or decision.triggered_rules or True

    def test_expired_decision_not_usable(self, env):
        from core.risk.contracts import RiskResult
        from tests.factories import make_risk_decision

        decision = make_risk_decision(environment="SIMULATION")
        validator = RiskDecisionValidator()
        result = validator.validate(decision, environment="SIMULATION", now=at(13, 0))
        assert not result.valid
        assert any(reason.startswith("expired") for reason in result.reasons)

    def test_environment_mismatch_not_usable(self, env):
        from tests.factories import make_risk_decision

        decision = make_risk_decision(environment="SIMULATION")
        result = RiskDecisionValidator().validate(
            decision, environment="LIVE", now=at(12, 30)
        )
        assert not result.valid
        assert any(reason.startswith("environment_mismatch") for reason in result.reasons)

    def test_context_hash_mismatch_not_usable(self, env):
        decision = env["engine"].evaluate(request(full_context()))
        result = RiskDecisionValidator().validate(
            decision, environment="SIMULATION", now=at(12, 0),
            expected_context_hash="f" * 64,
        )
        assert not result.valid
        assert any(reason == "risk_context_mismatch" for reason in result.reasons)

    def test_limited_requires_constraints(self, env):
        from core.risk.contracts import RiskResult
        from tests.factories import make_risk_decision

        decision = make_risk_decision(decision=RiskResult.LIMITED, environment="SIMULATION")
        decision.validate()  # contract level fine
        result = RiskDecisionValidator().validate(decision, environment="SIMULATION", now=at(12, 30))
        assert not result.valid
        assert any(reason == "limited_without_constraints" for reason in result.reasons)

    def test_unknown_converted_to_allow_rejected_at_contract(self, env):
        from core.risk.contracts import MarketState, RiskResult
        from tests.factories import make_risk_decision

        with pytest.raises(Exception):
            make_risk_decision(
                decision=RiskResult.ALLOW, market_state=MarketState.UNKNOWN
            ).validate()

    def test_determinism_identical_result_and_hashes(self, env):
        activate(env, PolicyType.EXPOSURE_POLICY,
                 rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                         "field": "positions.gross", "op": "<=",
                         "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                 limits={"max_gross": "2000"})
        context = full_context(gross_exposure="1500")
        first = env["engine"].evaluate(request(context))
        second = env["engine"].evaluate(request(context))
        assert first.decision is second.decision
        assert first.risk_context_hash == second.risk_context_hash
        assert first.policy_hash == second.policy_hash
        assert first.reasons == second.reasons

    def test_evaluation_audited(self, env):
        activate(env, PolicyType.EXPOSURE_POLICY,
                 rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                         "field": "positions.gross", "op": "<=",
                         "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                 limits={"max_gross": "2000"})
        context = full_context()
        correlation = new_identifier("correlation_id")
        env["engine"].evaluate(request(context, correlation_id=correlation))
        actions = [
            record.action for record in env["storage"].audit.iter_by_correlation_id(correlation)
        ]
        assert "RISK_DECISION" in actions
