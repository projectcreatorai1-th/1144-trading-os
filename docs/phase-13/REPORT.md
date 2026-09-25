# Phase 13 — Chaos / Failure Engineering — PASS

platform.chaos: ChaosScenario (5 callable phases: inject/detect/contain/
recover/verify, each must return real evidence) + ChaosRunner (environment
guard: SIMULATION/REPLAY/CONTROLLED_TEST only — LIVE refused at
construction). Built-in scenarios (tests/phase13_scenarios.py) drive REAL
components: duplicate tick, out-of-order tick, stale feed, clock drift
(Phase 10 fix proof), db unavailable, disk full (WinError 112), audit
unavailable fail-closed.
REAL DEFECT FOUND & FIXED: MT5MarketDataAdapter.freshness() without a
monitor returned CURRENT for stale ticks (fail-open); now computes from
the same policy thresholds as the monitor (never CURRENT past the
tick-stream timeout).
Tests: tests/test_phase13_chaos.py — 12/12.
