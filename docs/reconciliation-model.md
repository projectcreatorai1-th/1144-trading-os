# Reconciliation Model (Phase 2)

- **WHAT**: How internal belief and external truth are compared, classified and recorded.
- **WHY**: Reconciliation is the system's honesty mechanism: it reports differences instead of hiding them (SECTIONS 22-31).
- **SOURCE OF TRUTH**: internal state/ledger stores vs immutable external observations; tolerances from `architecture/tolerances.yaml`.
- **INPUT**: internal value map + observation list (+ comparison time, expected schema version, staleness boundary).
- **OUTPUT**: immutable ReconciliationResult + Difference objects (decimal semantics; floats never compared).
- **IMMUTABILITY**: results and observations are append-only history.
- **FAILURE**: UNKNOWN classifications (stale / schema-mismatched / duplicate / uncomparable) never silently match; environment mismatch fails closed; MATCH-with-differences is contractually impossible.
- **RECOVERY**: mismatches feed the audit trail (RECONCILIATION_MISMATCH records); correction is an explicit future workflow with permission + audit - the engine contains no mutating paths (validator rule RECON-001).
- **VERSION**: reconciliation contracts 1.0.0; tolerance registry 1.0.0.
- **TEST**: all 14 scenarios of SECTION 48 in `tests/test_phase2_reconciliation.py`.

## Flow (SECTION 29)

EXTERNAL OBSERVATION + INTERNAL STATE/LEDGER -> NORMALIZE -> MATCH -> DIFFERENCE -> CLASSIFY -> RECONCILIATION RESULT -> AUDIT.
