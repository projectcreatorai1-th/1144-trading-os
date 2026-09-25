# Phase 9 — Desktop Trading Workstation

The Windows desktop **control plane** for 1144-Trading OS (Phases 0-8).

Launch: `python ui/desktop/run_windows.py`
Verify build: `python ui/desktop/run_windows.py --verify-build`
Tests: `python -m pytest tests/test_phase9_core.py tests/test_phase9_invariants_e2e.py tests/test_phase9_validator.py`

## What it is

ONE professional workstation (the A-G layout, verbatim):

- **A** Navigation / global context (Overview, Markets, Intelligence,
  Portfolio, Execution, Research, Strategies, Risk, Operations)
- **B** Main workspace (tabs, presets, dock/float/collapse)
- **C** Persistent contextual inspector (identity/state/version/
  evidence/history/WHY; UNKNOWN is a first-class value)
- **D** Activity / blotter (orders, virtualized pages, global-vs-local
  filters that never silently overwrite each other)
- **E** Intelligence / decision (AI advisory chain + AI WHY; NEWS never
  becomes BUY/SELL)
- **F** Portfolio / risk (authoritative engine output: risk state,
  decision, limits, gross exposure)
- **G** Action / approval / attention (real action lifecycle:
  REQUESTED...APPLIED/REJECTED; dangerous commands need confirmation;
  attention items derived from real state only)

Plus: global search, Ctrl+K command palette, notifications center,
context stack with back/forward, explicit UI states
(INITIALIZING...PERMISSION_DENIED), freshness (CURRENT/STALE/UNKNOWN/
DISCONNECTED), environment identification (SIMULATION/DEMO only - LIVE
refuses to start).

## What it is NOT

Not a new engine of any kind. The desktop talks ONLY to
platform.api's DesktopGateway (GUI-001..GUI-012 validator-enforced);
the gateway composes the REAL Phase 0-8 stack and dispatches every
privileged action through security -> the existing authority chain.
Market data is SYNTHETIC and labeled as such everywhere.
