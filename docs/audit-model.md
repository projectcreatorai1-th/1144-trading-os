# Audit Model

- **WHAT**: Append-only audit records answering: in ki did what, when, to which entity, from/to which state, why, under which policy/risk/model versions, in which environment.
- **WHY**: Every important decision must be traceable back to its sources (RULE 017); audit is the evidence layer.
- **BOUNDARY**: `platform.audit` owns the contract and the repository port. Phase 0 defines the record contract; recording pipelines arrive in later phases.

## Record fields (v1.0.0)

Required: audit_id, actor_type (USER/SYSTEM/STRATEGY/AI_MODEL), actor_id, action, entity_type, entity_id, event_time, reason, source, environment, correlation_id, and at least one of before/after.
Optional: causation_id, policy_version, risk_version, model_version.

## Rules

- Audit records are immutable (`FrozenInstanceError` on mutation attempts).
- A record without before/after is invalid for state-changing actions (traceability).
- correlation_id is required so audit joins the causal chain (see docs/decision-trace.md).
- The audit repository port is append-only by contract.

## Immutability across the system

| Object | Immutability | Corrections |
|---|---|---|
| Event | frozen dataclass | new corrective event |
| LedgerEntry | frozen dataclass | ADJUSTMENT (adjusts_entry_id) / reversal (reverses_entry_id) entry |
| AuditRecord | frozen dataclass | never corrected - a new record supersedes |
| Decision/RiskDecision snapshots | frozen dataclass | new version, never overwrite |

- **INPUT**: actions from any module (later phases).
- **OUTPUT**: audit records; queries via `/audit` (API contract).
- **DEPENDENCY**: kernel only.
- **FAILURE**: invalid records are rejected deterministically; nothing is silently unaudited at contract level (state-changing records require before/after).
- **VERSION**: audit contract 1.0.0.
- **TEST**: `tests/test_audit.py`, `tests/test_ledger.py` (immutability + corrections).
