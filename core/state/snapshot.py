"""State snapshot service (owned by core.state).

Immutable, hash-verified checkpoints (SECTION 10/47). Corrupted snapshots
are rejected; rebuild falls back to events."""
from __future__ import annotations

from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc
from core.state.contracts import (
    CONTRACT_VERSION,
    StateCategory,
    StateRecord,
    StateSnapshot,
    compute_snapshot_hash,
)
from core.state.stores import SnapshotStore, StateStore
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION_SNAPSHOT = CONTRACT_VERSION


class SnapshotService:
    def __init__(self, snapshots: SnapshotStore, audit: AuditRepository) -> None:
        self._snapshots = snapshots
        self._audit = audit

    def create(
        self,
        *,
        state: StateRecord,
        event_sequence: int,
        last_event_id: str,
        created_at,
    ) -> StateSnapshot:
        payload = {"status": state.status, "payload": dict(state.payload)}
        snapshot = StateSnapshot(
            snapshot_id=new_identifier("snapshot_id"),
            entity_type=state.entity_type,
            entity_id=state.entity_id,
            state_version=state.state_version,
            event_sequence=event_sequence,
            event_id=last_event_id,
            created_at=ensure_utc(created_at, location="snapshot.created_at"),
            state_payload=payload,
            schema_version=CONTRACT_VERSION,
            environment=state.environment,
            hash=compute_snapshot_hash(
                entity_type=state.entity_type.value,
                entity_id=state.entity_id,
                state_version=state.state_version,
                event_sequence=event_sequence,
                state_payload=payload,
            ),
        )
        self._snapshots.append(snapshot)  # validates the hash (corruption rejected)
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.SYSTEM,
            actor_id="core.state.snapshot",
            action="SNAPSHOT_CREATED",
            entity_type="state_snapshot",
            entity_id=snapshot.snapshot_id,
            event_time=snapshot.created_at,
            before=None,
            after={"entity": f"{state.entity_type.value}:{state.entity_id}",
                   "state_version": state.state_version, "event_sequence": event_sequence},
            reason="checkpoint",
            source="core.state.snapshot",
            environment=snapshot.environment,
            correlation_id=state.correlation_id,
        ))
        return snapshot
