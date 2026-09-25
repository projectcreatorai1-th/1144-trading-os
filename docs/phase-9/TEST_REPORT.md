# Phase 9 Test Report

## Suites

- tests/test_phase9_core.py (32): gateway (persona login, wrong-secret
  rejection, unknown persona, full order chain incl. positions+ledger,
  pause blocks orders through the HARD policy, trader cannot
  emergency-stop, risk manager can pause, unauthenticated blocked,
  rate-limit burst, freshness + disconnect fail-closed, subscription
  dedup + unsubscribe errors, advisory intelligence, separate CORE WHY,
  search) + view models (context stack back/forward, context selection
  never mutates domain, blotter global-vs-local filters, virtual window
  pagination, all 7 presets, panel docking, persistence roundtrip +
  corrupted recovery, action lifecycle, dangerous-command confirmation,
  unknown command, palette pause as risk manager, palette pause blocked
  for trader, bounded notifications, search opens context, disconnect/
  reconnect states) + contracts (bad region, environment required,
  default workspace valid).
- tests/test_phase9_invariants_e2e.py (22): all SECTION 67 invariants
  executable + the SECTION 69 golden path (launch->login->symbol->
  inspect->intelligence->risk->order->CORE WHY->back->disconnect->
  blocked->reconnect) + SECTION 70 security E2E (login->session->
  action->audit; logout->blocked) + SECTION 71 LIVE safety (gateway
  refuses LIVE construction, subprocess-verified) + Tk shell smoke
  (skips cleanly when no display; on this machine it runs: shell
  constructed, rendered once with a live order row, destroyed).
- tests/test_phase9_validator.py (17): real project passes; all 12 GUI
  rules registered; corruption coverage for every rule (forbidden
  import, GUI engine, direct database, direct broker, direct OMS,
  direct intelligence, gateway removed, region removed, UNKNOWN state
  removed, environment label removed, confirmation removed, recovery
  removed) + Phase-10 boundary (web workspace + mobile blocked; the
  desktop app is legitimate).

## Build verification

python ui/desktop/run_windows.py --verify-build:
tkinter runtime / gateway startup / authentication / workspace
persistence / default workspace / event handling + dedup / order chain /
fail-closed disconnect / shutdown -> ALL PASS, BUILD PASS.
