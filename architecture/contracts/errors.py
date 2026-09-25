"""Contract error taxonomy.

All contract violations raise a ContractError carrying structured fields
(rule_id, location, message, details). The system fails closed: an unclear
validation state is an error, never a silent pass (RULE 019).
"""
from __future__ import annotations

from typing import Any


class ContractError(Exception):
    """Base class for all contract failures."""

    rule_id: str = "CONTRACT-000"
    severity: str = "FAIL"

    def __init__(
        self,
        message: str,
        *,
        location: str = "",
        rule_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.location = location
        self.details: dict[str, Any] = details or {}
        if rule_id is not None:
            self.rule_id = rule_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "location": self.location,
            "message": self.message,
            "details": self.details,
        }

    def __str__(self) -> str:  # deterministic representation
        return f"[{self.rule_id}] {self.location}: {self.message} details={self.details}"


class ContractValidationError(ContractError):
    """A contract instance violates its schema/constraints."""

    rule_id = "SCHEMA-000"


class IdentifierValidationError(ContractValidationError):
    """An identifier violates the global identifier standard."""

    rule_id = "IDENT-001"


class TimeValidationError(ContractValidationError):
    """A timestamp violates the time contract (naive/invalid/misordered)."""

    rule_id = "TIME-001"


class EnvironmentMismatchError(ContractError):
    """Two environments that must match do not match (fail closed)."""

    rule_id = "ENV-001"


class EnvironmentTransitionError(ContractError):
    """An environment transition is not explicitly validated."""

    rule_id = "ENV-002"


class StateTransitionError(ContractError):
    """A state transition is unknown or invalid."""

    def __init__(self, message: str, *, location: str = "", rule_id: str | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message, location=location, details=details)
        if rule_id is None:
            self.rule_id = "SM-001"


class UnknownStateError(StateTransitionError):
    """A referenced state is not part of the machine's state set."""

    rule_id = "SM-002"


class RequirementNotMetError(StateTransitionError):
    """A transition guard (e.g. VALID_RISK_DECISION) was not satisfied."""

    rule_id = "SM-004"


class VersioningError(ContractValidationError):
    """A contract version or version history is invalid."""

    rule_id = "VER-001"


class CompatibilityError(ContractError):
    """A contract change breaks compatibility rules."""

    rule_id = "SCHEMA-002"


class ProvenanceError(ContractValidationError):
    """A decision-critical object is missing provenance."""

    rule_id = "PROV-001"


class CausalityError(ContractValidationError):
    """A causal chain is broken (missing correlation/unknown causation/cycle)."""

    rule_id = "TRACE-002"


class ImmutabilityError(ContractError):
    """An immutable record was targeted for mutation."""

    rule_id = "IMM-001"


class RiskGateError(ContractError):
    """An order/execution path violated the risk gate (RULE 009/010/011)."""

    rule_id = "RISK-GATE"


class PermissionError_(ContractError):
    """A permission/role violation."""

    rule_id = "PERM-001"


class IngestionError(ContractError):
    """A data ingestion request was rejected (fail closed, structured reason)."""

    rule_id = "DATA-REJECT"


class StorageError(ContractError):
    """A storage (event/raw/normalized/lineage store) operation failed."""

    rule_id = "STORE-001"


class BusError(ContractError):
    """An event bus operation failed (validation or dispatch failure)."""

    rule_id = "BUS-001"
