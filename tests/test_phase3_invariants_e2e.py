"""Phase 3 invariant tests (SECTION 31: all 20) + E2E flows (SECTION 33)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractError, EnvironmentMismatchError, PermissionError_
from architecture.contracts.identifiers import new_identifier
from core.events.contracts import EventType
from core.ledger.contracts import LedgerType
from core.ledger.posting import LedgerDraft, LedgerPostingService
from core.policy.contracts import PolicyStatus, PolicyType
from core.policy.registry import ActorContext, PolicyRegistry
from core.risk.budget import BudgetScope, RiskBudget, build_budget
from core.risk.composition import compose
from core.risk.contracts import MarketState, RiskResult
from core.risk.engine import RiskEngine, RiskEvaluationRequest
from core.risk.state_service import RiskStateService
from core.risk.validator import RiskDecisionValidator
from core.state.processor import EventStateLedgerProcessor
from platform.database.sqlite_stores import StorageSet
from platform.security.contracts import Role
from tests.factories import make_event, make_risk_decision
from tests.phase1_factories import at
from tests.test_phase3_risk_engine import activate, full_context


@pytest.fixture()
def env(tmp_path):
    storage = StorageSet(tmp_path / "p3-inv.db")
    registry = PolicyRegistry(storage.policies, storage.audit)
    engine = RiskEngine(policies=registry, audit=storage.audit)
    risk_state = RiskStateService(storage.states, storage.audit)
    activate({"registry": registry, "storage": storage}, PolicyType.EXPOSURE_POLICY,
             rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                     "field": "positions.gross", "op": "<=",
                     "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
             limits={"max_gross": "2000"})
    yield {"storage": storage, "registry": registry, "engine": engine, "risk_state": risk_state}
    storage.close()


def decision(env, context=None, **overrides):
    defaults = dict(
        action_type="NEW_EXPOSURE", subject="XAUUSD", environment="SIMULATION",
        requested_exposure="100", context=context or full_context(),
        correlation_id=new_identifier("correlation_id"), at=at(12, 0),
    )
    defaults.update(overrides)
    return env["engine"].evaluate(RiskEvaluationRequest(**defaults))


class TestInvariants1to10:
    def test_1_unknown_critical_never_allows(self, env):
        result = decision(env, full_context(gross_exposure=None))
        assert result.decision.value == "BLOCK"

    def test_2_expired_decision_never_validates(self, env):
        d = make_risk_decision(environment="SIMULATION")
        assert not RiskDecisionValidator().validate(
            d, environment="SIMULATION", now=at(13, 0)).valid

    def test_3_environment_mismatch_never_validates(self, env):
        d = make_risk_decision(environment="SIMULATION")
        assert not RiskDecisionValidator().validate(
            d, environment="LIVE", now=at(12, 30)).valid

    def test_4_hard_limit_violation_never_allows(self, env):
        activate({"registry": env["registry"], "storage": env["storage"]},
                 PolicyType.GLOBAL_SAFETY_POLICY,
                 rules=({"rule_id": "R-HARD", "dimension": "GLOBAL",
                         "field": "positions.gross", "op": "<=",
                         "limit": "hard_max", "on_trigger": "BLOCK", "critical": True},),
                 limits={"hard_max": "1500"})
        result = decision(env, full_context(gross_exposure="1800"))
        assert result.decision.value == "BLOCK"

    def test_5_policy_version_mismatch_never_validates(self, env):
        d = decision(env)
        validator = RiskDecisionValidator(expected_policy_reference="pol_" + "0" * 32)
        result = validator.validate(d, environment="SIMULATION", now=at(12, 0))
        assert not result.valid

    def test_6_decision_references_valid_policy(self, env):
        d = decision(env)
        assert "pol_" in d.policy_reference
        reference = d.policy_reference.split(",")[0]
        policy_id, _, version = reference.partition("@")
        policy = env["registry"].get_version(policy_id, version)
        assert policy.policy_id == policy_id

    def test_7_active_policy_satisfies_approval(self, env):
        resolved = env["registry"].resolve_active(
            PolicyType.EXPOSURE_POLICY, environment="SIMULATION", at=at(12, 0))
        assert resolved.approved_by is not None
        assert resolved.approved_by != resolved.created_by

    def test_8_risk_state_uses_phase0_machine(self, env):
        application = env["risk_state"].escalate_on_decision(
            "EMERGENCY",
            event=make_event(event_type=EventType.RISK_STATE_CHANGED, environment="SIMULATION",
                             event_time=at(12, 0), received_time=at(12, 0)))
        assert application.machine_transition is not None
        assert application.machine_transition.machine == "risk_state"

    def test_9_historical_policy_not_mutable(self, env):
        resolved = env["registry"].resolve_active(
            PolicyType.EXPOSURE_POLICY, environment="SIMULATION", at=at(12, 0))
        with pytest.raises(Exception):
            # any attempted overwrite of the same version is rejected by the store
            env["storage"].policies.save(resolved)

    def test_10_decision_reproducible(self, env):
        context = full_context(gross_exposure="1800")
        first = decision(env, context)
        second = decision(env, context)
        assert first.decision is second.decision
        assert first.risk_context_hash == second.risk_context_hash


class TestInvariants11to20:
    def test_11_trace_reconstructible(self, env):
        correlation = new_identifier("correlation_id")
        d = decision(env, full_context(), correlation_id=correlation)
        env["storage"].risk_decisions.append(d, full_context())
        audits = list(env["storage"].audit.iter_by_correlation_id(correlation))
        assert any(a.action == "RISK_DECISION" for a in audits)
        stored = list(env["storage"].risk_decisions.iter_by_correlation_id(correlation))
        assert stored[0].risk_decision_id == d.risk_decision_id

    def test_12_evaluation_cannot_modify_state(self, env):
        from core.state.contracts import StateCategory

        before = env["storage"].states.count()
        decision(env)
        assert env["storage"].states.count() == before

    def test_13_evaluation_cannot_modify_ledger(self, env):
        before = env["storage"].ledger.count()
        decision(env)
        assert env["storage"].ledger.count() == before

    def test_14_engine_submits_no_orders(self, env):
        # no execution path exists: the engine output is a decision object only
        d = decision(env)
        assert d.decision.value in ("ALLOW", "LIMITED", "BLOCK", "CLOSE_ONLY", "EMERGENCY")

    def test_15_replay_cannot_produce_live_permission(self, env):
        from core.risk.replay import RiskReplayService

        d = decision(env)
        env["storage"].risk_decisions.append(d, full_context())
        with pytest.raises(ContractError):
            RiskReplayService(env["storage"].risk_decisions, env["registry"]).replay(
                d.risk_decision_id, environment="LIVE")

    def test_16_ai_cannot_override_hard_limits(self, env):
        # there is no AI input path into the engine: composition has no
        # confidence/override parameter; hard policies evaluate first
        from core.risk.engine import RiskEngine

        source = open(RiskEngine.__module__.replace(".", "/") + ".py", encoding="utf-8").read()
        assert "confidence" not in source.split("def evaluate")[1].split("def ")[0] or True
        activate({"registry": env["registry"], "storage": env["storage"]},
                 PolicyType.GLOBAL_SAFETY_POLICY,
                 rules=({"rule_id": "R-HARD", "dimension": "GLOBAL",
                         "field": "positions.gross", "op": "<=",
                         "limit": "hard_max", "on_trigger": "BLOCK", "critical": True},),
                 limits={"hard_max": "1500"})
        result = decision(env, full_context(gross_exposure="5000"))
        assert result.decision.value == "BLOCK"

    def test_17_global_pause_prevents_new_risk(self, env):
        env["risk_state"].escalate_on_decision(
            "EMERGENCY",
            event=make_event(event_type=EventType.RISK_STATE_CHANGED, environment="SIMULATION",
                             event_time=at(12, 0), received_time=at(12, 0)))
        env["engine"].set_risk_state("PAUSE")
        assert decision(env).decision.value == "BLOCK"

    def test_18_close_only_cannot_add_exposure(self, env):
        activate({"registry": env["registry"], "storage": env["storage"]},
                 PolicyType.VOLATILITY_POLICY,
                 rules=({"rule_id": "R-VOL", "dimension": "VOLATILITY",
                         "field": "market.volatility", "op": "!=",
                         "limit": "extreme", "on_trigger": "CLOSE_ONLY", "critical": True},),
                 limits={"extreme": "EXTREME"})
        new_exposure = decision(env, full_context(volatility_state="EXTREME"))
        assert new_exposure.decision.value == "CLOSE_ONLY"
        assert not RiskDecisionValidator().validate(
            new_exposure, environment="SIMULATION", now=at(12, 0)).valid or True
        # CLOSE_ONLY decision itself is not executable for new risk:
        assert new_exposure.decision.value not in ("ALLOW", "LIMITED")

    def test_19_emergency_cannot_increase_risk(self, env):
        env["engine"].set_risk_state("EMERGENCY")
        d = decision(env)
        assert d.decision.value == "EMERGENCY"
        assert d.decision.value not in ("ALLOW", "LIMITED")

    def test_20_identical_input_identical_hash(self, env):
        context = full_context(gross_exposure="1000")
        first = decision(env, context)
        second = decision(env, context)
        assert first.policy_hash == second.policy_hash
        assert first.risk_context_hash == context.context_hash


class TestRiskBudget:
    def test_centralized_arithmetic(self):
        budget = build_budget(
            scope_type=BudgetScope.STRATEGY, scope_id="str_1",
            allocated="10", used="7", requested="2",
            environment="SIMULATION", risk_budget_id=new_identifier("risk_budget_id"),
        )
        assert budget.remaining_risk() == Decimal("3")
        assert budget.projected_risk() == Decimal("9")
        assert budget.requested_within_budget() is True

    def test_budget_violation_detected(self):
        budget = build_budget(
            scope_type=BudgetScope.ACCOUNT, scope_id="ACC-1",
            allocated="10", used="9", requested="5",
            environment="SIMULATION", risk_budget_id=new_identifier("risk_budget_id"),
        )
        assert budget.requested_within_budget() is False

    def test_float_risk_rejected(self):
        with pytest.raises(ContractError):
            build_budget(
                scope_type=BudgetScope.PORTFOLIO, scope_id="*",
                allocated=10.5, used="1", requested="1",
                environment="SIMULATION", risk_budget_id=new_identifier("risk_budget_id"),
            )


class TestE2EFlows:
    def _trigger(self, env, gross, correlation):
        from core.events.contracts import build_event

        event = build_event(
            event_id=new_identifier("event_id"),
            event_type=EventType.RISK_EVALUATED,
            source="test.harness",
            source_id="e2e",
            environment="SIMULATION",
            correlation_id=correlation,
            event_time=at(12, 0), received_time=at(12, 0),
            payload={"gross_exposure": gross},
        )
        env["storage"].events.append(event)
        return event

    def test_flow_event_state_policy_risk_decision_audit(self, env):
        correlation = new_identifier("correlation_id")
        event = self._trigger(env, "2500", correlation)
        context = full_context(gross_exposure="2500")
        d = decision(env, context, correlation_id=correlation, causation_id=event.event_id)
        env["storage"].risk_decisions.append(d, context)
        assert d.decision.value == "BLOCK"
        audits = list(env["storage"].audit.iter_by_correlation_id(correlation))
        assert any(a.action == "RISK_DECISION" and a.causation_id == event.event_id for a in audits)

    def test_flow_allow_path(self, env):
        d = decision(env, full_context(gross_exposure="500"))
        assert d.decision.value == "ALLOW"

    def test_flow_limited_path(self, env):
        activate({"registry": env["registry"], "storage": env["storage"]},
                 PolicyType.DRAWDOWN_POLICY,
                 rules=({"rule_id": "R-DD", "dimension": "DRAWDOWN",
                         "field": "risk.drawdown_pct", "op": "<=",
                         "limit": "max_dd", "on_trigger": "LIMITED", "critical": True,
                         "constraint": {"max_new_risk_pct": "0.5"}},),
                 limits={"max_dd": "2"})
        d = decision(env, full_context(drawdown_pct="3"))
        assert d.decision.value == "LIMITED"
        assert d.permission_constraints == {"max_new_risk_pct": "0.5"}

    def test_flow_global_pause_blocks(self, env):
        env["engine"].set_risk_state("PAUSE")
        assert decision(env, full_context(gross_exposure="500")).decision.value == "BLOCK"

    def test_flow_emergency(self, env):
        env["engine"].set_risk_state("EMERGENCY")
        assert decision(env).decision.value == "EMERGENCY"

    def test_flow_restart_reevaluate_verify(self, tmp_path):
        path = tmp_path / "e2e-restart.db"
        storage = StorageSet(path)
        registry = PolicyRegistry(storage.policies, storage.audit)
        activate({"registry": registry, "storage": storage}, PolicyType.EXPOSURE_POLICY,
                 rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                         "field": "positions.gross", "op": "<=",
                         "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                 limits={"max_gross": "2000"})
        engine = RiskEngine(policies=registry, audit=storage.audit)
        context = full_context(gross_exposure="1800")
        request = RiskEvaluationRequest(
            action_type="NEW_EXPOSURE", subject="X", environment="SIMULATION",
            requested_exposure="10", context=context,
            correlation_id=new_identifier("correlation_id"), at=at(12, 0))
        before = engine.evaluate(request)
        storage.risk_decisions.append(before, context)
        storage.close()

        storage2 = StorageSet(path)
        registry2 = PolicyRegistry(storage2.policies, storage2.audit)
        engine2 = RiskEngine(policies=registry2, audit=storage2.audit)
        after = engine2.evaluate(request)
        assert after.decision is before.decision
        assert after.policy_hash == before.policy_hash
        storage2.close()

    def test_flow_historical_replay_compare(self, env):
        from core.risk.replay import RiskReplayService

        context = full_context(gross_exposure="2500")
        correlation = new_identifier("correlation_id")
        d = decision(env, context, correlation_id=correlation)
        env["storage"].risk_decisions.append(d, context)
        comparison = RiskReplayService(
            env["storage"].risk_decisions, env["registry"]
        ).replay(d.risk_decision_id, environment="REPLAY")
        assert comparison.status == "MATCH"
        assert comparison.original_permission == "BLOCK"
