"""State contracts (owned by core.state).

State = what the system BELIEVES the current status is - a derived,
rebuildable representation that always traces to its source event. Event,
state, ledger and external observation are distinct objects (SECTION 3/5).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError, StateTransitionError
from architecture.contracts.identifiers import validate_any_identifier, validate_identifier
from architecture.contracts.time import ensure_not_before, ensure_utc
from architecture.contracts.versioning import SemVer

CONTRACT_VERSION = "1.0.0"


class StateCategory(Enum):
    RISK_STATE = "RISK_STATE"
    SYSTEM_STATE = "SYSTEM_STATE"
    MARKET_STATE = "MARKET_STATE"
    DATA_STATE = "DATA_STATE"
    ACCOUNT_STATE = "ACCOUNT_STATE"
    ORDER_STATE = "ORDER_STATE"
    POSITION_STATE = "POSITION_STATE"
    LEDGER_STATE = "LEDGER_STATE"
    RECONCILIATION_STATE = "RECONCILIATION_STATE"


#: Categories backed by a Phase 0 state machine registry; their status values
#: MUST transition through the machine (no parallel transition logic).
MACHINE_BY_CATEGORY: dict[StateCategory, str] = {
    StateCategory.RISK_STATE: "risk_state",
    StateCategory.SYSTEM_STATE: "system_state",
    StateCategory.MARKET_STATE: "market_state",
    StateCategory.ORDER_STATE: "order_state",
    StateCategory.POSITION_STATE: "position_status",
}


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_snapshot_hash(
    *,
    entity_type: str,
    entity_id: str,
    state_version: int,
    event_sequence: int,
    state_payload: Mapping[str, Any],
) -> str:
    """Deterministic snapshot integrity hash (STATE-003)."""
    material = canonical_json({
        "entity_type": entity_type,
        "entity_id": entity_id,
        "state_version": state_version,
        "event_sequence": event_sequence,
        "state_payload": dict(state_payload),
    })
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StateRecord:
    state_id: str
    entity_type: StateCategory
    entity_id: str
    state_version: int
    status: str
    payload: Mapping[str, Any]
    effective_time: datetime
    observed_time: datetime
    processed_time: datetime
    source_event_id: str
    correlation_id: str
    environment: str
    schema_version: str
    causation_id: str | None = None

    def validate(self) -> None:
        validate_identifier("state_id", self.state_id, location="state.state_id")
        if not isinstance(self.entity_type, StateCategory):
            raise ContractValidationError(
                f"state.entity_type must be a StateCategory, got {self.entity_type!r}",
                location="state.entity_type",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.entity_id, str) or not self.entity_id:
            raise ContractValidationError(
                "state.entity_id must be a non-empty string",
                location="state.entity_id",
            )
        if not isinstance(self.state_version, int) or isinstance(self.state_version, bool) or self.state_version < 1:
            raise ContractValidationError(
                "state.state_version must be a positive integer",
                location="state.state_version",
            )
        if not isinstance(self.status, str) or not self.status:
            raise ContractValidationError(
                "state.status must be a non-empty string",
                location="state.status",
            )
        if not isinstance(self.payload, Mapping):
            raise ContractValidationError(
                "state.payload must be a mapping",
                location="state.payload",
            )
        effective = ensure_utc(self.effective_time, location="state.effective_time")
        observed = ensure_utc(self.observed_time, location="state.observed_time")
        processed = ensure_utc(self.processed_time, location="state.processed_time")
        ensure_not_before(observed, not_before=effective, location="state.observed_time")
        ensure_not_before(processed, not_before=observed, location="state.processed_time")
        validate_identifier("event_id", self.source_event_id, location="state.source_event_id")
        validate_any_identifier(self.correlation_id, location="state.correlation_id")
        parse_environment(self.environment, location="state.environment")
        SemVer.parse(self.schema_version, location="state.schema_version")
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="state.causation_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "entity_type": self.entity_type.value,
            "entity_id": self.entity_id,
            "state_version": self.state_version,
            "status": self.status,
            "payload": dict(self.payload),
            "effective_time": ensure_utc(self.effective_time).isoformat(),
            "observed_time": ensure_utc(self.observed_time).isoformat(),
            "processed_time": ensure_utc(self.processed_time).isoformat(),
            "source_event_id": self.source_event_id,
            "correlation_id": self.correlation_id,
            "environment": self.environment,
            "schema_version": self.schema_version,
            "causation_id": self.causation_id,
        }

    @classmethod
    def from_storage(cls, data: Mapping[str, Any]) -> "StateRecord":
        from architecture.contracts.time import parse_canonical

        record = cls(
            state_id=data["state_id"],
            entity_type=StateCategory(data["entity_type"]),
            entity_id=data["entity_id"],
            state_version=int(data["state_version"]),
            status=data["status"],
            payload=data["payload"],
            effective_time=parse_canonical(data["effective_time"]),
            observed_time=parse_canonical(data["observed_time"]),
            processed_time=parse_canonical(data["processed_time"]),
            source_event_id=data["source_event_id"],
            correlation_id=data["correlation_id"],
            environment=data["environment"],
            schema_version=data["schema_version"],
            causation_id=data.get("causation_id"),
        )
        record.validate()
        return record

    @classmethod
    def from_snapshot_seed(cls, snapshot: "StateSnapshot") -> "StateRecord":
        """Seed state for a rebuild that starts from a verified snapshot."""
        payload = dict(snapshot.state_payload.get("payload", {}))
        record = cls(
            state_id=f"{snapshot.snapshot_id}",
            entity_type=snapshot.entity_type,
            entity_id=snapshot.entity_id,
            state_version=snapshot.state_version,
            status=str(snapshot.state_payload.get("status", "")),
            payload=payload,
            effective_time=snapshot.created_at,
            observed_time=snapshot.created_at,
            processed_time=snapshot.created_at,
            source_event_id=snapshot.event_id,
            correlation_id=f"{snapshot.snapshot_id}",
            environment=snapshot.environment,
            schema_version=snapshot.schema_version,
        )
        if not record.status:
            raise ContractValidationError(
                "Snapshot seed payload must carry a status",
                location="state.from_snapshot_seed",
            )
        return record


@dataclass(frozen=True)
class StateTransitionRecord:
    transition_id: str
    entity_type: StateCategory
    entity_id: str
    new_state: str
    previous_version: int
    new_version: int
    event_id: str
    reason: str
    actor: str
    timestamp: datetime
    environment: str
    correlation_id: str
    previous_state: str | None = None

    def validate(self) -> None:
        validate_identifier("transition_id", self.transition_id, location="transition.transition_id")
        if not isinstance(self.entity_type, StateCategory):
            raise ContractValidationError(
                f"transition.entity_type must be a StateCategory, got {self.entity_type!r}",
                location="transition.entity_type",
                rule_id="SCHEMA-ENUM",
            )
        for name in ("entity_id", "new_state", "reason", "actor"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"transition.{name} must be a non-empty string",
                    location=f"transition.{name}",
                )
        if self.new_version != self.previous_version + 1:
            raise ContractValidationError(
                f"transition.new_version must be previous_version + 1 "
                f"(got {self.previous_version} -> {self.new_version})",
                location="transition.new_version",
            )
        if self.previous_version < 0:
            raise ContractValidationError(
                "transition.previous_version must be >= 0",
                location="transition.previous_version",
            )
        if self.previous_version == 0 and self.previous_state is not None:
            raise ContractValidationError(
                "Genesis transitions (previous_version 0) must have previous_state None",
                location="transition.previous_state",
            )
        if self.previous_version > 0 and not (isinstance(self.previous_state, str) and self.previous_state):
            raise ContractValidationError(
                "Non-genesis transitions must record previous_state",
                location="transition.previous_state",
            )
        validate_identifier("event_id", self.event_id, location="transition.event_id")
        ensure_utc(self.timestamp, location="transition.timestamp")
        parse_environment(self.environment, location="transition.environment")
        validate_any_identifier(self.correlation_id, location="transition.correlation_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "entity_type": self.entity_type.value,
            "entity_id": self.entity_id,
            "previous_state": self.previous_state,
            "new_state": self.new_state,
            "previous_version": self.previous_version,
            "new_version": self.new_version,
            "event_id": self.event_id,
            "reason": self.reason,
            "actor": self.actor,
            "timestamp": ensure_utc(self.timestamp).isoformat(),
            "environment": self.environment,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class StateSnapshot:
    snapshot_id: str
    entity_type: StateCategory
    entity_id: str
    state_version: int
    event_sequence: int
    event_id: str
    created_at: datetime
    state_payload: Mapping[str, Any]
    schema_version: str
    environment: str
    hash: str

    def validate(self) -> None:
        validate_identifier("snapshot_id", self.snapshot_id, location="snapshot.snapshot_id")
        if not isinstance(self.entity_type, StateCategory):
            raise ContractValidationError(
                f"snapshot.entity_type must be a StateCategory, got {self.entity_type!r}",
                location="snapshot.entity_type",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.entity_id, str) or not self.entity_id:
            raise ContractValidationError(
                "snapshot.entity_id must be a non-empty string",
                location="snapshot.entity_id",
            )
        if not isinstance(self.state_version, int) or self.state_version < 1:
            raise ContractValidationError(
                "snapshot.state_version must be a positive integer",
                location="snapshot.state_version",
            )
        if not isinstance(self.event_sequence, int) or self.event_sequence < 0:
            raise ContractValidationError(
                "snapshot.event_sequence must be a non-negative integer",
                location="snapshot.event_sequence",
            )
        validate_identifier("event_id", self.event_id, location="snapshot.event_id")
        ensure_utc(self.created_at, location="snapshot.created_at")
        if not isinstance(self.state_payload, Mapping):
            raise ContractValidationError(
                "snapshot.state_payload must be a mapping",
                location="snapshot.state_payload",
            )
        SemVer.parse(self.schema_version, location="snapshot.schema_version")
        parse_environment(self.environment, location="snapshot.environment")
        self.verify_hash()

    def compute_hash(self) -> str:
        return compute_snapshot_hash(
            entity_type=self.entity_type.value,
            entity_id=self.entity_id,
            state_version=self.state_version,
            event_sequence=self.event_sequence,
            state_payload=self.state_payload,
        )

    def verify_hash(self) -> None:
        expected = self.compute_hash()
        if self.hash != expected:
            raise ContractValidationError(
                "Snapshot hash mismatch (corrupted snapshot rejected)",
                location="snapshot.hash",
                rule_id="STATE-003",
                details={"expected": expected, "actual": self.hash},
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "entity_type": self.entity_type.value,
            "entity_id": self.entity_id,
            "state_version": self.state_version,
            "event_sequence": self.event_sequence,
            "event_id": self.event_id,
            "created_at": ensure_utc(self.created_at).isoformat(),
            "state_payload": dict(self.state_payload),
            "schema_version": self.schema_version,
            "environment": self.environment,
            "hash": self.hash,
        }
