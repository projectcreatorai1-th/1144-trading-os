"""Event contract (owned by core.events).

Events are immutable and link into causal chains via correlation_id and
causation_id (RULE 018). Phase 0 defines the contract; producers/consumers
arrive in Phase 1+.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import (
    is_valid_identifier,
    validate_any_identifier,
    validate_identifier,
)
from architecture.contracts.time import ensure_not_before, ensure_utc
from architecture.contracts.versioning import SemVer

CONTRACT_VERSION = "1.4.0"


class EventType(Enum):
    MARKET_DATA_RECEIVED = "MARKET_DATA_RECEIVED"
    NEWS_RECEIVED = "NEWS_RECEIVED"
    ECONOMIC_EVENT = "ECONOMIC_EVENT"
    MARKET_STATE_CHANGED = "MARKET_STATE_CHANGED"
    AI_ANALYSIS_COMPLETED = "AI_ANALYSIS_COMPLETED"
    STRATEGY_SIGNAL_CREATED = "STRATEGY_SIGNAL_CREATED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    RISK_EVALUATED = "RISK_EVALUATED"
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_UPDATED = "POSITION_UPDATED"
    POSITION_CLOSED = "POSITION_CLOSED"
    RECONCILIATION_COMPLETED = "RECONCILIATION_COMPLETED"
    LEDGER_POSTED = "LEDGER_POSTED"
    SYSTEM_STATE_CHANGED = "SYSTEM_STATE_CHANGED"
    RISK_STATE_CHANGED = "RISK_STATE_CHANGED"
    DATA_QUALITY_CHANGED = "DATA_QUALITY_CHANGED"
    DATA_SOURCE_CHANGED = "DATA_SOURCE_CHANGED"
    SYSTEM_TIME_ANOMALY = "SYSTEM_TIME_ANOMALY"
    EVENT_REJECTED = "EVENT_REJECTED"
    EVENT_ACCEPTED = "EVENT_ACCEPTED"
    REPLAY_STARTED = "REPLAY_STARTED"
    REPLAY_COMPLETED = "REPLAY_COMPLETED"
    # Phase 7 intelligence events (advisory plane only - never execution)
    FEATURE_CREATED = "FEATURE_CREATED"
    INTELLIGENCE_DATASET_CREATED = "INTELLIGENCE_DATASET_CREATED"
    TRAINING_STARTED = "TRAINING_STARTED"
    TRAINING_COMPLETED = "TRAINING_COMPLETED"
    TRAINING_FAILED = "TRAINING_FAILED"
    MODEL_REGISTERED = "MODEL_REGISTERED"
    MODEL_VALIDATED = "MODEL_VALIDATED"
    MODEL_SUSPENDED = "MODEL_SUSPENDED"
    MODEL_RETIRED = "MODEL_RETIRED"
    INFERENCE_COMPLETED = "INFERENCE_COMPLETED"
    AI_PROPOSAL_CREATED = "AI_PROPOSAL_CREATED"
    DRIFT_DETECTED = "DRIFT_DETECTED"
    MODEL_REPLAYED = "MODEL_REPLAYED"
    # Phase 8 security/governance events (guardrail plane)
    AUTHENTICATION_SUCCEEDED = "AUTHENTICATION_SUCCEEDED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    SESSION_CREATED = "SESSION_CREATED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    SESSION_REVOKED = "SESSION_REVOKED"
    CREDENTIAL_ISSUED = "CREDENTIAL_ISSUED"
    CREDENTIAL_ROTATED = "CREDENTIAL_ROTATED"
    CREDENTIAL_REVOKED = "CREDENTIAL_REVOKED"
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    PERMISSION_REVOKED = "PERMISSION_REVOKED"
    AUTHORIZATION_ALLOWED = "AUTHORIZATION_ALLOWED"
    AUTHORIZATION_BLOCKED = "AUTHORIZATION_BLOCKED"
    APPROVAL_CREATED = "APPROVAL_CREATED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    SECURITY_POLICY_CHANGED = "SECURITY_POLICY_CHANGED"
    CONFIG_CHANGE_REQUESTED = "CONFIG_CHANGE_REQUESTED"
    CONFIG_CHANGE_APPROVED = "CONFIG_CHANGE_APPROVED"
    REPLAY_DETECTED = "REPLAY_DETECTED"
    RATE_LIMIT_TRIGGERED = "RATE_LIMIT_TRIGGERED"
    PRIVILEGE_ESCALATION_BLOCKED = "PRIVILEGE_ESCALATION_BLOCKED"
    AUDIT_INTEGRITY_FAILURE = "AUDIT_INTEGRITY_FAILURE"
    INCIDENT_OPENED = "INCIDENT_OPENED"
    INCIDENT_CLOSED = "INCIDENT_CLOSED"
    BACKUP_CREATED = "BACKUP_CREATED"
    BACKUP_VERIFIED = "BACKUP_VERIFIED"
    RESTORE_VERIFIED = "RESTORE_VERIFIED"
    # Phase 10 connectivity/data-plane events
    CONNECTION_STATE_CHANGED = "CONNECTION_STATE_CHANGED"
    FEED_STALE = "FEED_STALE"
    FEED_RESYNC = "FEED_RESYNC"
    RECONCILIATION_MISMATCH_DETECTED = "RECONCILIATION_MISMATCH_DETECTED"


@dataclass(frozen=True)
class Event:
    """Immutable domain event envelope."""

    event_id: str
    event_type: EventType
    event_version: str
    event_time: datetime
    received_time: datetime
    source: str
    source_id: str
    environment: str
    correlation_id: str
    payload: Mapping[str, Any]
    causation_id: str | None = None
    entity_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("event_id", self.event_id, location="event.event_id")
        if not isinstance(self.event_type, EventType):
            raise ContractValidationError(
                f"event.event_type must be an EventType, got {self.event_type!r}",
                location="event.event_type",
                rule_id="SCHEMA-ENUM",
            )
        SemVer.parse(self.event_version, location="event.event_version")
        event_time = ensure_utc(self.event_time, location="event.event_time")
        received_time = ensure_utc(self.received_time, location="event.received_time")
        ensure_not_before(
            received_time, not_before=event_time, location="event.received_time"
        )
        for name in ("source", "source_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"event.{name} must be a non-empty string",
                    location=f"event.{name}",
                )
        parse_environment(self.environment, location="event.environment")
        validate_any_identifier(self.correlation_id, location="event.correlation_id")
        if self.causation_id is not None:
            if not is_valid_identifier("event_id", self.causation_id):
                raise ContractValidationError(
                    "event.causation_id must be None or a valid event_id (evt_ prefix)",
                    location="event.causation_id",
                    rule_id="TRACE-002",
                    details={"causation_id": self.causation_id},
                )
        if self.entity_id is not None and (
            not isinstance(self.entity_id, str) or not self.entity_id
        ):
            raise ContractValidationError(
                "event.entity_id must be None or a non-empty string",
                location="event.entity_id",
            )
        if not isinstance(self.payload, Mapping):
            raise ContractValidationError(
                "event.payload must be a mapping",
                location="event.payload",
            )
        if not isinstance(self.metadata, Mapping):
            raise ContractValidationError(
                "event.metadata must be a mapping",
                location="event.metadata",
            )
        SemVer.parse(self.contract_version, location="event.contract_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "event_version": self.event_version,
            "event_time": ensure_utc(self.event_time).isoformat(),
            "received_time": ensure_utc(self.received_time).isoformat(),
            "source": self.source,
            "source_id": self.source_id,
            "environment": self.environment,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "entity_id": self.entity_id,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
        }


def build_event(
    *,
    event_type: EventType,
    source: str,
    source_id: str,
    environment: str,
    correlation_id: str,
    event_time: datetime,
    received_time: datetime,
    payload: Mapping[str, Any],
    event_id: str,
    causation_id: str | None = None,
    entity_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    event_version: str = CONTRACT_VERSION,
) -> Event:
    """Construct and validate an Event in one step (fail closed)."""
    event = Event(
        event_id=event_id,
        event_type=event_type,
        event_version=event_version,
        event_time=event_time,
        received_time=received_time,
        source=source,
        source_id=source_id,
        environment=environment,
        correlation_id=correlation_id,
        payload=payload,
        causation_id=causation_id,
        entity_id=entity_id,
        metadata=metadata or {},
    )
    event.validate()
    return event
