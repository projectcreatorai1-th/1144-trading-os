# Data Quality (Phase 1)

- **WHAT**: Fifteen structured checks (DQ-001..DQ-015) and a five-state classification.
- **WHY**: The system must know how good its data is; UNKNOWN must never be read as valid, INVALID must never flow downstream (SECTIONS 8/9).
- **BOUNDARY**: Quality is evaluation, not repair - raw data is never edited.

## Rules (core/validation/engine.py)

| Rule | Check | Source |
|---|---|---|
| DQ-001 | missing required field | schema engine |
| DQ-002 | invalid type | schema engine |
| DQ-003 | invalid enum | schema engine |
| DQ-004 | invalid timestamp | time validation |
| DQ-005 | naive datetime | time validation |
| DQ-006 | future timestamp anomaly | time validation (source semantics aware) |
| DQ-007 | stale / heavily delayed data | time validation (threshold + judged_at evidence) |
| DQ-008 | duplicate data | dedup key lookup |
| DQ-009 | sequence gap / duplicate / regression | sequence monitor |
| DQ-010 | out-of-order data | ordering monitor |
| DQ-011 | invalid symbol (structural only) | symbol rule |
| DQ-012 | invalid/unknown/disabled source | source registry |
| DQ-013 | invalid schema version | schema registry |
| DQ-014 | payload hash mismatch | raw record validation |
| DQ-015 | missing provenance (derived records) | contract validation |

Every check is a `QualityCheckResult` (schema quality_check): rule_id, severity PASS/WARNING/FAIL, message, field, details (evidence REQUIRED for WARNING/FAIL). Never a bare boolean.

## Classification

`VALID(ATED) / DEGRADED / STALE / INVALID / UNKNOWN` (DataQualityLevel, extended in contract 1.1.0):

- any FAIL -> INVALID -> no normalization, no canonical event; raw kept + `EVENT_REJECTED`
- stale/delayed warnings -> STALE (explicit status + reasons + threshold + judged timestamp)
- other warnings -> DEGRADED
- nothing evaluable -> UNKNOWN (never treated as valid; blocks risk allowance in later phases)
- clean -> VALIDATED

`INVALID` and `UNKNOWN` (and DEGRADED/STALE) all `blocks_risk_allowance` for the future risk engine; only INVALID blocks downstream processing.

- **TEST**: `tests/test_phase1_quality.py`, pipeline rejection tests, `tests/test_ports_and_data_quality.py`.
