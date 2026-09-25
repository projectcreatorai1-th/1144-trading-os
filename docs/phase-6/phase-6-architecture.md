# Phase 6 Architecture (Phase 6)

- **WHAT**: Research plane: Dataset -> Point-in-Time -> Research -> Backtest -> Replay -> Validation -> Stress -> Robustness -> Evidence -> Phase 4 Strategy Registry.
- **WHY**: Answer what would have happened with correct point-in-time data - never execute (SECTION 1).
- **BOUNDARY**: No AI authority, no automatic promotion, no broker execution, no second risk/ledger/state/event engines.
- **SOURCE OF TRUTH**: Phase 1 data/time/event + Phase 2 state/ledger + Phase 3 risk + Phase 4 strategy/portfolio + Phase 5 simulation semantics, all reused.
- **ASSUMPTIONS**: Every cost/latency/liquidity assumption explicit and versioned (never implicit zero).
- **FAILURE**: Look-ahead/leakage -> INVALID; unknown critical data -> fail closed; hash mismatch -> fail closed.
- **RECOVERY**: Deterministic identity: re-running reproduces identical results (idempotent).
- **REPRODUCIBILITY**: Same inputs + versions + data = same identity and hashes.
- **TEST**: tests/test_phase6_core.py, tests/test_phase6_invariants_e2e.py
