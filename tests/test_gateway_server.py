"""SNIPER Gateway SERVER — unit + contract tests (platform.gateway).

The protocol-exact test client mirrors the OUR-EA client's wire messages
field-for-field (contract v1.0.0 SSOT) — it does NOT import the sibling
project.
"""
from __future__ import annotations

import json
import time

import pytest

from architecture.contracts.errors import ContractError
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.gateway.contracts import (
    CONTRACT_VERSION,
    ContractRequest,
    EventEnvelope,
    GatewayCommand,
    negotiate,
    new_id,
    utc_now_iso,
)
from platform.gateway.routers import (
    BackpressureManager,
    BackpressurePolicy,
    CommandRouter,
    CorrelationManager,
    IdempotencyManager,
    OrderingTracker,
    RoutingError,
)
from platform.gateway.server import GatewayServer, InProcessTransport
from platform.gateway.session import SessionRegistry


class FakeAudit(AuditRepository):
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)

    def verify(self):
        return {"records": len(self.records)}

    def get_by_id(self, audit_id):
        return next((r for r in self.records if r.audit_id == audit_id), None)

    def iter_by_correlation_id(self, correlation_id):
        return iter([r for r in self.records
                     if r.correlation_id == correlation_id])


# --------------------------------------------------------------------- #
# contract-exact EA-side helper
# --------------------------------------------------------------------- #
class ProtocolClient:
    """Speaks the EA's exact wire messages over InProcessTransport."""

    def __init__(self, server: GatewayServer):
        self.transport = InProcessTransport()
        self.server = server
        self.session_id = ""
        self.inbox: list[dict] = []

    def _rpc(self, message: dict) -> dict | None:
        self.server.handle_raw(
            json.dumps(message).encode("utf-8"), self.transport.server_send)
        raw = self.transport.recv(timeout=2.0)
        return json.loads(raw.decode("utf-8")) if raw else None

    def connect(self, *, client_id="OUR-EA-RUNTIME",
                client_type="OUR_EA", accepted_commands=None) -> dict:
        return self._rpc({
            "type": "CONTRACT_REQUEST", "protocol_version": "1.0",
            "contract_version": "1.0.0", "client_version": client_id,
            "client_type": client_type, "client_id": client_id,
            "client_capabilities": ["risk-gate", "execution"],
            "supported_features": ["heartbeat", "command.accept"],
            "accepted_commands": accepted_commands or [
                "REGISTER", "INITIALIZE", "START", "STOP", "PAUSE", "RESUME",
                "HALT", "KILL", "RECONCILE", "SYNC", "DEPLOY", "ROLLBACK",
                "UPDATE_CONFIG", "REQUEST_STATUS", "REQUEST_HEALTH"],
        })

    def heartbeat(self, client_id="OUR-EA-RUNTIME") -> dict:
        return self._rpc({"type": "HEARTBEAT", "client_id": client_id,
                          "timestamp": utc_now_iso()})

    def publish(self, event_type: str, sequence: int, *,
                source="OUR-EA", event_id=None) -> dict:
        env = {
            "event_id": event_id or new_id("EVT"),
            "event_type": event_type, "schema_version": "1.0.0",
            "created_at": utc_now_iso(), "source": source,
            "target": "1144-GATEWAY", "sequence": sequence,
            "correlation_id": new_id("COR"), "payload": {"x": 1}}
        return self._rpc({"type": "EVENT_PUBLISH", "event": env})

    def command(self, command_type: str, *, target="OUR_EA",
                idempotency_key="", request_id=None) -> dict:
        return self._rpc({
            "type": "COMMAND", "command_type": command_type,
            "request_id": request_id or new_id("REQ"),
            "correlation_id": new_id("COR"), "trace_id": new_id("TRC"),
            "target": target, "payload": {},
            "idempotency_key": idempotency_key,
            "client_id": "TRADING-OS"})

    def drain_pushes(self) -> list[dict]:
        out = []
        while True:
            raw = self.transport.recv(timeout=0.05)
            if raw is None:
                return out
            out.append(json.loads(raw.decode("utf-8")))


@pytest.fixture()
def audit():
    return FakeAudit()


@pytest.fixture()
def server(audit):
    srv = GatewayServer(audit)
    srv.start()
    # OS-registered routes for routed commands (§11): forward to EA session
    def route_to_ea(command: GatewayCommand) -> dict:
        targets = srv.sessions.by_type("OUR_EA")
        for session in targets:
            session.send({"type": "COMMAND",
                          "command": command.command_type,
                          "request_id": command.request_id,
                          "payload": dict(command.payload)})
        return {"state": "COMPLETED",
                "delivered": len(targets)}
    for cmd in ("START", "PAUSE", "RESUME", "HALT", "SYNC", "RECONCILE",
                "REQUEST_HEALTH", "REQUEST_STATUS"):
        srv.commands.register_target(cmd, route_to_ea)
    return srv


# --------------------------------------------------------------------- #
class TestContractNegotiation:
    def test_accept_compatible_client(self, server):
        client = ProtocolClient(server)
        resp = client.connect()
        assert resp["type"] == "CONTRACT_RESPONSE"
        assert resp["accepted"] is True and resp["result"] == "ACCEPT"
        assert resp["contract_version"] == CONTRACT_VERSION
        assert server.sessions.by_client("OUR-EA-RUNTIME").state == "ACTIVE"

    def test_reject_incompatible_contract(self, server):
        client = ProtocolClient(server)
        resp = client._rpc({
            "type": "CONTRACT_REQUEST", "protocol_version": "1.0",
            "contract_version": "9.9.9", "client_version": "X",
            "client_capabilities": [], "supported_features": [],
            "accepted_commands": []})
        assert resp["accepted"] is False and resp["result"] == "REJECT"
        # every rejection is audited (§8)
        actions = [r.action for r in server._audit.records
                   if r.action == "CONTRACT_REJECTED"]
        assert actions

    def test_degraded_on_unknown_commands(self, server):
        client = ProtocolClient(server)
        resp = client.connect(accepted_commands=["START", "WARP_DRIVE"])
        assert resp["result"] == "DEGRADED" and resp["accepted"] is True

    def test_reconnect_closes_old_session_one_per_client(self, server):
        client = ProtocolClient(server)
        client.connect()
        first = server.sessions.by_client("OUR-EA-RUNTIME")
        client.connect()
        assert server.sessions.by_client("OUR-EA-RUNTIME") is not first


class TestHeartbeatAndSweep:
    def test_heartbeat_ack_and_unknown_client_rejected(self, server):
        client = ProtocolClient(server)
        client.connect()
        ack = client.heartbeat()
        assert ack["type"] == "HEARTBEAT_ACK"
        unknown = client.heartbeat(client_id="GHOST")
        assert unknown["type"] == "ERROR"
        assert unknown["code"] == "UNAUTHORIZED"

    def test_stale_session_disconnected_by_sweep(self, server):
        client = ProtocolClient(server)
        client.connect()
        session = server.sessions.by_client("OUR-EA-RUNTIME")
        session.last_seen = time.time() - 120      # simulate silence
        session.heartbeat_at = time.time() - 120
        server.sweep()
        assert session.state in ("DISCONNECTED", "CLOSED")

    def test_request_timeout_audited(self, server):
        server.correlation.open(request_id="REQ-1", correlation_id="C",
                                trace_id="T", client_id="OS", target="EA")
        server.correlation._requests["REQ-1"].created_at = time.time() - 999
        result = server.sweep()
        assert result["timed_out"] == 1
        assert any(r.action == "REQUEST_TIMEOUT"
                   for r in server._audit.records)


class TestEvents:
    def test_event_routed_with_ordering_evidence(self, server, audit):
        received = []
        server.events.subscribe("ORDER_FILLED", received.append)
        client = ProtocolClient(server)
        client.connect()
        ack = client.publish("ORDER_FILLED", sequence=1)
        assert ack["accepted"] is True and ack["ordering_status"] == "OK"
        assert len(received) == 1
        ordered = [r for r in audit.records if r.action == "EVENT_ORDERED"]
        assert ordered[0].after["ordering_status"] == "OK"

    def test_duplicate_event_id_and_sequence(self, server):
        client = ProtocolClient(server)
        client.connect()
        first = client.publish("EA_HEARTBEAT", sequence=1,
                               event_id="EVT-ABC123DEF456")
        dup = client.publish("EA_HEARTBEAT", sequence=1,
                             event_id="EVT-ABC123DEF456")
        assert first["accepted"] is True
        assert dup["accepted"] is False and dup["reason"] == "DUPLICATE"
        assert server.counters["duplicates"] == 1

    def test_out_of_order_detected_not_hidden(self, server):
        client = ProtocolClient(server)
        client.connect()
        client.publish("RISK_UPDATED", sequence=5)
        ack = client.publish("RISK_UPDATED", sequence=3)
        assert ack["ordering_status"] == "OUT_OF_ORDER"
        assert server.counters["out_of_order"] == 1

    def test_missing_sequence_gap_reported(self, server):
        client = ProtocolClient(server)
        client.connect()
        client.publish("STATE", sequence=1)
        ack = client.publish("STATE", sequence=4)
        assert ack["ordering_status"] == "MISSING"

    def test_non_routable_event_type_rejected(self, server):
        client = ProtocolClient(server)
        client.connect()
        resp = client.publish("NOT_A_CONTRACT_EVENT", sequence=1)
        assert resp["type"] == "ERROR" and resp["code"] == "ROUTING_ERROR"

    def test_malformed_message_rejected_no_routing(self, server):
        resp = server.handle_message(b"not json {", sender=lambda b: None)
        assert resp["type"] == "ERROR" and resp["code"] == "SCHEMA_ERROR"


class TestCommands:
    def test_command_routed_with_correlation(self, server):
        ea = ProtocolClient(server)
        ea.connect()
        os_client = ProtocolClient(server)   # OS sends, EA receives push
        resp = os_client.command("START", request_id="REQ-START-1")
        assert resp["type"] == "COMMAND_RESPONSE"
        assert resp["state"] == "COMPLETED"
        assert resp["request_id"] == "REQ-START-1"   # response refs request
        pushed = ea.drain_pushes()
        assert any(m.get("command") == "START" for m in pushed)

    def test_idempotency_retry_is_one_logical_operation(self, server):
        ea = ProtocolClient(server)
        ea.connect()
        os_client = ProtocolClient(server)
        first = os_client.command("PAUSE", idempotency_key="IDEM-1",
                                  request_id="REQ-A")
        retry = os_client.command("PAUSE", idempotency_key="IDEM-1",
                                  request_id="REQ-B")
        assert first["duplicate"] is False
        assert retry["duplicate"] is True            # not re-executed
        pushed = ea.drain_pushes()
        # only ONE PAUSE reached the EA
        assert sum(1 for m in pushed if m.get("command") == "PAUSE") == 1

    def test_unknown_command_rejected(self, server):
        client = ProtocolClient(server)
        client.connect()
        resp = client.command("WARP_DRIVE")
        assert resp["state"] == "REJECTED"

    def test_duplicate_request_id_rejected(self, server):
        ea = ProtocolClient(server)
        ea.connect()
        os_client = ProtocolClient(server)
        os_client.command("SYNC", request_id="REQ-SAME")
        resp = os_client.command("SYNC", request_id="REQ-SAME")
        assert resp["state"] == "REJECTED"
        assert resp["error"]["code"] == "ROUTING_ERROR"


class TestKillSwitch:
    def test_kill_propagates_and_blocks_order_flow(self, server):
        ea = ProtocolClient(server)
        ea.connect()
        os_client = ProtocolClient(server)
        resp = os_client.command(
            "KILL",
            request_id="REQ-KILL-1",
            idempotency_key="KILL-1")
        assert resp["state"] == "COMPLETED"
        assert resp["result"]["order_flow"] == "BLOCKED"
        assert resp["result"]["acknowledged_by"] == 1
        pushed = ea.drain_pushes()
        assert any(m.get("command") == "KILL" for m in pushed)
        assert server.order_flow_open() is False
        # §20: no new order flow while killed
        blocked = ea.publish("ORDER_INTENT", sequence=1)
        assert blocked["type"] == "ERROR"
        assert "no new order flow" in blocked["message"]
        assert any(r.action == "KILL_PROPAGATED"
                   for r in server._audit.records)

    def test_invalid_kill_level_rejected(self, server):
        client = ProtocolClient(server)
        client.connect()
        message = {
            "type": "COMMAND", "command_type": "KILL",
            "request_id": "REQ-K-2", "correlation_id": "C", "trace_id": "T",
            "target": "OUR_EA", "payload": {"level": "UNIVERSE"},
            "client_id": "TRADING-OS"}
        resp = client._rpc(message)
        assert resp["state"] == "REJECTED"

    def test_set_safe_sends_halt_to_ea(self, server):
        client = ProtocolClient(server)
        client.connect()
        server.set_safe(False, "reconciliation unknown")
        pushed = client.drain_pushes()
        assert any(m.get("command") == "HALT" for m in pushed)


class TestSignalsAndFeedback:
    def test_signal_routed_to_ea_only_when_active(self, server):
        ea = ProtocolClient(server)
        ea.connect()
        signal = {
            "type": "SIGNAL_PUBLISH", "signal_id": new_id("SIG"),
            "strategy_id": "strat-1", "symbol": "EURUSD",
            "direction": "BUY", "signal_type": "ENTRY",
            "entry_reference": 1.1, "stop_reference": 1.09,
            "target_reference": 1.12, "confidence": 0.8,
            "created_at": utc_now_iso(),
            "expires_at": "2999-01-01T00:00:00+00:00",
            "correlation_id": "COR-1"}
        resp = server.handle_message(json.dumps(signal).encode(),
                                     sender=ea.transport.server_send)
        assert resp["type"] == "SIGNAL_ACK" and resp["accepted"] is True
        assert resp["delivered"] == 1
        pushed = ea.drain_pushes()
        assert any(m["type"] == "SIGNAL_PUBLISH" for m in pushed)
        # duplicate signal dropped
        resp2 = server.handle_message(json.dumps(signal).encode(),
                                      sender=ea.transport.server_send)
        assert resp2["accepted"] is False

    def test_execution_feedback_routed_to_analyzer(self, server):
        analyzer = ProtocolClient(server)
        analyzer.connect(client_id="SNIPER-ANALYZER", client_type="ANALYZER")
        ea = ProtocolClient(server)
        ea.connect()
        feedback = {
            "type": "EXECUTION_FEEDBACK",
            "feedback": {"execution_id": new_id("EXE"), "signal_id": "S",
                         "correlation_id": "C", "symbol": "EURUSD",
                         "requested_price": 1.1, "executed_price": 1.1001,
                         "volume": 0.1, "fill_count": 1, "slippage": 0.0001,
                         "spread": 0.0002, "latency_ms": 12.0,
                         "created_at": utc_now_iso(),
                         "executed_at": utc_now_iso(), "status": "FILLED"}}
        resp = ea._rpc(feedback)
        assert resp["type"] == "FEEDBACK_ACK" and resp["delivered"] == 1
        pushed = analyzer.drain_pushes()
        assert any(m["type"] == "EXECUTION_FEEDBACK" for m in pushed)


class TestBackpressure:
    def test_event_rate_limit_throttles(self):
        manager = BackpressureManager(BackpressurePolicy(
            max_queue_size=100, max_events_per_second=5,
            max_commands_per_second=5))
        outcomes = [manager.admit_event() for _ in range(20)]
        assert "THROTTLE" in outcomes

    def test_queue_overflow_rejects(self):
        manager = BackpressureManager(BackpressurePolicy(
            max_queue_size=3, max_events_per_second=10000,
            max_commands_per_second=10000))
        outcomes = [manager.admit_event() for _ in range(10)]
        assert "REJECT" in outcomes
        assert manager.dropped > 0

    def test_halt_blocks_everything(self):
        manager = BackpressureManager()
        manager.set_halted(True)
        assert manager.admit_event() == "HALT"
        assert manager.admit_command() == "HALT"


class TestUnitPieces:
    def test_ordering_tracker_semantics(self):
        tracker = OrderingTracker()
        assert tracker.check("A", 1).status == "OK"
        assert tracker.check("A", 1).status == "DUPLICATE"
        assert tracker.check("A", 5).status == "MISSING"
        assert tracker.check("A", 3).status == "OUT_OF_ORDER"

    def test_idempotency_lifecycle(self):
        manager = IdempotencyManager()
        assert manager.check("c", "k", "PAUSE") == "NEW"
        assert manager.check("c", "k", "PAUSE") == "PROCESSING"
        manager.settle("c", "k", "PAUSE", "COMPLETED")
        assert manager.check("c", "k", "PAUSE") == "COMPLETED"

    def test_correlation_rejects_unknown_response(self):
        manager = CorrelationManager()
        with pytest.raises(RoutingError):
            manager.complete("REQ-NOPE", "COMPLETED")

    def test_command_router_refuses_non_contract_command(self):
        router = CommandRouter()
        with pytest.raises(RoutingError):
            router.register_target("WARP", lambda c: {})
