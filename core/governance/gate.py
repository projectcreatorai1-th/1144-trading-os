"""Governance contracts + gate (owned by core.governance).

SECTION 27: every privileged artifact transition records WHO / WHAT /
WHEN / WHY / OLD VERSION / NEW VERSION / APPROVAL / EVIDENCE. The
GovernanceGate wraps the EXISTING lifecycles (Phase 3/4/7) - it creates
no second policy/strategy/model engine. It guards; it never authorizes
trading risk and never converts BLOCK into ALLOW.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier, validate_identifier
from architecture.contracts.time import ensure_utc

from core.security.authentication import SessionService
from core.security.authorization import (
    AuthorizationDecision,
    AuthorizationService,
)
from core.security.contracts import ApprovalRecord
from core.security.protection import AuditChain
from core.security.services import MakerCheckerService
from platform.audit.contracts import ActorType, AuditRecord
from platform.security.contracts import Permission

CONTRACT_VERSION = "1.0.0"


class GovernanceError(ContractError):
    rule_id = "SEC-GOVERNANCE"


class GovernedArtifactType(Enum):
    POLICY = "POLICY"
    RISK_CONFIG = "RISK_CONFIG"
    STRATEGY = "STRATEGY"
    MODEL = "MODEL"
    SECURITY_CONFIG = "SECURITY_CONFIG"
    ROLE_PERMISSION_CONFIG = "ROLE_PERMISSION_CONFIG"
    ENVIRONMENT_CONFIG = "ENVIRONMENT_CONFIG"


#: artifact type -> permission required to make/transition it
#: (the canonical registry decides; this table only routes).
ARTIFACT_PERMISSIONS: Mapping[GovernedArtifactType, Permission] = {
    GovernedArtifactType.POLICY: Permission.MODIFY_POLICY,
    GovernedArtifactType.RISK_CONFIG: Permission.MODIFY_RISK,
    GovernedArtifactType.STRATEGY: Permission.MODIFY_POLICY,
    GovernedArtifactType.MODEL: Permission.APPROVE,
    GovernedArtifactType.SECURITY_CONFIG: Permission.MANAGE_SECURITY,
    GovernedArtifactType.ROLE_PERMISSION_CONFIG: Permission.MANAGE_GOVERNANCE,
    GovernedArtifactType.ENVIRONMENT_CONFIG: Permission.CONFIGURE,
}

#: artifact types whose transitions ALWAYS require maker-checker.
SEPARATION_REQUIRED = tuple(GovernedArtifactType)


@dataclass(frozen=True)
class GovernanceArtifact:
    artifact_type: GovernedArtifactType
    artifact_id: str
    old_version: str | None
    new_version: str
    old_content_hash: str | None
    new_content_hash: str
    environment: str
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.artifact_type, GovernedArtifactType):
            raise GovernanceError(
                "artifact.type must be a GovernedArtifactType",
                location="gov.type", rule_id="SCHEMA-ENUM")
        if not isinstance(self.artifact_id, str) or not self.artifact_id:
            raise GovernanceError(
                "artifact.artifact_id must be a non-empty string",
                location="gov.artifact_id")
        if not isinstance(self.new_version, str) or not self.new_version:
            raise GovernanceError(
                "configuration changes are versioned: new_version required",
                location="gov.new_version", rule_id="SEC-CONFIG-GOV")
        for name in ("old_content_hash", "new_content_hash"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str)
                                      or len(value) != 64):
                raise GovernanceError(
                    f"artifact.{name} must be a sha-256 hex string",
                    location=f"gov.{name}")


@dataclass(frozen=True)
class GovernanceTransition:
    governance_transition_id: str
    artifact: GovernanceArtifact
    maker_actor_id: str
    checker_actor_id: str
    approval_id: str
    decided_at: datetime
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    environment: str = "RESEARCH"
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("governance_transition_id",
                            self.governance_transition_id,
                            location="gov.governance_transition_id")
        if not isinstance(self.artifact, GovernanceArtifact):
            raise GovernanceError(
                "transition.artifact must be a GovernanceArtifact",
                location="gov.artifact", rule_id="SCHEMA-ENUM")
        validate_identifier("user_id", self.maker_actor_id,
                            location="gov.maker_actor_id")
        validate_identifier("user_id", self.checker_actor_id,
                            location="gov.checker_actor_id")
        if self.maker_actor_id == self.checker_actor_id:
            raise GovernanceError(
                "maker and checker must differ (separation of duties)",
                location="gov.checker_actor_id", rule_id="SEC-SELF-APPROVAL")
        validate_identifier("approval_id", self.approval_id,
                            location="gov.approval_id")
        ensure_utc(self.decided_at, location="gov.decided_at")
        if not isinstance(self.reason, str) or not self.reason:
            raise GovernanceError(
                "transition.reason is mandatory (WHY)",
                location="gov.reason", rule_id="SEC-GOVERNANCE")


class GovernanceGate:
    """The single governance control point for privileged transitions.

    Guard order (all must pass, all fail closed):
    1. audit chain integrity verifies (else BLOCK)
    2. maker session valid + holds the artifact permission
    3. maker-checker approval ACTIVE and made by a DIFFERENT human
    4. artifact versioned with content hashes
    5. immutable GovernanceTransition + chained audit record
    """

    def __init__(self, *, sessions: SessionService,
                 authorization: AuthorizationService,
                 approvals: MakerCheckerService,
                 audit: AuditChain) -> None:
        self._sessions = sessions
        self._authorization = authorization
        self._approvals = approvals
        self._audit = audit
        self._transitions: dict[str, GovernanceTransition] = {}

    def guard(self, *, artifact: GovernanceArtifact, maker_session_id: str,
              approval_id: str, at: datetime, reason: str,
              evidence: Mapping[str, Any] | None = None) -> GovernanceTransition:
        moment = ensure_utc(at, location="gov.at")
        # 1. audit integrity gates privileged operations (SECTION 41)
        self._audit.require_verified()
        # 2. maker must be an authenticated, permitted actor
        permission = ARTIFACT_PERMISSIONS[artifact.artifact_type]
        maker_allowed = self._authorization.authorize(
            session_id=maker_session_id, permission=permission,
            environment=artifact.environment, at=moment)
        if maker_allowed.decision is not AuthorizationDecision.ALLOW:
            raise GovernanceError(
                f"maker not permitted: {maker_allowed.reasons}",
                location="gov.guard.maker", rule_id="SEC-GOVERNANCE",
                details={"permission": permission.value})
        maker = self._sessions.validate(maker_session_id, at=moment)
        # 3. maker-checker: ACTIVE approval by a different human
        approval = self._approvals.require_active(
            approval_id, operation=f"{artifact.artifact_type.value}_CHANGE")
        if approval.maker_actor_id != maker.actor_id:
            raise GovernanceError(
                "approval maker does not match the requesting actor "
                "(approval spoofing blocked)",
                location="gov.guard.approval", rule_id="SEC-APPROVAL-SPOOF")
        # 4. artifact versioning integrity
        artifact.validate()
        # 5. immutable evidence
        transition = GovernanceTransition(
            governance_transition_id=new_identifier("governance_transition_id"),
            artifact=artifact, maker_actor_id=maker.actor_id,
            checker_actor_id=approval.checker_actor_id or "",
            approval_id=approval.approval_id, decided_at=moment,
            reason=reason, evidence=dict(evidence or {}),
            environment=artifact.environment)
        if not transition.checker_actor_id:
            raise GovernanceError(
                "an ACTIVE approval must carry its checker",
                location="gov.guard.checker", rule_id="SEC-SELF-APPROVAL")
        transition.validate()
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.USER, actor_id=maker.actor_id,
            action="GOVERNANCE_TRANSITION",
            entity_type=artifact.artifact_type.value,
            entity_id=f"{artifact.artifact_id}@{artifact.new_version}",
            event_time=moment,
            before={"version": artifact.old_version,
                    "hash": artifact.old_content_hash},
            after={"version": artifact.new_version,
                   "hash": artifact.new_content_hash},
            reason=reason, source="core.governance.gate",
            environment=artifact.environment,
            correlation_id=artifact.artifact_id))
        self._transitions[transition.governance_transition_id] = transition
        return transition

    def transitions(self) -> tuple[GovernanceTransition, ...]:
        return tuple(self._transitions.values())

    def require_transition(self, artifact_type: GovernedArtifactType,
                           artifact_id: str, new_version: str) \
            -> GovernanceTransition:
        """Verify a privileged change carries its governance evidence."""
        for transition in self._transitions.values():
            if (transition.artifact.artifact_type is artifact_type
                    and transition.artifact.artifact_id == artifact_id
                    and transition.artifact.new_version == new_version):
                return transition
        raise GovernanceError(
            "no governance evidence for this artifact version (ungoverned "
            "privileged change blocked)",
            location="gov.require", rule_id="SEC-GOVERNANCE",
            details={"artifact": artifact_id, "version": new_version})
