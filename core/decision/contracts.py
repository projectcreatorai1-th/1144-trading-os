"""Decision contract (owned by core.decision).

A Decision is the pre-risk trading intention produced by strategy/intelligence.
It NEVER authorizes execution by itself: policy evaluation and a risk decision
must follow (chain: AI -> Strategy -> Policy -> Risk -> Order).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError, IdentifierValidationError, ProvenanceError
from architecture.contracts.identifiers import validate_any_identifier, validate_identifier
from architecture.contracts.provenance import Provenance
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    TransitionRecord,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_not_before, ensure_utc
from architecture.contracts.versioning import SemVer

CONTRACT_VERSION = "1.0.0"
DECISION_STATUS_MACHINE = "decision_status"


class DecisionType(Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    MODIFY = "MODIFY"
    HOLD = "HOLD"
    CLOSE = "CLOSE"
    REJECT = "REJECT"


class DecisionStatus(Enum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class Decision:
    decision_id: str
    decision_type: DecisionType
    status: DecisionStatus
    environment: str
    scope: str
    strategy_id: str
    source: str
    confidence: float
    reasons: tuple[str, ...]
    output: Mapping[str, Any]
    provenance: Provenance
    correlation_id: str
    decision_time: datetime
    causation_id: str | None = None
    expires_at: datetime | None = None
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("decision_id", self.decision_id, location="decision.decision_id")
        if not isinstance(self.decision_type, DecisionType):
            raise ContractValidationError(
                f"decision.decision_type must be a DecisionType, got {self.decision_type!r}",
                location="decision.decision_type",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.status, DecisionStatus):
            raise ContractValidationError(
                f"decision.status must be a DecisionStatus, got {self.status!r}",
                location="decision.status",
                rule_id="SCHEMA-ENUM",
            )
        parse_environment(self.environment, location="decision.environment")
        for name in ("scope", "source"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ContractValidationError(
                    f"decision.{name} must be a non-empty string",
                    location=f"decision.{name}",
                )
        validate_identifier("strategy_id", self.strategy_id, location="decision.strategy_id")
        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise ContractValidationError(
                "decision.confidence must be within [0.0, 1.0]; confidence is never a risk permission",
                location="decision.confidence",
            )
        if not self.reasons or not all(isinstance(r, str) and r for r in self.reasons):
            raise ContractValidationError(
                "decision.reasons must be a non-empty sequence of strings (traceability)",
                location="decision.reasons",
                rule_id="TRACE-001",
            )
        if not isinstance(self.output, Mapping):
            raise ContractValidationError(
                "decision.output must be a mapping",
                location="decision.output",
            )
        if not isinstance(self.provenance, Provenance):
            raise ProvenanceError(
                "decision.provenance is required (missing provenance fails closed)",
                location="decision.provenance",
            )
        self.provenance.validate()
        validate_any_identifier(self.correlation_id, location="decision.correlation_id")
        decision_time = ensure_utc(self.decision_time, location="decision.decision_time")
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="decision.causation_id")
        if self.expires_at is not None:
            ensure_not_before(
                self.expires_at, not_before=decision_time, location="decision.expires_at"
            )
        SemVer.parse(self.contract_version, location="decision.contract_version")

    def transition_status(
        self,
        target: DecisionStatus,
        *,
        reason: str,
        actor: str,
        machines: StateMachineRegistry | None = None,
    ) -> tuple["Decision", TransitionRecord]:
        """Apply a decision-status transition validated by the registry."""
        registry = machines or build_state_machine_registry()
        record = registry.apply(
            DECISION_STATUS_MACHINE,
            self.status.value,
            target.value,
            reason=reason,
            actor=actor,
            correlation_id=self.correlation_id,
        )
        updated = replace(self, status=target)
        updated.validate()
        return updated, record
