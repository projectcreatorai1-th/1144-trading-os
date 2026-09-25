# Event Store (Phase 1)

- **WHAT**: Append-only event storage with a technology-neutral port.
- **WHY**: Events are historical truth: never mutated, never deleted, replay-safe (SECTIONS 20/21).
- **BOUNDARY**: Port in core.events; implementations in infrastructure. No deletions exist in Phase 1 (retention/cleanup is an explicit future maintenance operation - silent deletion is impossible).

## Port (core/events/store.py)

`EventStore` (extends Phase 0 `EventRepository`): `append` (validates the contract, returns the store sequence), `get_by_id`, `iter_by_correlation_id`, `query_by_time`, `query_by_type`, `query_by_source`, `query_by_causation`, `find_by_dedup_key`, `get_sequence`, `count`, `iter_all`.

## Implementation (platform/database/sqlite_stores.py)

SQLite via the standard library - chosen for: deterministic behaviour, local-development friendliness, transactions, append-safety, testability, migration-friendliness (schema version pinned in `storage_meta`; incompatible versions refuse to open). One `StorageSet` groups raw/normalized/lineage/event stores over one database file (or `:memory:`).

- Duplicate `event_id` appends raise a structured `StorageError` (append-only).
- Invalid events are rejected at write time (contract validation).
- Restart-safe: reopening the same file preserves history; re-ingesting the same input is detected via persisted dedup keys (idempotent recovery, tested).
- Concurrency: single-threaded by design; no thread/process safety is claimed (SECTION 44).

## Retention boundary (SECTION 42)

Separate stores for RAW, NORMALIZED, EVENT (and audit from Phase 0). Phase 1 defines the boundary but performs NO deletion - no retention policy is versioned yet, so automatic deletion stays impossible.

- **TEST**: `tests/test_phase1_stores.py`, recovery tests in `tests/test_phase1_pipeline.py`.
