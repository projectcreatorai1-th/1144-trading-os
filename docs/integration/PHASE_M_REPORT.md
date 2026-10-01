# Phase M Report — WebSocket Cross-Process Validation

Status: **VERIFIED (local evidence below; CI re-verifies on push)** · 2026-10-01
Predecessor: Phase L closed (3/3 repos CI green + L1-L7 ecosystem compat green on GitHub).

## Objective

Prove the SNIPER Gateway contract v1.0.0 works across a **real OS process
boundary** over `ws://` — server as its own process, client in another —
with no `InProcessTransport` anywhere in the path. This is the last
structural gate before the DEMO E2E order-path tests (TEST-06..14).

## Harness

* `tools/gateway_ws_server.py` — boots `GatewayServer` + `WebSocketTransport`
  as a standalone process. Audits append to an on-disk JSONL file.
  Lifecycle reported as single-line JSON on stdout (`ready` / `stopped`).
  Stops gracefully via sentinel-file deletion or max-lifetime safety.
* `tests/test_phase_m_cross_process.py` — spawns that process, then speaks
  the exact EA wire messages (field-compatible with OUR-EA client SSOT)
  through real `websockets` connections.

## M1 — full lifecycle over the process boundary (PASS)

Three connections (EA / OS / analyzer), all over `ws://127.0.0.1:<port>`:

| Step | Evidence |
|---|---|
| Contract handshake EA + analyzer | `CONTRACT_RESPONSE accepted=true`, contract 1.0.0 |
| Command round trip (OS→gateway→EA push) | `COMPLETED`, `request_id` + `correlation_id` intact end-to-end |
| Event chain MARKET_EVENT→SIGNAL→DECISION→RISK_DECISION→ORDER_INTENT | `EVENT_ACK accepted=true` ×5 |
| Idempotent retry (PAUSE, same key, new request id) | 1st `duplicate=false`, retry `duplicate=true`, exactly ONE push to EA |
| Execution feedback EA→analyzer | `FEEDBACK_ACK delivered=1`, analyzer received the push |
| KILL switch | propagated to EA (`KILL` push), `order_flow=BLOCKED`, subsequent `ORDER_INTENT` publish → `ERROR` (fail-closed) |
| Control plane after kill | `HEARTBEAT_ACK` still served |
| Graceful stop | sentinel → exit 0, `stopped` JSON with counters: commands_routed≥2, events_routed≥5, kills_propagated=1, messages_received≥12 |
| On-disk audit | 18 JSONL records, 9 distinct actions incl. `KILL_PROPAGATED`, `FEEDBACK_ROUTED`, `MESSAGE_REJECTED` |

## M2 — abrupt disconnect + reconnect (PASS)

EA drops the socket without gateway-driven close, reconnects with the same
client identity: handshake accepted, heartbeat OK, and a subsequent OS
`START` is routed to the **new** connection.

## Evidence

* `docs/integration/evidence/phase-m/SAMPLE_AUDIT.jsonl` — the M1 run's
  actual 18-record audit trail written by the server process.
* Regression alongside: `test_gateway_server` + `test_gateway_e2e` +
  `test_premarket_scenarios` + `test_phase_m_cross_process` = 66/66 pass;
  zero orphan server processes after the run.

## Safety (unchanged)

LIVE remains **HARD-LOCKED**. Nothing in Phase M touches the broker or the
MT5 adapter; the kill-switch path demonstrated here is the same fail-closed
control the runtime uses. Next: DEMO E2E order-path TEST-06..14 inside the
London-NY session window (14:00 BKK / 07:00 UTC) on the DEMO account only.
