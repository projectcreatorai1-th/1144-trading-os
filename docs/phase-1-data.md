# Phase 1 — Data Foundation

- **WHAT**: RAW -> VALIDATE -> NORMALIZE -> QUALITY -> VERSION -> LINEAGE -> CONSUMABLE.
- **WHY**: The system must answer where data came from, when it happened, when it was received, how it was transformed, and how good it is (SECTION 1).
- **BOUNDARY**: Data only - no trading logic, no news interpretation, no sentiment (SECTION 2/46/47).

## Pipeline (core/data/pipeline.py)

`IngestionPipeline.ingest(IngestionRequest) -> IngestionOutcome` executes:

1. **Source resolution** - unknown/disabled/retired sources are rejected before anything is stored (DQ-012).
2. **Raw storage** - the immutable truth: `RawDataRecord` (raw_id, ingestion_id, source, timestamps, payload, sha-256 payload_hash, schema id/version, environment, correlation_id). Stored BEFORE judgement; wrong data stays raw and flagged (validator rule DATA-001 keeps store ports append-only).
3. **Schema validation** - registry-driven (`market_tick`, `news_item`, `calendar_event` payload schemas); nothing is hard-coded in ingestion code (schema version must match the registered current version; mismatch -> DQ-013 rejection).
4. **Time validation** - see docs/phase-1-time.md.
5. **Quality evaluation** - 15 structured rules, see docs/data-quality.md. INVALID inputs are never normalized; the raw record stays and an `EVENT_REJECTED` records the reasons (fail closed, no silent drops).
6. **Deduplication** - deterministic key over (source, source_id, source_event_ref | payload hash, canonical event time); persisted via the event store so restarts cannot create duplicate canonical history. Same content at a different time/reference is a legitimate separate event.
7. **Normalization** - `NormalizedDataRecord` preserves raw_id, source, all timestamps, schema versions, provenance, lineage id, data_version, quality level + reasons (validator rule DATA-002 enforces provenance/raw reference). Symbols are upper-cased (structural only); nothing is dropped, rounded or guessed. Corrections are new records (supersedes), never overwrites.
8. **Event creation** - Phase 0 Event contract (v1.1.0); payload validated against the data schema; derived events (`EVENT_ACCEPTED`, `DATA_QUALITY_CHANGED`, `SYSTEM_TIME_ANOMALY`, `EVENT_REJECTED`) keep correlation/causation.

Outcomes are structured: `ACCEPTED | REJECTED | DUPLICATE` with raw/normalized/event ids, quality evaluation, latency report and rejection reasons (rule id + message + details).

- **INPUT**: `IngestionRequest` (source, schema, payload, timestamps, environment, refs, sequence).
- **OUTPUT**: `IngestionOutcome` (+ events/lineage in stores).
- **DEPENDENCY**: kernel + core.time + core.events + core.validation (registry allow-lists).
- **FAILURE**: deterministic, structured, fail closed.
- **VERSION**: data contracts 1.0.0 (schemas raw_data/normalized_data/data_source/lineage_record).
- **TEST**: `tests/test_phase1_pipeline.py`, `test_phase1_quality.py`, `test_phase1_source_registry.py`.

## Data source registry (core/data/source_registry.py)

Registration + validation of sources (type, provider, timezone, timestamp semantics, reliability metadata incl. stale_threshold_seconds/future_tolerance_seconds, sensitive fields). The registry IS the data-source configuration boundary - separated from system/risk/strategy config. In-memory in Phase 1 (documented limitation; persistence with platform.database later). Secret-like keys are rejected at registration (SEC-001).
