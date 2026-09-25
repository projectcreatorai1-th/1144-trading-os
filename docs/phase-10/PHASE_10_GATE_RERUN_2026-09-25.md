# Phase 10 — Gate Rerun 2026-09-25 (post finding-fixes)

## Findings fixed (master command §16.3, real evidence)

**Finding 1 — server-local epoch (FDX-TIME)** — FIXED
`adapters/market_data/mt5_feed.py::MT5TickSource` now measures the server
timezone offset from each tick (server epoch − UTC receipt, snapped to half
hours, ±14h bound, non-timezone-like skews fail closed) and converts
event_time to true UTC. Tick contract gained a justified 2s clock-skew
tolerance (measured real inter-clock drift: 1.03s). Contract 1.0.0 → 1.1.0
(backward compatible).
**Real evidence:** `tests/test_phase10_finding_fixes.py` — 15/15 incl. live
terminal tick; gate step `real_ticks_pipeline` PASS against the real feed
(accepted=1, freshness CURRENT).

**Finding 2 — margin_mode enum (EXEC-003)** — FIXED
Real enum verified live: `ACCOUNT_MARGIN_MODE_RETAIL_NETTING=0`,
`_EXCHANGE=1`, `_RETAIL_HEDGING=2`. `adapters/mt5/execution.py` now maps all
three (`PositionSemantics` gained `EXCHANGE`, backward compatible). Real
account (margin_mode=2) → HEDGING → no longer UNKNOWN → EXEC-003 unblocked.
**Real evidence:** parametrized tests + live-terminal test in the same file.

**Defect discovered during rerun (masked by finding 1 before)** — FIXED
`MT5TickSource.ticks()` was `while True` over a POLL api
(`symbol_info_tick` returns the same latest tick every call) — once
validation passed it re-yielded forever and hung the feed loop. Now
delivers each NEW tick at most once and returns (bounded poll). Gate
`long_run_bounded` PASS: 60 polls / 30s, peak 0.02 MB, buffer within bound.

## Gate rerun result (REAL terminal, exit 1)

```text
PASS  real_connection          CONNECTED
PASS  real_ticks_pipeline      accepted=1, freshness CURRENT   <- finding 1 proven fixed on real data
PASS  long_run_bounded         60 polls, 0.02 MB peak
FAIL  prerequisites            account trade_mode=2 is NOT DEMO - refusing the gate
FAIL  demo_execution_full_chain (consequence: no execution on a non-DEMO account)
FAIL  reconciliation_real      (consequence)
PASS  safety_pause_blocks      REJECTED while paused
PASS  disconnect_reconnect_real
FAIL  live_safety              account is not DEMO
```

## New blocker (safety-critical, correctly fail-closed)

The MT5 terminal is currently logged into a **REAL account**
(login 411173797 · XMGlobal-MT5 16 · leverage 1:1000 ·
`ACCOUNT_TRADE_MODE_REAL=2`, enum verified from the live package).
The gate refused every execution path on it — this is the LIVE/REAL safety
boundary working against a real-world condition, not a tooling failure.

**Required human action:** switch the MT5 terminal login to a DEMO account
(terminal-side credentials; nothing in this system may do it). No orders,
no state changes, no account interaction of any kind were performed.

## Phase 10 status

```text
IMPLEMENTATION COMPLETE — RUNTIME GATE BLOCKED
```

Blocker: MT5 terminal logged into a REAL account; DEMO login required
(human-controlled, terminal-side). Everything independently verifiable was
verified against the real terminal (ticks, pipeline, freshness, bounds,
disconnect/reconnect, pause safety, REAL-account refusal).
