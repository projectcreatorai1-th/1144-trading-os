# Execution Failure Matrix (Phase 5)

- **WHAT**: The failure tests covering SECTION 59's matrix: missing/expired/mismatched risk, environment mismatches, permissions, safety controls, market/capability unknowns, invalid fields, duplicates, overfill, broker errors, timeouts, crashes, reconciliation mismatches.
- **WHY**: Every failure must fail closed or land in explicit UNKNOWN (SECTION 59).
- **BOUNDARY**: No failure silently degrades to ALLOW.
- **SOURCE OF TRUTH**: tests/test_phase5_boundary_oms.py + test_phase5_ems_adapters.py + test_phase5_invariants_e2e.py.
- **INPUT**: Corrupted/duplicate/hostile inputs.
- **OUTPUT**: REJECT or UNKNOWN with structured reasons.
- **FAILURE BEHAVIOR**: Every listed failure lands on REJECT or explicit UNKNOWN - never silent ALLOW.
- **RECOVERY**: UNKNOWN paths reconcile via poll(); duplicates deduplicate via idempotency keys.
- **AUDIT**: Rejections audited with reasons.
- **TEST**: See SOURCE OF TRUTH above.
