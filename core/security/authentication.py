"""Authentication + credential lifecycle (owned by core.security).

Deterministic, fail-closed. Credential verification uses PBKDF2-HMAC-
SHA256 (stdlib; no invented cryptography). Only a salted VERIFIER is
stored - the secret itself never persists, never logs, never enters
audit payloads or error messages.

UNKNOWN is never AUTHENTICATED (SECTION 5/55).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_utc

from core.security.contracts import (
    AUTH_STRENGTH,
    AuthMethod,
    AuthStatus,
    AuthenticationResult,
    CredentialRecord,
    CredentialStatus,
    SessionRecord,
    SessionStatus,
)

CONTRACT_VERSION = "1.0.0"
CREDENTIAL_MACHINE = "credential_lifecycle"
SESSION_MACHINE = "session_state"
PBKDF2_ITERATIONS = 100_000
LOCKOUT_THRESHOLD = 5
CREDENTIAL_TTL = timedelta(hours=12)


class AuthenticationError(ContractError):
    rule_id = "SEC-AUTH"


def _verifier(secret_material: str, salt_hex: str) -> str:
    derived = hashlib.pbkdf2_hmac(
        "sha256", secret_material.encode("utf-8"),
        bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
    return derived.hex()


def _safe_message(reason: str) -> str:
    """Error text never echoes secret material back."""
    return "authentication failed: " + reason.split("secret=")[0].strip()


class AuthenticationService:
    """Credential lifecycle + deterministic authentication."""

    def __init__(self, machines: StateMachineRegistry | None = None) -> None:
        self._machines = machines or build_state_machine_registry()
        self._credentials: dict[str, CredentialRecord] = {}
        self._failures: dict[str, int] = {}

    # ---------------- credential lifecycle ---------------- #
    def issue(self, *, actor_id: str, environment: str, method: AuthMethod,
              secret_material: str, at: datetime,
              ttl: timedelta = CREDENTIAL_TTL) -> tuple[CredentialRecord, str]:
        if not isinstance(secret_material, str) or len(secret_material) < 8:
            raise AuthenticationError(
                "credential material too weak (minimum 8 characters)",
                location="auth.issue", rule_id="SEC-CREDENTIAL")
        salt_hex = _secrets.token_hex(16)
        record = CredentialRecord(
            credential_id=new_identifier("credential_id"),
            actor_id=actor_id, status=CredentialStatus.ISSUED,
            method=method, environment=environment,
            issued_at=ensure_utc(at, location="auth.issue.at"),
            expires_at=ensure_utc(at) + ttl,
            salt_hex=salt_hex,
            verifier_hash=_verifier(secret_material, salt_hex),
            version=1)
        record.validate()
        self._activate(record)
        self._credentials[record.credential_id] = self._activate(record)
        return self._credentials[record.credential_id], secret_material

    def _activate(self, record: CredentialRecord) -> CredentialRecord:
        self._machines.apply(CREDENTIAL_MACHINE, record.status.value,
                             "ACTIVE", reason="activation", actor="system")
        from dataclasses import replace
        return replace(record, status=CredentialStatus.ACTIVE)

    def rotate(self, credential_id: str, *, new_material: str,
               at: datetime) -> tuple[CredentialRecord, CredentialRecord]:
        """Rotation preserves history: the old version stays immutable and
        can never regain authority; a NEW version is issued."""
        current = self._require(credential_id)
        self._machines.apply(CREDENTIAL_MACHINE, current.status.value,
                             "ROTATION_REQUIRED", reason="rotation policy",
                             actor="system")
        self._machines.apply(CREDENTIAL_MACHINE, "ROTATION_REQUIRED",
                             "ROTATED", reason="rotated", actor="system")
        from dataclasses import replace
        rotated = replace(current, status=CredentialStatus.ROTATED)
        salt_hex = _secrets.token_hex(16)
        successor = CredentialRecord(
            credential_id=new_identifier("credential_id"),
            actor_id=current.actor_id, status=CredentialStatus.ACTIVE,
            method=current.method, environment=current.environment,
            issued_at=ensure_utc(at, location="auth.rotate.at"),
            expires_at=ensure_utc(at) + CREDENTIAL_TTL,
            salt_hex=salt_hex,
            verifier_hash=_verifier(new_material, salt_hex),
            version=current.version + 1, rotated_from=rotated.credential_id)
        successor.validate()
        self._credentials[rotated.credential_id] = rotated
        self._credentials[successor.credential_id] = successor
        return rotated, successor

    def revoke(self, credential_id: str, *, at: datetime,
               reason: str) -> CredentialRecord:
        current = self._require(credential_id)
        if current.status in (CredentialStatus.REVOKED,
                              CredentialStatus.EXPIRED):
            raise AuthenticationError(
                f"credential already terminal ({current.status.value})",
                location="auth.revoke", rule_id="SEC-CREDENTIAL")
        self._machines.apply(CREDENTIAL_MACHINE, current.status.value,
                             "REVOKED", reason=reason, actor="system")
        from dataclasses import replace
        revoked = replace(current, status=CredentialStatus.REVOKED,
                          revoked_at=ensure_utc(at), revoked_reason=reason)
        revoked.validate()
        self._credentials[credential_id] = revoked
        return revoked

    # ---------------- authentication ---------------- #
    def authenticate(self, *, credential_id: str, secret_material: str,
                     environment: str, at: datetime) -> AuthenticationResult:
        moment = ensure_utc(at, location="auth.at")
        record = self._credentials.get(credential_id)
        if record is None:
            return self._failure(credential_id, environment, moment,
                                 AuthStatus.UNAUTHENTICATED,
                                 "unknown credential")
        # environment binding: a credential issued for one environment
        # never authenticates another
        if record.environment != environment:
            return self._failure(credential_id, environment, moment,
                                 AuthStatus.UNAUTHENTICATED,
                                 "environment mismatch")
        if record.status is CredentialStatus.REVOKED:
            return self._failure(credential_id, environment, moment,
                                 AuthStatus.REVOKED,
                                 f"revoked: {record.revoked_reason or 'policy'}")
        if record.status is CredentialStatus.EXPIRED or \
                ensure_utc(record.expires_at) < moment:
            return self._failure(credential_id, environment, moment,
                                 AuthStatus.EXPIRED, "credential expired")
        if self._failures.get(record.actor_id, 0) >= LOCKOUT_THRESHOLD:
            return self._failure(credential_id, environment, moment,
                                 AuthStatus.LOCKED,
                                 "too many failed attempts")
        if record.status not in (CredentialStatus.ACTIVE,
                                 CredentialStatus.ROTATION_REQUIRED):
            return self._failure(credential_id, environment, moment,
                                 AuthStatus.UNKNOWN,
                                 f"credential state {record.status.value}")
        if not hmac.compare_digest(
                _verifier(secret_material, record.salt_hex),
                record.verifier_hash):
            self._failures[record.actor_id] = \
                self._failures.get(record.actor_id, 0) + 1
            return self._failure(credential_id, environment, moment,
                                 AuthStatus.UNAUTHENTICATED,
                                 "verifier mismatch")
        self._failures[record.actor_id] = 0
        result = AuthenticationResult(
            actor_id=record.actor_id,
            status=AuthStatus.AUTHENTICATED,
            method=record.method,
            authenticated_at=moment,
            environment=environment,
            credential_id=credential_id,
            strength=AUTH_STRENGTH[record.method],
            expires_at=ensure_utc(record.expires_at))
        result.validate()
        return result

    def _failure(self, credential_id: str, environment: str,
                 moment: datetime, status: AuthStatus,
                 reason: str) -> AuthenticationResult:
        # actor_id is required by the contract; for unknown credentials a
        # synthetic non-actor id is used and the status stays UNAUTHENTICATED
        result = AuthenticationResult(
            actor_id="usr_" + "0" * 32,
            status=status,
            method=AuthMethod.TOKEN,
            authenticated_at=moment,
            environment=environment,
            credential_id=credential_id,
            strength=1,
            expires_at=moment,
            failure_reason=_safe_message(reason))
        result.validate()
        return result

    def record(self, credential_id: str) -> CredentialRecord:
        return self._require(credential_id)

    def _require(self, credential_id: str) -> CredentialRecord:
        record = self._credentials.get(credential_id)
        if record is None:
            raise AuthenticationError(
                "unknown credential", location="auth.store",
                rule_id="SEC-CREDENTIAL",
                details={"credential": credential_id})
        return record


class SessionService:
    """Sessions are created ONLY from AUTHENTICATED results (the contract
    enforces it). Expired/revoked/unknown sessions always BLOCK."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    def create(self, *, authentication: AuthenticationResult,
               role: str, permissions: tuple[str, ...],
               session_ttl: timedelta = timedelta(hours=8)) -> SessionRecord:
        if authentication.status is not AuthStatus.AUTHENTICATED:
            raise AuthenticationError(
                "sessions require an AUTHENTICATED result (UNKNOWN never "
                "becomes a session)",
                location="session.create", rule_id="SEC-FAIL-CLOSED")
        issued = ensure_utc(authentication.authenticated_at)
        session = SessionRecord(
            session_id=new_identifier("session_id"),
            actor_id=authentication.actor_id,
            environment=authentication.environment,
            issued_at=issued,
            expires_at=issued + session_ttl,
            authentication=authentication,
            role=role,
            permission_snapshot=tuple(sorted(set(permissions))),
            last_activity=issued)
        session.validate()
        self._sessions[session.session_id] = session
        return session

    def validate(self, session_id: str, *, at: datetime) -> SessionRecord:
        moment = ensure_utc(at, location="session.validate.at")
        session = self._sessions.get(session_id)
        if session is None:
            raise AuthenticationError(
                "unknown session", location="session.validate",
                rule_id="SEC-FAIL-CLOSED",
                details={"session": session_id})
        if session.status is SessionStatus.REVOKED:
            raise AuthenticationError(
                f"session revoked: {session.revoked_reason or 'policy'}",
                location="session.validate", rule_id="SEC-FAIL-CLOSED")
        if session.status is SessionStatus.EXPIRED or \
                ensure_utc(session.expires_at) < moment:
            raise AuthenticationError(
                "session expired", location="session.validate",
                rule_id="SEC-FAIL-CLOSED")
        return session

    def revoke(self, session_id: str, *, reason: str) -> SessionRecord:
        session = self._sessions.get(session_id)
        if session is None:
            raise AuthenticationError(
                "unknown session", location="session.revoke",
                rule_id="SEC-FAIL-CLOSED")
        from dataclasses import replace
        revoked = replace(session, status=SessionStatus.REVOKED,
                          revoked_reason=reason)
        revoked.validate()
        self._sessions[session_id] = revoked
        return revoked

    def expire(self, session_id: str) -> SessionRecord:
        session = self._sessions.get(session_id)
        if session is None:
            raise AuthenticationError(
                "unknown session", location="session.expire",
                rule_id="SEC-FAIL-CLOSED")
        from dataclasses import replace
        expired = replace(session, status=SessionStatus.EXPIRED)
        expired.validate()
        self._sessions[session_id] = expired
        return expired

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)
