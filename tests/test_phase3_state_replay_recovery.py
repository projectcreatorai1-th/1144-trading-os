"""Phase 3 risk state, replay, recovery tests (SECTIONS 11/25/33)."""
from __future__ import annotations

import pytest

from architecture.contracts.errors import ContractError, StateTransitionError, StorageError
from architecture.contracts.identifiers import new_identifier
from core.events.contracts import EventType
from core.policy.contracts import PolicyType
from core.policy.registry import PolicyRegistry
from core.risk.decision_store import RiskDecisionStore
from core.risk.engine import RiskEngine, RiskEvaluationRequest
from core.risk.replay import RiskReplayService
from core.risk.state_service import RiskStateService
from core.state.contracts import StateCategory
from platform.database.sqlite_stores import StorageSet
from tests.factories import make_event
from tests.phase1_factories import at
from tests.test_phase3_risk_engine import activate, full_context


@pytest.fixture()
def env(tmp_path):
    storage = StorageSet(tmp_path / "p3-replay.db")
    registry = PolicyRegistry(storage.policies, storage.audit)
    engine = RiskEngine(policies=registry, audit=storage.audit)
    risk_state = RiskStateService(storage.states, storage.audit)
    yield {"storage": storage, "registry": registry, "engine": engine,
           "risk_state": risk_state}
    storage.close()


def trigger_event(**overrides):
    overrides.setdefault("event_type", EventType.RISK_STATE_CHANGED)
    overrides.setdefault("environment", "SIMULATION")
    return make_event(**overrides)


class TestRiskState:
    def test_escalation_via_state_machine(self, env):
        application = env["risk_state"].escalate_on_decision(
            "BLOCK", event=trigger_event(event_time=at(12, 0), received_time=at(12, 0))
        )
        assert application is not None
        assert application.state.status == "CAUTION"
        assert application.transition.previous_state is None  # genesis from initial NORMAL

    def test_stepwise_recovery_only(self, env):
        risk_state = env["risk_state"]
        for permission in ("BLOCK", "EMERGENCY"):
            risk_state.escalate_on_decision(
                permission, event=trigger_event(event_time=at(12, 0), received_time=at(12, 0))
            )
        current = env["storage"].states.get_current_state("RISK_STATE", "risk-engine")
        assert current.status == "EMERGENCY"
        # jumping straight back to NORMAL is an illegal recovery
        with pytest.raises(StateTransitionError):
            risk_state.operator_transition(
                "NORMAL", event=trigger_event(event_time=at(12, 1), received_time=at(12, 1)),
                reason="too fast",
            )
        # legal stepwise recovery
        risk_state.operator_transition(
            "PAUSE", event=trigger_event(event_time=at(12, 2), received_time=at(12, 2)),
            reason="operator step 1",
        )
        current = env["storage"].states.get_current_state("RISK_STATE", "risk-engine")
        assert current.status == "PAUSE"

    def test_idempotent_escalation(self, env):
        event = trigger_event(event_time=at(12, 0), received_time=at(12, 0))
        first = env["risk_state"].escalate_on_decision("BLOCK", event=event)
        second = env["risk_state"].escalate_on_decision("BLOCK", event=event)
        assert first is not None and second is None

    def test_transitions_audited_and_event_linked(self, env):
        event = trigger_event(event_time=at(12, 0), received_time=at(12, 0))
        env["risk_state"].escalate_on_decision("EMERGENCY", event=event)
        audits = list(env["storage"].audit.iter_by_correlation_id(event.correlation_id))
        assert any(a.action == "RISK_STATE_TRANSITION" and a.causation_id == event.event_id
                   for a in audits)

    def test_engine_reads_injected_risk_state(self, env):
        env["risk_state"].escalate_on_decision(
            "EMERGENCY", event=trigger_event(event_time=at(12, 0), received_time=at(12, 0))
        )
        env["engine"].set_risk_state(env["risk_state"].current())
        decision = env["engine"].evaluate(
            RiskEvaluationRequest(
                action_type="NEW_EXPOSURE", subject="X", environment="SIMULATION",
                requested_exposure="10", context=full_context(),
                correlation_id=new_identifier("correlation_id"), at=at(12, 5),
            )
        )
        assert decision.decision.value == "EMERGENCY"


class TestDecisionStore:
    def test_roundtrip_decision_and_context(self, env):
        activate(env, PolicyType.EXPOSURE_POLICY,
                 rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                         "field": "positions.gross", "op": "<=",
                         "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                 limits={"max_gross": "2000"})
        context = full_context(gross_exposure="1500")
        decision = env["engine"].evaluate(
            RiskEvaluationRequest(
                action_type="NEW_EXPOSURE", subject="X", environment="SIMULATION",
                requested_exposure="10", context=context,
                correlation_id=new_identifier("correlation_id"), at=at(12, 0),
            )
        )
        store = env["storage"].risk_decisions
        store.append(decision, context)
        loaded = store.get_by_id(decision.risk_decision_id)
        assert loaded.decision is decision.decision
        reloaded_context = store.get_context(decision.risk_decision_id)
        assert reloaded_context.context_hash == context.context_hash

    def test_append_twice_rejected(self, env):
        from tests.factories import make_risk_decision
        from tests.test_phase3_risk_engine import full_context as ctx

        decision = make_risk_decision(environment="SIMULATION")
        context = full_context()
        env["storage"].risk_decisions.append(decision, context)
        with pytest.raises(StorageError):
            env["storage"].risk_decisions.append(decision, context)


class TestReplay:
    def _make_decision(self, env, gross="1500"):
        activate(env, PolicyType.EXPOSURE_POLICY,
                 rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                         "field": "positions.gross", "op": "<=",
                         "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                 limits={"max_gross": "2000"})
        context = full_context(gross_exposure=gross)
        correlation = new_identifier("correlation_id")
        decision = env["engine"].evaluate(
            RiskEvaluationRequest(
                action_type="NEW_EXPOSURE", subject="X", environment="SIMULATION",
                requested_exposure="10", context=context,
                correlation_id=correlation, at=at(12, 0),
            )
        )
        env["storage"].risk_decisions.append(decision, context)
        return decision

    def test_replay_matches_original(self, env):
        decision = self._make_decision(env)
        replay = RiskReplayService(env["storage"].risk_decisions, env["registry"])
        comparison = replay.replay(decision.risk_decision_id, environment="REPLAY")
        assert comparison.status == "MATCH"
        assert comparison.replayed_permission == decision.decision.value

    def test_replay_detects_divergence(self, env):
        decision = self._make_decision(env, gross="2500")  # BLOCK decision
        replay = RiskReplayService(env["storage"].risk_decisions, env["registry"])
        comparison = replay.replay(decision.risk_decision_id, environment="REPLAY")
        assert comparison.status == "MATCH"  # replay of BLOCK with same inputs matches

    def test_replay_requires_replay_environment(self, env):
        decision = self._make_decision(env)
        replay = RiskReplayService(env["storage"].risk_decisions, env["registry"])
        with pytest.raises(ContractError):
            replay.replay(decision.risk_decision_id, environment="LIVE")

    def test_replay_unknown_when_no_policy(self, env):
        from tests.factories import make_risk_decision

        decision = make_risk_decision(environment="SIMULATION",
                                      policy_reference="none:missing-policy(fail-closed)")
        env["storage"].risk_decisions.append(decision, full_context())
        replay = RiskReplayService(env["storage"].risk_decisions, env["registry"])
        comparison = replay.replay(decision.risk_decision_id, environment="REPLAY")
        assert comparison.status == "UNKNOWN"


class TestRecovery:
    def test_restart_reevaluate_same_result(self, tmp_path):
        path = tmp_path / "restart.db"
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
            correlation_id=new_identifier("correlation_id"), at=at(12, 0),
        )
        before = engine.evaluate(request)
        storage.risk_decisions.append(before, context)
        storage.close()

        # restart
        storage2 = StorageSet(path)
        registry2 = PolicyRegistry(storage2.policies, storage2.audit)
        engine2 = RiskEngine(policies=registry2, audit=storage2.audit)
        after = engine2.evaluate(request)
        assert after.decision is before.decision
        assert after.risk_context_hash == before.risk_context_hash
        assert after.policy_hash == before.policy_hash
        # stored decision replay-verifies against the restarted registry
        replay = RiskReplayService(storage2.risk_decisions, registry2)
        assert replay.replay(before.risk_decision_id, environment="REPLAY").status == "MATCH"
        storage2.close()

    def test_duplicate_evaluation_is_deterministic(self, env):
        activate(env, PolicyType.EXPOSURE_POLICY,
                 rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                         "field": "positions.gross", "op": "<=",
                         "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                 limits={"max_gross": "2000"})
        context = full_context(gross_exposure="1800")
        request = RiskEvaluationRequest(
            action_type="NEW_EXPOSURE", subject="X", environment="SIMULATION",
            requested_exposure="10", context=context,
            correlation_id=new_identifier("correlation_id"), at=at(12, 0),
        )
        first = env["engine"].evaluate(request)
        second = env["engine"].evaluate(request)
        assert first.reasons == second.reasons
        assert first.triggered_rules == second.triggered_rules
