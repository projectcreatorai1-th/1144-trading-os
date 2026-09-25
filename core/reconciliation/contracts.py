"""Reconciliation contracts (owned by core.reconciliation).

External observations are immutable external truth; reconciliation COMPARES
internal state/ledger against them with explicit tolerances and REPORTS -
it never auto-fixes (SECTIONS 22-31, 59).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_any_identifier, validate_identifier
from architecture.contracts.provenance import Provenance
from architecture.contracts.time import ensure_not_before, ensure_utc
from architecture.contracts.versioning import SemVer

CONTRACT_VERSION = "1.0.0"


def compute_observation_hash(payload: Mapping[str, Any]) -> str:
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReconciliationScope(Enum):
    ACCOUNT = "ACCOUNT"
    BALANCE = "BALANCE"
    POSITION = "POSITION"
    ORDER = "ORDER"
    LEDGER = "LEDGER"
    TRANSACTION = "TRANSACTION"
    STATE = "STATE"


class ReconciliationStatus(Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    PARTIAL = "PARTIAL"
    MISSING_INTERNAL = "MISSING_INTERNAL"
    MISSING_EXTERNAL = "MISSING_EXTERNAL"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


class DifferenceSeverity(Enum):
    INFO = "INFO"  # within tolerance
    ERROR = "ERROR"  # beyond tolerance


@dataclass(frozen=True)
class ExternalObservation:
    observation_id: str
    source: str
    entity_type: str
    entity_id: str
    observed_at: datetime
    received_at: datetime
    environment: str
    payload: Mapping[str, Any]
    payload_hash: str
    schema_version: str
    provenance: Provenance
    correlation_id: str
    causation_id: str | None = None

    def validate(self) -> None:
        validate_identifier("observation_id", self.observation_id, location="observation.observation_id")
        for name in ("source", "entity_type", "entity_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"observation.{name} must be a non-empty string",
                    location=f"observation.{name}",
                )
        observed = ensure_utc(self.observed_at, location="observation.observed_at")
        ensure_not_before(self.received_at, not_before=observed, location="observation.received_at")
        parse_environment(self.environment, location="observation.environment")
        if not isinstance(self.payload, Mapping):
            raise ContractValidationError(
                "observation.payload must be a mapping",
                location="observation.payload",
            )
        expected = compute_observation_hash(self.payload)
        if self.payload_hash != expected:
            raise ContractValidationError(
                "Observation payload hash mismatch",
                location="observation.payload_hash",
                rule_id="RECON-004",
                details={"expected": expected, "actual": self.payload_hash},
            )
        SemVer.parse(self.schema_version, location="observation.schema_version")
        if not isinstance(self.provenance, Provenance):
            raise ContractValidationError(
                "observation.provenance is required",
                location="observation.provenance",
                rule_id="PROV-001",
            )
        self.provenance.validate()
        validate_any_identifier(self.correlation_id, location="observation.correlation_id")
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="observation.causation_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source": self.source,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "observed_at": ensure_utc(self.observed_at).isoformat(),
            "received_at": ensure_utc(self.received_at).isoformat(),
            "environment": self.environment,
            "payload": dict(self.payload),
            "payload_hash": self.payload_hash,
            "schema_version": self.schema_version,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
        }


@dataclass(frozen=True)
class Difference:
    field: str
    internal_value: str
    external_value: str
    difference: str
    absolute_difference: str
    relative_difference: str
    tolerance: str
    severity: DifferenceSeverity
    reason: str

    def validate(self) -> None:
        for name in ("field", "internal_value", "external_value", "difference",
                     "absolute_difference", "relative_difference", "tolerance", "reason"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"difference.{name} must be a non-empty string (canonical decimal or 'undefined')",
                    location=f"difference.{name}",
                )
        if not isinstance(self.severity, DifferenceSeverity):
            raise ContractValidationError(
                f"difference.severity must be a DifferenceSeverity, got {self.severity!r}",
                location="difference.severity",
                rule_id="SCHEMA-ENUM",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "internal_value": self.internal_value,
            "external_value": self.external_value,
            "difference": self.difference,
            "absolute_difference": self.absolute_difference,
            "relative_difference": self.relative_difference,
            "tolerance": self.tolerance,
            "severity": self.severity.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReconciliationResult:
    reconciliation_id: str
    scope: ReconciliationScope
    status: ReconciliationStatus
    timestamp: datetime
    environment: str
    source: str
    correlation_id: str
    internal_version: str
    external_version: str
    tolerance: Mapping[str, Any]
    difference_summary: Mapping[str, Any]
    matched_items: tuple[str, ...]
    mismatched_items: tuple[Difference, ...]
    missing_internal: tuple[str, ...]
    missing_external: tuple[str, ...]
    unknown_items: tuple[str, ...]
    causation_id: str | None = None

    def validate(self) -> None:
        validate_identifier(
            "reconciliation_id", self.reconciliation_id, location="reconciliation.reconciliation_id"
        )
        if not isinstance(self.scope, ReconciliationScope):
            raise ContractValidationError(
                f"reconciliation.scope must be a ReconciliationScope, got {self.scope!r}",
                location="reconciliation.scope",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.status, ReconciliationStatus):
            raise ContractValidationError(
                f"reconciliation.status must be a ReconciliationStatus, got {self.status!r}",
                location="reconciliation.status",
                rule_id="SCHEMA-ENUM",
            )
        ensure_utc(self.timestamp, location="reconciliation.timestamp")
        parse_environment(self.environment, location="reconciliation.environment")
        for name in ("source", "internal_version", "external_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"reconciliation.{name} must be a non-empty string",
                    location=f"reconciliation.{name}",
                )
        validate_any_identifier(self.correlation_id, location="reconciliation.correlation_id")
        for name in ("tolerance", "difference_summary"):
            if not isinstance(getattr(self, name), Mapping):
                raise ContractValidationError(
                    f"reconciliation.{name} must be a mapping",
                    location=f"reconciliation.{name}",
                )
        for difference in self.mismatched_items:
            difference.validate()
        if self.status is ReconciliationStatus.MATCH and (self.mismatched_items or self.missing_internal or self.missing_external or self.unknown_items):
            raise ContractValidationError(
                "MATCH requires zero differences, zero missing items and zero unknowns "
                "(UNKNOWN never becomes MATCH without evidence, RECON-002)",
                location="reconciliation.status",
                rule_id="RECON-002",
            )
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="reconciliation.causation_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reconciliation_id": self.reconciliation_id,
            "scope": self.scope.value,
            "status": self.status.value,
            "timestamp": ensure_utc(self.timestamp).isoformat(),
            "environment": self.environment,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "internal_version": self.internal_version,
            "external_version": self.external_version,
            "tolerance": dict(self.tolerance),
            "difference_summary": dict(self.difference_summary),
            "matched_items": list(self.matched_items),
            "mismatched_items": [d.to_dict() for d in self.mismatched_items],
            "missing_internal": list(self.missing_internal),
            "missing_external": list(self.missing_external),
            "unknown_items": list(self.unknown_items),
            "causation_id": self.causation_id,
        }
