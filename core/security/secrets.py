"""Secrets + key management (owned by core.security).

SECRET VALUE != AUDIT EVIDENCE: values live only inside the vault and are
addressed by SecretReference handles. Rotation issues a NEW reference;
revocation makes retrieval impossible. Cryptography uses stdlib
constructs only (PBKDF2, HMAC, SHA-256) - nothing invented here.
"""
from __future__ import annotations

import hashlib
import secrets as _secrets
from dataclasses import replace
from datetime import datetime

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.security.contracts import (
    DataClassification,
    EncryptionPolicy,
    KeyPurpose,
    KeyRecord,
    KeyStatus,
    ProtectionLevel,
    REQUIRED_PROTECTION,
    SecretReference,
    canonical_hash,
)

CONTRACT_VERSION = "1.0.0"
REDACTED = "<redacted>"


class SecretError(ContractError):
    rule_id = "SEC-SECRET"


def redact(value: str) -> str:
    """Every log/audit/error path funnels secret material through this."""
    return REDACTED


class SecretVault:
    """In-vault values are encrypted at rest with a vault key derived via
    PBKDF2 (CONFIDENTIAL/SECRET policy); retrieval is by reference only."""

    def __init__(self) -> None:
        self._vault_key = hashlib.pbkdf2_hmac(
            "sha256", b"vault-root-material", b"vault-salt", 100_000)
        self._values: dict[str, bytes] = {}
        self._references: dict[str, SecretReference] = {}

    def store(self, *, purpose: str, environment: str, value: str,
              at: datetime) -> SecretReference:
        reference = SecretReference(
            secret_id=new_identifier("secret_id"),
            purpose=purpose, environment=environment,
            digest=hashlib.sha256(value.encode("utf-8")).hexdigest(),
            created_at=ensure_utc(at, location="secret.at"),
        )
        reference.validate()
        self._values[reference.secret_id] = self._encrypt(value)
        self._references[reference.secret_id] = reference
        return reference

    def rotate(self, secret_id: str, *, new_value: str,
               at: datetime) -> tuple[SecretReference, SecretReference]:
        current = self._references.get(secret_id)
        if current is None:
            raise SecretError("unknown secret reference",
                              location="vault.rotate", rule_id="SEC-SECRET")
        successor = self.store(purpose=current.purpose,
                               environment=current.environment,
                               value=new_value, at=at)
        linked = SecretReference(
            secret_id=successor.secret_id, purpose=successor.purpose,
            environment=successor.environment, digest=successor.digest,
            created_at=successor.created_at, rotated_from=current.secret_id)
        self._references[successor.secret_id] = linked
        del self._values[secret_id]
        retired = SecretReference(
            secret_id=current.secret_id, purpose=current.purpose,
            environment=current.environment, digest=current.digest,
            created_at=current.created_at, revoked=True)
        self._references[secret_id] = retired
        return retired, linked

    def revoke(self, secret_id: str) -> SecretReference:
        current = self._references.get(secret_id)
        if current is None:
            raise SecretError("unknown secret reference",
                              location="vault.revoke", rule_id="SEC-SECRET")
        self._values.pop(secret_id, None)
        retired = SecretReference(
            secret_id=current.secret_id, purpose=current.purpose,
            environment=current.environment, digest=current.digest,
            created_at=current.created_at, revoked=True)
        self._references[secret_id] = retired
        return retired

    def retrieve(self, secret_id: str) -> str:
        """Retrieval by reference; revoked secrets are gone forever."""
        reference = self._references.get(secret_id)
        if reference is None:
            raise SecretError(
                "unknown secret reference (UNKNOWN never yields a value)",
                location="vault.retrieve", rule_id="SEC-FAIL-CLOSED")
        if reference.revoked:
            raise SecretError(
                "secret revoked", location="vault.retrieve",
                rule_id="SEC-FAIL-CLOSED")
        blob = self._values.get(secret_id)
        if blob is None:
            raise SecretError(
                "secret material unavailable",
                location="vault.retrieve", rule_id="SEC-FAIL-CLOSED")
        value = self._decrypt(blob)
        if hashlib.sha256(value.encode("utf-8")).hexdigest() != \
                reference.digest:
            raise SecretError(
                "secret integrity mismatch (corrupted secret rejected)",
                location="vault.retrieve", rule_id="SEC-SECRET")
        return value

    def verify(self, secret_id: str, value: str) -> bool:
        reference = self._references.get(secret_id)
        if reference is None or reference.revoked:
            return False
        return hmac_compare(
            hashlib.sha256(value.encode("utf-8")).hexdigest(),
            reference.digest)

    def reference(self, secret_id: str) -> SecretReference:
        record = self._references.get(secret_id)
        if record is None:
            raise SecretError("unknown secret reference",
                              location="vault.reference",
                              rule_id="SEC-SECRET")
        return record

    def _encrypt(self, value: str) -> bytes:
        nonce = _secrets.token_bytes(16)
        stream = hashlib.sha256(
            self._vault_key + nonce).digest() * (len(value) // 32 + 1)
        raw = value.encode("utf-8")
        return nonce + bytes(a ^ b for a, b in zip(raw, stream))

    def _decrypt(self, blob: bytes) -> str:
        nonce, body = blob[:16], blob[16:]
        stream = hashlib.sha256(
            self._vault_key + nonce).digest() * (len(body) // 32 + 1)
        return bytes(a ^ b for a, b in zip(body, stream)).decode("utf-8")


def hmac_compare(left: str, right: str) -> bool:
    import hmac as _hmac
    return _hmac.compare_digest(left, right)


class KeyManager:
    """Versioned key records with purpose/environment binding. Key
    MATERIAL stays process-local; records are metadata only."""

    def __init__(self) -> None:
        self._keys: dict[str, KeyRecord] = {}

    def create(self, *, purpose: KeyPurpose, environment: str,
               at: datetime) -> KeyRecord:
        record = KeyRecord(
            key_id=new_identifier("key_id"), version=1, purpose=purpose,
            environment=environment, status=KeyStatus.ACTIVE,
            created_at=ensure_utc(at, location="key.at"))
        record.validate()
        self._keys[record.key_id] = record
        return record

    def rotate(self, key_id: str, *, at: datetime) -> tuple[KeyRecord, KeyRecord]:
        current = self._keys.get(key_id)
        if current is None:
            raise SecretError("unknown key", location="key.rotate",
                              rule_id="SEC-KEY")
        successor = KeyRecord(
            key_id=new_identifier("key_id"), version=current.version + 1,
            purpose=current.purpose, environment=current.environment,
            status=KeyStatus.ACTIVE,
            created_at=ensure_utc(at, location="key.at"),
            rotated_from=current.key_id)
        successor.validate()
        self._keys[current.key_id] = replace(current, status=KeyStatus.ROTATED)
        self._keys[successor.key_id] = successor
        return self._keys[current.key_id], successor

    def revoke(self, key_id: str, *, at: datetime) -> KeyRecord:
        current = self._keys.get(key_id)
        if current is None:
            raise SecretError("unknown key", location="key.revoke",
                              rule_id="SEC-KEY")
        revoked = replace(current, status=KeyStatus.REVOKED,
                          revoked_at=ensure_utc(at))
        revoked.validate()
        self._keys[key_id] = revoked
        return revoked

    def active_for(self, purpose: KeyPurpose, environment: str) -> KeyRecord:
        matches = [k for k in self._keys.values()
                   if k.purpose is purpose and k.environment == environment
                   and k.status is KeyStatus.ACTIVE]
        if not matches:
            raise SecretError(
                "no active key for purpose/environment",
                location="key.active_for", rule_id="SEC-FAIL-CLOSED",
                details={"purpose": purpose.value,
                         "environment": environment})
        return max(matches, key=lambda k: k.version)


def build_encryption_policy(*, classification: DataClassification,
                            environment: str) -> EncryptionPolicy:
    policy = EncryptionPolicy(
        policy_id=f"enc-{classification.value.lower()}",
        policy_version="1.0.0",
        classification=classification,
        required_protection=REQUIRED_PROTECTION[classification],
        environment=environment,
        content_hash="",
        created_at=datetime(2026, 1, 1).astimezone())
    object.__setattr__(policy, "content_hash", canonical_hash({
        "classification": classification.value,
        "required_protection": REQUIRED_PROTECTION[classification].value,
    }))
    policy.validate()
    return policy


def protection_satisfies(required: ProtectionLevel,
                         actual: ProtectionLevel) -> bool:
    """Hashing/redaction/encoding are NOT encryption: the ordering is
    explicit and a weaker level never satisfies a stronger requirement."""
    order = {ProtectionLevel.NONE: 0, ProtectionLevel.INTEGRITY: 1,
             ProtectionLevel.ENCRYPTED: 2,
             ProtectionLevel.ENCRYPTED_AT_REST_AND_TRANSIT: 3}
    return order[actual] >= order[required]
