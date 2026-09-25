# Backtest Engine (Phase 6)

- **WHAT**: BacktestEngine with deterministic SimulationClock: dataset -> PIT view -> strategy action -> policy gate -> simulated fill -> metrics.
- **WHY**: Historical simulation with production-consistent semantics (SECTION 25-28).
- **BOUNDARY**: Clock walks historical time only; no wall clock; policy gate fail-closed.
- **SOURCE OF TRUTH**: core/backtest/engine.py
- **ASSUMPTIONS**: Spread/slippage/commission explicit; UNKNOWN costs fail closed (no zero fallback).
- **FAILURE**: Clock regression, unknown costs, unpriced observations -> errors.
- **RECOVERY**: Deterministic re-run.
- **REPRODUCIBILITY**: Identical inputs -> identical metrics/equity curve.
- **TEST**: TestBacktest
