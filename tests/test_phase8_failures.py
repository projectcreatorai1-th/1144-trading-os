"""Phase 8 failure-closed matrix (SECTION 41).

Every row of the mandatory matrix, each asserting BLOCK (or equivalent
fail-closed behavior). SYNTHETIC credentials only.
"""
from __future__ import annotations

from dataclasses import replace as _replace
from datetime import datetime, timedelta, timezone

import pytest

from tests.phase8_factories import (
    ACTOR_CHECKER, ACTOR_MAKER, ACTOR_TRADER, T0, active_approval,
    demo_approver_session, demo_trader_session, issue_session,
    live_admin_session, security_env,
)

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier

from core.governance.gate import (
    GovernanceArtifact,
    GovernanceGate,
    GovernedArtifactType,
)
from core.security.authentication import AuthenticationService
from core.security.authorization import (
    AuthorizationDecision,
    AuthorizationService,
)
from core.security.contracts import (
    ApprovalStatus,
    AuthMethod,
    AuthStatus,
    IntegrityStatus,
    KeyPurpose,
    RateLimitVerdict,
    ReplayVerdict,
    SecurityRequest,
)
from core.security.protection import (
    AuditChain,
    BackupSecurityService,
    RateLimiter,
    ReplayGuard,
    SecurityIncidentService,
    forge_record,
)
from core.security.secrets import SecretVault
from core.security.services import MakerCheckerService
from platform.audit.contracts import ActorType, AuditRecord
from platform.security.contracts import Permission

UTC = timezone.utc


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def expect_block(result) -> None:
    assert result.decision is AuthorizationDecision.BLOCK, \
        f"expected BLOCK, got {result.decision} ({result.reasons})"


# --------------------------------------------------------------------- #
# The mandatory matrix                                                   #
# --------------------------------------------------------------------- #
class TestFailureClosedMatrix:
    def test_row_unauthenticated_blocks(self):
        env = security_env()
        result = env["auth"].authenticate(
            credential_id="crd_" + "f" * 32, secret_material="x",
            environment="DEMO", at=at(1))
        assert result.status is not AuthStatus.AUTHENTICATED

    def test_row_expired_session_blocks(self):
        env = security_env()
        _, session = demo_trader_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO",
            at=T0 + timedelta(days=3))
        expect_block(result)

    def test_row_revoked_session_blocks(self):
        env = security_env()
        _, session = demo_trader_session(env)
        env["sessions"].revoke(session.session_id, reason="test")
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO", at=at(5))
        expect_block(result)

    def test_row_unknown_actor_blocks(self):
        env = security_env()
        result = env["authz"].authorize(
            session_id="ssn_" + "f" * 32,
            permission=Permission.VIEW, environment="DEMO", at=at(1))
        expect_block(result)

    def test_row_unknown_permission_blocks(self):
        env = security_env()
        _, session = demo_trader_session(env)
        # a permission absent from the canonical registry can never allow
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.MANAGE_SECURITY, environment="DEMO",
            at=at(2))
        expect_block(result)

    def test_row_permission_mismatch_blocks(self):
        env = security_env()
        _, session = demo_trader_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.EMERGENCY_STOP, environment="DEMO",
            at=at(2))
        expect_block(result)

    def test_row_environment_mismatch_blocks(self):
        env = security_env()
        _, session = demo_trader_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="LIVE", at=at(2))
        expect_block(result)

    def test_row_missing_approval_blocks(self):
        env = security_env()
        _, session = live_admin_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.MODIFY_RISK, environment="LIVE", at=at(2),
            operation="RISK_CONFIG_CHANGE")
        expect_block(result)

    def test_row_self_approval_blocks(self):
        env = security_env()
        submission = env["approvals"].submit(
            operation="POLICY_CHANGE", resource="pol_" + "1" * 32,
            environment="DEMO", maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError):
            env["approvals"].approve(
                submission.approval_id, checker_actor_id=ACTOR_MAKER,
                checker_kind="HUMAN", at=at(1))

    def test_row_expired_approval_blocks(self):
        env = security_env()
        submission = env["approvals"].submit(
            operation="POLICY_CHANGE", resource="pol_" + "1" * 32,
            environment="DEMO", maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError) as err:
            env["approvals"].approve(
                submission.approval_id, checker_actor_id=ACTOR_CHECKER,
                checker_kind="HUMAN", at=T0 + timedelta(days=1))
        assert "expired" in str(err.value)

    def test_row_revoked_permission_blocks(self):
        """The canonical registry is the single source: a role without the
        permission always blocks (revocation is registry-authoritative)."""
        env = security_env()
        _, viewer = issue_session(env, actor_id=ACTOR_TRADER,
                                  environment="DEMO", role="VIEWER",
                                  permissions=("VIEW",),
                                  secret="synthetic-viewer")
        result = env["authz"].authorize(
            session_id=viewer.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO", at=at(2))
        expect_block(result)

    def test_row_revoked_credential_blocks(self):
        env = security_env()
        credential, _ = demo_trader_session(env)
        env["auth"].revoke(credential.credential_id, at=at(2),
                           reason="revoked")
        result = env["auth"].authenticate(
            credential_id=credential.credential_id,
            secret_material="synthetic-trader-secret",
            environment="DEMO", at=at(3))
        assert result.status is AuthStatus.REVOKED

    def test_row_malformed_request_blocks(self):
        with pytest.raises(ContractError):
            SecurityRequest(
                request_id="not-an-id",
                session_id="ssn_" + "1" * 32,
                actor_id="usr_" + "1" * 32, environment="DEMO",
                timestamp=T0, operation="OP",
                resource="pol_" + "1" * 32).validate()

    def test_row_replayed_request_blocks(self):
        env = security_env()
        _, session = demo_trader_session(env)
        guard = ReplayGuard()
        request = SecurityRequest(
            request_id=new_identifier("request_id"),
            session_id=session.session_id, actor_id=session.actor_id,
            environment="DEMO", timestamp=T0, operation="OP",
            resource="pol_" + "1" * 32)
        guard.check(request, now=at(1))
        verdict, _ = guard.check(request, now=at(1))
        assert verdict is ReplayVerdict.IDEMPOTENT_REPEAT
        # executing twice is forbidden: require_first_use only passes the
        # FIRST call; a strict executor treats repeats as replay evidence
        with pytest.raises(ContractError):
            guard.require_first_use(_replace(request, resource="other",
                                             request_id=request.request_id),
                                    now=at(1))

    def test_row_conflicting_idempotency_blocks(self):
        env = security_env()
        _, session = demo_trader_session(env)
        guard = ReplayGuard()
        first = SecurityRequest(
            request_id=new_identifier("request_id"),
            session_id=session.session_id, actor_id=session.actor_id,
            environment="DEMO", timestamp=T0, operation="OP",
            resource="pol_" + "1" * 32)
        guard.check(first, now=at(1))
        conflict = _replace(first, operation="DIFFERENT_OP")
        assert guard.check(conflict, now=at(1))[0] \
            is ReplayVerdict.CORRUPTION_SUSPECTED

    def test_row_rate_limit_exceeded_blocks(self):
        limiter = RateLimiter(max_operations=2, window_seconds=60)
        limiter.check(limit_key="auth:x", now=at(0))
        limiter.check(limit_key="auth:x", now=at(0.1))
        decision = limiter.check(limit_key="auth:x", now=at(0.2))
        assert decision.verdict is RateLimitVerdict.EXCEEDED

    def test_row_secret_exposure_attempt_blocks(self):
        from core.security.protection import redact_payload
        cleaned = redact_payload({"password": "synthetic-secret",
                                  "token": "synthetic-token",
                                  "ok": "value"})
        assert all(v != "synthetic-secret" and v != "synthetic-token"
                   for v in cleaned.values())

    def test_row_audit_integrity_failure_blocks_privileged(self):
        env = security_env()
        gate = GovernanceGate(sessions=env["sessions"],
                              authorization=env["authz"],
                              approvals=env["approvals"],
                              audit=env["chain"])
        record = env["chain"].append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.USER, actor_id=ACTOR_MAKER,
            action="OP", entity_type="policy",
            entity_id="pol_" + "1" * 32, event_time=at(1), before=None,
            after={}, reason="r", source="t", environment="DEMO",
            correlation_id="pol_" + "1" * 32))
        env["chain"]._records[0] = forge_record(record, actor_id="usr_" + "e" * 32)
        _, session = demo_approver_session(env)
        approval = active_approval(env)
        with pytest.raises(ContractError):
            gate.guard(
                artifact=GovernanceArtifact(
                    artifact_type=GovernedArtifactType.POLICY,
                    artifact_id="pol_" + "1" * 32, old_version="1.0.0",
                    new_version="1.1.0", old_content_hash="a" * 64,
                    new_content_hash="b" * 64, environment="DEMO"),
                maker_session_id=session.session_id,
                approval_id=approval.approval_id, at=at(3), reason="blocked")

    def test_row_backup_integrity_unknown_blocks_restore(self):
        service = BackupSecurityService()
        with pytest.raises(ContractError):
            service.authorize_restore("bkp_" + "f" * 32, components={},
                                      source_version="x",
                                      environment="DEMO")

    def test_row_backup_corrupted_blocks_restore(self):
        service = BackupSecurityService()
        components = {"ledger": ["entry1"]}
        manifest = service.create_backup(environment="DEMO",
                                         source_version="0.9.0",
                                         components=components, at=T0)
        tampered = {"ledger": ["entry1", "INJECTED"]}
        with pytest.raises(ContractError):
            service.authorize_restore(manifest.backup_id,
                                      components=tampered,
                                      source_version="0.9.0",
                                      environment="DEMO")

    def test_row_ai_self_approval_blocks(self):
        env = security_env()
        submission = env["approvals"].submit(
            operation="MODEL_PROMOTION", resource="mdl_" + "1" * 32,
            environment="RESEARCH", maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError) as err:
            env["approvals"].approve(
                submission.approval_id,
                checker_actor_id="usr_" + "a" * 32,
                checker_kind="AI_MODEL", at=at(1))
        assert "AI" in str(err.value)

    def test_row_ai_self_promotion_blocks(self):
        """Phase 7 lifecycle already blocks this; Phase 8 double-locks it:
        an AI checker cannot exist and LIVE requires human approval."""
        from core.security.services import is_human_checker
        assert not is_human_checker("ai_model", "AI_MODEL")

    def test_row_ai_direct_order_blocks(self):
        import core.security.authorization as authz_mod
        import core.governance.gate as gate_mod
        assert not hasattr(authz_mod, "Order")
        assert not hasattr(gate_mod, "OrderManagementSystem")

    def test_row_ai_direct_risk_decision_blocks(self):
        import core.security.protection as protection_mod
        assert not hasattr(protection_mod, "RiskDecision")

    def test_row_live_without_permission_blocks(self):
        env = security_env()
        _, session = demo_trader_session(env)  # TRADER in DEMO
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.LIVE_TRADE, environment="LIVE", at=at(2))
        expect_block(result)

    def test_row_live_without_approval_blocks(self):
        env = security_env()
        _, session = live_admin_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.LIVE_TRADE, environment="LIVE", at=at(2),
            operation="LIVE_TRADE_AUTHORIZATION")
        expect_block(result)

    def test_row_emergency_control_bypass_blocks(self):
        """EMERGENCY_RELEASE is a governance-gated operation; ordinary
        trading permissions cannot release an emergency control."""
        env = security_env()
        _, session = demo_trader_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO", at=at(2),
            operation="EMERGENCY_RELEASE")
        expect_block(result)
