# GATEWAY_CONTRACT_REPORT.md

Status: CONTRACT-ALIGNED (v1.0.0) · Authorities: SNIPER Analyzer
core/gateway/contracts.py (contract authority) + OUR-EA
our_ea/gateway/contracts.py (client SSOT) — BOTH UNTOUCHED.

Alignment evidence (field-for-field, verified against the authorities):
- Wire types: CONTRACT_REQUEST/RESPONSE, HEARTBEAT/_ACK, EVENT_PUBLISH/
  EVENT_ACK, EXECUTION_FEEDBACK/_ACK, SIGNAL_PUBLISH/_ACK, COMMAND/
  COMMAND_RESPONSE, STATE_SNAPSHOT, ERROR — same names/shapes as the EA
  client's `_send` protocol.
- EventEnvelope: event_id/event_type/schema_version/created_at/source/
  target/sequence/correlation_id/payload — identical fields & meanings.
- Negotiation rule the EA enforces client-side (contract major.minor must
  match + accepted flag) is honored server-side: server always answers
  CONTRACT_RESPONSE with contract_version "1.0.0".
- SIGNAL payload fields mirror GatewaySignal v1.0.0 (validated client-side
  by the EA; the gateway routes without interpretation).

DOCUMENTED CONTRACT CONFLICT (per §52/§61 — not silently resolved):
The EA SSOT declares command `UPDATE_CONFIG`; master §11 lists
`UPDATE_STRATEGY`/`UPDATE_PARAMETER`. Resolution at the correct owner
(the gateway's routable set): both spellings are routed as the same
command family. No semantics interpreted; neither EA nor Analyzer changed.

Contract tests: negotiation ACCEPT/DEGRADED/REJECT; rejection audited;
incompatible contract REJECT; degraded on unknown commands; reconnect =
one session per client. All in tests/test_gateway_server.py (29) +
E2E (11).
