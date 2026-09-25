# Execution Recovery (Phase 5)

- **WHAT**: Recovery model: idempotency (semantic order keys + execution report keys), outbox (at-least-once), reconciliation (poll), replay (read-only re-ingest).
- **WHY**: No cross-system atomicity is claimed; convergence comes from deterministic identities (SECTION 31/32).
- **BOUNDARY**: No duplicate order / fill / ledger effect; no lost confirmed execution.
- **SOURCE OF TRUTH**: core/oms/engine.py + core/ems/outbox.py + SQLite stores (storage 2.4.0).
- **INPUT**: Stored orders/reports/outbox after restart.
- **OUTPUT**: Deterministic resume; idempotent re-processing.
- **FAILURE BEHAVIOR**: Duplicate appends rejected by unique keys; partial writes leave earlier truth intact.
- **RECOVERY**: recover() scans non-terminal orders; test_crash_* scenarios pass.
- **AUDIT**: Recovery operations audited.
- **TEST**: TestCrashRecovery in test_phase5_invariants_e2e.py
