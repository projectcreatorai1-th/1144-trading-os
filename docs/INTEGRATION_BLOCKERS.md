# INTEGRATION_BLOCKERS.md

Status: 2 environment blockers (outside this repository's control) + 1
pending cross-project step. NOTHING was faked as passed.

## B1 — Real broker execution / DEMO runtime: ENVIRONMENT_BLOCKED
- Blocker: the MT5 terminal is logged into a REAL account (411173797,
  XMGlobal-MT5 16, ACCOUNT_TRADE_MODE_REAL). Every execution path is
  correctly refused (fail-closed). Gateway order-flow routing itself is
  implemented and tested at the transport level.
- Dependency: a DEMO account login in the MT5 terminal.
- Required environment: MT5 terminal + DEMO account.
- Next action (human, terminal-side): switch the terminal login to a
  DEMO account, then rerun the Phase 10 runtime gate
  (`python tools/p10_runtime_gate.py`) and the gateway order-flow E2E
  against the adapter.

## B2 — Standalone Security/Quality audit tool: BLOCKED (pre-existing)
- Blocker: `tools/security_quality_audit.py` was deleted by an external
  repository modification; the original was searched for exhaustively and
  not found; recreating it from memory is forbidden (no fake tools).
- Next action: owner supplies the original artifact.

## B3 — Cross-process E2E with the real OUR-EA project: PENDING
- The EA's own transport notes: "The production WebSocket transport ships
  when the 1144 Gateway server is deployed". This server now exists and
  speaks contract v1.0.0 over WebSocket; the EA-side WebSocket transport
  plus a live two-process run is the EA owner's next step (we must not
  modify OUR-EA).
- Interim proof: field-compatible contract tests (GATEWAY_CONTRACT_REPORT)
  + a real-localhost WebSocket handshake/heartbeat from this repository.

Environment labeling: everything in this phase ran in SIMULATION (clearly
labeled, never called DEMO); DEMO only after B1; LIVE = LOCKED everywhere.
