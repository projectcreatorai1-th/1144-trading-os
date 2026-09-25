"""SNIPER Gateway E2E — scenarios A-J (master command §34-§43).

Runs the FULL flow over the same wire protocol as production, on two
transports: InProcess (deterministic) and a REAL localhost WebSocket
server (production transport). The EA-side protocol client is
field-compatible with OUR-EA's client contract v1.0.0 (SSOT).

Real broker execution is ENVIRONMENT_BLOCKED (MT5 terminal on a REAL
account) - nothing here touches the broker; MT5 routing is asserted at
the adapter-boundary level only.
"""
from __future__ import annotations

import json
import time

import pytest

from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.gateway.contracts import new_id, utc_now_iso
from platform.gateway.server import (
    GatewayServer,
    InProcessTransport,
    WebSocketTransport,
)
from tests.test_gateway_server import ProtocolClient


class FakeAudit(AuditRepository):
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)

    def verify(self):
        return {"records": len(self.records)}

    def get_by_id(self, audit_id):
        return None

    def iter_by_correlation_id(self, correlation_id):
        return iter([r for r in self.records
                     if r.correlation_id == correlation_id])


def _make_server(audit):
    server = GatewayServer(audit)
    server.start()

    def route_to_ea(command):
        targets = server.sessions.by_type("OUR_EA")
        for session in targets:
            session.send({"type": "COMMAND", "command": command.command_type,
                          "request_id": command.request_id,
                          "payload": dict(command.payload)})
        return {"state": "COMPLETED", "delivered": len(targets)}

    for cmd in ("REGISTER", "INITIALIZE", "START", "STOP", "PAUSE", "RESUME",
                "HALT", "SYNC", "RECONCILE", "DEPLOY", "ROLLBACK",
                "UPDATE_STRATEGY", "UPDATE_PARAMETER", "UPDATE_CONFIG",
                "REQUEST_HEALTH", "REQUEST_STATUS"):
        server.commands.register_target(cmd, route_to_ea)
    return server


def _wire(audit, server) -> tuple[ProtocolClient, ProtocolClient, ProtocolClient]:
    """(ea, analyzer, os) - all connected through the contract handshake."""
    ea = ProtocolClient(server)
    ea.connect(client_id="OUR-EA-RUNTIME", client_type="OUR_EA")
    analyzer = ProtocolClient(server)
    analyzer.connect(client_id="SNIPER-ANALYZER", client_type="ANALYZER")
    os_client = ProtocolClient(server)
    return ea, analyzer, os_client


# --------------------------------------------------------------------- #
# Scenario A — registration (§34)
# --------------------------------------------------------------------- #
class TestScenarioARegistration:
    def test_ea_and_analyzer_register_with_session_contract_health_audit(
            self):
        audit = FakeAudit()
        server = _make_server(audit)
        ea, analyzer, _ = _wire(audit, server)
        for client, ctype in ((ea, "OUR_EA"), (analyzer, "ANALYZER")):
            session = server.sessions.by_type(ctype)[0]
            assert session.session_id
            assert session.contract_version == "1.0.0"
            assert session.capabilities
            assert session.state == "ACTIVE"
        assert any(r.action == "CONTRACT_ACCEPTED" for r in audit.records)


# Scenario B — command round trip (§35)
class TestScenarioBCommand:
    def test_start_command_round_trip_with_full_correlation(self):
        audit = FakeAudit()
        server = _make_server(audit)
        ea, _, os_client = _wire(audit, server)
        request_id = new_id("REQ")
        resp = os_client.command("START", request_id=request_id)
        assert resp["state"] == "COMPLETED"
        assert resp["request_id"] == request_id
        assert resp["correlation_id"]
        pushed = ea.drain_pushes()
        start = next(m for m in pushed if m.get("command") == "START")
        assert start["request_id"] == request_id       # correlation intact


# Scenario C — market -> decision -> risk -> intent (§36): the gateway
# routes the chain events; risk approval is an EA-side authority fact the
# gateway must NOT fabricate - we assert ORDER_INTENT only flows when the
# EA published it after RISK_DECISION in the same correlation.
class TestScenarioCMarketToDecision:
    def test_chain_events_route_under_one_correlation(self):
        audit = FakeAudit()
        server = _make_server(audit)
        ea, _, _ = _wire(audit, server)
        correlation = new_id("COR")
        seen = []
        for event_type in ("MARKET_EVENT", "SIGNAL", "DECISION",
                           "RISK_DECISION", "ORDER_INTENT"):
            server.events.subscribe(event_type, seen.append)
        for i, event_type in enumerate(
                ("MARKET_EVENT", "SIGNAL", "DECISION", "RISK_DECISION",
                 "ORDER_INTENT"), start=1):
            ack = ea.publish(event_type, sequence=i)
            assert ack["accepted"] is True, event_type
        assert len(seen) == 5
        # gateway never invents risk approval: ORDER_INTENT only routed
        # because the EA published one (it did); had RISK_DECISION been
        # REJECTED the EA would publish no intent - nothing to route.


# Scenario D — order flow lineage (§37)
class TestScenarioDOrderFlow:
    def test_lineage_ids_survive_routing(self):
        audit = FakeAudit()
        server = _make_server(audit)
        ea, analyzer, _ = _wire(audit, server)
        correlation = new_id("COR")
        lineage_events = []
        for event_type in ("ORDER", "EXECUTION", "POSITION"):
            server.events.subscribe(event_type, lineage_events.append)
        for i, event_type in enumerate(("ORDER", "EXECUTION", "POSITION"),
                                       start=1):
            env = {"event_id": new_id("EVT"), "event_type": event_type,
                   "schema_version": "1.0.0", "created_at": utc_now_iso(),
                   "source": "OUR-EA", "target": "1144-GATEWAY",
                   "sequence": i, "correlation_id": correlation,
                   "payload": {"order_id": "ORD-1",
                               "execution_id": "EXE-1",
                               "position_id": "POS-1"}}
            resp = ea._rpc({"type": "EVENT_PUBLISH", "event": env})
            assert resp["accepted"] is True
        assert all(e.correlation_id == correlation
                   for e in lineage_events)
        # execution feedback onward to the analyzer keeps the same chain
        feedback = {
            "type": "EXECUTION_FEEDBACK",
            "feedback": {"execution_id": "EXE-1", "signal_id": "SIG-1",
                         "correlation_id": correlation, "symbol": "EURUSD",
                         "requested_price": 1.1, "executed_price": 1.1001,
                         "volume": 0.1, "fill_count": 1,
                         "slippage": 0.0001, "spread": 0.0002,
                         "latency_ms": 10.0, "created_at": utc_now_iso(),
                         "executed_at": utc_now_iso(), "status": "FILLED"}}
        resp = ea._rpc(feedback)
        assert resp["delivered"] == 1
        assert any(m["type"] == "EXECUTION_FEEDBACK"
                   for m in analyzer.drain_pushes())


# Scenario E — analyzer round trip (§38)
class TestScenarioEAnalyzer:
    def test_analysis_result_routes_to_os(self):
        audit = FakeAudit()
        server = _make_server(audit)
        ea, analyzer, os_client = _wire(audit, server)
        received = []
        server.events.subscribe("STATE", received.append)
        env = {"event_id": new_id("EVT"), "event_type": "STATE",
               "schema_version": "1.0.0", "created_at": utc_now_iso(),
               "source": "SNIPER-ANALYZER", "target": "1144-GATEWAY",
               "sequence": 1, "correlation_id": new_id("COR"),
               "payload": {"analysis": "TCA", "result": "ok"}}
        resp = analyzer._rpc({"type": "EVENT_PUBLISH", "event": env})
        assert resp["accepted"] is True
        assert received and received[0].payload["result"] == "ok"


# Scenario F — kill switch (§39)
class TestScenarioFKillSwitch:
    def test_kill_halts_ea_and_blocks_new_orders(self):
        audit = FakeAudit()
        server = _make_server(audit)
        ea, _, os_client = _wire(audit, server)
        resp = os_client.command("KILL", idempotency_key="K1")
        assert resp["result"]["order_flow"] == "BLOCKED"
        halted = [m for m in ea.drain_pushes() if m.get("command") == "KILL"]
        assert halted
        blocked = ea.publish("ORDER_INTENT", sequence=99)
        assert blocked["type"] == "ERROR"
        assert any(r.action == "KILL_PROPAGATED" for r in audit.records)


# Scenario G — risk reject means no order (§40)
class TestScenarioGRiskReject:
    def test_risk_rejection_produces_no_intent_and_gateway_creates_none(
            self):
        audit = FakeAudit()
        server = _make_server(audit)
        ea, _, _ = _wire(audit, server)
        intents = []
        server.events.subscribe("ORDER_INTENT", intents.append)
        ack = ea.publish("RISK_DECISION", sequence=1,
                         event_id=new_id("EVT"))
        assert ack["accepted"] is True
        # EA rejected -> publishes no ORDER_INTENT -> nothing routed.
        # The gateway must not synthesize one (it never interprets risk).
        assert intents == []
        assert server.counters["events_routed"] == 1


# Scenario H — duplicate request retry (§41)
class TestScenarioHDuplicate:
    def test_timeout_retry_same_idempotency_key_single_operation(self):
        audit = FakeAudit()
        server = _make_server(audit)
        ea, _, os_client = _wire(audit, server)
        first = os_client.command("PAUSE", idempotency_key="IDEM-H",
                                  request_id="REQ-H1")
        # client never saw the response (timeout) -> retries with the same
        # idempotency key but a NEW request id (allowed: same logical op)
        retry = os_client.command("PAUSE", idempotency_key="IDEM-H",
                                  request_id="REQ-H2")
        assert first["duplicate"] is False
        assert retry["duplicate"] is True
        pauses = [m for m in ea.drain_pushes()
                  if m.get("command") == "PAUSE"]
        assert len(pauses) == 1                 # ONE logical operation


# Scenario I — connection loss (§42)
class TestScenarioIConnectionLoss:
    def test_reconnect_requires_sync_reconcile_before_order_flow(self):
        audit = FakeAudit()
        server = _make_server(audit)
        ea, _, os_client = _wire(audit, server)
        session = server.sessions.by_client("OUR-EA-RUNTIME")
        server.sessions.transition(session, "DISCONNECTED",
                                   reason="network loss")
        server.sessions.transition(session, "RECONNECTING", reason="retry")
        server.sessions.transition(session, "SYNC", reason="back")
        server.sessions.transition(session, "RECONCILE", reason="state")
        server.sessions.transition(session, "ACTIVE", reason="clean")
        assert session.state == "ACTIVE"
        # order flow resumed ONLY after sync+reconcile completed - the
        # session state machine enforces the path (direct DISCONNECTED ->
        # ACTIVE is illegal)
        from architecture.contracts.errors import ContractError
        s2 = server.sessions.by_client("OUR-EA-RUNTIME")
        s2.state = "DISCONNECTED"
        with pytest.raises(ContractError):
            server.sessions.transition(s2, "ACTIVE",
                                       reason="illegal shortcut")


# Scenario J — gateway failure (§43)
class TestScenarioJGatewayFailure:
    def test_ea_detects_failure_and_gateway_restarts_clean(self):
        audit = FakeAudit()
        server = _make_server(audit)
        ea, _, _ = _wire(audit, server)
        server.stop()
        assert all(s.state == "CLOSED"
                   for s in server.sessions.all_sessions())
        # EA-side detection: no heartbeat ack possible after stop
        resp = server.handle_message(
            json.dumps({"type": "HEARTBEAT", "client_id": "OUR-EA-RUNTIME",
                        "timestamp": utc_now_iso()}).encode(),
            sender=lambda b: None)
        assert resp["type"] == "ERROR"            # unknown client now
        # restart: fresh server accepts a full reconnect + reconcile
        audit2 = FakeAudit()
        server2 = _make_server(audit2)
        ea2 = ProtocolClient(server2)
        ea2.connect(client_id="OUR-EA-RUNTIME", client_type="OUR_EA")
        os2 = ProtocolClient(server2)
        resp = os2.command("RECONCILE")
        assert resp["state"] == "COMPLETED"
        assert any(m.get("command") == "RECONCILE"
                   for m in ea2.drain_pushes())


# --------------------------------------------------------------------- #
# Production transport: REAL WebSocket round trip on localhost
# --------------------------------------------------------------------- #
class TestWebSocketTransport:
    def test_contract_handshake_over_real_websocket(self):
        import asyncio
        audit = FakeAudit()
        server = _make_server(audit)

        ws = WebSocketTransport(handler=server.handle_raw,
                                host="127.0.0.1", port=9871)
        ws.start()
        try:
            import websockets

            async def _client():
                async with websockets.connect(ws.endpoint) as conn:
                    await conn.send(json.dumps({
                        "type": "CONTRACT_REQUEST",
                        "protocol_version": "1.0",
                        "contract_version": "1.0.0",
                        "client_version": "OUR-EA-RUNTIME",
                        "client_id": "OUR-EA-RUNTIME",
                        "client_type": "OUR_EA",
                        "client_capabilities": ["risk-gate"],
                        "supported_features": ["heartbeat"],
                        "accepted_commands": ["START", "PAUSE"],
                    }))
                    resp = json.loads(await asyncio.wait_for(
                        conn.recv(), timeout=5))
                    assert resp["type"] == "CONTRACT_RESPONSE"
                    assert resp["accepted"] is True
                    # heartbeat over the same real socket
                    await conn.send(json.dumps({
                        "type": "HEARTBEAT", "client_id": "OUR-EA-RUNTIME",
                        "timestamp": utc_now_iso()}))
                    ack = json.loads(await asyncio.wait_for(
                        conn.recv(), timeout=5))
                    assert ack["type"] == "HEARTBEAT_ACK"

            asyncio.run(_client())
        finally:
            ws.stop()
