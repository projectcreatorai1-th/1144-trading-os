# 1144 Trading OS

Phase 0 — Architecture + Contracts: **complete**.
Phase 1 — Data + Time + Event: **complete**.
Phase 2 — State + Ledger + Reconciliation: **complete**.
Phase 3 — Policy + Risk Engine: **complete**.
Phase 4 — Strategy + Portfolio: **complete**.
Phase 5 — OMS + EMS + MT5 Adapter: **complete**.
Phase 6 — Research + Backtest + Replay + Validation: **complete**.

Phase 2 adds the state engine (event-sourced, rebuildable, snapshot-checked),
the immutable hash-chained ledger with idempotent posting and canonical
decimal money, balance reconstruction, external observations and the
report-only reconciliation engine with explicit tolerances - all on the
Phase 0/1 foundation.

No trading strategy, risk/portfolio behavior, MT5 connection, AI behavior or
GUI exists yet (later phases).

Entry points:

- Run tests: `python -m pytest` (from this directory)
- Run the Architecture Validator: `python -m architecture.validator`
- Benchmarks: `python benchmarks/phase1_benchmark.py`, `python benchmarks/phase2_benchmark.py`, `python benchmarks/phase3_benchmark.py`, `python benchmarks/phase4_benchmark.py`, `python benchmarks/phase5_benchmark.py`, `python benchmarks/phase6_benchmark.py`
- Reports: `docs/phase-0-report.md` .. `docs/phase-4-report.md`, `docs/phase-5/phase-5-report.md`, `docs/phase-6/phase-6-report.md`

Source of truth for all architecture rules: `architecture/architecture.yaml`.
