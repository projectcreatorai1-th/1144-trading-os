# Risk Decision (Phase 3)

- **WHAT**: RiskDecision contract 1.2.0 + RiskDecisionValidator - the permission boundary every future execution phase must pass through.
- **WHY**: downstream may act ONLY on a valid, unexpired, environment-matched decision (SECTIONS 12-14/51).
- **SOURCE OF TRUTH**: decisions live in the risk decision store WITH the exact context they were made against.
- **INPUT**: engine output; validator takes (decision, environment, now, expected hashes/policy).
- **OUTPUT**: permission ALLOW/LIMITED/BLOCK/CLOSE_ONLY/EMERGENCY + constraints (LIMITED) + full rule evidence + hashes + expiry.
- **IMMUTABILITY**: append-only; re-append of the same id rejected.
- **FAILURE**: see docs/fail-closed-matrix.md - expired/mismatched/hash-broken/permission-invalid decisions never validate.
- **RECOVERY**: deterministic re-evaluation + replay verification (docs/risk-replay.md).
- **VERSION**: risk_decision schema 1.2.0 (non-breaking: action identity, rule evidence, hashes, constraints, provenance).
- **TEST**: decision tests in `tests/test_phase3_risk_engine.py`; invariants 1-5, 20 in `test_phase3_invariants_e2e.py`.

## Validation checks

schema (contract validate incl. UNKNOWN-fail-closed semantics), environment match, expiration, context-hash integrity, policy reference/hash, hard-limit consistency (ALLOW/LIMITED with blocking rules is invalid), LIMITED-requires-constraints.

## Permissions

ALLOW: action permitted as requested. LIMITED: permitted within `permission_constraints` (from policy content - never hard-coded defaults). BLOCK: no new action. CLOSE_ONLY: risk-reducing actions only (CLOSE/REDUCE map to LIMITED). EMERGENCY: emergency control - nothing may increase risk.
