# Phase 7 Failure Tests

45 executable failure scenarios (tests/test_phase7_failures.py), every
one asserting fail-closed behavior:

missing feature, stale feature, future feature (contract PIT reject),
future normalization, future label contamination, corrupted dataset hash,
cross-split leakage, corrupted model hash, corrupted artifact, malformed
artifact, wrong feature schema, UNKNOWN feature value (never zero),
wrong model version, wrong environment, expired model, expired
inference, missing provenance, broken (lying) provenance, invalid
probability/confidence, model conflict, OOD critical, critical drift,
training failure -> no model, evaluation with insufficient labels
(explicit marker, never fabricated), validation missing/UNKNOWN critical
evidence, failed training cannot reach the gate, missing hyperparameter
(NO_IMPLICIT_DEFAULTS), replay mismatch, unauthorized promotion,
unauthorized deployment (chain skip), direct OMS attempt
(architecturally impossible), direct RiskDecision attempt, AI proposal
cannot become Order, execution-verb recommendation rejected, duplicate
inference idempotent, crash/retry never duplicates, unsafe
deserialization, resource limits enforced, UNKNOWN drift/OOD/feature/
health/calibration semantics (SECTION 109).
