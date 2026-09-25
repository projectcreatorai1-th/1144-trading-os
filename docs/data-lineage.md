# Data Lineage (Phase 1)

- **WHAT**: SOURCE -> RAW -> NORMALIZED -> EVENT links, queryable and unbroken.
- **WHY**: Every consumable record must trace back to its origin (SECTION 10); later phases extend the chain (ANALYZED -> DECISION -> ORDER -> EXECUTION -> RESULT are declared stages reserved by the contract).
- **BOUNDARY**: Phase 1 creates links for the four implemented stages only.

## Records (schema lineage_record)

`LineageRecord`: lineage_id, stage, entity_type, entity_id, parent_id (required for every non-SOURCE stage), correlation_id, recorded_at, metadata.

## Storage & queries

`LineageStore` port (core.data) + `SqliteLineageStore` (platform.database). `chain_for(entity)` walks parents back to the SOURCE root with cycle detection; `iter_by_correlation_id` reconstructs everything one ingestion touched.

## Guarantees (tested)

- raw -> normalized -> event ids are connected through event metadata AND lineage
- every accepted ingestion writes the full four-stage chain
- correlation_id and causation_id stay intact across the chain (Phase 0 causal validator)

## Data versioning

Raw records are immutable. Normalized records carry `data_version` (semver, starts 1.0.0); semantic changes require a NEW normalized_id (+ data_version bump, `supersedes` reference) - silent overwrites are impossible by contract and by store design (append-only). Versions never move backward (invariant test).

- **TEST**: `tests/test_phase1_stores.py` (lineage), `tests/test_phase1_pipeline.py` (chain), `tests/test_phase1_invariants.py`, `tests/test_phase1_e2e.py`.
