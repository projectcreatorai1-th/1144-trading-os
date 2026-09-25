"""Security contracts (owned by core.security).

Phase 8 guardrail plane. Security establishes WHO is acting and WHETHER
the action is permitted - it never replaces Policy, Risk, Strategy
eligibility, Portfolio, OMS or EMS, and never converts BLOCK into ALLOW.

Fail closed everywhere: UNKNOWN is never AUTHENTICATED, never ALLOWED.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import new_identifier, validate_identifier
from architecture.contracts.time import ensure_utc

CONTRACT_VERSION = "1.0.0"


def canonical_hash(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _require_sha256(value: str, location: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
            c not in "0123456789abcdef" for c in value):
        raise ContractValidationError(
            f"{location} must be a sha-256 hex string", location=location)


# --------------------------------------------------------------------- #
# Authentication                                                         #
# --------------------------------------------------------------------- #
class AuthStatus(Enum):
    AUTHENTICATED = "AUTHENTICATED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    LOCKED = "LOCKED"
    UNKNOWN = "UNKNOWN"


class AuthMethod(Enum):
    API_KEY = "API_KEY"
    TOKEN = "TOKEN"
    CERTIFICATE = "CERTIFICATE"


#: Authentication strength ordering (higher = stronger).
AUTH_STRENGTH = {AuthMethod.TOKEN: 1, AuthMethod.CERTIFICATE: 2,
                 AuthMethod.API_KEY: 1}


@dataclass(frozen=True)
class AuthenticationResult:
    actor_id: str
    status: AuthStatus
    method: AuthMethod
    authenticated_at: datetime
    environment: str
    credential_id: str
    strength: int
    expires_at: datetime
    failure_reason: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("user_id", self.actor_id,
                            location="auth.actor_id")
        if not isinstance(self.status, AuthStatus):
            raise ContractValidationError(
                "auth.status must be an AuthStatus",
                location="auth.status", rule_id="SCHEMA-ENUM")
        if not isinstance(self.method, AuthMethod):
            raise ContractValidationError(
                "auth.method must be an AuthMethod",
                location="auth.method", rule_id="SCHEMA-ENUM")
        ensure_utc(self.authenticated_at, location="auth.authenticated_at")
        parse_environment(self.environment, location="auth.environment")
        validate_identifier("credential_id", self.credential_id,
                            location="auth.credential_id")
        if not isinstance(self.strength, int) or self.strength < 1:
            raise ContractValidationError(
                "auth.strength must be a positive integer",
                location="auth.strength")
        ensure_utc(self.expires_at, location="auth.expires_at")
        if self.status is AuthStatus.AUTHENTICATED and self.failure_reason:
            raise ContractValidationError(
                "an AUTHENTICATED result cannot carry a failure reason",
                location="auth.failure_reason", rule_id="SEC-CONTRACT")
        if self.status is not AuthStatus.AUTHENTICATED and \
                self.status is not AuthStatus.UNKNOWN and not self.failure_reason:
            raise ContractValidationError(
                f"a {self.status.value} result must record its failure reason",
                location="auth.failure_reason", rule_id="SEC-CONTRACT")


# --------------------------------------------------------------------- #
# Credentials                                                            #
# --------------------------------------------------------------------- #
class CredentialStatus(Enum):
    ISSUED = "ISSUED"
    ACTIVE = "ACTIVE"
    ROTATION_REQUIRED = "ROTATION_REQUIRED"
    ROTATED = "ROTATED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class CredentialRecord:
    """A credential's stored identity. The SECRET NEVER lives here: only a
    salted verifier (pbkdf2) is stored, and even that never enters logs,
    audit payloads or error messages (SECTION 6/7)."""
    credential_id: str
    actor_id: str
    status: CredentialStatus
    method: AuthMethod
    environment: str
    issued_at: datetime
    expires_at: datetime
    salt_hex: str
    verifier_hash: str
    version: int
    rotated_from: str | None = None
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("credential_id", self.credential_id,
                            location="credential.credential_id")
        validate_identifier("user_id", self.actor_id,
                            location="credential.actor_id")
        if not isinstance(self.status, CredentialStatus):
            raise ContractValidationError(
                "credential.status must be a CredentialStatus",
                location="credential.status", rule_id="SCHEMA-ENUM")
        if not isinstance(self.method, AuthMethod):
            raise ContractValidationError(
                "credential.method must be an AuthMethod",
                location="credential.method", rule_id="SCHEMA-ENUM")
        parse_environment(self.environment, location="credential.environment")
        ensure_utc(self.issued_at, location="credential.issued_at")
        ensure_utc(self.expires_at, location="credential.expires_at")
        if ensure_utc(self.expires_at) < ensure_utc(self.issued_at):
            raise ContractValidationError(
                "credential expiry cannot precede issue",
                location="credential.expires_at", rule_id="SEC-CONTRACT")
        _require_sha256(self.verifier_hash, "credential.verifier_hash")
        if not isinstance(self.salt_hex, str) or len(self.salt_hex) < 16:
            raise ContractValidationError(
                "credential.salt_hex must be a non-trivial salt",
                location="credential.salt_hex", rule_id="SEC-CONTRACT")
        if not isinstance(self.version, int) or self.version < 1:
            raise ContractValidationError(
                "credential.version must be a positive integer",
                location="credential.version")
        if self.status is CredentialStatus.REVOKED and self.revoked_at is None:
            raise ContractValidationError(
                "a REVOKED credential must record revoked_at",
                location="credential.revoked_at", rule_id="SEC-CONTRACT")
        if self.rotated_from is not None:
            validate_identifier("credential_id", self.rotated_from,
                                location="credential.rotated_from")

    @property
    def reference(self) -> str:
        """Safe log/audit reference: id + status only, never material."""
        return f"{self.credential_id}:{self.status.value}"


# --------------------------------------------------------------------- #
# Sessions                                                               #
# --------------------------------------------------------------------- #
class SessionStatus(Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    actor_id: str
    environment: str
    issued_at: datetime
    expires_at: datetime
    authentication: AuthenticationResult
    role: str
    permission_snapshot: tuple[str, ...]
    status: SessionStatus = SessionStatus.ACTIVE
    last_activity: datetime | None = None
    revoked_reason: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("session_id", self.session_id,
                            location="session.session_id")
        validate_identifier("user_id", self.actor_id,
                            location="session.actor_id")
        parse_environment(self.environment, location="session.environment")
        ensure_utc(self.issued_at, location="session.issued_at")
        ensure_utc(self.expires_at, location="session.expires_at")
        if ensure_utc(self.expires_at) < ensure_utc(self.issued_at):
            raise ContractValidationError(
                "session expiry cannot precede issue",
                location="session.expires_at", rule_id="SEC-CONTRACT")
        if not isinstance(self.authentication, AuthenticationResult):
            raise ContractValidationError(
                "session.authentication must be an AuthenticationResult",
                location="session.authentication", rule_id="SCHEMA-ENUM")
        if self.authentication.status is not AuthStatus.AUTHENTICATED:
            raise ContractValidationError(
                "a session can only be created from an AUTHENTICATED result "
                "(UNAUTHENTICATED/UNKNOWN never create sessions)",
                location="session.authentication", rule_id="SEC-FAIL-CLOSED")
        if not isinstance(self.status, SessionStatus):
            raise ContractValidationError(
                "session.status must be a SessionStatus",
                location="session.status", rule_id="SCHEMA-ENUM")
        if not self.permission_snapshot:
            raise ContractValidationError(
                "session.permission_snapshot must be recorded (the frozen "
                "permission set at issue time)",
                location="session.permission_snapshot", rule_id="SEC-CONTRACT")
        if self.last_activity is not None:
            ensure_utc(self.last_activity, location="session.last_activity")


# --------------------------------------------------------------------- #
# Secrets + keys                                                         #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class SecretReference:
    """A secret HANDLE. The value lives only inside the vault; this record
    is what may appear in logs/audit (SECRET VALUE != AUDIT EVIDENCE)."""
    secret_id: str
    purpose: str
    environment: str
    digest: str
    created_at: datetime
    rotated_from: str | None = None
    revoked: bool = False
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("secret_id", self.secret_id,
                            location="secret.secret_id")
        if not isinstance(self.purpose, str) or not self.purpose:
            raise ContractValidationError(
                "secret.purpose must be a non-empty string",
                location="secret.purpose")
        parse_environment(self.environment, location="secret.environment")
        _require_sha256(self.digest, "secret.digest")
        ensure_utc(self.created_at, location="secret.created_at")
        if self.rotated_from is not None:
            validate_identifier("secret_id", self.rotated_from,
                                location="secret.rotated_from")

    @property
    def redacted(self) -> str:
        return f"<secret:{self.secret_id}>"


class KeyStatus(Enum):
    ACTIVE = "ACTIVE"
    ROTATED = "ROTATED"
    REVOKED = "REVOKED"


class KeyPurpose(Enum):
    AUDIT_INTEGRITY = "AUDIT_INTEGRITY"
    SECRET_ENVELOPE = "SECRET_ENVELOPE"
    REQUEST_SIGNING = "REQUEST_SIGNING"


@dataclass(frozen=True)
class KeyRecord:
    key_id: str
    version: int
    purpose: KeyPurpose
    environment: str
    status: KeyStatus
    created_at: datetime
    rotated_from: str | None = None
    revoked_at: datetime | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("key_id", self.key_id, location="key.key_id")
        if not isinstance(self.version, int) or self.version < 1:
            raise ContractValidationError(
                "key.version must be a positive integer",
                location="key.version")
        if not isinstance(self.purpose, KeyPurpose):
            raise ContractValidationError(
                "key.purpose must be a KeyPurpose",
                location="key.purpose", rule_id="SCHEMA-ENUM")
        parse_environment(self.environment, location="key.environment")
        if not isinstance(self.status, KeyStatus):
            raise ContractValidationError(
                "key.status must be a KeyStatus",
                location="key.status", rule_id="SCHEMA-ENUM")
        ensure_utc(self.created_at, location="key.created_at")
        if self.status is KeyStatus.REVOKED and self.revoked_at is None:
            raise ContractValidationError(
                "a REVOKED key must record revoked_at",
                location="key.revoked_at", rule_id="SEC-CONTRACT")


# --------------------------------------------------------------------- #
# Encryption policy                                                      #
# --------------------------------------------------------------------- #
class DataClassification(Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    SECRET = "SECRET"


class ProtectionLevel(Enum):
    NONE = "NONE"
    INTEGRITY = "INTEGRITY"
    ENCRYPTED = "ENCRYPTED"
    ENCRYPTED_AT_REST_AND_TRANSIT = "ENCRYPTED_AT_REST_AND_TRANSIT"


#: Classification -> minimum required protection (SECTION 9). Hashing,
#: redaction and encoding are NOT encryption - the policy says so and the
#: tests enforce that a hash never satisfies an ENCRYPTED requirement.
REQUIRED_PROTECTION: Mapping[DataClassification, ProtectionLevel] = {
    DataClassification.PUBLIC: ProtectionLevel.NONE,
    DataClassification.INTERNAL: ProtectionLevel.INTEGRITY,
    DataClassification.CONFIDENTIAL: ProtectionLevel.ENCRYPTED,
    DataClassification.SECRET: ProtectionLevel.ENCRYPTED_AT_REST_AND_TRANSIT,
}


@dataclass(frozen=True)
class EncryptionPolicy:
    policy_id: str
    policy_version: str
    classification: DataClassification
    required_protection: ProtectionLevel
    environment: str
    content_hash: str
    created_at: datetime
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id:
            raise ContractValidationError(
                "policy.policy_id must be a non-empty string",
                location="encpolicy.policy_id")
        from architecture.contracts.versioning import SemVer
        SemVer.parse(self.policy_version, location="encpolicy.policy_version")
        if not isinstance(self.classification, DataClassification):
            raise ContractValidationError(
                "encpolicy.classification must be a DataClassification",
                location="encpolicy.classification", rule_id="SCHEMA-ENUM")
        if not isinstance(self.required_protection, ProtectionLevel):
            raise ContractValidationError(
                "encpolicy.required_protection must be a ProtectionLevel",
                location="encpolicy.required_protection", rule_id="SCHEMA-ENUM")
        if REQUIRED_PROTECTION[self.classification] != self.required_protection:
            raise ContractValidationError(
                f"classification {self.classification.value} requires "
                f"{REQUIRED_PROTECTION[self.classification].value} - weaker "
                "policy is rejected",
                location="encpolicy.required_protection", rule_id="SEC-ENCRYPTION")
        parse_environment(self.environment, location="encpolicy.environment")
        expected = canonical_hash({
            "classification": self.classification.value,
            "required_protection": self.required_protection.value,
        })
        if self.content_hash != expected:
            raise ContractValidationError(
                "encpolicy.content_hash mismatch",
                location="encpolicy.content_hash", rule_id="SEC-ENCRYPTION")


# --------------------------------------------------------------------- #
# Approvals (maker-checker)                                              #
# --------------------------------------------------------------------- #
class ApprovalStatus(Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACTIVE = "ACTIVE"


#: Actor types that may NEVER check (approve). AI is on this list forever.
NON_HUMAN_CHECKERS = ("AI_MODEL", "SYSTEM_AUTOMATED")


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    operation: str
    resource: str
    environment: str
    maker_actor_id: str
    checker_actor_id: str | None
    status: ApprovalStatus
    submitted_at: datetime
    decided_at: datetime | None = None
    reason: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("approval_id", self.approval_id,
                            location="approval.approval_id")
        for name in ("operation", "resource"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ContractValidationError(
                    f"approval.{name} must be a non-empty string",
                    location=f"approval.{name}")
        parse_environment(self.environment, location="approval.environment")
        validate_identifier("user_id", self.maker_actor_id,
                            location="approval.maker_actor_id")
        if self.checker_actor_id is not None:
            validate_identifier("user_id", self.checker_actor_id,
                                location="approval.checker_actor_id")
            if self.checker_actor_id == self.maker_actor_id:
                raise ContractValidationError(
                    "self-approval is impossible: maker and checker must be "
                    "different actors (maker-checker separation)",
                    location="approval.checker_actor_id", rule_id="SEC-SELF-APPROVAL")
        if not isinstance(self.status, ApprovalStatus):
            raise ContractValidationError(
                "approval.status must be an ApprovalStatus",
                location="approval.status", rule_id="SCHEMA-ENUM")
        ensure_utc(self.submitted_at, location="approval.submitted_at")
        if self.decided_at is not None:
            ensure_utc(self.decided_at, location="approval.decided_at")
            if ensure_utc(self.decided_at) < ensure_utc(self.submitted_at):
                raise ContractValidationError(
                    "approval decision cannot precede submission",
                    location="approval.decided_at", rule_id="SEC-CONTRACT")
        if self.expires_at is not None:
            ensure_utc(self.expires_at, location="approval.expires_at")
        # an APPROVED/ACTIVE record must carry a distinct human checker
        if self.status in (ApprovalStatus.APPROVED, ApprovalStatus.ACTIVE) \
                and self.checker_actor_id is None:
            raise ContractValidationError(
                "an approved record requires a distinct checker actor",
                location="approval.checker_actor_id", rule_id="SEC-SELF-APPROVAL")


# --------------------------------------------------------------------- #
# Requests / replay / rate limiting                                      #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class SecurityRequest:
    """Trusted security context for a privileged request (SECTION 18).

    The actor/session/environment come from the SERVER-side security
    state, never from the client body (actor spoofing is rejected by
    construction: there is no field here a client can set)."""
    request_id: str
    session_id: str
    actor_id: str
    environment: str
    timestamp: datetime
    operation: str
    resource: str
    schema_version: str = CONTRACT_VERSION
    idempotency_key: str | None = None
    causation_id: str | None = None

    def validate(self) -> None:
        validate_identifier("request_id", self.request_id,
                            location="secreq.request_id")
        validate_identifier("session_id", self.session_id,
                            location="secreq.session_id")
        validate_identifier("user_id", self.actor_id,
                            location="secreq.actor_id")
        parse_environment(self.environment, location="secreq.environment")
        ensure_utc(self.timestamp, location="secreq.timestamp")
        for name in ("operation", "resource"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ContractValidationError(
                    f"secreq.{name} must be a non-empty string",
                    location=f"secreq.{name}")

    def fingerprint(self) -> str:
        """Semantic fingerprint for replay detection: same id + same
        semantics = idempotent; same id + different semantics = corruption."""
        return canonical_hash({
            "request_id": self.request_id,
            "session_id": self.session_id,
            "actor_id": self.actor_id,
            "environment": self.environment,
            "operation": self.operation,
            "resource": self.resource,
            "idempotency_key": self.idempotency_key,
        })


class ReplayVerdict(Enum):
    FIRST_SEEN = "FIRST_SEEN"
    IDEMPOTENT_REPEAT = "IDEMPOTENT_REPEAT"
    REPLAY_DETECTED = "REPLAY_DETECTED"
    CORRUPTION_SUSPECTED = "CORRUPTION_SUSPECTED"


class RateLimitVerdict(Enum):
    ALLOWED = "ALLOWED"
    EXCEEDED = "EXCEEDED"


@dataclass(frozen=True)
class RateLimitDecision:
    limit_key: str
    verdict: RateLimitVerdict
    window_start: datetime
    window_seconds: int
    max_operations: int
    operations_used: int
    decided_at: datetime
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.limit_key, str) or not self.limit_key:
            raise ContractValidationError(
                "ratelimit.limit_key must be a non-empty string",
                location="ratelimit.limit_key")
        if not isinstance(self.verdict, RateLimitVerdict):
            raise ContractValidationError(
                "ratelimit.verdict must be a RateLimitVerdict",
                location="ratelimit.verdict", rule_id="SCHEMA-ENUM")
        ensure_utc(self.window_start, location="ratelimit.window_start")
        if not isinstance(self.window_seconds, int) or self.window_seconds < 1:
            raise ContractValidationError(
                "ratelimit.window_seconds must be a positive integer",
                location="ratelimit.window_seconds")
        if not isinstance(self.max_operations, int) or self.max_operations < 1:
            raise ContractValidationError(
                "ratelimit.max_operations must be a positive integer",
                location="ratelimit.max_operations")
        if not isinstance(self.operations_used, int) or self.operations_used < 0:
            raise ContractValidationError(
                "ratelimit.operations_used must be a non-negative integer",
                location="ratelimit.operations_used")
        ensure_utc(self.decided_at, location="ratelimit.decided_at")


# --------------------------------------------------------------------- #
# Incidents + backup                                                     #
# --------------------------------------------------------------------- #
class IncidentSeverity(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SecurityIncidentStatus(Enum):
    DETECTED = "DETECTED"
    OPEN = "OPEN"
    CONTAINED = "CONTAINED"
    INVESTIGATING = "INVESTIGATING"
    RECOVERING = "RECOVERING"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class SecurityIncident:
    security_incident_id: str
    severity: IncidentSeverity
    title: str
    affected_environment: str
    affected_resources: tuple[str, ...]
    detected_at: datetime
    detected_by: str
    status: SecurityIncidentStatus
    evidence: Mapping[str, Any]
    timeline: tuple[Mapping[str, Any], ...] = ()
    closed_at: datetime | None = None
    closure_approver: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("security_incident_id",
                            self.security_incident_id,
                            location="incident.security_incident_id")
        if not isinstance(self.severity, IncidentSeverity):
            raise ContractValidationError(
                "incident.severity must be an IncidentSeverity",
                location="incident.severity", rule_id="SCHEMA-ENUM")
        if not isinstance(self.title, str) or not self.title:
            raise ContractValidationError(
                "incident.title must be a non-empty string",
                location="incident.title")
        parse_environment(self.affected_environment,
                          location="incident.affected_environment")
        if not self.affected_resources:
            raise ContractValidationError(
                "incident.affected_resources must be non-empty",
                location="incident.affected_resources")
        ensure_utc(self.detected_at, location="incident.detected_at")
        if not isinstance(self.detected_by, str) or not self.detected_by:
            raise ContractValidationError(
                "incident.detected_by must be recorded",
                location="incident.detected_by")
        if not isinstance(self.status, SecurityIncidentStatus):
            raise ContractValidationError(
                "incident.status must be a SecurityIncidentStatus",
                location="incident.status", rule_id="SCHEMA-ENUM")
        if not self.evidence:
            raise ContractValidationError(
                "incidents preserve evidence (empty evidence rejected)",
                location="incident.evidence", rule_id="SEC-INCIDENT")
        if self.status is SecurityIncidentStatus.CLOSED:
            if self.closed_at is None or self.closure_approver is None:
                raise ContractValidationError(
                    "a CLOSED incident requires closure time + human approver",
                    location="incident.closed_at", rule_id="SEC-INCIDENT")


class IntegrityStatus(Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    CORRUPTED = "CORRUPTED"


@dataclass(frozen=True)
class BackupManifest:
    backup_id: str
    created_at: datetime
    environment: str
    source_version: str
    component_hashes: Mapping[str, str]
    content_hash: str
    manifest_hash: str
    integrity_status: IntegrityStatus
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("backup_id", self.backup_id,
                            location="backup.backup_id")
        ensure_utc(self.created_at, location="backup.created_at")
        parse_environment(self.environment, location="backup.environment")
        if not isinstance(self.source_version, str) or not self.source_version:
            raise ContractValidationError(
                "backup.source_version must be recorded",
                location="backup.source_version")
        if not self.component_hashes:
            raise ContractValidationError(
                "backup.component_hashes must cover every persisted store",
                location="backup.component_hashes", rule_id="SEC-BACKUP")
        for name, digest in self.component_hashes.items():
            _require_sha256(digest, f"backup.component_hashes.{name}")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "backup.content_hash mismatch",
                location="backup.content_hash", rule_id="SEC-BACKUP")
        expected_manifest = canonical_hash(
            {"backup_id": self.backup_id,
             "content_hash": self.content_hash,
             "integrity_status": self.integrity_status.value})
        if self.manifest_hash != expected_manifest:
            raise ContractValidationError(
                "backup.manifest_hash mismatch",
                location="backup.manifest_hash", rule_id="SEC-BACKUP")
        if not isinstance(self.integrity_status, IntegrityStatus):
            raise ContractValidationError(
                "backup.integrity_status must be an IntegrityStatus",
                location="backup.integrity_status", rule_id="SCHEMA-ENUM")

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "created_at": ensure_utc(self.created_at).isoformat(),
            "environment": self.environment,
            "source_version": self.source_version,
            "components": dict(self.component_hashes),
        })
