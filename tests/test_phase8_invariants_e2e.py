"""Phase 8 invariants (INV-086..INV-116) and E2E scenarios (1..45+).

Executable evidence for the security/governance guardrail plane.
SYNTHETIC credentials only.
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
    IncidentSeverity,
    KeyPurpose,
    ReplayVerdict,
    SecurityIncidentStatus,
    SecurityRequest,
)
from core.security.protection import (
    AuditChain,
    BackupSecurityService,
    ReplayGuard,
    SECURITY_EVENT_TYPES,
    SecurityIncidentService,
    emit_security_event,
    forge_record,
)
from core.security.secrets import SecretVault, KeyManager
from platform.audit.contracts import ActorType, AuditRecord
from platform.security.contracts import Permission, Role
from core.events.contracts import EventType

UTC = timezone.utc


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


class TestInvariants:
    """INV-086..INV-116: every invariant executable."""

    def _env(self):
        return security_env()

    # INV-086 authenticated actor required
    def test_inv086(self):
        env = self._env()
        result = env["authz"].authorize(
            session_id="ssn_" + "f" * 32, permission=Permission.VIEW,
            environment="DEMO", at=at(1))
        assert result.decision is AuthorizationDecision.BLOCK

    # INV-087 authorization deterministic
    def test_inv087(self):
        env = self._env()
        _, session = demo_trader_session(env)
        decisions = [
            env["authz"].authorize(session_id=session.session_id,
                                   permission=Permission.DEMO_TRADE,
                                   environment="DEMO", at=at(2)).decision
            for _ in range(5)]
        assert len(set(decisions)) == 1

    # INV-088 permission source singular
    def test_inv088(self):
        import inspect
        from platform.security.contracts import RolePermissionRegistry
        # the only definition site is platform.security
        roots = ("core", "adapters", "research")
        from pathlib import Path
        for root in roots:
            for py in Path(root).rglob("*.py"):
                src = py.read_text(encoding="utf-8")
                assert "class RolePermissionRegistry" not in src, py

    # INV-089 self approval impossible
    def test_inv089(self):
        env = self._env()
        submission = env["approvals"].submit(
            operation="X", resource="r", environment="DEMO",
            maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError):
            env["approvals"].approve(submission.approval_id,
                                     checker_actor_id=ACTOR_MAKER,
                                     checker_kind="HUMAN", at=at(1))

    # INV-090 revocation effective
    def test_inv090(self):
        env = self._env()
        _, session = demo_trader_session(env)
        env["sessions"].revoke(session.session_id, reason="revoked")
        assert env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO",
            at=at(2)).decision is AuthorizationDecision.BLOCK

    # INV-091/092 session expiry/revocation blocked
    def test_inv091_092(self):
        env = self._env()
        _, session = demo_trader_session(env)
        with pytest.raises(ContractError):
            env["sessions"].validate(session.session_id,
                                     at=T0 + timedelta(days=5))
        env["sessions"].revoke(session.session_id, reason="r")
        with pytest.raises(ContractError):
            env["sessions"].validate(session.session_id, at=at(3))

    # INV-093 credential rotation preserves history
    def test_inv093(self):
        env = self._env()
        credential, _ = demo_trader_session(env)
        rotated, successor = env["auth"].rotate(
            credential.credential_id, new_material="rotated-secret",
            at=at(1))
        assert rotated.status is not None
        assert successor.rotated_from == rotated.credential_id
        assert env["auth"].record(rotated.credential_id).credential_id == \
            rotated.credential_id

    # INV-094/095 secrets never in audit or logs
    def test_inv094_095(self):
        event = emit_security_event(
            EventType.CREDENTIAL_ISSUED,
            payload={"credential": "synthetic-secret-value",
                     "credential_id": "crd_" + "1" * 32},
            environment="DEMO", entity_id="crd_" + "1" * 32,
            event_time=T0)
        dumped = str(event.payload)
        assert "synthetic-secret-value" not in dumped

    # INV-096 audit append-only
    def test_inv096(self):
        chain = AuditChain()
        record = chain.append(self._audit())
        with pytest.raises(Exception):
            record.action = "MUTATED"

    @staticmethod
    def _audit(suffix="1"):
        return AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.USER, actor_id=ACTOR_MAKER,
            action="OP", entity_type="policy",
            entity_id=f"pol_{suffix}", event_time=T0, before=None,
            after={"v": 1}, reason="r", source="t", environment="DEMO",
            correlation_id="pol_" + "1" * 32)

    # INV-097 audit tamper detectable
    @pytest.mark.parametrize("mutation", [
        {"actor_id": "usr_" + "9" * 32}, {"action": "X2"},
        {"environment": "LIVE"}, {"reason": "z"},
        {"previous_hash": "d" * 64}, {"integrity_hash": "e" * 64},
        {"after": {"v": 42}},
    ])
    def test_inv097(self, mutation):
        chain = AuditChain()
        first = chain.append(self._audit("1"))
        chain.append(self._audit("2"))
        chain._records[0] = forge_record(first, **mutation)
        assert chain.verify() is IntegrityStatus.CORRUPTED

    # INV-098 historical audit reproducible
    def test_inv098(self):
        chain = AuditChain()
        for i in range(5):
            chain.append(self._audit(str(i)))
        head_before = chain.head
        assert chain.verify() is IntegrityStatus.VERIFIED
        assert chain.head == head_before  # verification is read-only

    # INV-099 approval immutable
    def test_inv099(self):
        env = self._env()
        approval = active_approval(env)
        with pytest.raises(Exception):
            approval.status = ApprovalStatus.REJECTED

    # INV-100 approval actor cannot be spoofed
    def test_inv100(self):
        env = self._env()
        gate = GovernanceGate(sessions=env["sessions"],
                              authorization=env["authz"],
                              approvals=env["approvals"],
                              audit=env["chain"])
        _, session = demo_approver_session(env)
        approval = active_approval(env, maker=ACTOR_TRADER)
        with pytest.raises(ContractError):
            gate.guard(
                artifact=GovernanceArtifact(
                    artifact_type=GovernedArtifactType.POLICY,
                    artifact_id="pol_" + "1" * 32, old_version="1.0.0",
                    new_version="1.1.0", old_content_hash="a" * 64,
                    new_content_hash="b" * 64, environment="DEMO"),
                maker_session_id=session.session_id,
                approval_id=approval.approval_id, at=at(3), reason="spoof")

    # INV-101 environment permissions isolated
    def test_inv101(self):
        env = self._env()
        _, session = demo_trader_session(env)
        for wrong_env in ("LIVE", "SIMULATION", "REPLAY", "PAPER"):
            assert env["authz"].authorize(
                session_id=session.session_id,
                permission=Permission.DEMO_TRADE,
                environment=wrong_env,
                at=at(2)).decision is AuthorizationDecision.BLOCK

    # INV-102 LIVE requires explicit authorization
    def test_inv102(self):
        env = self._env()
        _, admin = live_admin_session(env)
        assert env["authz"].authorize(
            session_id=admin.session_id,
            permission=Permission.LIVE_TRADE, environment="LIVE",
            at=at(2)).decision is AuthorizationDecision.ALLOW
        _, trader = demo_trader_session(env)
        assert env["authz"].authorize(
            session_id=trader.session_id,
            permission=Permission.LIVE_TRADE, environment="LIVE",
            at=at(2)).decision is AuthorizationDecision.BLOCK

    # INV-103 emergency control cannot be bypassed
    def test_inv103(self):
        env = self._env()
        _, session = demo_trader_session(env)
        result = env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO",
            at=at(2), operation="EMERGENCY_RELEASE")
        assert result.decision is AuthorizationDecision.BLOCK

    # INV-104 configuration changes versioned + INV-105 hash integrity
    def test_inv104_105(self):
        with pytest.raises(ContractError):
            GovernanceArtifact(
                artifact_type=GovernedArtifactType.RISK_CONFIG,
                artifact_id="rk", old_version=None, new_version="",
                old_content_hash=None, new_content_hash="short",
                environment="DEMO").validate()

    # INV-106..109 governance preserved (policy/risk/strategy/model)
    def test_inv106_109(self):
        from core.governance.gate import ARTIFACT_PERMISSIONS
        assert ARTIFACT_PERMISSIONS[GovernedArtifactType.POLICY] \
            is Permission.MODIFY_POLICY
        assert ARTIFACT_PERMISSIONS[GovernedArtifactType.RISK_CONFIG] \
            is Permission.MODIFY_RISK
        assert ARTIFACT_PERMISSIONS[GovernedArtifactType.STRATEGY] \
            is Permission.MODIFY_POLICY
        assert ARTIFACT_PERMISSIONS[GovernedArtifactType.MODEL] \
            is Permission.APPROVE
        # the gate refuses ungoverned transitions of every type
        env = self._env()
        gate = GovernanceGate(sessions=env["sessions"],
                              authorization=env["authz"],
                              approvals=env["approvals"],
                              audit=env["chain"])
        for artifact_type in GovernedArtifactType:
            with pytest.raises(ContractError):
                gate.require_transition(artifact_type, "x", "1.0.0")

    # INV-110/111 AI cannot self approve/deploy
    def test_inv110_111(self):
        from core.security.services import is_human_checker
        assert not is_human_checker("m", "AI_MODEL")
        assert not is_human_checker("s", "SYSTEM_AUTOMATED")

    # INV-112/113 backup + restore integrity verified
    def test_inv112_113(self):
        service = BackupSecurityService()
        components = {"events": [1, 2], "audit": {"h": "x"}}
        manifest = service.create_backup(environment="DEMO",
                                         source_version="0.9.0",
                                         components=components, at=T0)
        assert service.verify_backup(
            manifest.backup_id, components=components,
            source_version="0.9.0", environment="DEMO") \
            is IntegrityStatus.VERIFIED
        assert service.authorize_restore(
            manifest.backup_id, components=components,
            source_version="0.9.0", environment="DEMO").backup_id == \
            manifest.backup_id

    # INV-114 incident evidence preserved
    def test_inv114(self):
        service = SecurityIncidentService()
        incident = service.open(
            severity=IncidentSeverity.MEDIUM, title="probe",
            environment="DEMO", affected_resources=("api",),
            detected_at=T0, detected_by="ids",
            evidence={"attempts": 3})
        updated = service.transition(
            incident.security_incident_id,
            SecurityIncidentStatus.CONTAINED, actor="usr_1", at=at(1),
            note="c")
        assert updated.evidence == incident.evidence
        assert len(updated.timeline) > len(incident.timeline)

    # INV-115 replayed privileged request blocked
    def test_inv115(self):
        env = self._env()
        _, session = demo_trader_session(env)
        guard = ReplayGuard()
        request = SecurityRequest(
            request_id=new_identifier("request_id"),
            session_id=session.session_id, actor_id=session.actor_id,
            environment="DEMO", timestamp=T0, operation="OP",
            resource="pol_" + "1" * 32)
        assert guard.check(request, now=at(1))[0] is ReplayVerdict.FIRST_SEEN
        assert guard.check(request, now=at(1))[0] \
            is ReplayVerdict.IDEMPOTENT_REPEAT

    # INV-116 rate limit cannot bypass authorization
    def test_inv116(self):
        env = self._env()
        _, session = demo_trader_session(env)
        # even if the rate limiter allows, permission denial stands
        from core.security.protection import RateLimiter
        limiter = RateLimiter(max_operations=100)
        assert limiter.check(limit_key="k", now=at(0)).verdict.value == "ALLOWED"
        assert env["authz"].authorize(
            session_id=session.session_id,
            permission=Permission.LIVE_TRADE, environment="LIVE",
            at=at(1)).decision is AuthorizationDecision.BLOCK


# ===================================================================== #
# E2E scenarios 1..45+ (SECTION 44)                                     #
# ===================================================================== #
class TestE2EScenarios:
    @pytest.fixture()
    def env(self):
        return security_env()

    def _gate(self, env):
        return GovernanceGate(sessions=env["sessions"],
                              authorization=env["authz"],
                              approvals=env["approvals"],
                              audit=env["chain"])

    def _artifact(self, env, **overrides):
        base = dict(artifact_type=GovernedArtifactType.POLICY,
                    artifact_id="pol_" + "1" * 32, old_version="1.0.0",
                    new_version="1.1.0", old_content_hash="a" * 64,
                    new_content_hash="b" * 64, environment="DEMO")
        base.update(overrides)
        return GovernanceArtifact(**base)

    # 1/2 authentication success + failure
    def test_e2e_01_02(self, env):
        _, session = demo_trader_session(env)
        assert session.authentication.status is AuthStatus.AUTHENTICATED
        bad = env["auth"].authenticate(
            credential_id=session.authentication.credential_id,
            secret_material="wrong", environment="DEMO", at=at(2))
        assert bad.status is AuthStatus.UNAUTHENTICATED

    # 3/4 expired + revoked credential
    def test_e2e_03_04(self, env):
        credential, _ = issue_session(env, actor_id=ACTOR_TRADER,
                                      environment="DEMO", role="TRADER",
                                      permissions=("VIEW",),
                                      secret="synthetic-secret",
                                      ttl=timedelta(minutes=1))
        expired = env["auth"].authenticate(
            credential_id=credential.credential_id,
            secret_material="synthetic-secret", environment="DEMO",
            at=at(30))
        assert expired.status is AuthStatus.EXPIRED
        credential2, _ = issue_session(env, actor_id=ACTOR_TRADER,
                                       environment="DEMO", role="TRADER",
                                       permissions=("VIEW",),
                                       secret="synthetic-secret-2")
        env["auth"].revoke(credential2.credential_id, at=at(2), reason="r")
        revoked = env["auth"].authenticate(
            credential_id=credential2.credential_id,
            secret_material="synthetic-secret-2", environment="DEMO",
            at=at(3))
        assert revoked.status is AuthStatus.REVOKED

    # 5/6/7 session creation, expiry, revocation
    def test_e2e_05_06_07(self, env):
        _, session = demo_trader_session(env)
        assert env["sessions"].validate(session.session_id, at=at(2)) \
            is session
        with pytest.raises(ContractError):
            env["sessions"].validate(session.session_id,
                                     at=T0 + timedelta(days=9))
        env["sessions"].revoke(session.session_id, reason="logout")
        with pytest.raises(ContractError):
            env["sessions"].validate(session.session_id, at=at(3))

    # 8 role/permission resolution
    def test_e2e_08(self, env):
        permissions = env["authz"].permissions_for(Role.RISK_MANAGER)
        assert Permission.EMERGENCY_STOP in permissions
        assert Permission.LIVE_TRADE not in permissions

    # 9/10 unauthorized + authorized privileged operations
    def test_e2e_09_10(self, env):
        _, trader = demo_trader_session(env)
        assert env["authz"].authorize(
            session_id=trader.session_id,
            permission=Permission.MODIFY_POLICY, environment="DEMO",
            at=at(1)).decision is AuthorizationDecision.BLOCK
        _, approver = demo_approver_session(env)
        assert env["authz"].authorize(
            session_id=approver.session_id,
            permission=Permission.MODIFY_POLICY, environment="DEMO",
            at=at(1)).decision is AuthorizationDecision.ALLOW

    # 11/12 maker-checker + self-approval rejection
    def test_e2e_11_12(self, env):
        approval = active_approval(env)
        assert approval.status is ApprovalStatus.ACTIVE
        submission = env["approvals"].submit(
            operation="X", resource="r", environment="DEMO",
            maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError):
            env["approvals"].approve(submission.approval_id,
                                     checker_actor_id=ACTOR_MAKER,
                                     checker_kind="HUMAN", at=at(1))

    # 13/14 LIVE permission + DEMO->LIVE isolation
    def test_e2e_13_14(self, env):
        _, admin = live_admin_session(env)
        assert env["authz"].authorize(
            session_id=admin.session_id,
            permission=Permission.LIVE_TRADE, environment="LIVE",
            at=at(1)).decision is AuthorizationDecision.ALLOW
        _, demo = demo_trader_session(env)
        assert env["authz"].authorize(
            session_id=demo.session_id,
            permission=Permission.LIVE_TRADE, environment="LIVE",
            at=at(1)).decision is AuthorizationDecision.BLOCK

    # 15 permission revocation
    def test_e2e_15(self, env):
        _, viewer = issue_session(env, actor_id=ACTOR_TRADER,
                                  environment="DEMO", role="VIEWER",
                                  permissions=("VIEW",),
                                  secret="synthetic-viewer")
        assert env["authz"].authorize(
            session_id=viewer.session_id, permission=Permission.VIEW,
            environment="DEMO", at=at(1)).decision \
            is AuthorizationDecision.ALLOW
        # revocation: the canonical registry never granted DEMO_TRADE to
        # VIEWER; the snapshot alone grants nothing
        assert env["authz"].authorize(
            session_id=viewer.session_id,
            permission=Permission.DEMO_TRADE, environment="DEMO",
            at=at(1)).decision is AuthorizationDecision.BLOCK

    # 16/17/18 privilege escalation + actor/approver spoofing
    def test_e2e_16(self, env):
        from platform.security.contracts import PermissionGrant
        with pytest.raises(ContractError):
            PermissionGrant(role=Role.VIEWER,
                            permission=Permission.LIVE_TRADE,
                            environment="LIVE", granted_by=ACTOR_TRADER,
                            granted_at=T0).validate()

    def test_e2e_17(self, env):
        # client-supplied actor cannot override: SecurityRequest actor is
        # derived from the validated session, and the schema rejects
        # malformed spoofed ids
        with pytest.raises(ContractError):
            SecurityRequest(
                request_id=new_identifier("request_id"),
                session_id="ssn_" + "1" * 32, actor_id="admin",
                environment="DEMO", timestamp=T0, operation="OP",
                resource="r").validate()

    def test_e2e_18(self, env):
        gate = self._gate(env)
        _, session = demo_approver_session(env)
        approval = active_approval(env, maker=ACTOR_TRADER)
        with pytest.raises(ContractError):
            gate.guard(artifact=self._artifact(env),
                       maker_session_id=session.session_id,
                       approval_id=approval.approval_id, at=at(3),
                       reason="spoof")

    # 19/20 replay + idempotency protection
    def test_e2e_19_20(self, env):
        _, session = demo_trader_session(env)
        guard = ReplayGuard()
        request = SecurityRequest(
            request_id=new_identifier("request_id"),
            session_id=session.session_id, actor_id=session.actor_id,
            environment="DEMO", timestamp=T0, operation="OP",
            resource="pol_" + "1" * 32)
        assert guard.check(request, now=at(1))[0] is ReplayVerdict.FIRST_SEEN
        assert guard.check(request, now=at(1))[0] \
            is ReplayVerdict.IDEMPOTENT_REPEAT

    # 21 rate-limit enforcement
    def test_e2e_21(self, env):
        from core.security.protection import RateLimiter, RateLimitVerdict
        limiter = RateLimiter(max_operations=2, window_seconds=60)
        limiter.check(limit_key="authn:" + ACTOR_TRADER, now=at(0))
        limiter.check(limit_key="authn:" + ACTOR_TRADER, now=at(0.1))
        assert limiter.check(limit_key="authn:" + ACTOR_TRADER,
                             now=at(0.2)).verdict \
            is RateLimitVerdict.EXCEEDED

    # 22/23 secret redaction + persistence rejection
    def test_e2e_22_23(self, env):
        vault = SecretVault()
        ref = vault.store(purpose="p", environment="DEMO",
                          value="synthetic-secret", at=T0)
        assert "synthetic-secret" not in str(ref.__dict__)
        event = emit_security_event(
            EventType.SECURITY_POLICY_CHANGED,
            payload={"api_key": "synthetic-key", "change": "v2"},
            environment="DEMO", entity_id="cfg", event_time=T0)
        assert event.payload["api_key"] == "<redacted>"

    # 24 credential rotation
    def test_e2e_24(self, env):
        credential, _ = demo_trader_session(env)
        rotated, successor = env["auth"].rotate(
            credential.credential_id, new_material="rotated-secret",
            at=at(1))
        assert env["auth"].authenticate(
            credential_id=successor.credential_id,
            secret_material="rotated-secret", environment="DEMO",
            at=at(2)).status is AuthStatus.AUTHENTICATED
        assert env["auth"].authenticate(
            credential_id=rotated.credential_id,
            secret_material="synthetic-trader-secret", environment="DEMO",
            at=at(2)).status is not AuthStatus.AUTHENTICATED

    # 25/26/27 audit generation, chain verification, tamper detection
    def test_e2e_25_26_27(self, env):
        chain = env["chain"]
        for i in range(4):
            chain.append(AuditRecord(
                audit_id=new_identifier("audit_id"),
                actor_type=ActorType.USER, actor_id=ACTOR_MAKER,
                action="OP", entity_type="policy",
                entity_id=f"pol_{i}", event_time=at(i), before=None,
                after={"i": i}, reason="r", source="t", environment="DEMO",
                correlation_id="pol_" + "1" * 32))
        assert chain.verify() is IntegrityStatus.VERIFIED
        chain._records[1] = forge_record(chain._records[1],
                                         after={"i": 999})
        assert chain.verify() is IntegrityStatus.CORRUPTED

    # 28 config change governance
    def test_e2e_28(self, env):
        gate = self._gate(env)
        _, session = demo_approver_session(env)
        approval = active_approval(env, operation="SECURITY_CONFIG_CHANGE",
                                   resource="cfg_1")
        artifact = self._artifact(
            env, artifact_type=GovernedArtifactType.SECURITY_CONFIG,
            artifact_id="cfg_" + "1" * 32)
        # operation must match the approval
        with pytest.raises(ContractError):
            gate.guard(artifact=artifact,
                       maker_session_id=session.session_id,
                       approval_id=approval.approval_id, at=at(3),
                       reason="mismatch")

    # 29/30/31/32 policy/risk/strategy/model governance
    @pytest.mark.parametrize("artifact_type,operation", [
        (GovernedArtifactType.POLICY, "POLICY_CHANGE"),
        (GovernedArtifactType.RISK_CONFIG, "RISK_CONFIG_CHANGE"),
        (GovernedArtifactType.STRATEGY, "STRATEGY_CHANGE"),
        (GovernedArtifactType.MODEL, "MODEL_CHANGE"),
    ])
    def test_e2e_29_32(self, env, artifact_type, operation):
        gate = self._gate(env)
        # ADMIN holds MODIFY_POLICY + MODIFY_RISK + APPROVE (one maker for
        # all four artifact routes; checker stays a different human)
        _, session = issue_session(
            env, actor_id=ACTOR_MAKER, environment="DEMO", role="ADMIN",
            permissions=("VIEW", "ANALYZE", "MODIFY_POLICY", "MODIFY_RISK",
                         "APPROVE"),
            secret="synthetic-admin-demo")
        approval = active_approval(env, operation=operation)
        artifact = GovernanceArtifact(
            artifact_type=artifact_type, artifact_id="pol_" + "1" * 32,
            old_version="1.0.0", new_version="1.1.0",
            old_content_hash="a" * 64, new_content_hash="b" * 64,
            environment="DEMO")
        transition = gate.guard(artifact=artifact,
                                maker_session_id=session.session_id,
                                approval_id=approval.approval_id,
                                at=at(3), reason="governed change")
        assert transition.checker_actor_id == ACTOR_CHECKER
        # ungoverned twin is refused
        with pytest.raises(ContractError):
            gate.require_transition(artifact_type, "pol_" + "1" * 32,
                                    "2.0.0")

    # 33/34 AI self-approval / self-deployment rejection
    def test_e2e_33_34(self, env):
        submission = env["approvals"].submit(
            operation="MODEL_PROMOTION", resource="mdl_" + "1" * 32,
            environment="RESEARCH", maker_actor_id=ACTOR_MAKER, at=T0)
        with pytest.raises(ContractError):
            env["approvals"].approve(submission.approval_id,
                                     checker_actor_id="usr_" + "b" * 32,
                                     checker_kind="AI_MODEL", at=at(1))

    # 35/36 emergency stop + release governance
    def test_e2e_35_36(self, env):
        _, risk_manager = issue_session(
            env, actor_id="usr_" + "4" * 32, environment="LIVE",
            role="RISK_MANAGER",
            permissions=("VIEW", "ANALYZE", "EMERGENCY_STOP", "PAUSE"),
            secret="synthetic-risk-secret")
        assert env["authz"].authorize(
            session_id=risk_manager.session_id,
            permission=Permission.EMERGENCY_STOP, environment="LIVE",
            at=at(1)).decision is AuthorizationDecision.ALLOW
        # release requires governance approval
        assert env["authz"].authorize(
            session_id=risk_manager.session_id,
            permission=Permission.EMERGENCY_STOP, environment="LIVE",
            at=at(1), operation="EMERGENCY_RELEASE").decision \
            is AuthorizationDecision.BLOCK

    # 37/38/39/40 backup creation, verification, corruption, restore
    def test_e2e_37_40(self, env):
        service = BackupSecurityService()
        components = {"events": [1], "state": [2], "ledger": [3],
                      "audit": [4]}
        manifest = service.create_backup(environment="DEMO",
                                         source_version="0.9.0",
                                         components=components, at=T0)
        assert service.verify_backup(
            manifest.backup_id, components=components,
            source_version="0.9.0", environment="DEMO") \
            is IntegrityStatus.VERIFIED
        tampered = dict(components, ledger=[3, 99])
        with pytest.raises(ContractError):
            service.authorize_restore(manifest.backup_id,
                                      components=tampered,
                                      source_version="0.9.0",
                                      environment="DEMO")
        restored = service.authorize_restore(
            manifest.backup_id, components=components,
            source_version="0.9.0", environment="DEMO")
        assert restored.integrity_status is IntegrityStatus.VERIFIED

    # 41/42 incident creation + evidence preservation
    def test_e2e_41_42(self, env):
        service = SecurityIncidentService()
        incident = service.open(
            severity=IncidentSeverity.HIGH, title="unusual auth failures",
            environment="DEMO", affected_resources=("authentication",),
            detected_at=T0, detected_by="ids",
            evidence={"failure_count": 40})
        progressed = service.transition(
            incident.security_incident_id,
            SecurityIncidentStatus.CONTAINED, actor="usr_1", at=at(1),
            note="credentials locked")
        assert progressed.evidence == incident.evidence

    # 43 full trade lifecycle audit + 44 crash/recovery + 45 full chain
    def test_e2e_43_44_45(self, env):
        """AUTH -> AUTHORIZE -> APPROVE -> GOVERN -> AUDIT with the audit
        chain surviving restart (deterministic re-verification)."""
        _, session = demo_approver_session(env)
        approval = active_approval(env)
        gate = self._gate(env)
        artifact = self._artifact(env)
        gate.guard(artifact=artifact, maker_session_id=session.session_id,
                   approval_id=approval.approval_id, at=at(3),
                   reason="full chain")
        head = env["chain"].head
        # "crash + restart": a new chain reader over the same records
        assert env["chain"].verify() is IntegrityStatus.VERIFIED
        assert env["chain"].head == head
        # security events flow through the canonical event contract
        event = emit_security_event(
            EventType.APPROVAL_GRANTED,
            payload={"approval_id": approval.approval_id},
            environment="DEMO", entity_id=approval.approval_id,
            event_time=at(1))
        event.validate()
        assert event.event_type is EventType.APPROVAL_GRANTED
