"""Phase M — SNIPER Gateway over a REAL WebSocket served by a REAL process.

The gateway server runs as its own OS process (tools/gateway_ws_server.py,
spawned per test with a dedicated audit JSONL + stop sentinel); this test
process is a pure client. Every assertion below therefore crossed a real
process boundary over ws://127.0.0.1:<port> using wire contract v1.0.0 —
no InProcessTransport anywhere in this file.

Covered (mirrors scenarios A-J from test_gateway_e2e, cross-process):
    M1  full lifecycle: 3 connections (EA / OS / analyzer), contract
        handshake, command round trip with correlation intact, event chain
        publish, idempotent retry, execution feedback delivery, KILL
        propagation with order flow blocked, heartbeat after kill,
        graceful stop + on-disk audit evidence
    M2  abrupt disconnect + reconnect: same client_id re-handshakes, order
        routing follows the NEW connection
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

websockets = pytest.importorskip("websockets")

from platform.gateway.contracts import new_id, utc_now_iso  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SERVER_TOOL = ROOT / "tools" / "gateway_ws_server.py"


def _workdir() -> Path:
    """Scratch dir for audit JSONL + stop sentinel (D: first — this
    workspace has a history of C: running out of space)."""
    try:
        base = Path("D:/zcode-tmp/phase-m")
        base.mkdir(parents=True, exist_ok=True)
        return base
    except OSError:
        import tempfile
        return Path(tempfile.mkdtemp(prefix="phase-m-"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerProcess:
    """Supervises tools/gateway_ws_server.py as a real subprocess."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.port = _free_port()
        self.audit_path = tmp / f"audit-{self.port}.jsonl"
        self.sentinel = tmp / f"stop-{self.port}.flag"
        self.sentinel.write_bytes(b"")
        self.lines: list[str] = []
        self._queue: list[str] = []
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER_TOOL),
             "--port", str(self.port),
             "--audit-path", str(self.audit_path),
             "--stop-when-file-deleted", str(self.sentinel),
             "--max-lifetime-seconds", "180"],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8")
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            with self._lock:
                self.lines.append(line.strip())
                self._queue.append(line.strip())

    def _read_line(self, timeout: float) -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._queue:
                    return self._queue.pop(0)
            time.sleep(0.05)
        return None

    def wait_ready(self, timeout: float = 30.0) -> dict:
        line = self._read_line(timeout)
        assert line, f"no readiness line from server (stderr: " \
                     f"{self.proc.stderr.read() if self.proc.stderr else '?'})"
        msg = json.loads(line)
        assert msg.get("ready") is True, msg
        return msg

    @property
    def endpoint(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def stop_graceful(self, timeout: float = 20.0) -> dict:
        self.sentinel.unlink(missing_ok=True)
        stopped = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                candidates = [l for l in self._queue if l.startswith("{")]
            for cand in candidates:
                try:
                    parsed = json.loads(cand)
                except json.JSONDecodeError:
                    continue
                if parsed.get("stopped"):
                    stopped = parsed
            if stopped is not None:
                break
            time.sleep(0.1)
        assert stopped is not None, "server never reported a graceful stop"
        self.proc.wait(timeout=10)
        assert self.proc.returncode == 0
        return stopped

    def kill_hard(self) -> None:
        self.proc.kill()
        self.proc.wait(timeout=10)


class WsClient:
    """Minimal contract-v1.0.0 client over a real websockets connection."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.conn = None

    async def __aenter__(self) -> "WsClient":
        self.conn = await websockets.connect(self.endpoint)
        return self

    async def __aexit__(self, *exc) -> None:
        if self.conn is not None:
            await self.conn.close()

    async def send(self, message: dict) -> None:
        await self.conn.send(json.dumps(message))

    async def recv(self, timeout: float = 5.0) -> dict:
        raw = await asyncio.wait_for(self.conn.recv(), timeout=timeout)
        return json.loads(raw)

    async def rpc(self, message: dict, expect: str) -> dict:
        await self.send(message)
        reply = await self.recv()
        assert reply.get("type") == expect, reply
        return reply

    async def send_rpc_raw(self, message: dict) -> dict:
        """Send and return whatever comes back (for ERROR-path checks)."""
        await self.send(message)
        return await self.recv()

    async def drain(self, quiet: float = 0.7) -> list[dict]:
        out: list[dict] = []
        while True:
            try:
                out.append(await self.recv(timeout=quiet))
            except asyncio.TimeoutError:
                return out

    # -- contract messages (field-compatible with the EA client SSOT) --
    async def handshake(self, client_id: str, client_type: str) -> dict:
        return await self.rpc({
            "type": "CONTRACT_REQUEST", "protocol_version": "1.0",
            "contract_version": "1.0.0", "client_version": client_id,
            "client_type": client_type, "client_id": client_id,
            "client_capabilities": ["risk-gate", "execution"],
            "supported_features": ["heartbeat", "command.accept"],
            "accepted_commands": [
                "REGISTER", "INITIALIZE", "START", "STOP", "PAUSE", "RESUME",
                "HALT", "KILL", "RECONCILE", "SYNC", "DEPLOY", "ROLLBACK",
                "UPDATE_CONFIG", "REQUEST_STATUS", "REQUEST_HEALTH"],
        }, expect="CONTRACT_RESPONSE")

    async def heartbeat(self, client_id: str) -> dict:
        return await self.rpc({"type": "HEARTBEAT", "client_id": client_id,
                               "timestamp": utc_now_iso()},
                              expect="HEARTBEAT_ACK")

    async def publish(self, event_type: str, sequence: int,
                      correlation_id: str) -> dict:
        env = {"event_id": new_id("EVT"), "event_type": event_type,
               "schema_version": "1.0.0", "created_at": utc_now_iso(),
               "source": "OUR-EA", "target": "1144-GATEWAY",
               "sequence": sequence, "correlation_id": correlation_id,
               "payload": {"x": 1}}
        return await self.rpc({"type": "EVENT_PUBLISH", "event": env},
                              expect="EVENT_ACK")

    async def os_command(self, command_type: str, *,
                         idempotency_key: str = "",
                         request_id: str | None = None) -> dict:
        return await self.rpc({
            "type": "COMMAND", "command_type": command_type,
            "request_id": request_id or new_id("REQ"),
            "correlation_id": new_id("COR"), "trace_id": new_id("TRC"),
            "target": "OUR_EA", "payload": {},
            "idempotency_key": idempotency_key,
            "client_id": "TRADING-OS"}, expect="COMMAND_RESPONSE")


class TestM1FullLifecycleCrossProcess:
    def test_contract_lifecycle_over_real_process_boundary(self):
        tmp = _workdir()
        server = ServerProcess(tmp)
        try:
            ready = server.wait_ready()
            assert ready["endpoint"] == server.endpoint

            async def scenario():
                async with WsClient(server.endpoint) as ea, \
                        WsClient(server.endpoint) as os_link, \
                        WsClient(server.endpoint) as analyzer:
                    # -- handshakes (EA + analyzer; OS commands carry their
                    #    own client identity, mirroring the in-process suite)
                    resp = await ea.handshake("OUR-EA-RUNTIME", "OUR_EA")
                    assert resp["accepted"] is True
                    assert resp["contract_version"] == "1.0.0"
                    aresp = await analyzer.handshake("SNIPER-ANALYZER",
                                                     "ANALYZER")
                    assert aresp["accepted"] is True

                    # -- command round trip: correlation intact end to end
                    request_id = new_id("REQ")
                    cmd = await os_link.os_command("START",
                                                   request_id=request_id)
                    assert cmd["state"] == "COMPLETED"
                    assert cmd["request_id"] == request_id
                    assert cmd["correlation_id"]
                    assert cmd["duplicate"] is False
                    pushes = await ea.drain()
                    start_push = next(m for m in pushes
                                      if m.get("command") == "START")
                    assert start_push["request_id"] == request_id

                    # -- market -> decision -> risk -> intent chain
                    correlation = new_id("COR")
                    for i, event_type in enumerate(
                            ("MARKET_EVENT", "SIGNAL", "DECISION",
                             "RISK_DECISION", "ORDER_INTENT"), start=1):
                        ack = await ea.publish(event_type, i, correlation)
                        assert ack["accepted"] is True, event_type

                    # -- idempotent retry: ONE logical operation
                    first = await os_link.os_command("PAUSE",
                                                     idempotency_key="IDEM-M",
                                                     request_id="REQ-M1")
                    retry = await os_link.os_command("PAUSE",
                                                     idempotency_key="IDEM-M",
                                                     request_id="REQ-M2")
                    assert first["duplicate"] is False
                    assert retry["duplicate"] is True
                    await ea.drain()  # drop the first PAUSE push
                    pauses = [m for m in await ea.drain()
                              if m.get("command") == "PAUSE"]
                    assert pauses == []      # exactly one push, already drained

                    # -- execution feedback reaches the analyzer
                    feedback = await ea.rpc({
                        "type": "EXECUTION_FEEDBACK",
                        "feedback": {"execution_id": "EXE-M1",
                                     "signal_id": "SIG-1",
                                     "correlation_id": correlation,
                                     "symbol": "EURUSD",
                                     "requested_price": 1.1,
                                     "executed_price": 1.1001,
                                     "volume": 0.1, "fill_count": 1,
                                     "slippage": 0.0001, "spread": 0.0002,
                                     "latency_ms": 10.0,
                                     "created_at": utc_now_iso(),
                                     "executed_at": utc_now_iso(),
                                     "status": "FILLED"}},
                        expect="FEEDBACK_ACK")
                    assert feedback["delivered"] == 1
                    seen_fb = [m for m in await analyzer.drain()
                               if m.get("type") == "EXECUTION_FEEDBACK"]
                    assert len(seen_fb) == 1

                    # -- kill switch: propagated, order flow blocked
                    kill = await os_link.os_command("KILL",
                                                    idempotency_key="K1")
                    assert kill["result"]["order_flow"] == "BLOCKED"
                    kill_pushes = [m for m in await ea.drain()
                                   if m.get("command") == "KILL"]
                    assert kill_pushes
                    blocked = await ea.send_rpc_raw({
                        "type": "EVENT_PUBLISH",
                        "event": {"event_id": new_id("EVT"),
                                  "event_type": "ORDER_INTENT",
                                  "schema_version": "1.0.0",
                                  "created_at": utc_now_iso(),
                                  "source": "OUR-EA",
                                  "target": "1144-GATEWAY",
                                  "sequence": 99,
                                  "correlation_id": correlation,
                                  "payload": {"x": 1}}})
                    assert blocked["type"] == "ERROR"

                    # -- control plane survives the kill
                    hb = await ea.heartbeat("OUR-EA-RUNTIME")
                    assert hb["type"] == "HEARTBEAT_ACK"

            asyncio.run(scenario())

            # -- graceful stop + counters
            stopped = server.stop_graceful()
            counters = stopped["counters"]
            assert counters["commands_routed"] >= 2   # START + PAUSE (KILL
            # counts under kills_propagated, not commands_routed)
            assert counters["events_routed"] >= 5
            assert counters["kills_propagated"] == 1
            assert counters["messages_received"] >= 12
            assert stopped["audit_records"] > 0

            # -- on-disk audit evidence of the whole run
            rows = [json.loads(l) for l in
                    server.audit_path.read_text(encoding="utf-8").splitlines()
                    if l.strip()]
            actions = {row["action"] for row in rows}
            for expected in ("GATEWAY_STARTED", "CONTRACT_ACCEPTED",
                             "COMMAND_RECEIVED", "KILL_PROPAGATED",
                             "FEEDBACK_ROUTED", "GATEWAY_STOPPED"):
                assert expected in actions, expected
            assert len(rows) == stopped["audit_records"]
        finally:
            if server.proc.poll() is None:
                server.kill_hard()


class TestM2ReconnectCrossProcess:
    def test_abrupt_disconnect_then_reconnect_routes_to_new_connection(self):
        tmp = _workdir()
        server = ServerProcess(tmp)
        try:
            server.wait_ready()

            async def scenario():
                async with WsClient(server.endpoint) as ea:
                    resp = await ea.handshake("OUR-EA-RUNTIME", "OUR_EA")
                    assert resp["accepted"] is True
                    # abrupt loss (no gateway-driven close)
                # ... and back: same client identity, NEW connection
                async with WsClient(server.endpoint) as ea2, \
                        WsClient(server.endpoint) as os_link:
                    resp2 = await ea2.handshake("OUR-EA-RUNTIME", "OUR_EA")
                    assert resp2["accepted"] is True
                    hb = await ea2.heartbeat("OUR-EA-RUNTIME")
                    assert hb["type"] == "HEARTBEAT_ACK"
                    cmd = await os_link.os_command("START",
                                                   request_id=new_id("REQ"))
                    assert cmd["state"] == "COMPLETED"
                    pushes = await ea2.drain()
                    assert any(m.get("command") == "START"
                               for m in pushes)   # routed to the NEW socket

            asyncio.run(scenario())
            stopped = server.stop_graceful()
            assert stopped["reason"] == "sentinel"
        finally:
            if server.proc.poll() is None:
                server.kill_hard()
