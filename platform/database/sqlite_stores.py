"""SQLite storage implementations (owned by platform.database).

Phase 1 storage choice (SECTION 29): SQLite via the standard library -
deterministic, local-development friendly, transactional, append-safe,
testable, migration-friendly (schema versioned in code). No deletions exist
in Phase 1: retention/cleanup is an explicit future maintenance operation.

Core talks only to the ports in core.data / core.events - never to this
module directly (SECTION 28).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterator

from architecture.contracts.errors import StorageError
from architecture.contracts.time import canonical, parse_canonical
from core.data.contracts import LineageRecord, LineageStage, NormalizedDataRecord, RawDataRecord
from core.data.stores import LineageStore, NormalizedDataStore, RawDataStore
from core.events.contracts import Event, EventType
from core.events.store import EventStore

STORAGE_VERSION = "2.4.0"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_data (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id TEXT NOT NULL UNIQUE,
    ingestion_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT,
    received_time TEXT NOT NULL,
    source_timestamp TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    environment TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    metadata TEXT,
    duplicate_of TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_source_hash ON raw_data (source, payload_hash);

CREATE TABLE IF NOT EXISTS normalized_data (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_id TEXT NOT NULL UNIQUE,
    raw_id TEXT NOT NULL,
    source TEXT NOT NULL,
    event_time TEXT NOT NULL,
    received_time TEXT NOT NULL,
    processed_time TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    payload TEXT NOT NULL,
    provenance TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    data_version TEXT NOT NULL,
    quality_level TEXT NOT NULL,
    quality_reasons TEXT NOT NULL,
    supersedes TEXT
);
CREATE INDEX IF NOT EXISTS idx_normalized_raw ON normalized_data (raw_id);

CREATE TABLE IF NOT EXISTS lineage (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    lineage_id TEXT NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    parent_id TEXT,
    correlation_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_lineage_entity ON lineage (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_lineage_correlation ON lineage (correlation_id);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    event_version TEXT NOT NULL,
    event_time TEXT NOT NULL,
    received_time TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    entity_id TEXT,
    payload TEXT NOT NULL,
    metadata TEXT NOT NULL,
    dedup_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events (event_time);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_source ON events (source);
CREATE INDEX IF NOT EXISTS idx_events_correlation ON events (correlation_id);
CREATE INDEX IF NOT EXISTS idx_events_causation ON events (causation_id);
CREATE INDEX IF NOT EXISTS idx_events_dedup ON events (dedup_key);

CREATE TABLE IF NOT EXISTS storage_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS states (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    state_id TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    effective_time TEXT NOT NULL,
    observed_time TEXT NOT NULL,
    processed_time TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    causation_id TEXT,
    UNIQUE (entity_type, entity_id, state_version)
);
CREATE INDEX IF NOT EXISTS idx_states_entity ON states (entity_type, entity_id, state_version);

CREATE TABLE IF NOT EXISTS state_transitions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    transition_id TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT NOT NULL,
    previous_version INTEGER NOT NULL,
    new_version INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    environment TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    UNIQUE (entity_type, entity_id, event_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    event_sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    state_payload TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    environment TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_entry_id TEXT NOT NULL UNIQUE,
    entry_type TEXT NOT NULL,
    account_id TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    environment TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    source_event_id TEXT,
    order_id TEXT,
    execution_id TEXT,
    position_id TEXT,
    strategy_id TEXT,
    causation_id TEXT,
    adjusts_entry_id TEXT,
    reverses_entry_id TEXT,
    quantity TEXT,
    symbol TEXT,
    status TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    previous_entry_hash TEXT,
    idempotency_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_ledger_account ON ledger_entries (account_id, currency, seq);

CREATE TABLE IF NOT EXISTS observations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    environment TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    provenance TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_entity ON observations (entity_type, entity_id);

CREATE TABLE IF NOT EXISTS reconciliations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    reconciliation_id TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    environment TEXT NOT NULL,
    source TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    internal_version TEXT NOT NULL,
    external_version TEXT NOT NULL,
    tolerance TEXT NOT NULL,
    difference_summary TEXT NOT NULL,
    matched_items TEXT NOT NULL,
    mismatched_items TEXT NOT NULL,
    missing_internal TEXT NOT NULL,
    missing_external TEXT NOT NULL,
    unknown_items TEXT NOT NULL,
    causation_id TEXT
);

CREATE TABLE IF NOT EXISTS audit_records (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL UNIQUE,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_time TEXT NOT NULL,
    before JSON,
    after JSON,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    environment TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    policy_version TEXT,
    risk_version TEXT,
    model_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit_records (correlation_id);

CREATE TABLE IF NOT EXISTS policies (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_type TEXT NOT NULL,
    status TEXT NOT NULL,
    scope TEXT NOT NULL,
    priority INTEGER NOT NULL,
    environment TEXT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (policy_id, policy_version)
);
CREATE INDEX IF NOT EXISTS idx_policies_type ON policies (policy_type, status);

CREATE TABLE IF NOT EXISTS policy_evaluations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id TEXT NOT NULL UNIQUE,
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    result TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    environment TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pev_correlation ON policy_evaluations (correlation_id);

CREATE TABLE IF NOT EXISTS risk_decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    risk_decision_id TEXT NOT NULL UNIQUE,
    environment TEXT NOT NULL,
    permission TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    decision_content TEXT NOT NULL,
    context_content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risk_decisions_correlation ON risk_decisions (correlation_id);

CREATE TABLE IF NOT EXISTS strategies (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    environment TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (strategy_id, strategy_version)
);
CREATE INDEX IF NOT EXISTS idx_strategies_env ON strategies (environment, lifecycle_status);

CREATE TABLE IF NOT EXISTS capability_profiles (
    capability_profile_id TEXT PRIMARY KEY,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_configs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    environment TEXT NOT NULL,
    content TEXT NOT NULL,
    UNIQUE (strategy_id, strategy_version)
);

CREATE TABLE IF NOT EXISTS strategy_intents (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL UNIQUE,
    strategy_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intents_correlation ON strategy_intents (correlation_id);

CREATE TABLE IF NOT EXISTS portfolios (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id TEXT NOT NULL,
    portfolio_version TEXT NOT NULL,
    status TEXT NOT NULL,
    environment TEXT NOT NULL,
    content TEXT NOT NULL,
    UNIQUE (portfolio_id, portfolio_version)
);

CREATE TABLE IF NOT EXISTS portfolio_memberships (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    priority INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    effective_from TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memberships ON portfolio_memberships (portfolio_id);

CREATE TABLE IF NOT EXISTS capital_allocations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    capital_allocation_id TEXT NOT NULL UNIQUE,
    portfolio_id TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_decision_id TEXT NOT NULL UNIQUE,
    portfolio_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    order_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    environment TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    content TEXT NOT NULL,
    UNIQUE (order_id, order_version)
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status, seq);

CREATE TABLE IF NOT EXISTS execution_reports (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exec_reports_order ON execution_reports (order_id, seq);

CREATE TABLE IF NOT EXISTS outbox_messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    environment TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_messages (delivery_status, seq);
"""


def open_database(path: str | Path) -> sqlite3.Connection:
    """Open (or create) a Phase 1 storage database with the versioned schema."""
    try:
        connection = sqlite3.connect(str(path), isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_SCHEMA)
        connection.execute(
            "INSERT OR IGNORE INTO storage_meta (key, value) VALUES ('storage_version', ?)",
            (STORAGE_VERSION,),
        )
        stored = connection.execute(
            "SELECT value FROM storage_meta WHERE key = 'storage_version'"
        ).fetchone()
        if stored is None or stored[0] != STORAGE_VERSION:
            connection.close()
            raise StorageError(
                f"Incompatible storage schema version: {stored[0] if stored else None} "
                f"(expected {STORAGE_VERSION})",
                location="sqlite.open",
            )
        return connection
    except sqlite3.Error as exc:
        raise StorageError(f"Storage open failure: {exc}", location="sqlite.open") from exc


def _json_dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class SqliteRawDataStore(RawDataStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, record: RawDataRecord) -> None:
        record.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO raw_data (raw_id, ingestion_id, source, source_id, source_version,"
                    " received_time, source_timestamp, payload, payload_hash, schema_id,"
                    " schema_version, environment, correlation_id, metadata, duplicate_of)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.raw_id, record.ingestion_id, record.source, record.source_id,
                        record.source_version, canonical(record.received_time),
                        canonical(record.source_timestamp), _json_dump(dict(record.payload)),
                        record.payload_hash, record.schema_id, record.schema_version,
                        record.environment, record.correlation_id,
                        _json_dump(dict(record.metadata)) if record.metadata is not None else None,
                        record.duplicate_of,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Raw record {record.raw_id} already stored (append-only, no overwrite)",
                location="sqlite.raw.append",
                details={"raw_id": record.raw_id},
            ) from exc
        except sqlite3.Error as exc:
            raise StorageError(f"Raw store write failure: {exc}", location="sqlite.raw.append") from exc

    def get_by_id(self, raw_id: str) -> RawDataRecord:
        row = self._db.execute(
            "SELECT raw_id, ingestion_id, source, source_id, source_version, received_time,"
            " source_timestamp, payload, payload_hash, schema_id, schema_version, environment,"
            " correlation_id, metadata, duplicate_of FROM raw_data WHERE raw_id = ?",
            (raw_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Raw record '{raw_id}' not found", location="sqlite.raw.get_by_id"
            )
        return self._row_to_record(row)

    def find_by_hash(self, source: str, payload_hash: str) -> RawDataRecord | None:
        row = self._db.execute(
            "SELECT raw_id, ingestion_id, source, source_id, source_version, received_time,"
            " source_timestamp, payload, payload_hash, schema_id, schema_version, environment,"
            " correlation_id, metadata, duplicate_of FROM raw_data"
            " WHERE source = ? AND payload_hash = ? ORDER BY seq LIMIT 1",
            (source, payload_hash),
        ).fetchone()
        return None if row is None else self._row_to_record(row)

    def iter_all(self) -> Iterator[RawDataRecord]:
        cursor = self._db.execute(
            "SELECT raw_id, ingestion_id, source, source_id, source_version, received_time,"
            " source_timestamp, payload, payload_hash, schema_id, schema_version, environment,"
            " correlation_id, metadata, duplicate_of FROM raw_data ORDER BY seq"
        )
        for row in cursor:
            yield self._row_to_record(row)

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM raw_data").fetchone()[0])

    @staticmethod
    def _row_to_record(row: tuple) -> RawDataRecord:
        return RawDataRecord.from_storage({
            "raw_id": row[0],
            "ingestion_id": row[1],
            "source": row[2],
            "source_id": row[3],
            "source_version": row[4],
            "received_time": row[5],
            "source_timestamp": row[6],
            "payload": json.loads(row[7]),
            "payload_hash": row[8],
            "schema_id": row[9],
            "schema_version": row[10],
            "environment": row[11],
            "correlation_id": row[12],
            "metadata": json.loads(row[13]) if row[13] is not None else None,
            "duplicate_of": row[14],
        })


class SqliteNormalizedDataStore(NormalizedDataStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, record: NormalizedDataRecord) -> None:
        record.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO normalized_data (normalized_id, raw_id, source, event_time,"
                    " received_time, processed_time, schema_id, schema_version, payload,"
                    " provenance, lineage_id, data_version, quality_level, quality_reasons, supersedes)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.normalized_id, record.raw_id, record.source,
                        canonical(record.event_time), canonical(record.received_time),
                        canonical(record.processed_time), record.schema_id,
                        record.schema_version, _json_dump(dict(record.payload)),
                        _json_dump({
                            "source": record.provenance.source,
                            "source_id": record.provenance.source_id,
                            "source_version": record.provenance.source_version,
                            "event_time": canonical(record.provenance.event_time),
                            "ingestion_time": canonical(record.provenance.ingestion_time),
                            "processing_time": canonical(record.provenance.processing_time)
                            if record.provenance.processing_time else None,
                            "model_version": record.provenance.model_version,
                            "policy_version": record.provenance.policy_version,
                            "data_version": record.provenance.data_version,
                        }),
                        record.lineage_id, record.data_version, record.quality_level.value,
                        _json_dump(list(record.quality_reasons)), record.supersedes,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Normalized record {record.normalized_id} already stored",
                location="sqlite.normalized.append",
            ) from exc
        except sqlite3.Error as exc:
            raise StorageError(
                f"Normalized store write failure: {exc}", location="sqlite.normalized.append"
            ) from exc

    def get_by_id(self, normalized_id: str) -> NormalizedDataRecord:
        row = self._db.execute(
            "SELECT normalized_id, raw_id, source, event_time, received_time, processed_time,"
            " schema_id, schema_version, payload, provenance, lineage_id, data_version,"
            " quality_level, quality_reasons, supersedes FROM normalized_data WHERE normalized_id = ?",
            (normalized_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Normalized record '{normalized_id}' not found",
                location="sqlite.normalized.get_by_id",
            )
        return self._row_to_record(row)

    def iter_by_raw_id(self, raw_id: str) -> Iterator[NormalizedDataRecord]:
        cursor = self._db.execute(
            "SELECT normalized_id, raw_id, source, event_time, received_time, processed_time,"
            " schema_id, schema_version, payload, provenance, lineage_id, data_version,"
            " quality_level, quality_reasons, supersedes FROM normalized_data"
            " WHERE raw_id = ? ORDER BY seq",
            (raw_id,),
        )
        for row in cursor:
            yield self._row_to_record(row)

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM normalized_data").fetchone()[0])

    @staticmethod
    def _row_to_record(row: tuple) -> NormalizedDataRecord:
        return NormalizedDataRecord.from_storage({
            "normalized_id": row[0],
            "raw_id": row[1],
            "source": row[2],
            "event_time": row[3],
            "received_time": row[4],
            "processed_time": row[5],
            "schema_id": row[6],
            "schema_version": row[7],
            "payload": json.loads(row[8]),
            "provenance": json.loads(row[9]),
            "lineage_id": row[10],
            "data_version": row[11],
            "quality_level": row[12],
            "quality_reasons": json.loads(row[13]),
            "supersedes": row[14],
        })


class SqliteLineageStore(LineageStore):
    _COLUMNS = ("lineage_id, stage, entity_type, entity_id, parent_id, correlation_id,"
                " recorded_at, metadata")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, record: LineageRecord) -> None:
        record.validate()
        try:
            with self._db:
                self._db.execute(
                    f"INSERT INTO lineage ({self._COLUMNS}) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        record.lineage_id, record.stage.value, record.entity_type,
                        record.entity_id, record.parent_id, record.correlation_id,
                        canonical(record.recorded_at),
                        _json_dump(dict(record.metadata)) if record.metadata is not None else None,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(
                f"Lineage store write failure: {exc}", location="sqlite.lineage.append"
            ) from exc

    def iter_by_entity(self, entity_type: str, entity_id: str) -> Iterator[LineageRecord]:
        cursor = self._db.execute(
            f"SELECT {self._COLUMNS} FROM lineage WHERE entity_type = ? AND entity_id = ?"
            " ORDER BY seq",
            (entity_type, entity_id),
        )
        for row in cursor:
            yield self._row_to_record(row)

    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[LineageRecord]:
        cursor = self._db.execute(
            f"SELECT {self._COLUMNS} FROM lineage WHERE correlation_id = ? ORDER BY seq",
            (correlation_id,),
        )
        for row in cursor:
            yield self._row_to_record(row)

    def chain_for(self, entity_type: str, entity_id: str) -> list[LineageRecord]:
        """Walk parents from the given entity back to its SOURCE root."""
        chain: list[LineageRecord] = []
        current_id = entity_id
        seen: set[str] = set()
        while current_id is not None:
            row = self._db.execute(
                f"SELECT {self._COLUMNS} FROM lineage WHERE entity_id = ?"
                " ORDER BY seq DESC LIMIT 1",
                (current_id,),
            ).fetchone()
            if row is None:
                break
            record = self._row_to_record(row)
            key = f"{record.entity_type}:{record.entity_id}"
            if key in seen:
                raise StorageError(
                    "Lineage cycle detected", location="sqlite.lineage.chain_for",
                    details={"entity_id": entity_id},
                )
            seen.add(key)
            chain.append(record)
            if record.stage is LineageStage.SOURCE:
                break
            current_id = record.parent_id
        chain.reverse()
        return chain

    @staticmethod
    def _row_to_record(row: tuple) -> LineageRecord:
        return LineageRecord(
            lineage_id=row[0], stage=LineageStage(row[1]), entity_type=row[2],
            entity_id=row[3], parent_id=row[4], correlation_id=row[5],
            recorded_at=parse_canonical(row[6]),
            metadata=json.loads(row[7]) if row[7] is not None else None,
        )


class SqliteEventStore(EventStore):
    _COLUMNS = ("event_id, event_type, event_version, event_time, received_time, source,"
                " source_id, environment, correlation_id, causation_id, entity_id, payload,"
                " metadata, dedup_key")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, event: Event) -> int:
        event.validate()
        dedup_key = event.metadata.get("dedup_key") if event.metadata else None
        try:
            with self._db:
                cursor = self._db.execute(
                    "INSERT INTO events (event_id, event_type, event_version, event_time,"
                    " received_time, source, source_id, environment, correlation_id,"
                    " causation_id, entity_id, payload, metadata, dedup_key)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event.event_id, event.event_type.value, event.event_version,
                        canonical(event.event_time), canonical(event.received_time),
                        event.source, event.source_id, event.environment,
                        event.correlation_id, event.causation_id, event.entity_id,
                        _json_dump(dict(event.payload)), _json_dump(dict(event.metadata)),
                        dedup_key,
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Event {event.event_id} already stored (history is append-only)",
                location="sqlite.events.append",
                details={"event_id": event.event_id},
            ) from exc
        except sqlite3.Error as exc:
            raise StorageError(
                f"Event store write failure: {exc}", location="sqlite.events.append",
                details={"event_id": event.event_id},
            ) from exc

    def get_by_id(self, event_id: str) -> Event:
        row = self._db.execute(
            f"SELECT {self._COLUMNS} FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Event '{event_id}' not found", location="sqlite.events.get_by_id"
            )
        return self._row_to_event(row)

    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[Event]:
        for row in self._db.execute(
            f"SELECT {self._COLUMNS} FROM events WHERE correlation_id = ? ORDER BY seq",
            (correlation_id,),
        ):
            yield self._row_to_event(row)

    def query_by_time(self, start: datetime, end: datetime) -> Iterator[Event]:
        for row in self._db.execute(
            f"SELECT {self._COLUMNS} FROM events WHERE event_time >= ? AND event_time <= ?"
            " ORDER BY seq",
            (canonical(start), canonical(end)),
        ):
            yield self._row_to_event(row)

    def query_by_type(self, event_type: str) -> Iterator[Event]:
        for row in self._db.execute(
            f"SELECT {self._COLUMNS} FROM events WHERE event_type = ? ORDER BY seq",
            (event_type,),
        ):
            yield self._row_to_event(row)

    def query_by_source(self, source: str) -> Iterator[Event]:
        for row in self._db.execute(
            f"SELECT {self._COLUMNS} FROM events WHERE source = ? ORDER BY seq",
            (source,),
        ):
            yield self._row_to_event(row)

    def query_by_causation(self, causation_id: str) -> Iterator[Event]:
        for row in self._db.execute(
            f"SELECT {self._COLUMNS} FROM events WHERE causation_id = ? ORDER BY seq",
            (causation_id,),
        ):
            yield self._row_to_event(row)

    def find_by_dedup_key(self, dedup_key: str) -> Event | None:
        row = self._db.execute(
            f"SELECT {self._COLUMNS} FROM events WHERE dedup_key = ? ORDER BY seq LIMIT 1",
            (dedup_key,),
        ).fetchone()
        return None if row is None else self._row_to_event(row)

    def get_sequence(self, event_id: str) -> int:
        row = self._db.execute(
            "SELECT seq FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Event '{event_id}' not found", location="sqlite.events.get_sequence"
            )
        return int(row[0])

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def iter_all(self) -> Iterator[Event]:
        for row in self._db.execute(
            f"SELECT {self._COLUMNS} FROM events ORDER BY seq"
        ):
            yield self._row_to_event(row)

    @staticmethod
    def _row_to_event(row: tuple) -> Event:
        return Event(
            event_id=row[0],
            event_type=EventType(row[1]),
            event_version=row[2],
            event_time=parse_canonical(row[3]),
            received_time=parse_canonical(row[4]),
            source=row[5],
            source_id=row[6],
            environment=row[7],
            correlation_id=row[8],
            causation_id=row[9],
            entity_id=row[10],
            payload=json.loads(row[11]),
            metadata=json.loads(row[12]),
        )


class StorageSet:
    """One database file, all Phase 1 + Phase 2 stores (transactional, append-safe)."""

    def __init__(self, path: str | Path) -> None:
        self.path = path
        self.connection = open_database(path)
        from platform.database.sqlite_phase2 import (
            SqliteAuditStore,
            SqliteLedgerStore,
            SqliteObservationStore,
            SqliteReconciliationStore,
            SqliteSnapshotStore,
            SqliteStateStore,
        )
        from platform.database.sqlite_phase3 import (
            SqlitePolicyEvaluationStore,
            SqlitePolicyStore,
            SqliteRiskDecisionStore,
        )
        from platform.database.sqlite_phase4 import (
            SqliteAllocationStore,
            SqliteIntentStore,
            SqliteMembershipStore,
            SqlitePortfolioDecisionStore,
            SqlitePortfolioStore,
            SqliteStrategyStore,
        )
        from platform.database.sqlite_phase5 import (
            SqliteExecutionReportStore,
            SqliteOrderStore,
            SqliteOutboxStore,
        )

        self.raw = SqliteRawDataStore(self.connection)
        self.normalized = SqliteNormalizedDataStore(self.connection)
        self.lineage = SqliteLineageStore(self.connection)
        self.events = SqliteEventStore(self.connection)
        self.states = SqliteStateStore(self.connection)
        self.snapshots = SqliteSnapshotStore(self.connection)
        self.ledger = SqliteLedgerStore(self.connection)
        self.observations = SqliteObservationStore(self.connection)
        self.reconciliations = SqliteReconciliationStore(self.connection)
        self.audit = SqliteAuditStore(self.connection)
        self.policies = SqlitePolicyStore(self.connection)
        self.policy_evaluations = SqlitePolicyEvaluationStore(self.connection)
        self.risk_decisions = SqliteRiskDecisionStore(self.connection)
        self.strategies = SqliteStrategyStore(self.connection)
        self.intents = SqliteIntentStore(self.connection)
        self.portfolios = SqlitePortfolioStore(self.connection)
        self.memberships = SqliteMembershipStore(self.connection)
        self.allocations = SqliteAllocationStore(self.connection)
        self.portfolio_decisions = SqlitePortfolioDecisionStore(self.connection)
        self.orders = SqliteOrderStore(self.connection)
        self.execution_reports = SqliteExecutionReportStore(self.connection)
        self.outbox = SqliteOutboxStore(self.connection)

    def close(self) -> None:
        self.connection.close()
