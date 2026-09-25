# PRE_MARKET_BASELINE.md — 2026-09-26 (pre-market readiness run)

## Repository / environment (live-checked)

- Commit: `eddb7f4` (integration phase-1 artifacts) · working tree CLEAN
- Architecture validator: **PASS, 0 violations**
- Windows build (`run_windows --verify-build`): **PASS 9/9**
- Full-suite baseline: **2155 collected / 0 failed / 0 errors / 1 documented
  conditional skip** (2026-09-25); fresh targeted re-run today: phase10
  core + finding-fixes + chaos + GUI repair + gateway server =
  112 passed / 0 failed / 1 documented skip
- **MT5 terminal state changed since the last run**: now logged into a
  **DEMO account** — login 113126589 · trade_mode **0 = DEMO** ✓ ·
  ticks streaming (EURUSD 1.13909/1.13909) → the Integration-Phase-1 §3
  blocker (REAL account 411173797) is **cleared by the user**; DEMO
  runtime validation is now unblocked for the market-open E2E.

## Existing blockers (carried, unchanged)

1. G2 standalone Security/Quality audit tool — deleted externally, not
   recoverable, not recreated (validator SEC rules provide partial cover)
2. GitHub ecosystem repos/CI (projectcreatorai1-th/our-ea +
   1144-trading-os missing; workflow-scope push rejected) — BLOCKED at
   ecosystem Phase E (see BLOCKED_BY_EXTERNAL_DEPENDENCY.md)

## Existing UNKNOWN / PARTIAL / HYPOTHESIS (strategy domain)

From the spec's OWN status fields (STRATEGY_SPEC_AUDIT.md):
- UNKNOWN: restart-entry, emergency-close mechanism, session rules
- PARTIAL: basket-close trigger, partial-close trigger, grid anchor,
  exit/grid-add signals
- HYPOTHESIS: hypotheses are nested inside PARTIAL items and tracked by
  SNIPER's uncertainty matrix — never promoted to rules
The EA enforces these fail-closed (never guesses an UNKNOWN rule).

## Demo/live safety state

- LIVE = HARD-LOCKED everywhere (structural refusal, re-proven against a
  live REAL-account event on 2026-09-25 and by every suite since)
- Terminal NOW on DEMO (trade_mode=0) — first time a valid DEMO session
  exists for runtime gates

## SSOT / no-duplication check

Canonical strategy artifacts live in SNIPER `STRATEGY_SPEC/` (tag
spec/1.0.0); EA consumes via build contract; OS consumes signals via the
authority chain. No parallel spec/risk/OMS/gateway/replay systems exist
(validator + module registry enforce).
