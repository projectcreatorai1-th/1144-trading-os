# Risk Replay (Phase 3)

- **WHAT**: `RiskReplayService` - re-evaluate a stored decision against its stored context and historical policy version, then compare.
- **WHY**: forensic/audit capability - prove what the engine would decide today about yesterday (SECTION 25).
- **SOURCE OF TRUTH**: the risk decision store (decision + exact context) + the policy store (immutable historical versions).
- **INPUT**: risk_decision_id + environment (must be REPLAY).
- **OUTPUT**: `ReplayComparison` (MATCH / MISMATCH / UNKNOWN + original/replayed permission + reasons).
- **IMMUTABILITY**: replay is read-only; comparisons are never auto-corrected into the stored decision.
- **FAILURE**: missing historical policy -> UNKNOWN; replay environment != REPLAY -> hard error.
- **RECOVERY**: replay IS the verification mechanism for recovery (restart -> replay -> MATCH).
- **VERSION**: replay service 1.0.0; decision/context schemas as per contracts.
- **TEST**: `tests/test_phase3_state_replay_recovery.py` (match, unknown, environment gate) + restart E2E.

## Semantics

The replayed permission comes from re-composing the SAME dimension evaluations with the same precedence. Engine-level gates (hard policy precedence, injected risk state, CLOSE-action mapping) are reflected in the comparison reasons when they differ - status is a strict permission equality verdict.
