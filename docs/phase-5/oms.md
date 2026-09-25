# Oms (Phase 5)

- **WHAT**: OrderManagementSystem (core.oms): validation, persistence, lifecycle, idempotency, cancel/replace, execution-report reconciliation, recovery.
- **WHY**: OMS = WHAT ORDER EXISTS (SECTION 7/16).
- **BOUNDARY**: OMS never calls MT5, never invents fills, never overrides risk (validator rule OMS-002 enforces).
- **SOURCE OF TRUTH**: core/oms/engine.py + Phase 0 state machine engine.
- **INPUT**: Canonical Order + RiskDecision + ExecutionContext.
- **OUTPUT**: Immutable order versions + accepted/rejected outcomes.
- **FAILURE BEHAVIOR**: Duplicate semantic request -> existing order returned; invalid chain -> REJECTED with reasons; overfill -> UNKNOWN (corruption path).
- **RECOVERY**: recover() returns non-terminal orders for reconciliation; idempotent re-submission.
- **AUDIT**: ORDER_ACCEPTED/REJECTED/<STATUS> per transition.
- **TEST**: tests/test_phase5_boundary_oms.py
