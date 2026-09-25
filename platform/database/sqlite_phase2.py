"""SQLite storage implementations for Phase 2 (owned by platform.database).

State, snapshots, ledger (hash-chained, idempotent), observations,
reconciliation history and audit records. All append-only; the ledger store
enforces the per-account hash chain and idempotency keys at the storage
boundary (SECTIONS 16-19, 31, 36)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Iterator

from architecture.contracts.errors import StorageError
from architecture.contracts.time import canonical, ensure_utc
from core.ledger.contracts import LedgerEntry
from core.ledger.store import LedgerStore
from core.reconciliation.contracts import (
    ExternalObservation,
    ReconciliationResult,
    ReconciliationScope,
    ReconciliationStatus,
    Difference,
    DifferenceSeverity,
    compute_observation_hash,
)
from core.reconciliation.stores import ObservationStore, ReconciliationStore
from core.state.contracts import (
    StateCategory,
    StateRecord,
    StateSnapshot,
    StateTransitionRecord,
)
from core.state.stores import SnapshotStore, StateStore
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _optional_dump(value: object) -> str | None:
    return _dump(value) if value is not None else None


class SqliteStateStore(StateStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def save_state(self, state: StateRecord, transition: StateTransitionRecord) -> None:
        state.validate()
        transition.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO states (state_id, entity_type, entity_id, state_version, status,"
                    " payload, effective_time, observed_time, processed_time, source_event_id,"
                    " correlation_id, environment, schema_version, causation_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        state.state_id, state.entity_type.value, state.entity_id,
                        state.state_version, state.status, _dump(dict(state.payload)),
                        canonical(state.effective_time), canonical(state.observed_time),
                        canonical(state.processed_time), state.source_event_id,
                        state.correlation_id, state.environment, state.schema_version,
                        state.causation_id,
                    ),
                )
                self._db.execute(
                    "INSERT INTO state_transitions (transition_id, entity_type, entity_id,"
                    " previous_state, new_state, previous_version, new_version, event_id, reason,"
                    " actor, timestamp, environment, correlation_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        transition.transition_id, transition.entity_type.value,
                        transition.entity_id, transition.previous_state, transition.new_state,
                        transition.previous_version, transition.new_version, transition.event_id,
                        transition.reason, transition.actor, canonical(transition.timestamp),
                        transition.environment, transition.correlation_id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                "State save rejected (duplicate version or event already applied - idempotent replay)",
                location="sqlite.state.save",
                details={
                    "entity": f"{state.entity_type.value}:{state.entity_id}",
                    "state_version": state.state_version,
                    "event_id": transition.event_id,
                },
            ) from exc
        except sqlite3.Error as exc:
            raise StorageError(
                f"State store write failure: {exc}", location="sqlite.state.save"
            ) from exc

    def get_current_state(self, entity_type: str, entity_id: str) -> StateRecord | None:
        row = self._db.execute(
            "SELECT state_id, entity_type, entity_id, state_version, status, payload,"
            " effective_time, observed_time, processed_time, source_event_id, correlation_id,"
            " environment, schema_version, causation_id FROM states"
            " WHERE entity_type = ? AND entity_id = ? ORDER BY state_version DESC LIMIT 1",
            (entity_type, entity_id),
        ).fetchone()
        return None if row is None else self._row_to_state(row)

    def get_state_at_time(self, entity_type: str, entity_id: str, at: datetime) -> StateRecord | None:
        row = self._db.execute(
            "SELECT state_id, entity_type, entity_id, state_version, status, payload,"
            " effective_time, observed_time, processed_time, source_event_id, correlation_id,"
            " environment, schema_version, causation_id FROM states"
            " WHERE entity_type = ? AND entity_id = ? AND effective_time <= ?"
            " ORDER BY state_version DESC LIMIT 1",
            (entity_type, entity_id, canonical(ensure_utc(at, location="state.at"))),
        ).fetchone()
        return None if row is None else self._row_to_state(row)

    def get_state_history(self, entity_type: str, entity_id: str) -> Iterator[StateRecord]:
        for row in self._db.execute(
            "SELECT state_id, entity_type, entity_id, state_version, status, payload,"
            " effective_time, observed_time, processed_time, source_event_id, correlation_id,"
            " environment, schema_version, causation_id FROM states"
            " WHERE entity_type = ? AND entity_id = ? ORDER BY state_version",
            (entity_type, entity_id),
        ):
            yield self._row_to_state(row)

    def has_applied_event(self, entity_type: str, entity_id: str, event_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM state_transitions WHERE entity_type = ? AND entity_id = ?"
            " AND event_id = ? LIMIT 1",
            (entity_type, entity_id, event_id),
        ).fetchone()
        return row is not None

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM states").fetchone()[0])

    @staticmethod
    def _row_to_state(row: tuple) -> StateRecord:
        return StateRecord.from_storage({
            "state_id": row[0],
            "entity_type": row[1],
            "entity_id": row[2],
            "state_version": row[3],
            "status": row[4],
            "payload": json.loads(row[5]),
            "effective_time": row[6],
            "observed_time": row[7],
            "processed_time": row[8],
            "source_event_id": row[9],
            "correlation_id": row[10],
            "environment": row[11],
            "schema_version": row[12],
            "causation_id": row[13],
        })


class SqliteSnapshotStore(SnapshotStore):
    _COLUMNS = ("snapshot_id, entity_type, entity_id, state_version, event_sequence, event_id,"
                " created_at, state_payload, schema_version, environment, hash")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, snapshot: StateSnapshot) -> None:
        snapshot.validate()  # verifies the integrity hash (corrupted snapshots rejected)
        try:
            with self._db:
                self._db.execute(
                    f"INSERT INTO snapshots ({self._COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        snapshot.snapshot_id, snapshot.entity_type.value, snapshot.entity_id,
                        snapshot.state_version, snapshot.event_sequence, snapshot.event_id,
                        canonical(snapshot.created_at), _dump(dict(snapshot.state_payload)),
                        snapshot.schema_version, snapshot.environment, snapshot.hash,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(
                f"Snapshot store write failure: {exc}", location="sqlite.snapshot.append"
            ) from exc

    def get_by_id(self, snapshot_id: str) -> StateSnapshot:
        row = self._db.execute(
            f"SELECT {self._COLUMNS} FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Snapshot '{snapshot_id}' not found", location="sqlite.snapshot.get_by_id"
            )
        return self._row_to_snapshot(row)

    def latest_for(self, entity_type: str, entity_id: str) -> StateSnapshot | None:
        row = self._db.execute(
            f"SELECT {self._COLUMNS} FROM snapshots"
            " WHERE entity_type = ? AND entity_id = ? ORDER BY seq DESC LIMIT 1",
            (entity_type, entity_id),
        ).fetchone()
        return None if row is None else self._row_to_snapshot(row)

    @staticmethod
    def _row_to_snapshot(row: tuple) -> StateSnapshot:
        from architecture.contracts.time import parse_canonical

        snapshot = StateSnapshot(
            snapshot_id=row[0],
            entity_type=StateCategory(row[1]),
            entity_id=row[2],
            state_version=row[3],
            event_sequence=row[4],
            event_id=row[5],
            created_at=parse_canonical(row[6]),
            state_payload=json.loads(row[7]),
            schema_version=row[8],
            environment=row[9],
            hash=row[10],
        )
        snapshot.verify_hash()
        return snapshot


class SqliteLedgerStore(LedgerStore):
    _COLUMNS = ("ledger_entry_id, entry_type, account_id, amount, currency, environment,"
                " entry_time, correlation_id, source_event_id, order_id, execution_id,"
                " position_id, strategy_id, causation_id, adjusts_entry_id, reverses_entry_id,"
                " quantity, symbol, status, entry_hash, previous_entry_hash, idempotency_key")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, entry: LedgerEntry) -> None:
        entry.validate()
        if entry.entry_hash is None:
            raise StorageError(
                "Posted ledger entries require entry_hash (integrity, LEDGER-001)",
                location="sqlite.ledger.append",
            )
        if entry.idempotency_key is None:
            raise StorageError(
                "Posted ledger entries require a deterministic idempotency key (LEDGER-003)",
                location="sqlite.ledger.append",
            )
        last_hash = self.last_entry_hash(entry.account_id)
        if last_hash != entry.previous_entry_hash:
            raise StorageError(
                "Ledger hash chain broken: previous_entry_hash does not match the account head",
                location="sqlite.ledger.append",
                rule_id="LEDGER-001",
                details={
                    "account_id": entry.account_id,
                    "expected_previous": last_hash,
                    "provided_previous": entry.previous_entry_hash,
                },
            )
        existing = self.find_by_idempotency_key(entry.idempotency_key)
        if existing is not None:
            raise StorageError(
                "Duplicate ledger post rejected (idempotency key already used, LEDGER-003)",
                location="sqlite.ledger.append",
                rule_id="LEDGER-003",
                details={
                    "idempotency_key": entry.idempotency_key,
                    "existing_entry": existing.ledger_entry_id,
                },
            )
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO ledger_entries (ledger_entry_id, entry_type, account_id, amount,"
                    " currency, environment, entry_time, correlation_id, source_event_id, order_id,"
                    " execution_id, position_id, strategy_id, causation_id, adjusts_entry_id,"
                    " reverses_entry_id, quantity, symbol, status, entry_hash, previous_entry_hash,"
                    " idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        entry.ledger_entry_id, entry.entry_type.value, entry.account_id,
                        str(entry.amount), entry.currency, entry.environment,
                        canonical(entry.entry_time), entry.correlation_id,
                        entry.source_event_id, entry.order_id, entry.execution_id,
                        entry.position_id, entry.strategy_id, entry.causation_id,
                        entry.adjusts_entry_id, entry.reverses_entry_id,
                        str(entry.quantity) if entry.quantity is not None else None,
                        entry.symbol, entry.status, entry.entry_hash,
                        entry.previous_entry_hash, entry.idempotency_key,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(
                f"Ledger store write failure: {exc}", location="sqlite.ledger.append"
            ) from exc

    def get_by_id(self, ledger_entry_id: str) -> LedgerEntry:
        row = self._db.execute(
            f"SELECT {self._COLUMNS} FROM ledger_entries WHERE ledger_entry_id = ?",
            (ledger_entry_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Ledger entry '{ledger_entry_id}' not found", location="sqlite.ledger.get_by_id"
            )
        return self._row_to_entry(row)

    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[LedgerEntry]:
        for row in self._db.execute(
            f"SELECT {self._COLUMNS} FROM ledger_entries WHERE correlation_id = ? ORDER BY seq",
            (correlation_id,),
        ):
            yield self._row_to_entry(row)

    def iter_by_account(self, account_id: str, currency: str | None = None) -> Iterator[LedgerEntry]:
        if currency is None:
            cursor = self._db.execute(
                f"SELECT {self._COLUMNS} FROM ledger_entries WHERE account_id = ? ORDER BY seq",
                (account_id,),
            )
        else:
            cursor = self._db.execute(
                f"SELECT {self._COLUMNS} FROM ledger_entries"
                " WHERE account_id = ? AND currency = ? ORDER BY seq",
                (account_id, currency),
            )
        for row in cursor:
            yield self._row_to_entry(row)

    def find_by_idempotency_key(self, idempotency_key: str) -> LedgerEntry | None:
        row = self._db.execute(
            f"SELECT {self._COLUMNS} FROM ledger_entries WHERE idempotency_key = ? LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        return None if row is None else self._row_to_entry(row)

    def last_entry_hash(self, account_id: str) -> str | None:
        row = self._db.execute(
            "SELECT entry_hash FROM ledger_entries WHERE account_id = ?"
            " ORDER BY seq DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def verify_account_chain(self, account_id: str) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        previous: str | None = None
        for entry in self.iter_by_account(account_id):
            expected_hash = entry.compute_hash()
            if entry.entry_hash != expected_hash:
                issues.append({
                    "ledger_entry_id": entry.ledger_entry_id,
                    "issue": "ENTRY_HASH_MISMATCH",
                    "expected": expected_hash,
                    "actual": entry.entry_hash or "",
                })
            if entry.previous_entry_hash != previous:
                issues.append({
                    "ledger_entry_id": entry.ledger_entry_id,
                    "issue": "CHAIN_LINK_MISMATCH",
                    "expected": previous or "",
                    "actual": entry.previous_entry_hash or "",
                })
            previous = entry.entry_hash
        return issues

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0])

    @staticmethod
    def _row_to_entry(row: tuple) -> LedgerEntry:
        return LedgerEntry.from_storage({
            "ledger_entry_id": row[0],
            "entry_type": row[1],
            "account_id": row[2],
            "amount": row[3],
            "currency": row[4],
            "environment": row[5],
            "entry_time": row[6],
            "correlation_id": row[7],
            "source_event_id": row[8],
            "order_id": row[9],
            "execution_id": row[10],
            "position_id": row[11],
            "strategy_id": row[12],
            "causation_id": row[13],
            "adjusts_entry_id": row[14],
            "reverses_entry_id": row[15],
            "quantity": row[16],
            "symbol": row[17],
            "status": row[18],
            "entry_hash": row[19],
            "previous_entry_hash": row[20],
            "idempotency_key": row[21],
        })


class SqliteObservationStore(ObservationStore):
    _COLUMNS = ("observation_id, source, entity_type, entity_id, observed_at, received_at,"
                " environment, payload, payload_hash, schema_version, provenance,"
                " correlation_id, causation_id")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, observation: ExternalObservation) -> None:
        observation.validate()
        try:
            with self._db:
                self._db.execute(
                    f"INSERT INTO observations ({self._COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        observation.observation_id, observation.source, observation.entity_type,
                        observation.entity_id, canonical(observation.observed_at),
                        canonical(observation.received_at), observation.environment,
                        _dump(dict(observation.payload)), observation.payload_hash,
                        observation.schema_version,
                        _dump({
                            "source": observation.provenance.source,
                            "source_id": observation.provenance.source_id,
                            "source_version": observation.provenance.source_version,
                            "event_time": canonical(observation.provenance.event_time),
                            "ingestion_time": canonical(observation.provenance.ingestion_time),
                            "processing_time": canonical(observation.provenance.processing_time)
                            if observation.provenance.processing_time else None,
                        }),
                        observation.correlation_id, observation.causation_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(
                f"Observation store write failure: {exc}", location="sqlite.observation.append"
            ) from exc

    def get_by_id(self, observation_id: str) -> ExternalObservation:
        row = self._db.execute(
            f"SELECT {self._COLUMNS} FROM observations WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Observation '{observation_id}' not found", location="sqlite.observation.get_by_id"
            )
        return self._row_to_observation(row)

    def iter_by_entity(self, entity_type: str, entity_id: str) -> Iterator[ExternalObservation]:
        for row in self._db.execute(
            f"SELECT {self._COLUMNS} FROM observations"
            " WHERE entity_type = ? AND entity_id = ? ORDER BY seq",
            (entity_type, entity_id),
        ):
            yield self._row_to_observation(row)

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM observations").fetchone()[0])

    @staticmethod
    def _row_to_observation(row: tuple) -> ExternalObservation:
        from architecture.contracts.provenance import Provenance
        from architecture.contracts.time import parse_canonical

        provenance_data = json.loads(row[10])
        processing = provenance_data.get("processing_time")
        observation = ExternalObservation(
            observation_id=row[0], source=row[1], entity_type=row[2], entity_id=row[3],
            observed_at=parse_canonical(row[4]), received_at=parse_canonical(row[5]),
            environment=row[6], payload=json.loads(row[7]), payload_hash=row[8],
            schema_version=row[9],
            provenance=Provenance(
                source=provenance_data["source"],
                source_id=provenance_data["source_id"],
                source_version=provenance_data.get("source_version"),
                event_time=parse_canonical(provenance_data["event_time"]),
                ingestion_time=parse_canonical(provenance_data["ingestion_time"]),
                processing_time=parse_canonical(processing) if processing else None,
            ),
            correlation_id=row[11], causation_id=row[12],
        )
        observation.validate()
        return observation


class SqliteReconciliationStore(ReconciliationStore):
    _COLUMNS = ("reconciliation_id, scope, status, timestamp, environment, source,"
                " correlation_id, internal_version, external_version, tolerance,"
                " difference_summary, matched_items, mismatched_items, missing_internal,"
                " missing_external, unknown_items, causation_id")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, result: ReconciliationResult) -> None:
        result.validate()
        try:
            with self._db:
                self._db.execute(
                    f"INSERT INTO reconciliations ({self._COLUMNS})"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        result.reconciliation_id, result.scope.value, result.status.value,
                        canonical(result.timestamp), result.environment, result.source,
                        result.correlation_id, result.internal_version, result.external_version,
                        _dump(dict(result.tolerance)), _dump(dict(result.difference_summary)),
                        _dump(list(result.matched_items)),
                        _dump([d.to_dict() for d in result.mismatched_items]),
                        _dump(list(result.missing_internal)),
                        _dump(list(result.missing_external)),
                        _dump(list(result.unknown_items)), result.causation_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(
                f"Reconciliation store write failure: {exc}", location="sqlite.reconciliation.append"
            ) from exc

    def get_by_id(self, reconciliation_id: str) -> ReconciliationResult:
        row = self._db.execute(
            f"SELECT {self._COLUMNS} FROM reconciliations WHERE reconciliation_id = ?",
            (reconciliation_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Reconciliation '{reconciliation_id}' not found",
                location="sqlite.reconciliation.get_by_id",
            )
        return self._row_to_result(row)

    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[ReconciliationResult]:
        for row in self._db.execute(
            f"SELECT {self._COLUMNS} FROM reconciliations WHERE correlation_id = ? ORDER BY seq",
            (correlation_id,),
        ):
            yield self._row_to_result(row)

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM reconciliations").fetchone()[0])

    @staticmethod
    def _row_to_result(row: tuple) -> ReconciliationResult:
        from architecture.contracts.time import parse_canonical

        result = ReconciliationResult(
            reconciliation_id=row[0],
            scope=ReconciliationScope(row[1]),
            status=ReconciliationStatus(row[2]),
            timestamp=parse_canonical(row[3]),
            environment=row[4],
            source=row[5],
            correlation_id=row[6],
            internal_version=row[7],
            external_version=row[8],
            tolerance=json.loads(row[9]),
            difference_summary=json.loads(row[10]),
            matched_items=tuple(json.loads(row[11])),
            mismatched_items=tuple(
                Difference(
                    field=d["field"], internal_value=d["internal_value"],
                    external_value=d["external_value"], difference=d["difference"],
                    absolute_difference=d["absolute_difference"],
                    relative_difference=d["relative_difference"], tolerance=d["tolerance"],
                    severity=DifferenceSeverity(d["severity"]), reason=d["reason"],
                )
                for d in json.loads(row[12])
            ),
            missing_internal=tuple(json.loads(row[13])),
            missing_external=tuple(json.loads(row[14])),
            unknown_items=tuple(json.loads(row[15])),
            causation_id=row[16],
        )
        result.validate()
        return result


class SqliteAuditStore(AuditRepository):
    _COLUMNS = ("audit_id, actor_type, actor_id, action, entity_type, entity_id, event_time,"
                " before, after, reason, source, environment, correlation_id, causation_id,"
                " policy_version, risk_version, model_version")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, record: AuditRecord) -> None:
        record.validate()
        try:
            with self._db:
                self._db.execute(
                    f"INSERT INTO audit_records ({self._COLUMNS})"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.audit_id, record.actor_type.value, record.actor_id, record.action,
                        record.entity_type, record.entity_id, canonical(record.event_time),
                        _optional_dump(dict(record.before)) if record.before is not None else None,
                        _optional_dump(dict(record.after)) if record.after is not None else None,
                        record.reason, record.source, record.environment, record.correlation_id,
                        record.causation_id, record.policy_version, record.risk_version,
                        record.model_version,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(
                f"Audit store write failure: {exc}", location="sqlite.audit.append"
            ) from exc

    def get_by_id(self, audit_id: str) -> AuditRecord:
        row = self._db.execute(
            f"SELECT {self._COLUMNS} FROM audit_records WHERE audit_id = ?", (audit_id,)
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Audit record '{audit_id}' not found", location="sqlite.audit.get_by_id"
            )
        return self._row_to_audit(row)

    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[AuditRecord]:
        for row in self._db.execute(
            f"SELECT {self._COLUMNS} FROM audit_records WHERE correlation_id = ? ORDER BY seq",
            (correlation_id,),
        ):
            yield self._row_to_audit(row)

    @staticmethod
    def _row_to_audit(row: tuple) -> AuditRecord:
        from architecture.contracts.time import parse_canonical

        record = AuditRecord(
            audit_id=row[0], actor_type=ActorType(row[1]), actor_id=row[2], action=row[3],
            entity_type=row[4], entity_id=row[5], event_time=parse_canonical(row[6]),
            before=json.loads(row[7]) if row[7] is not None else None,
            after=json.loads(row[8]) if row[8] is not None else None,
            reason=row[9], source=row[10], environment=row[11], correlation_id=row[12],
            causation_id=row[13], policy_version=row[14], risk_version=row[15],
            model_version=row[16],
        )
        record.validate()
        return record
