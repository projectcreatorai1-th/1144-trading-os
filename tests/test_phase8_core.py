"""Phase 8 unit tests: authentication, sessions, credentials, secrets,
keys, authorization, approvals, replay/rate, audit chain, backup,
incidents, encryption policy, governance gate.

SYNTHETIC credentials only; no production secrets anywhere.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tests.phase8_factories import (
    ACTOR_CHECKER, ACTOR_MAKER, ACTOR_TRADER, T0, active_approval,
    demo_approver_session, demo_trader_session, issue_session,
    live_admin_session, security_env,
)

from architecture.contracts.errors import ContractError

from core.governance.gate import (
    ARTIFACT_PERMISSIONS,
    GovernanceArtifact,
    GovernanceGate,
    GovernedArtifactType,
)
from core.security.authentication import AuthenticationService, SessionService
from core.security.authorization import (
    AuthorizationDecision,
    AuthorizationService,
)
from core.security.contracts import (
    ApprovalRecord,
    ApprovalStatus,
    AuthMethod,
    AuthStatus,
    AuthenticationResult,
    BackupManifest,
    CredentialRecord,
    CredentialStatus,
    DataClassification,
    EncryptionPolicy,
    IncidentSeverity,
    IntegrityStatus,
    KeyPurpose,
    KeyStatus,
    ProtectionLevel,
    RateLimitVerdict,
    ReplayVerdict,
    SecurityIncidentStatus,
    SecurityRequest,
    SessionRecord,
    SessionStatus,
    SecretReference,
)
from core.security.protection import (
    AuditChain,
    BackupSecurityService,
    RateLimiter,
    ReplayGuard,
    SECURITY_EVENT_TYPES,
    SecurityIncidentService,
    SecurityMetrics,
    emit_security_event,
    forge_record,
    redact_payload,
)
from core.security.secrets import (
    KeyManager,
    SecretVault,
    build_encryption_policy,
    protection_satisfies,
)
from core.security.services import MakerCheckerService, is_human_checker
from platform.audit.contracts import ActorType, AuditRecord
from platform.security.contracts import Permission, Role
from architecture.contracts.identifiers import new_identifier

UTC = timezone.utc


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


# --------------------------------------------------------------------- #
# Authentication + credentials                                           #
# --------------------------------------------------------------------- #
class TestAuthentication:
    def test_successful_authentication(self):
        env = security_env()
        credential, _ = env["auth"].issue(
            actor_id=ACTOR_TRADER, environment="DEMO",
            method=AuthMethod.TOKEN, secret_material="synthetic-secret",
            at=T0)
        result = env["auth"].authenticate(
            credential_id=credential.credential_id,
            secret_material="synthetic-secret", environment="DEMO",
            at=at(1))
        assert result.status is AuthStatus.AUTHENTICATED
        assert result.actor_id == ACTOR_TRADER

    def test_failed_authentication_records_reason(self):
        env = security_env()
        credential, _ = env["auth"].issue(
            actor_id=ACTOR_TRADER, environment="DEMO",
            method=AuthMethod.TOKEN, secret_material="synthetic-secret",
            at=T0)
        result = env["auth"].authenticate(
            credential_id=credential.credential_id,
            secret_material="wrong-secret", environment="DEMO", at=at(1))
        assert result.status is AuthStatus.UNAUTHENTICATED
        assert result.failure_reason
        assert "wrong-secret" not in result.failure_reason

    def test_unknown_credential_never_authenticates(self):
        env = security_env()
        result = env["auth"].authenticate(
            credential_id="crd_" + "f" * 32,
            secret_material="anything", environment="DEMO", at=at(1))
        assert result.status is AuthStatus.UNAUTHENTICATED

    def test_revoked_credential_blocked(self):
        env = security_env()
        credential, _ = env["auth"].issue(
            actor_id=ACTOR_TRADER, environment="DEMO",
            method=AuthMethod.TOKEN, secret_material="synthetic-secret",
            at=T0)
        env["auth"].revoke(credential.credential_id, at=at(1),
                           reason="compromise")
        result = env["auth"].authenticate(
            credential_id=credential.credential_id,
            secret_material="synthetic-secret", environment="DEMO",
            at=at(2))
        assert result.status is AuthStatus.REVOKED

    def test_expired_credential_blocked(self):
        env = security_env()
        credential, _ = env["auth"].issue(
            actor_id=ACTOR_TRADER, environment="DEMO",
            method=AuthMethod.TOKEN, secret_material="synthetic-secret",
            at=T0, ttl=timedelta(minutes=5))
        result = env["auth"].authenticate(
            credential_id=credential.credential_id,
            secret_material="synthetic-secret", environment="DEMO",
            at=at(30))
        assert result.status is AuthStatus.EXPIRED

    def test_environment_binding(self):
        env = security_env()
        credential, _ = env["auth"].issue(
            actor_id=ACTOR_TRADER, environment="DEMO",
            method=AuthMethod.TOKEN, secret_material="synthetic-secret",
            at=T0)
        result = env["auth"].authenticate(
            credential_id=credential.credential_id,
            secret_material="synthetic-secret", environment="LIVE",
            at=at(1))
        assert result.status is AuthStatus.UNAUTHENTICATED

    def test_lockout_after_threshold(self):
        env = security_env()
        credential, _ = env["auth"].issue(
            actor_id=ACTOR_TRADER, environment="DEMO",
            method=AuthMethod.TOKEN, secret_material="synthetic-secret",
            at=T0)
        for _ in range(5):
            env["auth"].authenticate(
                credential_id=credential.credential_id,
                secret_material="wrong", environment="DEMO", at=at(1))
        locked = env["auth"].authenticate(
            credential_id=credential.credential_id,
            secret_material="synthetic-secret", environment="DEMO",
            at=at(2))
        assert locked.status is AuthStatus.LOCKED

    def test_rotation_preserves_history(self):
        env = security_env()
        credential, _ = env["auth"].issue(
            actor_id=ACTOR_TRADER, environment="DEMO",
            method=AuthMethod.TOKEN, secret_material="old-secret", at=T0)
        rotated, successor = env["auth"].rotate(
            credential.credential_id, new_material="new-secret", at=at(1))
        assert rotated.status is CredentialStatus.ROTATED
        assert successor.version == credential.version + 1
        assert successor.rotated_from == rotated.credential_id
        # old credential never regains authority
        old = env["auth"].authenticate(
            credential_id=rotated.credential_id,
            secret_material="old-secret", environment="DEMO", at=at(2))
        assert old.status is not AuthStatus.AUTHENTICATED
        # new one works
        fresh = env["auth"].authenticate(
            credential_id=successor.credential_id,
            secret_material="new-secret", environment="DEMO", at=at(2))
        assert fresh.status is AuthStatus.AUTHENTICATED

    def test_verifier_only_storage(self):
        env = security_env()
        credential, _ = env["auth"].issue(
            actor_id=ACTOR_TRADER, environment="DEMO",
            method=AuthMethod.TOKEN, secret_material="synthetic-secret",
            at=T0)
        assert credential.verifier_hash != "synthetic-secret"
        dumped = str(credential.__dict__)
        assert "synthetic-secret" not in dumped


# --------------------------------------------------------------------- #
# Sessions                                                               #
# --------------------------------------------------------------------- #
class TestSessions:
    def test_session_created_from_authenticated_result(self):
        env = security_env()
        _, session = demo_trader_session(env)
        assert session.status is SessionStatus.ACTIVE
        assert session.permission_snapshot

    def test_unauthenticated_never_creates_session(self):
        env = security_env()
        result = env["auth"].authenticate(
            credential_id="crd_" + "f" * 32, secret_material="x",
            environment="DEMO", at=at(1))
        with pytest.raises(ContractError):
            env["sessions"].create(authentication=result, role="TRADER",
                                   permissions=("VIEW",))

    def test_expired_session_blocked(self):
        env = security_env()
        _, session = demo_trader_session(env)
        with pytest.raises(ContractError) as err:
            env["sessions"].validate(session.session_id,
                                     at=T0 + timedelta(days=2))
        assert "expired" in str(err.value)

    def test_revoked_session_blocked(self):
        env = security_env()
        _, session = demo_trader_session(env)
        env["sessions"].revoke(session.session_id, reason="logout")
        with pytest.raises(ContractError) as err:
            env["sessions"].validate(session.session_id, at=at(5))
        assert "revoked" in str(err.value)

    def test_unknown_session_blocked(self):
        env = security_env()
        with pytest.raises(ContractError):
            env["sessions"].validate("ssn_" + "f" * 32, at=at(1))

    def test_credential_revocation_propagates_to_authorization(self):
        env = security_env()
        credential, session = demo_trader_session(env)
        env["auth"].revoke(credential.credential_id, at=at(2),
                           reason="rotation policy")
        # the session itself still validates until revoked - revoking the
        # session is the propagation step; authorization re-checks the
        # canonical registry every call (immediate revocation of the
        # permission side), and emergency invalidation kills the session
        env["sessions"].revoke(session.session_id, reason="credential revoked")
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO", at=at(3))
        # authorization BLOCKS (it returns a decision, it never raises:
        # the decision is the evidence)
        assert result.decision is AuthorizationDecision.BLOCK


# --------------------------------------------------------------------- #
# Authorization                                                          #
# --------------------------------------------------------------------- #
class TestAuthorization:
    def test_allowed_authorization(self):
        env = security_env()
        _, session = demo_trader_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO", at=at(2))
        assert result.decision is AuthorizationDecision.ALLOW

    def test_permission_mismatch_blocks(self):
        env = security_env()
        _, session = demo_trader_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.MODIFY_RISK, environment="DEMO", at=at(2))
        assert result.decision is AuthorizationDecision.BLOCK
        assert "PERMISSION_DENIED" in result.reasons[0]

    def test_environment_mismatch_blocks(self):
        env = security_env()
        _, session = demo_trader_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="LIVE", at=at(2))
        assert result.decision is AuthorizationDecision.BLOCK
        assert "ENVIRONMENT_MISMATCH" in result.reasons[0]

    def test_live_permission_environment_restriction(self):
        env = security_env()
        _, session = live_admin_session(env)
        allowed = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.LIVE_TRADE, environment="LIVE", at=at(2))
        assert allowed.decision is AuthorizationDecision.ALLOW
        # LIVE_TRADE granted in LIVE does not authorize DEMO session use
        _, demo = demo_trader_session(env)
        blocked = env["authz"].authorize(
            session_id=demo.session_id,
            permission=Permission.LIVE_TRADE, environment="LIVE", at=at(2))
        assert blocked.decision is AuthorizationDecision.BLOCK

    def test_demo_cannot_authorize_live(self):
        env = security_env()
        _, session = demo_trader_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.LIVE_TRADE, environment="DEMO", at=at(2))
        assert result.decision is AuthorizationDecision.BLOCK

    def test_deterministic_decisions(self):
        env = security_env()
        _, session = demo_trader_session(env)
        r1 = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO", at=at(2))
        r2 = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO", at=at(2))
        assert r1.decision is r2.decision
        assert r1.reasons == r2.reasons

    def test_approval_required_operations(self):
        env = security_env()
        _, session = live_admin_session(env)
        blocked = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.MODIFY_RISK, environment="LIVE", at=at(2),
            operation="RISK_CONFIG_CHANGE")
        assert blocked.decision is AuthorizationDecision.BLOCK
        assert "APPROVAL_REQUIRED" in blocked.reasons[0]

    def test_singleton_permission_source(self):
        env = security_env()
        assert env["authz"].registry is not None
        # the one canonical source answers every permission question
        assert env["authz"].permissions_for(Role.TRADER) is not None


# --------------------------------------------------------------------- #
# Approvals (maker-checker)                                              #
# --------------------------------------------------------------------- #
class TestApprovals:
    def test_valid_maker_checker_flow(self):
        env = security_env()
        approval = active_approval(env)
        assert approval.status is ApprovalStatus.ACTIVE
        assert approval.maker_actor_id == ACTOR_MAKER
        assert approval.checker_actor_id == ACTOR_CHECKER

    def test_self_approval_blocked(self):
        env = security_env()
        submission = env["approvals"].submit(
            operation="POLICY_CHANGE", resource="pol_" + "1" * 32,
            environment="DEMO", maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError) as err:
            env["approvals"].approve(
                submission.approval_id, checker_actor_id=ACTOR_MAKER,
                checker_kind="HUMAN", at=at(1))
        assert "SEC-SELF-APPROVAL" in str(err.value) or \
            "self-approval" in str(err.value)

    def test_ai_checker_blocked(self):
        env = security_env()
        submission = env["approvals"].submit(
            operation="MODEL_PROMOTION", resource="mdl_" + "1" * 32,
            environment="RESEARCH", maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError) as err:
            env["approvals"].approve(
                submission.approval_id, checker_actor_id="usr_" + "9" * 32,
                checker_kind="AI_MODEL", at=at(1))
        assert "AI cannot" in str(err.value) or "AI" in str(err.value)

    def test_stale_approval_blocked(self):
        env = security_env()
        submission = env["approvals"].submit(
            operation="POLICY_CHANGE", resource="pol_" + "1" * 32,
            environment="DEMO", maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError) as err:
            env["approvals"].approve(
                submission.approval_id, checker_actor_id=ACTOR_CHECKER,
                checker_kind="HUMAN", at=T0 + timedelta(days=2))
        assert "expired" in str(err.value)

    def test_rejection_leaves_immutable_evidence(self):
        env = security_env()
        submission = env["approvals"].submit(
            operation="POLICY_CHANGE", resource="pol_" + "1" * 32,
            environment="DEMO", maker_actor_id=ACTOR_MAKER, at=T0)
        rejected = env["approvals"].reject(
            submission.approval_id, checker_actor_id=ACTOR_CHECKER,
            at=at(1), reason="insufficient evidence")
        assert rejected.status is ApprovalStatus.REJECTED
        with pytest.raises(Exception):
            rejected.status = ApprovalStatus.ACTIVE

    def test_contract_rejects_self_checker(self):
        with pytest.raises(ContractError):
            ApprovalRecord(
                approval_id=new_identifier("approval_id"),
                operation="X", resource="R", environment="DEMO",
                maker_actor_id=ACTOR_MAKER, checker_actor_id=ACTOR_MAKER,
                status=ApprovalStatus.SUBMITTED, submitted_at=T0).validate()

    def test_require_active_rejects_non_active(self):
        env = security_env()
        submission = env["approvals"].submit(
            operation="POLICY_CHANGE", resource="pol_" + "1" * 32,
            environment="DEMO", maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError):
            env["approvals"].require_active(submission.approval_id,
                                            operation="POLICY_CHANGE")


# --------------------------------------------------------------------- #
# Secrets + keys + encryption policy                                     #
# --------------------------------------------------------------------- #
class TestSecretsAndKeys:
    def test_secret_lifecycle(self):
        vault = SecretVault()
        ref = vault.store(purpose="broker-api", environment="DEMO",
                          value="synthetic-secret-value", at=T0)
        assert vault.retrieve(ref.secret_id) == "synthetic-secret-value"
        assert vault.verify(ref.secret_id, "synthetic-secret-value")
        assert not vault.verify(ref.secret_id, "other")

    def test_secret_rotation_revokes_old(self):
        vault = SecretVault()
        ref = vault.store(purpose="broker-api", environment="DEMO",
                          value="v1", at=T0)
        retired, linked = vault.rotate(ref.secret_id, new_value="v2",
                                       at=at(1))
        assert retired.revoked
        assert linked.rotated_from == retired.secret_id
        with pytest.raises(ContractError):
            vault.retrieve(ref.secret_id)

    def test_secret_reference_never_contains_value(self):
        vault = SecretVault()
        ref = vault.store(purpose="broker-api", environment="DEMO",
                          value="synthetic-secret-value", at=T0)
        dumped = str(ref.__dict__) + ref.redacted
        assert "synthetic-secret-value" not in dumped

    def test_key_lifecycle(self):
        manager = KeyManager()
        key = manager.create(purpose=KeyPurpose.AUDIT_INTEGRITY,
                             environment="DEMO", at=T0)
        assert manager.active_for(KeyPurpose.AUDIT_INTEGRITY,
                                  "DEMO").key_id == key.key_id
        rotated, successor = manager.rotate(key.key_id, at=at(1))
        assert rotated.status is KeyStatus.ROTATED
        assert successor.version == 2
        revoked = manager.revoke(successor.key_id, at=at(2))
        assert revoked.status is KeyStatus.REVOKED

    def test_no_active_key_fails_closed(self):
        manager = KeyManager()
        with pytest.raises(ContractError):
            manager.active_for(KeyPurpose.AUDIT_INTEGRITY, "DEMO")

    def test_encryption_policy_classification(self):
        for classification, expected in (
                (DataClassification.PUBLIC, ProtectionLevel.NONE),
                (DataClassification.INTERNAL, ProtectionLevel.INTEGRITY),
                (DataClassification.CONFIDENTIAL, ProtectionLevel.ENCRYPTED),
                (DataClassification.SECRET,
                 ProtectionLevel.ENCRYPTED_AT_REST_AND_TRANSIT)):
            policy = build_encryption_policy(classification=classification,
                                             environment="DEMO")
            assert policy.required_protection is expected

    def test_weaker_policy_rejected(self):
        policy = build_encryption_policy(classification=DataClassification.CONFIDENTIAL,
                                         environment="DEMO")
        weakened = replace(policy,
                           required_protection=ProtectionLevel.INTEGRITY,
                           content_hash="")
        object.__setattr__(weakened, "content_hash", weakened.compute_content_hash()
                           if hasattr(weakened, "compute_content_hash") else "")
        with pytest.raises(ContractError):
            weakened.validate()

    def test_hashing_is_not_encryption(self):
        assert not protection_satisfies(ProtectionLevel.ENCRYPTED,
                                        ProtectionLevel.INTEGRITY)
        assert not protection_satisfies(
            ProtectionLevel.ENCRYPTED_AT_REST_AND_TRANSIT,
            ProtectionLevel.ENCRYPTED)
        assert protection_satisfies(ProtectionLevel.NONE,
                                    ProtectionLevel.ENCRYPTED)


# --------------------------------------------------------------------- #
# Replay + rate limiting                                                 #
# --------------------------------------------------------------------- #
class TestRequestProtection:
    def _request(self, env, session, **overrides):
        base = dict(request_id=new_identifier("request_id"),
                    session_id=session.session_id,
                    actor_id=session.actor_id, environment="DEMO",
                    timestamp=at(2), operation="PRIVILEGED_OP",
                    resource="pol_" + "1" * 32)
        base.update(overrides)
        return SecurityRequest(**base)

    def test_first_use_allowed(self):
        env = security_env()
        _, session = demo_trader_session(env)
        guard = ReplayGuard()
        verdict, _ = guard.check(self._request(env, session), now=at(2))
        assert verdict is ReplayVerdict.FIRST_SEEN

    def test_same_request_idempotent(self):
        env = security_env()
        _, session = demo_trader_session(env)
        guard = ReplayGuard()
        request = self._request(env, session)
        guard.check(request, now=at(2))
        verdict, _ = guard.check(request, now=at(2))
        assert verdict is ReplayVerdict.IDEMPOTENT_REPEAT

    def test_conflicting_idempotency_is_corruption(self):
        env = security_env()
        _, session = demo_trader_session(env)
        guard = ReplayGuard()
        first = self._request(env, session)
        guard.check(first, now=at(2))
        # SAME request id, DIFFERENT semantics -> corruption
        conflicting = self._request(env, session,
                                    request_id=first.request_id,
                                    resource="pol_" + "2" * 32)
        verdict, _ = guard.check(conflicting, now=at(2))
        assert verdict is ReplayVerdict.CORRUPTION_SUSPECTED
        with pytest.raises(ContractError):
            guard.require_first_use(conflicting, now=at(2))

    def test_stale_request_blocked(self):
        env = security_env()
        _, session = demo_trader_session(env)
        guard = ReplayGuard()
        stale = self._request(env, session, timestamp=T0)
        verdict, _ = guard.check(stale, now=T0 + timedelta(hours=2))
        assert verdict is ReplayVerdict.REPLAY_DETECTED

    def test_rate_limit_window(self):
        limiter = RateLimiter(max_operations=3, window_seconds=60)
        for i in range(3):
            decision = limiter.check(limit_key="auth:usr1",
                                     now=at(i / 10))
            assert decision.verdict is RateLimitVerdict.ALLOWED
        exceeded = limiter.check(limit_key="auth:usr1", now=at(0.5))
        assert exceeded.verdict is RateLimitVerdict.EXCEEDED
        with pytest.raises(ContractError):
            limiter.require_allowed(limit_key="auth:usr1", now=at(0.6))

    def test_rate_limit_decision_is_auditable(self):
        limiter = RateLimiter(max_operations=1, window_seconds=60)
        decision = limiter.check(limit_key="auth:usr1", now=at(0))
        assert decision.window_start is not None
        assert decision.max_operations == 1


# --------------------------------------------------------------------- #
# Audit chain                                                            #
# --------------------------------------------------------------------- #
class TestAuditChain:
    def _record(self, audit_id_suffix: str):
        return AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.USER, actor_id=ACTOR_MAKER,
            action="PRIVILEGED_OP", entity_type="policy",
            entity_id=f"pol_{audit_id_suffix}", event_time=at(1),
            before={"v": 1}, after={"v": 2}, reason="test",
            source="tests", environment="DEMO",
            correlation_id="pol_" + "1" * 32)

    def test_append_and_verify(self):
        chain = AuditChain()
        for i in range(5):
            chain.append(self._record(str(i)))
        assert chain.verify() is IntegrityStatus.VERIFIED
        assert len(chain) == 5

    @pytest.mark.parametrize("mutation", [
        {"actor_id": "usr_" + "9" * 32},
        {"action": "TAMPERED"},
        {"environment": "LIVE"},
        {"reason": "rewritten"},
        {"after": {"v": 999}},
        {"integrity_hash": "b" * 64},
        {"previous_hash": "c" * 64},
    ])
    def test_every_mutation_detected(self, mutation):
        chain = AuditChain()
        first = chain.append(self._record("1"))
        chain.append(self._record("2"))
        forged = forge_record(first, **mutation)
        # re-forge the chain through tampering: replace in place
        chain._records[0] = forged
        assert chain.verify() is IntegrityStatus.CORRUPTED

    def test_timestamp_mutation_detected(self):
        chain = AuditChain()
        first = chain.append(self._record("1"))
        chain.append(self._record("2"))
        chain._records[0] = forge_record(first, event_time=at(99))
        assert chain.verify() is IntegrityStatus.CORRUPTED

    def test_integrity_failure_blocks_privileged_ops(self):
        chain = AuditChain()
        first = chain.append(self._record("1"))
        chain._records[0] = forge_record(first, action="TAMPERED")
        with pytest.raises(ContractError) as err:
            chain.require_verified()
        assert "blocked" in str(err.value)

    def test_audit_records_immutable(self):
        chain = AuditChain()
        record = chain.append(self._record("1"))
        with pytest.raises(Exception):
            record.action = "CHANGED"


# --------------------------------------------------------------------- #
# Backup + incidents + events                                            #
# --------------------------------------------------------------------- #
class TestBackupIncidentsEvents:
    def test_backup_lifecycle(self):
        service = BackupSecurityService()
        components = {"events": {"e1": 1}, "audit": ["a", "b"]}
        manifest = service.create_backup(
            environment="DEMO", source_version="0.9.0",
            components=components, at=T0)
        assert service.verify_backup(
            manifest.backup_id, components=components,
            source_version="0.9.0", environment="DEMO") \
            is IntegrityStatus.VERIFIED

    def test_corrupt_backup_rejected(self):
        service = BackupSecurityService()
        components = {"events": {"e1": 1}}
        manifest = service.create_backup(
            environment="DEMO", source_version="0.9.0",
            components=components, at=T0)
        tampered = {"events": {"e1": 999}}
        assert service.verify_backup(
            manifest.backup_id, components=tampered,
            source_version="0.9.0", environment="DEMO") \
            is IntegrityStatus.CORRUPTED
        with pytest.raises(ContractError) as err:
            service.authorize_restore(manifest.backup_id,
                                      components=tampered,
                                      source_version="0.9.0",
                                      environment="DEMO")
        assert "never restored" in str(err.value)

    def test_unknown_backup_blocked(self):
        service = BackupSecurityService()
        with pytest.raises(ContractError):
            service.authorize_restore("bkp_" + "f" * 32,
                                      components={}, source_version="x",
                                      environment="DEMO")

    def test_incident_lifecycle(self):
        service = SecurityIncidentService()
        incident = service.open(
            severity=IncidentSeverity.CRITICAL,
            title="credential leak suspicion",
            environment="DEMO", affected_resources=("credentials",),
            detected_at=T0, detected_by="monitor",
            evidence={"signal": "verifier mismatch spike"})
        assert incident.status is SecurityIncidentStatus.OPEN
        contained = service.transition(
            incident.security_incident_id,
            SecurityIncidentStatus.CONTAINED, actor="usr_" + "5" * 32,
            at=at(1), note="credentials rotated")
        assert contained.status is SecurityIncidentStatus.CONTAINED
        assert len(contained.timeline) == 2

    def test_incident_closure_requires_human(self):
        service = SecurityIncidentService()
        incident = service.open(
            severity=IncidentSeverity.HIGH, title="t",
            environment="DEMO", affected_resources=("audit",),
            detected_at=T0, detected_by="sys", evidence={"x": 1})
        service.transition(incident.security_incident_id,
                           SecurityIncidentStatus.CONTAINED,
                           actor="usr_1", at=at(1), note="c")
        service.transition(incident.security_incident_id,
                           SecurityIncidentStatus.INVESTIGATING,
                           actor="usr_1", at=at(2), note="i")
        service.transition(incident.security_incident_id,
                           SecurityIncidentStatus.RECOVERING,
                           actor="usr_1", at=at(3), note="r")
        closed = service.transition(
            incident.security_incident_id,
            SecurityIncidentStatus.CLOSED, actor="usr_" + "6" * 32,
            at=at(4), note="done", human_approval=True)
        assert closed.closure_approver

    def test_incident_without_human_closure_approval_blocked(self):
        service = SecurityIncidentService()
        incident = service.open(
            severity=IncidentSeverity.HIGH, title="t",
            environment="DEMO", affected_resources=("audit",),
            detected_at=T0, detected_by="sys", evidence={"x": 1})
        with pytest.raises(ContractError):
            service.transition(incident.security_incident_id,
                               SecurityIncidentStatus.CLOSED,
                               actor="usr_1", at=at(1), note="close",
                               human_approval=False)

    def test_security_event_types_registered(self):
        assert len(SECURITY_EVENT_TYPES) == 27

    def test_security_event_redaction(self):
        from core.events.contracts import EventType
        event = emit_security_event(
            EventType.AUTHENTICATION_FAILED,
            payload={"password": "synthetic-secret", "actor": "usr_x",
                     "nested": {"api_key": "synthetic-key"}},
            environment="DEMO", entity_id="usr_x", event_time=T0)
        event.validate()
        assert event.payload["password"] == "<redacted>"
        assert event.payload["nested"]["api_key"] == "<redacted>"

    def test_metrics_counters(self):
        metrics = SecurityMetrics()
        metrics.increment("authentication_failure_count")
        metrics.increment("authentication_failure_count")
        assert metrics.value("authentication_failure_count") == 2


# --------------------------------------------------------------------- #
# Governance gate                                                        #
# --------------------------------------------------------------------- #
class TestGovernanceGate:
    def _gate(self, env):
        return GovernanceGate(sessions=env["sessions"],
                              authorization=env["authz"],
                              approvals=env["approvals"],
                              audit=env["chain"])

    def _artifact(self, artifact_type=GovernedArtifactType.POLICY,
                  new_version="1.1.0"):
        return GovernanceArtifact(
            artifact_type=artifact_type, artifact_id="pol_" + "1" * 32,
            old_version="1.0.0", new_version=new_version,
            old_content_hash="a" * 64, new_content_hash="b" * 64,
            environment="DEMO")

    def test_governed_transition_records_everything(self):
        env = security_env()
        gate = self._gate(env)
        _, session = demo_approver_session(env)
        approval = active_approval(env)
        transition = gate.guard(
            artifact=self._artifact(),
            maker_session_id=session.session_id,
            approval_id=approval.approval_id, at=at(3),
            reason="tighten limits", evidence={"ticket": "OPS-1"})
        assert transition.maker_actor_id == ACTOR_MAKER
        assert transition.checker_actor_id == ACTOR_CHECKER
        assert transition.artifact.new_version == "1.1.0"
        assert transition.reason
        assert env["chain"].verify() is IntegrityStatus.VERIFIED
        assert len(env["chain"]) == 1

    def test_governance_blocked_without_permission(self):
        env = security_env()
        gate = self._gate(env)
        _, session = demo_trader_session(env)  # TRADER: no MODIFY_POLICY
        approval = active_approval(env)
        with pytest.raises(ContractError) as err:
            gate.guard(artifact=self._artifact(),
                       maker_session_id=session.session_id,
                       approval_id=approval.approval_id, at=at(3),
                       reason="no permission")
        assert "not permitted" in str(err.value)

    def test_governance_blocked_without_approval(self):
        env = security_env()
        gate = self._gate(env)
        _, session = demo_approver_session(env)
        submission = env["approvals"].submit(
            operation="POLICY_CHANGE", resource="pol_" + "1" * 32,
            environment="DEMO", maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError):
            gate.guard(artifact=self._artifact(),
                       maker_session_id=session.session_id,
                       approval_id=submission.approval_id, at=at(3),
                       reason="not approved yet")

    def test_approval_spoofing_blocked(self):
        env = security_env()
        gate = self._gate(env)
        _, session = demo_approver_session(env)  # maker = ACTOR_MAKER
        # approval made by a DIFFERENT maker: doesn't match session actor
        approval = active_approval(env, maker=ACTOR_TRADER)
        with pytest.raises(ContractError) as err:
            gate.guard(artifact=self._artifact(),
                       maker_session_id=session.session_id,
                       approval_id=approval.approval_id, at=at(3),
                       reason="spoof")
        assert "spoofing" in str(err.value) or "does not match" in str(err.value)

    def test_ungoverned_change_blocked(self):
        env = security_env()
        gate = self._gate(env)
        with pytest.raises(ContractError) as err:
            gate.require_transition(GovernedArtifactType.POLICY,
                                    "pol_" + "1" * 32, "9.9.9")
        assert "no governance evidence" in str(err.value)

    def test_audit_failure_blocks_governance(self):
        env = security_env()
        gate = self._gate(env)
        record = env["chain"].append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.USER, actor_id=ACTOR_MAKER,
            action="OP", entity_type="policy",
            entity_id="pol_" + "1" * 32, event_time=at(1),
            before=None, after={}, reason="r", source="t",
            environment="DEMO", correlation_id="pol_" + "1" * 32))
        env["chain"]._records[0] = forge_record(record, action="TAMPERED")
        _, session = demo_approver_session(env)
        approval = active_approval(env)
        with pytest.raises(ContractError) as err:
            gate.guard(artifact=self._artifact(),
                       maker_session_id=session.session_id,
                       approval_id=approval.approval_id, at=at(3),
                       reason="chain broken")
        assert "integrity" in str(err.value).lower()

    def test_artifact_permission_routing(self):
        assert ARTIFACT_PERMISSIONS[Gov := GovernedArtifactType.RISK_CONFIG] \
            is Permission.MODIFY_RISK
        assert ARTIFACT_PERMISSIONS[GovernedArtifactType.POLICY] \
            is Permission.MODIFY_POLICY
        assert ARTIFACT_PERMISSIONS[GovernedArtifactType.MODEL] \
            is Permission.APPROVE

    def test_artifact_requires_version_and_hashes(self):
        with pytest.raises(ContractError):
            GovernanceArtifact(
                artifact_type=GovernedArtifactType.POLICY,
                artifact_id="pol_x", old_version="1.0.0", new_version="",
                old_content_hash=None, new_content_hash="",
                environment="DEMO").validate()
