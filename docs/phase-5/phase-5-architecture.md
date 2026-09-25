# Phase 5 Architecture (Phase 5)

- **WHAT**: Execution boundary: Order -> OMS -> EMS -> ExecutionAdapter (MT5/Simulation) -> ExecutionReport -> Position (Phase 2 state) -> Ledger (Phase 2 posting) -> Reconciliation (Phase 2).
- **WHY**: Bridge authorized intents to broker execution with contract, state, audit, idempotency, recovery and environment isolation (SECTION 0).
- **BOUNDARY**: No strategy/portfolio/AI logic; Phase 5 executes only what Strategy->Portfolio->Risk already authorized (SECTION 57/75).
- **SOURCE OF TRUTH**: Phase 0-4 foundations reused everywhere: state machine engine, risk decision validator, permission model, event store, state engine, ledger, reconciliation.
- **INPUT**: Canonical Orders (order contract 1.1.0) carrying the full causal chain (intent/portfolio/risk).
- **OUTPUT**: Executed orders, immutable execution reports, projected positions, posted ledger entries.
- **FAILURE BEHAVIOR**: Fail closed everywhere: no valid risk decision -> REJECT; UNKNOWN -> BLOCK; timeout -> UNKNOWN (never FAILED without evidence).
- **RECOVERY**: Idempotency + outbox + reconciliation + replay (no cross-system atomicity claims).
- **AUDIT**: Every execution-critical action audited with causal chain.
- **TEST**: tests/test_phase5_*.py
