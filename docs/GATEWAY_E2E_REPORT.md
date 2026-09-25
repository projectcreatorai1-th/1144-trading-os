# GATEWAY_E2E_REPORT.md

Status: TESTED (scenarios A-J + real WebSocket) · Transport: InProcess
(deterministic) + REAL localhost WebSocket (production transport)

| Scenario | What was proven | Result |
|---|---|---|
| A Registration | EA + Analyzer contract handshake -> session_id/contract/capabilities/ACTIVE + audit | PASS |
| B Command | OS -> START -> gateway -> EA push -> ACK; full request/correlation ids intact | PASS |
| C Market->Decision | MARKET_EVENT/SIGNAL/DECISION/RISK_DECISION/ORDER_INTENT routed under one correlation; gateway invents no risk approval | PASS |
| D Order flow lineage | ORDER/EXECUTION/POSITION events + EXECUTION_FEEDBACK to Analyzer keep correlation/ids end-to-end | PASS |
| E Analyzer round trip | Analyzer analysis-result event routes onward (OS subscribers) unmodified | PASS |
| F Kill switch | OS KILL -> EA halted (ack count), order flow BLOCKED, audit complete | PASS |
| G Risk reject | RISK_DECISION routed; NO ORDER_INTENT existed and gateway created none | PASS |
| H Duplicate | retry with same idempotency_key (new request id) = ONE logical op (exactly one PAUSE delivered) | PASS |
| I Connection loss | DISCONNECTED->RECONNECTING->SYNC->RECONCILE->ACTIVE enforced; direct shortcut REJECTED by the state machine | PASS |
| J Gateway failure | stop() closes all sessions (heartbeat -> ERROR/unknown); fresh server accepts full reconnect + RECONCILE | PASS |
| WS transport | real websockets 17 handshake + heartbeat on ws://127.0.0.1 | PASS |

Environment: SIMULATION (clearly labeled; never called DEMO). Broker
execution: ENVIRONMENT_BLOCKED (see blockers). Live EA-process cross-test:
pending the EA's WebSocket transport against this server (its client ships
Loopback/File today) — protocol is field-compatible per contract test
GATEWAY_CONTRACT_REPORT.md.
