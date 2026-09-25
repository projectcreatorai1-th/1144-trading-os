"""SNIPER Gateway SERVER (platform.gateway).

ONE gateway, multiple client sessions (OUR_EA / ANALYZER / MT5 /
TRADING_OS). Transport + routing + correlation + idempotency + ordering +
heartbeat + backpressure + kill-switch propagation + integration audit.
The gateway never interprets strategy/risk/research semantics and never
becomes an execution engine: order flow to MT5 is gated fail-closed and
real broker execution stays with the (DEMO-gated) MT5 adapter runtime.

Transports: InProcessTransport (tests/E2E) and WebSocketTransport
(production `websockets` server on localhost, run in a background thread).
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier

from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.gateway.contracts import (
    CONTRACT_VERSION,
    EVENT_SCHEMA_VERSION,
    GATEWAY_ID,
    PROTOCOL_VERSION,
    ContractRequest,
    ContractResponse,
    EventEnvelope,
    GatewayCommand,
    KILL_LEVELS,
    negotiate,
    new_id,
    utc_now_iso,
)
from platform.gateway.routers import (
    BackpressureManager,
    BackpressurePolicy,
    CommandRouter,
    CorrelationManager,
    EventRouter,
    IdempotencyManager,
    OrderingTracker,
    RoutingError,
)
from platform.gateway.session import GatewaySession, SessionRegistry

#: session states from which order flow may run
ORDER_FLOW_STATES = ("ACTIVE",)

HEARTBEAT_TIMEOUT = 10.0        # seconds without traffic -> DEGRADED
HEARTBEAT_STALE = 45.0          # -> STALE -> DISCONNECTED by sweep


class GatewayServerError(ContractError):
    rule_id = "GWS-003"


# --------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------- #
class InProcessTransport:
    """Queue-backed request/response transport speaking the SAME wire
    protocol as the WebSocket transport (deterministic tests, no sockets)."""

    def __init__(self) -> None:
        import queue
        self._inbox: "queue.Queue[bytes]" = queue.Queue()
        self._outbox: "queue.Queue[bytes]" = queue.Queue()

    # test-client side API
    def connect(self, endpoint: str = "inprocess://gateway") -> None:
        return None

    def disconnect(self) -> None:
        return None

    def send(self, data: bytes) -> None:
        self._inbox.put(data)

    def recv(self, timeout: float = 5.0) -> bytes | None:
        try:
            return self._outbox.get(timeout=timeout)
        except Exception:
            return None

    # server side API
    def server_recv(self) -> bytes | None:
        try:
            return self._inbox.get(timeout=0.05)
        except Exception:
            return None

    def server_send(self, data: bytes) -> None:
        self._outbox.put(data)


class WebSocketTransport:
    """Production transport: a `websockets` server on a background thread
    with its own event loop; each connection gets a bridge that pumps
    messages into the synchronous gateway handler."""

    def __init__(self, handler: Callable[[bytes, "WebSocketBridge"], None],
                 host: str = "127.0.0.1", port: int = 8765) -> None:
        self._handler = handler
        self._host = host
        self._port = port
        self._thread: threading.Thread | None = None
        self._loop = None
        self._server = None
        self.ready = threading.Event()
        self.error: str = ""

    def start(self) -> None:
        import asyncio

        async def _on_connection(ws):
            bridge = WebSocketBridge(ws, self)
            try:
                async for raw in ws:
                    data = raw.encode("utf-8") if isinstance(raw, str)                         else raw
                    self._handler(data, bridge.send)
            except Exception:                     # noqa: BLE001 - transport
                pass

        async def _serve():
            from websockets.asyncio.server import serve as ws_serve
            self._server = await ws_serve(
                _on_connection, self._host, self._port)
            self.ready.set()

        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(_serve())
                self._loop.run_forever()
            except Exception as exc:              # noqa: BLE001 - report
                self.error = str(exc)
                self.ready.set()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        if not self.ready.wait(timeout=10) and self.error:
            raise GatewayServerError(
                f"websocket server failed to start: {self.error}",
                location="gateway.ws.start")

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def endpoint(self) -> str:
        return f"ws://{self._host}:{self._port}"


class WebSocketBridge:
    """Adapter giving the sync handler a `send(bytes)` for one connection."""

    def __init__(self, ws, transport: WebSocketTransport) -> None:
        self._ws = ws
        self._transport = transport

    def send(self, data: bytes) -> None:
        import asyncio
        asyncio.run_coroutine_threadsafe(
            self._ws.send(data.decode("utf-8")), self._transport._loop)


# --------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------- #
class GatewayServer:
    def __init__(self, audit: AuditRepository, *,
                 backpressure: BackpressurePolicy | None = None) -> None:
        if not isinstance(audit, AuditRepository):
            raise GatewayServerError(
                "GatewayServer requires an AuditRepository (§22)",
                location="gateway.init")
        self._audit = audit
        self.sessions = SessionRegistry()
        self.events = EventRouter()
        self.commands = CommandRouter()
        self.correlation = CorrelationManager()
        self.idempotency = IdempotencyManager()
        self.ordering = OrderingTracker()
        self.backpressure = BackpressureManager(backpressure)
        self._seen_event_ids: dict[str, set[str]] = {}
        self._seen_signal_ids: set[str] = set()
        self._kill_active: dict[str, dict] = {}
        self._order_flow_open = True
        self.counters = {
            "messages_received": 0, "events_routed": 0,
            "commands_routed": 0, "rejected": 0, "dropped": 0,
            "duplicates": 0, "out_of_order": 0, "timeouts": 0,
            "reconnects": 0, "errors": 0, "kills_propagated": 0,
        }
        self.started_at = time.time()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self._audit_gateway("GATEWAY_STARTED", {"gateway": GATEWAY_ID})

    def stop(self) -> None:
        for session in self.sessions.all_sessions():
            self.sessions.force_close(session)
        self._audit_gateway("GATEWAY_STOPPED", {})

    # ------------------------------------------------------------------ #
    # message entry point (transport-agnostic)
    # ------------------------------------------------------------------ #
    def handle_raw(self, raw: bytes, send: Callable[[bytes], None]) -> None:
        self.counters["messages_received"] += 1
        reply = self.handle_message(raw, sender=send)
        if reply is not None:
            send(json.dumps(reply).encode("utf-8"))

    def handle_message(self, raw: bytes,
                       sender: Callable[[bytes], None]) -> dict | None:
        def send_dict(message: dict) -> None:
            sender(json.dumps(message).encode("utf-8"))

        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            return self._protocol_error("SCHEMA_ERROR", str(error))

        if not isinstance(message, dict):
            return self._protocol_error("SCHEMA_ERROR", "message not object")
        mtype = message.get("type", "")
        try:
            if mtype == "CONTRACT_REQUEST":
                return self._on_contract_request(message, send_dict)
            if mtype == "HEARTBEAT":
                return self._on_heartbeat(message)
            if mtype == "EVENT_PUBLISH":
                return self._on_event_publish(message)
            if mtype == "SIGNAL_PUBLISH":
                return self._on_signal_publish(message, send_dict)
            if mtype == "EXECUTION_FEEDBACK":
                return self._on_execution_feedback(message)
            if mtype == "COMMAND":
                return self._on_command(message, send_dict)
            if mtype == "STATE_SNAPSHOT":
                return {"type": "STATE_SNAPSHOT_ACK",
                        "received_at": utc_now_iso()}
            return self._protocol_error(
                "ROUTING_ERROR", f"unknown message type {mtype!r}")
        except RoutingError as error:
            return self._protocol_error("ROUTING_ERROR", str(error))
        except ContractError as error:
            return self._protocol_error("CONTRACT_ERROR", str(error))

    # ------------------------------------------------------------------ #
    # handlers
    # ------------------------------------------------------------------ #
    def _on_contract_request(self, message: dict, send_dict) -> dict:
        request = ContractRequest.from_dict(message)
        result, reason = negotiate(request)
        if result == "REJECT":
            self.counters["rejected"] += 1
            self._audit_gateway(
                "CONTRACT_REJECTED",
                {"client": request.client_id or request.client_version,
                 "reason": reason})
            return ContractResponse(accepted=False, result="REJECT",
                                    reason=reason).to_dict()
        session = self.sessions.create(
            request, send=send_dict, negotiation=result)
        self.sessions.transition(session, "ACTIVE",
                                 reason=f"negotiation {result}")
        self._audit_gateway("CONTRACT_ACCEPTED", {
            "session_id": session.session_id, "client": session.client_id,
            "client_type": session.client_type, "result": result,
            "contract_version": session.contract_version})
        return ContractResponse(accepted=True, result=result,
                                session_id=session.session_id,
                                reason=reason).to_dict()

    def _on_heartbeat(self, message: dict) -> dict:
        client_id = str(message.get("client_id", ""))
        session = self.sessions.by_client(client_id)
        if session is None:
            return {"type": "ERROR", "code": "UNAUTHORIZED",
                    "message": f"unknown client {client_id!r}"}
        session.touch()
        session.heartbeat_at = time.time()
        if session.state == "DEGRADED":
            self.sessions.transition(session, "ACTIVE", reason="heartbeat")
        return {"type": "HEARTBEAT_ACK", "server_time": utc_now_iso()}

    def _on_event_publish(self, message: dict) -> dict:
        raw_event = message.get("event")
        if not isinstance(raw_event, dict):
            return self._protocol_error("SCHEMA_ERROR", "event missing")
        try:
            envelope = EventEnvelope.from_dict(raw_event)
        except (KeyError, TypeError, ValueError) as error:
            return self._protocol_error("SCHEMA_ERROR",
                                        f"invalid envelope: {error}")

        admit = self.backpressure.admit_event()
        if admit in ("REJECT", "HALT"):
            self.counters["dropped"] += 1
            return self._protocol_error(
                "CAPACITY" if admit == "REJECT" else "INTERNAL_ERROR",
                f"backpressure {admit}")
        if admit == "THROTTLE":
            self.counters["dropped"] += 1
            return {"type": "EVENT_ACK", "accepted": False,
                    "reason": "THROTTLED"}

        source = envelope.source or "unknown"
        seen = self._seen_event_ids.setdefault(source, set())
        if envelope.event_id in seen:
            self.counters["duplicates"] += 1
            return {"type": "EVENT_ACK", "accepted": False,
                    "reason": "DUPLICATE", "event_id": envelope.event_id}
        if len(seen) > 5000:
            seen.clear()
        seen.add(envelope.event_id)

        decision = self.ordering.check(source, envelope.sequence)
        if decision.status == "DUPLICATE":
            self.counters["duplicates"] += 1
        elif decision.status == "OUT_OF_ORDER":
            self.counters["out_of_order"] += 1
        self._audit_gateway("EVENT_ORDERED", {
            "event_id": envelope.event_id, "source": source,
            "original_sequence": decision.original_sequence,
            "observed_sequence": decision.observed_sequence,
            "ordering_status": decision.status,
            "correlation_id": envelope.correlation_id})

        # fail-closed (§20): no new ORDER INTENT flow while halted
        if envelope.event_type == "ORDER_INTENT" \
                and not self._order_flow_open:
            return self._protocol_error(
                "INTERNAL_ERROR",
                "gateway fail-closed: no new order flow (unsafe state)")

        self.events.route(envelope)
        self.backpressure.release()
        self.counters["events_routed"] += 1
        return {"type": "EVENT_ACK", "accepted": True,
                "ordering_status": decision.status,
                "correlation_id": envelope.correlation_id}

    def _on_signal_publish(self, message: dict, send_dict) -> dict:
        signal_id = str(message.get("signal_id", ""))
        if not signal_id:
            return self._protocol_error("SCHEMA_ERROR", "signal_id missing")
        if signal_id in self._seen_signal_ids:
            self.counters["duplicates"] += 1
            return {"type": "SIGNAL_ACK", "accepted": False,
                    "reason": "DUPLICATE"}
        if len(self._seen_signal_ids) > 5000:
            self._seen_signal_ids.clear()
        self._seen_signal_ids.add(signal_id)

        admit = self.backpressure.admit_event()
        if admit != "OK" and admit != "WARN":
            self.counters["dropped"] += 1
            return {"type": "SIGNAL_ACK", "accepted": False,
                    "reason": f"BACKPRESSURE_{admit}"}

        delivered = 0
        for session in self.sessions.by_type("OUR_EA"):
            if session.state in ORDER_FLOW_STATES:
                send = session.send
                send({**message, "target": session.client_id})
                delivered += 1
        self._audit_gateway("SIGNAL_ROUTED", {
            "signal_id": signal_id, "delivered_to": delivered,
            "correlation_id": message.get("correlation_id", "")})
        self.backpressure.release()
        return {"type": "SIGNAL_ACK", "accepted": True,
                "delivered": delivered}

    def _on_execution_feedback(self, message: dict) -> dict:
        feedback = message.get("feedback", {})
        if not feedback.get("execution_id"):
            return self._protocol_error("SCHEMA_ERROR",
                                        "execution_id missing")
        delivered = 0
        for session in self.sessions.by_type("ANALYZER"):
            session.send({"type": "EXECUTION_FEEDBACK",
                          "feedback": feedback})
            delivered += 1
        self._audit_gateway("FEEDBACK_ROUTED", {
            "execution_id": feedback.get("execution_id"),
            "delivered_to": delivered})
        return {"type": "FEEDBACK_ACK", "accepted": True,
                "delivered": delivered}

    def _on_command(self, message: dict, send_dict) -> dict:
        command = GatewayCommand.from_dict(message)
        errors = command.validate()
        if errors:
            self.counters["rejected"] += 1
            return self._command_error(command, "SCHEMA_ERROR",
                                       "; ".join(errors))
        admit = self.backpressure.admit_command()
        if admit == "HALT":
            return self._command_error(command, "INTERNAL_ERROR",
                                       "gateway halted")
        if admit == "THROTTLE":
            return self._command_error(command, "CAPACITY", "throttled")

        sender_client = str(message.get("client_id", "TRADING_OS"))
        idem_state = self.idempotency.check(
            sender_client, command.idempotency_key, command.command_type)
        if idem_state != "NEW":
            # retry of a known logical operation: report, never re-execute
            return self._command_response(
                command, "COMPLETED", duplicate=True,
                result={"idempotency_state": idem_state})

        try:
            record = self.correlation.open(
                request_id=command.request_id,
                correlation_id=command.correlation_id,
                trace_id=command.trace_id, client_id=sender_client,
                target=command.target)
        except RoutingError as error:
            return self._command_error(command, "ROUTING_ERROR",
                                       str(error))

        self._audit_gateway("COMMAND_RECEIVED", {
            "request_id": command.request_id,
            "correlation_id": record.correlation_id,
            "command": command.command_type, "target": command.target,
            "client": sender_client})

        if command.command_type == "KILL":
            return self._on_kill(command, record)

        try:
            result = self.commands.route(command)
        except RoutingError as error:
            self.correlation.complete(command.request_id, "REJECTED")
            self.idempotency.settle(sender_client, command.idempotency_key,
                                    command.command_type, "FAILED")
            self.counters["rejected"] += 1
            return self._command_error(command, "ROUTING_ERROR",
                                       str(error))
        self.correlation.complete(command.request_id, "COMPLETED")
        self.idempotency.settle(sender_client, command.idempotency_key,
                                command.command_type, "COMPLETED")
        self.counters["commands_routed"] += 1
        response = self._command_response(command, result.get(
            "state", "COMPLETED"), result=result)
        self._audit_gateway("COMMAND_ROUTED", {
            "request_id": command.request_id,
            "command": command.command_type,
            "result": result.get("state", "COMPLETED")})
        return response

    def _on_kill(self, command: GatewayCommand, record) -> dict:
        """§21: propagate, audit, verify acknowledgement. The gateway never
        decides WHY - the OS owns the kill decision."""
        level = str(command.payload.get("level", "EA"))
        if level not in KILL_LEVELS:
            self.correlation.complete(command.request_id, "REJECTED")
            return self._command_error(command, "SCHEMA_ERROR",
                                       f"kill level {level!r}")
        acknowledged = 0
        for session in self.sessions.by_type("OUR_EA"):
            session.send({"type": "COMMAND", "command": "KILL",
                          "level": level,
                          "request_id": command.request_id,
                          "correlation_id": record.correlation_id,
                          "payload": dict(command.payload)})
            acknowledged += 1
        self._kill_active[level] = {
            "request_id": command.request_id,
            "acknowledged_by": acknowledged, "at": utc_now_iso()}
        # fail-closed: kill halts new order flow through the gateway
        self._order_flow_open = False
        self.backpressure.set_halted(False)  # control plane stays alive
        self.correlation.complete(command.request_id, "COMPLETED")
        self.idempotency.settle(command.target, command.idempotency_key,
                                "KILL", "COMPLETED")
        self.counters["kills_propagated"] += 1
        self._audit_gateway("KILL_PROPAGATED", {
            "level": level, "request_id": command.request_id,
            "acknowledged_by": acknowledged})
        return self._command_response(
            command, "COMPLETED",
            result={"acknowledged_by": acknowledged, "level": level,
                    "order_flow": "BLOCKED"})

    # ------------------------------------------------------------------ #
    # fail-closed control (§20)
    # ------------------------------------------------------------------ #
    def set_safe(self, safe: bool, reason: str) -> None:
        """OS-owned control: unsafe state = NO NEW ORDER FLOW."""
        self._order_flow_open = safe
        if not safe:
            for session in self.sessions.by_type("OUR_EA"):
                session.send({"type": "COMMAND", "command": "HALT",
                              "reason": reason})
        self._audit_gateway("FAIL_CLOSED_CHANGED",
                            {"order_flow_open": safe, "reason": reason})

    def order_flow_open(self) -> bool:
        return self._order_flow_open

    # ------------------------------------------------------------------ #
    # maintenance
    # ------------------------------------------------------------------ #
    def sweep(self) -> dict:
        """Heartbeat sweep + request timeouts. Returns evidence."""
        for session in self.sessions.all_sessions():
            status = session.heartbeat_status(
                timeout=HEARTBEAT_TIMEOUT, stale_after=HEARTBEAT_STALE)
            if status == "DEGRADED" and session.state == "ACTIVE":
                self.sessions.transition(session, "DEGRADED",
                                          reason="heartbeat aged")
        expired = self.correlation.expired()
        self.counters["timeouts"] += len(expired)
        for record in expired:
            self._audit_gateway("REQUEST_TIMEOUT", {
                "request_id": record.request_id,
                "correlation_id": record.correlation_id})
        closed = self.sessions.sweep(timeout=HEARTBEAT_TIMEOUT,
                                     stale_after=HEARTBEAT_STALE)
        return {"timed_out": len(expired), "closed_sessions": closed}

    # ------------------------------------------------------------------ #
    # diagnostics (§24) - no secrets
    # ------------------------------------------------------------------ #
    def diagnostics(self) -> dict:
        sessions = [s.metadata() for s in self.sessions.all_sessions()]
        return {
            "gateway": GATEWAY_ID,
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "contract_version": CONTRACT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "order_flow_open": self._order_flow_open,
            "active_kills": dict(self._kill_active),
            "sessions": sessions,
            "queue_depth": self.backpressure.queue_depth,
            "dropped": self.backpressure.dropped,
            "throttled": self.backpressure.throttled,
            "counters": dict(self.counters),
        }

    # ------------------------------------------------------------------ #
    def _command_response(self, command: GatewayCommand, state: str, *,
                          duplicate: bool = False, result: dict | None = None,
                          idem_state: str = "") -> dict:
        return {"type": "COMMAND_RESPONSE", "request_id": command.request_id,
                "correlation_id": command.correlation_id
                or new_id("COR"),
                "state": state, "duplicate": duplicate,
                "idempotency_state": idem_state,
                "result": result or {}}

    def _command_error(self, command: GatewayCommand, code: str,
                       detail: str) -> dict:
        self.counters["errors"] += 1
        self._audit_gateway("COMMAND_REJECTED", {
            "request_id": command.request_id, "code": code,
            "detail": detail[:200]})
        return {"type": "COMMAND_RESPONSE",
                "request_id": command.request_id, "state": "REJECTED",
                "error": {"code": code, "message": detail[:200]}}

    def _protocol_error(self, code: str, detail: str) -> dict:
        self.counters["errors"] += 1
        self.counters["rejected"] += 1
        self._audit_gateway("MESSAGE_REJECTED",
                            {"code": code, "detail": detail[:200]})
        return {"type": "ERROR", "code": code, "message": detail[:200]}

    def _audit_gateway(self, action: str, detail: dict) -> None:
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.SYSTEM, actor_id="platform.gateway",
            action=action, entity_type="gateway", entity_id=GATEWAY_ID,
            event_time=__import__(
                "architecture.contracts.time", fromlist=["utc_now"]
            ).utc_now(),
            before=None, after=detail, reason="gateway integration audit",
            source="platform.gateway", environment="SIMULATION",
            correlation_id=detail.get("correlation_id")))
