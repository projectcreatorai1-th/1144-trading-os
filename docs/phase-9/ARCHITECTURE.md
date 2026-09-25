# Phase 9 Architecture

## Layers (SECTION 6)

```
Desktop UI (tkinter shell, A-G)
  -> View Models / UI State (ui.desktop.viewmodels, ui.desktop.contracts)
    -> Desktop Adapter (platform.api.desktop_gateway + desktop_actions
       + desktop_feed)
      -> Existing Core Ports (Phases 0-8)
        -> Existing Domain Engines
```

## Module ownership (architecture.yaml 1.9.0)

- `ui.desktop` (ACTIVE): contracts (workspace_state schema + UI enums),
  viewmodels (context stack, blotter, presets, palette, notifications),
  shell (A-G tkinter), app (entry), run_windows (launcher/verifier).
  Allowed deps: platform.api + architecture.contracts ONLY.
- `platform.api` (extended): desktop_gateway / desktop_actions /
  desktop_feed - the ONE facade; composes the real stack; never an
  authority.

## The authority chain the desktop dispatches into

```
desktop action
  -> gateway._authorize (Phase 8 session + canonical registry)
  -> StrategyIntent (Phase 4 contract)
  -> PortfolioDecisionEngine (Phase 4)
  -> IntentGate -> RiskEngine (Phase 3, evaluated twice: base + projected)
  -> Order (Phase 5 contract)
  -> OMS.submit (validation, idempotency)
  -> EMS -> SimulationExecutionAdapter (synthetic fills - DEMO/SIMULATION)
  -> ExecutionReport -> OMS ingest -> ExecutionProjector
  -> Position state (Phase 2) + Ledger (Phase 2)
  -> AuditChain (Phase 8) + security events
```

Safety wiring: a GLOBAL_SAFETY_POLICY (hard, EMERGENCY on trigger)
halts NEW_EXPOSURE while the risk state machine is PAUSE/EMERGENCY; the
gateway's risk context reflects the LIVE risk state, so a paused system
blocks orders through the real engine - not through UI logic.

## Registries (REUSE -> EXTEND -> VERSION)

architecture.yaml 1.9.0 (ui.desktop ACTIVE, platform.api extended),
schema-registry 1.9.0 (+workspace_state), manifest 1.0.0 / phase 9.
No Phase 0-8 contract changed.
