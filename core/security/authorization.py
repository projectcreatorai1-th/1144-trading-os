"""Canonical authorization (owned by core.security).

There is exactly ONE permission source: platform.security's
RolePermissionRegistry backed by architecture/permissions.yaml (SECTION
3.2). This service adds the guardrail checks AROUND that source -
session validity, credential validity, environment binding - and never
introduces a second matrix, a hidden check or a bypass.

The decision is deterministic: same actor/session/permission/environment/
versions -> same decision (SECTION 54). Security never converts BLOCK
into ALLOW and never evaluates trading risk (that is core.risk's sole
authority).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from architecture.contracts.errors import ContractError
from architecture.contracts.time import ensure_utc

from core.security.authentication import SessionService
from core.security.contracts import (
    AuthStatus,
    SessionRecord,
    SessionStatus,
)
from platform.security.contracts import (
    Permission,
    Role,
    RolePermissionRegistry,
    build_role_permission_registry,
)

CONTRACT_VERSION = "1.0.0"


class AuthorizationDecision(Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class AuthorizationError(ContractError):
    rule_id = "SEC-AUTHZ"


@dataclass(frozen=True)
class AuthorizationResult:
    decision: AuthorizationDecision
    actor_id: str
    permission: str
    environment: str
    reasons: tuple[str, ...]
    session_id: str

    @property
    def allowed(self) -> bool:
        return self.decision is AuthorizationDecision.ALLOW


#: Operations that additionally require a distinct human approval BEFORE
#: the authorization call can ever return ALLOW (SECTION 13).
APPROVAL_REQUIRED_OPERATIONS = (
    "LIVE_TRADE_AUTHORIZATION",
    "SECURITY_POLICY_CHANGE",
    "ROLE_PERMISSION_CHANGE",
    "CREDENTIAL_AUTHORITY_CHANGE",
    "POLICY_ACTIVATION",
    "RISK_CONFIG_CHANGE",
    "STRATEGY_PROMOTION",
    "MODEL_PROMOTION",
    "PRODUCTION_CONFIG_CHANGE",
    "EMERGENCY_RELEASE",
    "PRIVILEGED_RECOVERY",
)


class AuthorizationService:
    """The single authorization boundary. Deterministic; fail closed."""

    def __init__(self, sessions: SessionService,
                 registry: RolePermissionRegistry | None = None) -> None:
        self._sessions = sessions
        self._registry = registry or build_role_permission_registry()

    @property
    def registry(self) -> RolePermissionRegistry:
        """The one canonical permission source (no second engine)."""
        return self._registry

    def authorize(self, *, session_id: str, permission: Permission,
                  environment: str, at: datetime,
                  approval_ref: str | None = None,
                  operation: str | None = None) -> AuthorizationResult:
        moment = ensure_utc(at, location="authz.at")

        def block(reason: str, actor: str = "usr_" + "0" * 32) \
                -> AuthorizationResult:
            return AuthorizationResult(
                decision=AuthorizationDecision.BLOCK,
                actor_id=actor,
                permission=permission.value,
                environment=environment,
                reasons=(reason,),
                session_id=session_id)

        try:
            session = self._sessions.validate(session_id, at=moment)
        except ContractError as error:
            return block(f"session invalid: {error.rule_id} "
                         f"({type(error).__name__})")
        if session.environment != environment:
            return block(
                f"ENVIRONMENT_MISMATCH: session is bound to "
                f"{session.environment}, request targets {environment} "
                "(no cross-environment fallback)",
                session.actor_id)
        # permission snapshot check: the frozen permissions at issue time;
        # a revoked permission drops out of the live registry and the
        # snapshot is re-derived from the canonical registry each call.
        if not self._registry.has_permission(session.role, permission):
            return block(
                f"PERMISSION_DENIED: role '{session.role}' does not hold "
                f"'{permission.value}' in the canonical registry",
                session.actor_id)
        allowed_envs = self._registry.allowed_environments(permission)
        if allowed_envs is not None and environment not in allowed_envs:
            return block(
                f"ENVIRONMENT_PERMISSION_MISMATCH: '{permission.value}' is "
                f"restricted to {allowed_envs}; request targets {environment}",
                session.actor_id)
        if operation in APPROVAL_REQUIRED_OPERATIONS and approval_ref is None:
            return block(
                "APPROVAL_REQUIRED: this operation needs a distinct human "
                "approval reference before it can be allowed",
                session.actor_id)
        if permission is Permission.LIVE_TRADE and \
                session.authentication.method.value == "API_KEY":
            return block(
                "LIVE requires strong authentication (API keys never "
                "authorize LIVE)",
                session.actor_id)
        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            actor_id=session.actor_id,
            permission=permission.value,
            environment=environment,
            reasons=("canonical registry grant",),
            session_id=session_id)

    def permissions_for(self, role: Role) -> frozenset[Permission]:
        return self._registry.permissions_for(role)
