"""Phase 3 policy tests: lifecycle, registry, resolution, evaluator (SECTIONS 5-7)."""
from __future__ import annotations

import pytest

from architecture.contracts.errors import ContractError, PermissionError_
from architecture.contracts.identifiers import new_identifier
from core.policy.contracts import PolicyStatus, PolicyType
from core.policy.evaluation import PolicyEvaluator, policy_content_hash
from core.policy.registry import ActorContext, PolicyRegistry, PolicyRegistryError
from platform.database.sqlite_stores import StorageSet
from platform.security.contracts import Role
from tests.phase1_factories import at


@pytest.fixture()
def env(tmp_path):
    storage = StorageSet(tmp_path / "p3-policy.db")
    registry = PolicyRegistry(storage.policies, storage.audit)
    yield {"storage": storage, "registry": registry}
    storage.close()


AUTHOR = ActorContext(user_id=new_identifier("user_id"), role=Role.APPROVER)
AUTHOR_WITH_POLICY = ActorContext(user_id=new_identifier("user_id"), role=Role.ADMIN)


def make_risk_policy(**overrides):
    from tests.factories import make_policy

    defaults = dict(
        policy_type=PolicyType.EXPOSURE_POLICY,
        status=PolicyStatus.DRAFT,
        environment="SIMULATION",
        name="exposure guard",
        description="caps gross exposure",
        conditions=(
            {"rule_id": "R-EXP-01", "dimension": "EXPOSURE", "field": "positions.gross",
             "op": "<=", "limit": "max_gross_exposure", "on_trigger": "BLOCK", "critical": True},
        ),
        actions=({"constrain": "new_exposure"},),
        limits={"max_gross_exposure": "10000"},
        priority=10,
    )
    defaults.update(overrides)
    return make_policy(**defaults)


def full_lifecycle(registry, policy, at_time):
    author = ActorContext(user_id=policy.created_by, role=Role.ADMIN)
    approver = ActorContext(user_id=new_identifier("user_id"), role=Role.APPROVER)
    registry.create(policy, author, at=at_time)
    latest = latest_version(registry, policy.policy_id)
    registry.submit_review(policy.policy_id, latest.policy_version, author, at=at_time)
    latest = latest_version(registry, policy.policy_id)
    registry.approve(policy.policy_id, latest.policy_version, approver, at=at_time)
    latest = latest_version(registry, policy.policy_id)
    registry.activate(policy.policy_id, latest.policy_version, author, at=at_time)
    return latest_version(registry, policy.policy_id)


def latest_version(registry, policy_id):
    versions = list(registry._store.iter_versions(policy_id))
    return versions[-1]


class TestLifecycle:
    def test_full_lifecycle_reaches_active(self, env):
        policy = make_risk_policy()
        active = full_lifecycle(env["registry"], policy, at(9, 0))
        assert active.status is PolicyStatus.ACTIVE
        assert active.approved_by != policy.created_by  # approval separation

    def test_activation_requires_approved(self, env):
        policy = make_risk_policy()
        registry = env["registry"]
        author = ActorContext(user_id=policy.created_by, role=Role.ADMIN)
        registry.create(policy, author, at=at(9, 0))
        latest = latest_version(registry, policy.policy_id)
        with pytest.raises(PolicyRegistryError) as excinfo:
            registry.activate(policy.policy_id, latest.policy_version, author, at=at(9, 1))
        assert excinfo.value.rule_id == "POLICY-002"

    def test_self_approval_forbidden(self, env):
        registry = env["registry"]
        policy = make_risk_policy()
        author = ActorContext(user_id=policy.created_by, role=Role.ADMIN)
        registry.create(policy, author, at=at(9, 0))
        latest = latest_version(registry, policy.policy_id)
        registry.submit_review(policy.policy_id, latest.policy_version, author, at=at(9, 1))
        latest = latest_version(registry, policy.policy_id)
        with pytest.raises(PolicyRegistryError) as excinfo:
            registry.approve(policy.policy_id, latest.policy_version, author, at=at(9, 2))
        assert excinfo.value.rule_id == "POLICY-003"

    def test_unauthorized_role_rejected(self, env):
        viewer = ActorContext(user_id=new_identifier("user_id"), role=Role.VIEWER)
        with pytest.raises(PermissionError_):
            env["registry"].create(make_risk_policy(), viewer, at=at(9, 0))

    def test_permissionless_approver_rejected(self, env):
        trader = ActorContext(user_id=new_identifier("user_id"), role=Role.TRADER)
        with pytest.raises(PermissionError_):
            env["registry"].approve("pol_x", "1.0.0", trader, at=at(9, 0))

    def test_lifecycle_audited(self, env):
        policy = make_risk_policy()
        full_lifecycle(env["registry"], policy, at(9, 0))
        actions = [
            record.action for record in env["storage"].audit.iter_by_correlation_id(policy.policy_id)
        ]
        for expected in ("POLICY_CREATED", "POLICY_REVIEW_SUBMITTED",
                         "POLICY_APPROVED", "POLICY_ACTIVATED"):
            assert expected in actions

    def test_versions_never_overwritten(self, env):
        policy = make_risk_policy()
        full_lifecycle(env["registry"], policy, at(9, 0))
        versions = [p.policy_version for p in env["storage"].policies.iter_versions(policy.policy_id)]
        assert len(versions) >= 4  # draft, review, approved, active (+ patches)
        assert len(set(versions)) == len(versions)


class TestResolution:
    def test_resolve_active_policy(self, env):
        policy = make_risk_policy()
        full_lifecycle(env["registry"], policy, at(9, 0))
        resolved = env["registry"].resolve_active(
            PolicyType.EXPOSURE_POLICY, environment="SIMULATION", at=at(12, 0)
        )
        assert resolved is not None and resolved.policy_id == policy.policy_id

    def test_missing_policy_returns_none(self, env):
        assert env["registry"].resolve_active(
            PolicyType.MARGIN_POLICY, environment="SIMULATION", at=at(12, 0)
        ) is None

    def test_wrong_environment_not_resolved(self, env):
        policy = make_risk_policy()
        full_lifecycle(env["registry"], policy, at(9, 0))
        assert env["registry"].resolve_active(
            PolicyType.EXPOSURE_POLICY, environment="LIVE", at=at(12, 0)
        ) is None

    def test_expired_policy_not_resolved(self, env):
        policy = make_risk_policy(effective_to=at(10, 0))
        full_lifecycle(env["registry"], policy, at(9, 0))
        assert env["registry"].resolve_active(
            PolicyType.EXPOSURE_POLICY, environment="SIMULATION", at=at(12, 0)
        ) is None

    def test_priority_precedence(self, env):
        low = make_risk_policy(priority=5, limits={"max_gross_exposure": "5000"})
        high = make_risk_policy(priority=1, limits={"max_gross_exposure": "1000"})
        full_lifecycle(env["registry"], low, at(9, 0))
        full_lifecycle(env["registry"], high, at(9, 0))
        resolved = env["registry"].resolve_active(
            PolicyType.EXPOSURE_POLICY, environment="SIMULATION", at=at(12, 0)
        )
        assert resolved.policy_id == high.policy_id  # lower number = higher precedence

    def test_ambiguous_priority_fails_closed(self, env):
        first = make_risk_policy(priority=10)
        second = make_risk_policy(priority=10)
        full_lifecycle(env["registry"], first, at(9, 0))
        full_lifecycle(env["registry"], second, at(9, 0))
        with pytest.raises(PolicyRegistryError) as excinfo:
            env["registry"].resolve_active(
                PolicyType.EXPOSURE_POLICY, environment="SIMULATION", at=at(12, 0)
            )
        assert excinfo.value.rule_id == "POLICY-002"

    def test_effective_time_lookup(self, env):
        policy = make_risk_policy()
        full_lifecycle(env["registry"], policy, at(9, 0))
        historical = env["registry"].version_at_time(policy.policy_id, at(12, 0))
        assert historical is not None


class TestPolicyEvaluator:
    EVALUATOR = PolicyEvaluator()

    def _context(self, gross=None):
        from core.risk.context import build_context

        values = {}
        if gross is not None:
            values["gross_exposure"] = gross
        return build_context(as_of=at(12, 0), environment="SIMULATION", **values)

    def _policy(self, **overrides):
        return make_risk_policy(**overrides)

    def test_within_limit_allows(self, env):
        evaluation = self.EVALUATOR.evaluate(
            policy=self._policy(), context=self._context(gross="8000").flattened(),
            environment="SIMULATION", timestamp=at(12, 0),
            correlation_id=new_identifier("correlation_id"),
        )
        assert evaluation.result == "ALLOW"
        assert evaluation.triggered_rules == ()

    def test_beyond_limit_blocks(self, env):
        evaluation = self.EVALUATOR.evaluate(
            policy=self._policy(), context=self._context(gross="12000").flattened(),
            environment="SIMULATION", timestamp=at(12, 0),
            correlation_id=new_identifier("correlation_id"),
        )
        assert evaluation.result == "BLOCK"
        assert evaluation.triggered_rules[0]["rule_id"] == "R-EXP-01"
        assert evaluation.triggered_rules[0]["value"] == "12000"

    def test_critical_unknown_blocks(self, env):
        evaluation = self.EVALUATOR.evaluate(
            policy=self._policy(), context=self._context(gross=None).flattened(),
            environment="SIMULATION", timestamp=at(12, 0),
            correlation_id=new_identifier("correlation_id"),
        )
        assert evaluation.result == "BLOCK"
        assert evaluation.triggered_rules[0]["value"] == "UNKNOWN"

    def test_non_critical_unknown_warns_only(self, env):
        policy = self._policy(conditions=(
            {"rule_id": "R-SOFT", "dimension": "GENERAL", "field": "risk.recovery_state",
             "op": "==", "limit": "recovery_not_allowed_value", "on_trigger": "LIMITED",
             "critical": False},
        ), limits={"recovery_not_allowed_value": "LOCKED"})
        evaluation = self.EVALUATOR.evaluate(
            policy=policy, context=self._context(gross="1").flattened(),
            environment="SIMULATION", timestamp=at(12, 0),
            correlation_id=new_identifier("correlation_id"),
        )
        assert evaluation.result == "ALLOW"
        assert any("unknown_field" in warning for warning in evaluation.warnings)

    def test_environment_mismatch_fails_closed(self, env):
        policy = self._policy(environment="LIVE")
        evaluation = self.EVALUATOR.evaluate(
            policy=policy, context=self._context(gross="1").flattened(),
            environment="SIMULATION", timestamp=at(12, 0),
            correlation_id=new_identifier("correlation_id"),
        )
        assert evaluation.result == "BLOCK"
        assert "policy_environment_mismatch" in evaluation.failed_rules

    def test_unknown_limit_reference_fails_closed(self, env):
        policy = self._policy(limits={"something_else": "1"})
        evaluation = self.EVALUATOR.evaluate(
            policy=policy, context=self._context(gross="1").flattened(),
            environment="SIMULATION", timestamp=at(12, 0),
            correlation_id=new_identifier("correlation_id"),
        )
        assert evaluation.result == "BLOCK"
        assert evaluation.failed_rules

    def test_reproducible_same_input_same_output(self, env):
        policy = self._policy()
        context = self._context(gross="9500").flattened()
        correlation = new_identifier("correlation_id")
        first = self.EVALUATOR.evaluate(
            policy=policy, context=context, environment="SIMULATION",
            timestamp=at(12, 0), correlation_id=correlation,
        )
        second = self.EVALUATOR.evaluate(
            policy=policy, context=context, environment="SIMULATION",
            timestamp=at(12, 0), correlation_id=correlation,
        )
        assert first.result == second.result
        assert first.context_hash == second.context_hash
        assert first.policy_hash == second.policy_hash

    def test_policy_hash_changes_with_content(self, env):
        assert policy_content_hash(self._policy()) != policy_content_hash(
            self._policy(limits={"max_gross_exposure": "9999"})
        )

    def test_evaluation_persisted_and_reloaded(self, env):
        policy = self._policy()
        evaluation = self.EVALUATOR.evaluate(
            policy=policy, context=self._context(gross="1").flattened(),
            environment="SIMULATION", timestamp=at(12, 0),
            correlation_id=new_identifier("correlation_id"),
        )
        env["storage"].policy_evaluations.append(evaluation)
        loaded = env["storage"].policy_evaluations.get_by_id(evaluation.evaluation_id)
        assert loaded.result == evaluation.result
        assert loaded.context_hash == evaluation.context_hash
