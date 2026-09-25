"""Audit contract (owned by platform.audit).

An AuditRecord answers: who did what, when, to which entity, from/to which
state, why, under which policy/risk/model versions, in which environment.
Audit records are immutable (append-only).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_any_identifier, validate_identifier
from architecture.contracts.time import ensure_utc

CONTRACT_VERSION = "1.1.0"


class ActorType(Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    STRATEGY = "STRATEGY"
    AI_MODEL = "AI_MODEL"


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    actor_type: ActorType
    actor_id: str
    action: str
    entity_type: str
    entity_id: str
    event_time: datetime
    reason: str
    source: str
    environment: str
    correlation_id: str
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None
    causation_id: str | None = None
    policy_version: str | None = None
    risk_version: str | None = None
    model_version: str | None = None
    integrity_hash: str | None = None
    previous_hash: str | None = None
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("audit_id", self.audit_id, location="audit.audit_id")
        if not isinstance(self.actor_type, ActorType):
            raise ContractValidationError(
                f"audit.actor_type must be an ActorType, got {self.actor_type!r}",
                location="audit.actor_type",
                rule_id="SCHEMA-ENUM",
            )
        for name in ("actor_id", "action", "entity_type", "entity_id", "reason", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"audit.{name} must be a non-empty string",
                    location=f"audit.{name}",
                )
        ensure_utc(self.event_time, location="audit.event_time")
        parse_environment(self.environment, location="audit.environment")
        validate_any_identifier(self.correlation_id, location="audit.correlation_id")
        for name in ("before", "after"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Mapping):
                raise ContractValidationError(
                    f"audit.{name} must be a mapping or None",
                    location=f"audit.{name}",
                )
        if self.before is None and self.after is None:
            raise ContractValidationError(
                "State-changing audit records must include before and/or after "
                "(traceability, RULE 017)",
                location="audit.before_after",
                rule_id="TRACE-001",
            )
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="audit.causation_id")
        for name in ("policy_version", "risk_version", "model_version"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ContractValidationError(
                    f"audit.{name} must be a non-empty string or None",
                    location=f"audit.{name}",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "actor_type": self.actor_type.value,
            "actor_id": self.actor_id,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "event_time": ensure_utc(self.event_time).isoformat(),
            "before": dict(self.before) if self.before is not None else None,
            "after": dict(self.after) if self.after is not None else None,
            "reason": self.reason,
            "source": self.source,
            "environment": self.environment,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "policy_version": self.policy_version,
            "risk_version": self.risk_version,
            "model_version": self.model_version,
        }
