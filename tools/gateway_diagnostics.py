"""Gateway one-click diagnostics + redacted bundle export (§24/§38).

Starts the SNIPER Gateway Server on the InProcess transport with the OS
audit store, runs a self-probe (contract handshake + heartbeat), prints
the diagnostics view and writes a SECRET-FREE bundle to
runtime/gateway_diagnostic_bundle.json. Never opens MT5 or any broker.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.modules.pop("platform", None)

from platform.audit.contracts import ActorType, AuditRecord  # noqa: E402
from platform.audit.repository import AuditRepository  # noqa: E402
from platform.gateway.contracts import utc_now_iso  # noqa: E402
from platform.gateway.server import (  # noqa: E402
    GatewayServer,
    InProcessTransport,
)

SECRET_MARKERS = ("password", "secret", "token", "api_key", "apikey",
                  "credential", "login")


def _redact(node):
    if isinstance(node, dict):
        return {k: ("[redacted]" if any(m in k.lower()
                                        for m in SECRET_MARKERS) else _redact(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_redact(x) for x in node]
    return node


class _Audit(AuditRepository):
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)

    def verify(self):
        return {"records": len(self.records)}

    def get_by_id(self, audit_id):
        return None

    def iter_by_correlation_id(self, correlation_id):
        return iter(())


def main() -> int:
    audit = _Audit()
    server = GatewayServer(audit)
    server.start()

    # self-probe: full contract handshake + heartbeat over the wire protocol
    transport = InProcessTransport()
    probe_request = json.dumps({
        "type": "CONTRACT_REQUEST", "protocol_version": "1.0",
        "contract_version": "1.0.0", "client_version": "DIAG-PROBE",
        "client_id": "DIAG-PROBE", "client_type": "TRADING_OS",
        "client_capabilities": [], "supported_features": ["heartbeat"],
        "accepted_commands": []}).encode()
    server.handle_raw(probe_request, transport.server_send)
    handshake = json.loads(transport.recv(timeout=2).decode())
    server.handle_raw(json.dumps({
        "type": "HEARTBEAT", "client_id": "DIAG-PROBE",
        "timestamp": utc_now_iso()}).encode(), transport.server_send)
    heartbeat = json.loads(transport.recv(timeout=2).decode())

    diagnostics = server.diagnostics()
    bundle = _redact({
        "generated_at": utc_now_iso(),
        "probe": {"handshake": handshake, "heartbeat": heartbeat},
        "diagnostics": diagnostics,
    })
    out = ROOT / "runtime" / "gateway_diagnostic_bundle.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

    print("=== SNIPER Gateway diagnostics (SIMULATION; no broker) ===")
    print(f"handshake accepted : {handshake.get('accepted')}")
    print(f"heartbeat          : {heartbeat.get('type')}")
    print(f"order_flow_open    : {diagnostics['order_flow_open']}")
    print(f"sessions           : {len(diagnostics['sessions'])}")
    print(f"counters           : {diagnostics['counters']}")
    print(f"bundle (redacted)  -> {out}")
    server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
