# PROJECT_BASELINE_REPORT.md — 1144 Trading OS (Phase 1-44 command baseline)

Generated 2026-09-25 · Repository state: post Phase 0-20 roadmap execution
(full regression 2113 collected / 0 failed; validator 0 violations)

## Architecture AS BUILT (maps the command's architecture)

Command: SNIPER Analyzer ↔ 1144 Trading Gateway ↔ 1144 Trading OS ↔ OUR EA ↔ MT5 ↔ Broker
As built (authority chain, architecture.yaml single source of truth):

  SNIPER CashFlow Analyzer (separate project — RESEARCH authority boundary)
        ↕  signal/evidence enters as research data + STRATEGY_SIGNAL_CREATED
  Desktop GUI (ui.desktop — CONTROL PLANE; never touches MT5/events directly, GF-002)
        ↕  DesktopGateway (platform.api — TRANSPORT: dispatch, session, events,
           heartbeat surface; internal module, NOT a separate GUI app)
  Phase 8 Security → Phase 3 Policy/Risk → Phase 4 Strategy/Portfolio
        ↕  (STRATEGY + RISK + EXECUTION authority — the "OUR EA" role is
           owned by the deterministic core engines per the frozen Phase 0-10
           contracts; there is no second authority and no MQL EA duplicate)
  Phase 5 OMS/EMS → MT5 adapter (adapters.mt5 — DEMO-bound transport)
        ↕
  MT5 terminal (RUNTIME) → Broker (MARKET EXECUTION)

Divergence note (per command §89 STOP-DUPLICATION → REUSE → ADAPT): the
command places strategy/risk/execution authority in a separate EA process;
the existing frozen architecture places those authorities in core engines
behind the gateway with LIVE structurally refused. Adapting the contract =
the EA authority role is fulfilled by core.strategy/core.risk/core.ems;
creating a parallel EA engine would violate the command's own §1.

## Existing components (verified green in the 2113-test suite)
- Kernel: contracts (identifiers 1.10.0, time, env, schemas, state machines,
  config envelope), validator (234+ rules, 0 violations)
- Core engines: data pipeline (P1), reconciliation (P2), policy+risk (P3),
  strategy/portfolio (P4), OMS/EMS (P5), ledger (P6), intelligence/AI
  advisory (P7), security/governance/audit (P8)
- Desktop: workstation GUI (P9, live-validated 17/17), pythonw entry
- MT5 plane (P10): connection monitor/state machine, tick feed (epoch fix),
  transport, reconciliation, connectivity config, runtime gate driver
- P11-20: observability (metrics/SLI/SLO/audited alerts/health), incident
  management + 13 runbooks, backup/restore + measured RPO/RTO, fail-closed
  recovery, chaos framework (+7 scenarios; found+fixed a real freshness
  fail-open), trace assembler + clock quality, config governance,
  promotion ledger, UI semantic contract, ORR/PRR (PASS), Phase-20 gate

## Existing tests: 2113 collected, 2034+79 new-suite = all passing
(0 failed / 0 errors; 1 documented deployment-conditional skip)

## Existing runtime integration (real evidence)
- MT5 terminal reachable, DEMO-gated; real ticks streamed and were accepted
  by the fixed feed (gate rerun 2026-09-25)
- CURRENT BLOCKER: the terminal is logged into a REAL account (411173797,
  XMGlobal, ACCOUNT_TRADE_MODE_REAL) — every execution path correctly
  refused. DEMO validation needs a human terminal-side switch to a DEMO
  login.

## Existing defects found & fixed this roadmap
1. server-local epoch in ticks (FDX-TIME) — fixed with measured offset
2. margin_mode enum mislabel (EXEC-003) — fixed against real enum
3. tick source poll-loop hang (masked by #1) — fixed to bounded poll
4. freshness without monitor = CURRENT (fail-open) — found by chaos, fixed
5. GUI: RC-2/RC-3/RC-F — fixed, live-validated

## Missing / mapped-as-is
- SNIPER Analyzer runtime integration: no analyzer runtime is wired into
  this repository (separate project); boundary contract exists
  (signal/evidence ingest + STRATEGY_SIGNAL_CREATED). TBD until the
  analyzer owner defines the payload.
- Event/command catalogs: mandated names map onto existing contracts
  (connectivity → SYSTEM_STATE_CHANGED/DATA_SOURCE_CHANGED; commands →
  pause/close_only/emergency_stop + OMS intents incl. close/modify paths).
  Renaming would break frozen contracts for zero capability gain.
- Standalone Security/Quality audit tool: deleted externally; not
  recreatable (no fake tools). BLOCKED.
- Installer/update/rollback APPLICATION packaging: config-level
  backup/rollback exists (P12/P15); app-package updater = TBD (no release
  pipeline in scope).
- Long DEMO session (>30s bounded run): blocked by the REAL-account
  blocker.

## Phase 1-44 mapping summary
P1-5 foundation → kernel+platform (VERIFIED) · P6-10 control plane →
core.* managers + canonical state in stores (VERIFIED) · P11-15 gateway →
DesktopGateway + connectivity plane + event/command surfaces (VERIFIED,
metrics via P11) · P16-20 EA/MT5 runtime → core authorities + MT5 adapter
(VERIFIED; runtime gate BLOCKED on REAL account) · P21-26 hardening →
timeouts/backoff (connectivity config), dedup/idempotency (pipeline+OMS),
safe shutdown/startup (lifecycle+runbook rb_restart), chaos suite
(VERIFIED) · safe mode → risk PAUSE/EMERGENCY states + banner visibility
(VERIFIED) · readiness split → HealthAggregator + PRR readiness (VERIFIED)
· P27-34 GUI pages → P9 workstation presets incl. Dashboard/Trading/EA/
Risk/Orders/Positions/History/Diagnostics/Settings equivalents (VERIFIED,
17/17 live) · P35-44 analyzer/observability/incident/diagnostics/security/
audit/persistence/safety/versioning → P7+P11-P15+P8 (VERIFIED) · retention
→ tick history bounds + bounded metric buffers (VERIFIED) · performance →
benchmarks/ dir exists; measured latencies recorded in gate evidence ·
large data/search → pagination in stores; TBD deeper UX virtualization ·
DR/backup/restore/sim/replay → P12 + REPLAY env (VERIFIED) · contract
testing/version handshake/compat matrix/feature flags → schema registry +
env isolation + capability negotiation (VERIFIED; matrix doc TBD) ·
installer/first-run wizard → shortcut + run_windows entry (PARTIAL) ·
operational reports → generator TBD (data exists in stores).
