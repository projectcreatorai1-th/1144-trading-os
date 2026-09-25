# Phase 2 — Reconciliation

- **WHAT**: External observation contract, tolerance policy, difference engine and reconciliation engine (`core.reconciliation`).
- **WHY**: The system must detect where internal belief and external truth differ - and REPORT, never auto-fix (SECTIONS 22-31, 59).
- **SOURCE OF TRUTH**: internal = state + ledger stores; external = immutable observations; comparison rules = `architecture/tolerances.yaml`.
- **INPUT**: internal values (e.g. balances per account) + `ExternalObservation` records (observation_id, source, entity, observed/received times, payload + payload_hash, provenance, environment).
- **OUTPUT**: immutable `ReconciliationResult` (status, matched/mismatched/missing/unknown items, difference summary, tolerance, versions, environment, correlation) + `Difference` objects (field, values, difference, absolute/relative difference, tolerance, severity, reason - canonical decimal strings).
- **IMMUTABILITY**: observations and results are frozen + append-only (RECON-004); observations are never edited to make a match.
- **FAILURE**: environment mismatches fail closed; schema-mismatched, stale, duplicate or uncomparable observations classify UNKNOWN - never MATCH without evidence (RECON-002); MATCH with any difference/missing/unknown is contractually impossible.
- **RECOVERY**: reconciliation history is permanent evidence; corrections happen through explicit adjustment workflows in later phases with permission/audit - never automatically.
- **VERSION**: external_observation/reconciliation_result/difference schemas 1.0.0; tolerances registry 1.0.0.
- **TEST**: `tests/test_phase2_reconciliation.py` (all 14 scenarios of SECTION 48 + no-auto-fix proofs).

## Statuses

MATCH, MISMATCH, PARTIAL, MISSING_INTERNAL, MISSING_EXTERNAL, UNKNOWN, ERROR.

## Tolerance semantics

Within BOTH absolute AND relative tolerance = match; zero tolerance = exact decimal equality; every comparison carries its tolerance in the result. Modules resolve tolerances from the registry only (RECON-003) - none are hard-coded.
