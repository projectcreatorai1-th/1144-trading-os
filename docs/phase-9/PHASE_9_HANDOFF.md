# Phase 9 Handoff

## Current Status

PASS (final verification re-run at freeze time; see PHASE_9_BASELINE.json
for the measured numbers).

## What Is Complete

Phases 0-9 complete and frozen: contracts kernel, data/time/events,
state/ledger/reconciliation, policy/risk, strategy/portfolio,
OMS/EMS/MT5-adapter contracts, research/backtest/replay/validation,
intelligence (advisory AI), security/governance/audit, and the A-G
desktop workstation with a verified Windows build.

## Architecture

Desktop -> DesktopGateway (platform.api) -> Phase 8 security ->
Phase 8 governance -> Phase 3/4 core authority -> Phase 5 OMS/EMS ->
simulation adapter. GUI = control plane; Core = authority; AI =
advisory. Full detail: PHASE_9_ARCHITECTURE_BASELINE.md.

## A-G Workstation

A navigation/global context; B workspace (7 presets, dock states,
persisted UI-only workspace_state with safe recovery); C persistent
inspector (UNKNOWN first-class); D blotter (engine-truth, virtualized,
global-vs-local filters); E intelligence (AI WHY, advisory chain);
F portfolio/risk (authoritative snapshots); G action/approval/attention
(real receipts, confirmation gates). Cross-cutting: context stack,
global search, Ctrl+K palette, notifications, freshness + explicit UI
states.

## Security

Phase 8 stack drives everything: persona login (server-side roles),
sessions with TTL/revocation, canonical permission registry per action,
fail-closed on unauthenticated/disconnected/rate-limited, chained audit
on every desktop action.

## Governance

MakerChecker + GovernanceGate available through the gateway
(submit_governance_change / approve_governance_change); maker/checker
separation enforced by Core.

## Risk

RiskEngine is the sole authority; a hard GLOBAL_SAFETY_POLICY halts
NEW_EXPOSURE while the risk state machine is PAUSE/EMERGENCY. Pause /
close-only / emergency-stop dispatch through permissions + the risk
state machine (operator_transition), never UI state.

## OMS / EMS

Real engines: OMS submit (idempotency), EMS routing to the simulation
adapter, fill ingestion, projection to positions + ledger. No broker
is contacted; MT5 adapters remain contract-only.

## AI

Advisory only: PIT features -> deterministic classifier -> advisory
proposal with explicit validity window; AI WHY separated from CORE WHY;
no execution path (validator AI-001..036 + GUI-006).

## Realtime

In-process event bus with event-id dedup (bounded cache), subscription
ownership + unsubscribe errors, freshness from tick age + connection
flag, disconnect blocks privileged actions, reconnect resynchronizes.

## Research / Replay

Phase 6/7 planes are unchanged and reachable read-only through the
gateway (datasets, intelligence stack); replay isolation preserved
(RESEARCH environment + validity windows).

## Audit

Every privileged desktop action appends a chained AuditRecord (Phase 8
tamper evidence); security events flow through event contract 1.3.0.

## Workspace

UI-only, persisted (workspace_state 1.0.0), corrupted input recovers
to the default A-G layout; can never become trading authority.

## Testing

Phase 9: 71 tests (32 core, 22 invariants/E2E incl. golden path, LIVE
safety, Tk shell smoke; 17 validator corruption). Full suite:
see PHASE_9_BASELINE.json (measured at freeze). Build verification:
9/9 checks PASS via `python ui/desktop/run_windows.py --verify-build`.

## Packaging

Launcher: `python ui/desktop/run_windows.py` (mainloop) /
`--verify-build` (headless verification). Dependencies: Python 3.10+,
PyYAML, stdlib tkinter. No installer signed artifacts were produced
(out of scope; documented as a limitation in the final report).

## Known Limitations

SYNTHETIC market data; simulation execution only (LIVE structurally
refused); price table instead of canvas chart; single-window
multi-monitor persistence; Tk smoke covers construct/render/destroy.

## Explicit Non-Goals

Web workspace, mobile apps, new trading strategies, new asset classes,
new broker engines, AI execution, generic analytics, admin portals.

## Future Work

See PHASE_9_FUTURE.md (recorded only - none implemented).

## Rules For Next Phase

Do not redesign A-G.
Do not create a second authority.
Do not bypass DesktopGateway.
Do not bypass Security.
Do not bypass Governance.
Do not bypass Risk.
Do not create GUI execution authority.
Do not create AI execution authority.
Do not mutate authoritative state from UI.
Do not remove existing safety gates.
Do not weaken UNKNOWN/STALE/DISCONNECTED handling.
Do not treat Phase 9 UI state as domain state.
