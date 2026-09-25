# GATEWAY_FAILURE_RECOVERY_REPORT.md

Status: TESTED (gateway-level failure/recovery paths)

Covered with evidence (tests/test_gateway_server.py + test_gateway_e2e.py):
- Duplicate event (id + sequence) -> DUPLICATE ack, not re-routed
- Out-of-order / missing sequence -> detected + audited evidence
  (original/observed/ordering_status), never silently reordered
- Message storm -> backpressure THROTTLE/REJECT/HALT (bounded windows,
  bounded queues; no unbounded memory)
- Stale heartbeat -> DEGRADED -> sweep DISCONNECTS (process-alive is
  never treated as connected)
- Request timeout -> TIMEOUT state + audit record
- KILL -> immediate NO NEW ORDER FLOW (ORDER_INTENT refused) + HALT to EA
- Gateway stop/crash -> all sessions CLOSED; heartbeat gets ERROR;
  restart -> clean reconnect + RECONCILE (Scenario J)
- Reconnect ordering -> SYNC+RECONCILE before ACTIVE enforced by the
  registered state machine (Scenario I)

Recovery baseline FAILURE->FREEZE->RECONNECT->SYNC->RECONCILE->VALIDATE->
RESUME/HALT is enforced structurally (state machine) and proven in E2E.
Cross-PROCESS failure drills (killing a real EA process / network cable
pull) and broker-side recovery: ENVIRONMENT_BLOCKED (no DEMO runtime).
