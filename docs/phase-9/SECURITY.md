# Phase 9 Security Model

## Authentication
Phase 8 AuthenticationService + SessionService via the gateway.
Personas are SERVER-SIDE (gateway table): trader / risk_manager; the
role never comes from the client (client-supplied roles are never
trusted). Secrets are presented at login and never stored, logged, or
persisted by the GUI.

## Authorization
Every privileged gateway action authorizes through Phase 8's
AuthorizationService against the canonical permission registry:
- submit_order: SIMULATE (SIMULATION) / DEMO_TRADE (DEMO)
- pause: PAUSE; close_only: CLOSE_ONLY; emergency_stop: EMERGENCY_STOP
Unauthenticated/disconnected/rate-limited requests fail closed.

## Environment
The gateway operates SIMULATION/DEMO only; constructing it for LIVE
raises immediately (test_live_e2e_safety). The top bar identifies the
environment; SIMULATION != DEMO != LIVE is enforced structurally.

## Fail-closed matrix (tested)
unauthenticated act -> BLOCK; disconnected act -> BLOCK; trader
emergency-stop -> BLOCK; paused system + order -> hard-policy
EMERGENCY rejection; rate-limit burst -> BLOCK; duplicate event ids ->
single delivery; unknown symbol -> error; corrupted workspace ->
default layout recovery.

## Live safety (SECTION 71)
LIVE is unreachable from the desktop: gateway constructor refuses it,
and the EMS has only the simulation adapter registered. There is no
code path from the GUI to any broker.

## AI boundary
Intelligence views are advisory_only=True with AI WHY; the gateway
exposes no RiskDecision constructor to the UI, and the validator's
GUI-006 forbids the desktop from importing core.intelligence at all.
