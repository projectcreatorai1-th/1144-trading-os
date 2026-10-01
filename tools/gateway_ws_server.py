"""Phase M — run the 1144 gateway as a REAL OS process serving WebSocket.

Boots GatewayServer (wire contract v1.0.0) + WebSocketTransport, appends
every audit record to a JSONL file on disk, and reports lifecycle state on
stdout as single-line JSON so harnesses and operators can supervise:

    {"ready": true, "endpoint": "ws://127.0.0.1:8765", "pid": ...}
    {"stopped": true, "reason": "sentinel", "counters": {...}}

Stop controls (whichever fires first):
    --stop-when-file-deleted PATH   graceful stop when PATH disappears
    --max-lifetime-seconds N        safety stop so no orphan process
                                    survives a crashed harness (default 600)

The wire protocol is EXACTLY platform/gateway/contracts.py v1.0.0; this
tool adds no protocol surface of its own.

Usage:
    python tools/gateway_ws_server.py --port 8765 \
        --audit-path runtime/gateway_audit.jsonl \
        --stop-when-file-deleted runtime/gateway.stop
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

# Import hygiene: preload uuid while the stdlib still wins module
# resolution (eager-uuid interpreters call platform.system() at import
# time, and the kernel package below is literally named `platform`).
import uuid  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from platform.audit.contracts import AuditRecord          # noqa: E402
from platform.audit.repository import AuditRepository      # noqa: E402
from platform.gateway.server import (                     # noqa: E402
    GatewayServer, WebSocketTransport)

ROUTED_COMMANDS = ("REGISTER", "INITIALIZE", "START", "STOP", "PAUSE",
                   "RESUME", "HALT", "SYNC", "RECONCILE", "DEPLOY",
                   "ROLLBACK", "UPDATE_STRATEGY", "UPDATE_PARAMETER",
                   "UPDATE_CONFIG", "REQUEST_HEALTH", "REQUEST_STATUS")


class JsonlAuditRepository(AuditRepository):
    """Append-only JSONL audit sink — the on-disk evidence of this run.

    The gateway only calls append()/verify() at runtime; the by-id /
    by-correlation views here scan the JSONL and return raw dicts (the
    post-mortem analysis path reads the file directly).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._count = 0

    def append(self, record: AuditRecord) -> None:
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._count += 1

    def verify(self):
        return {"records": self._count, "path": str(self._path)}

    def get_by_id(self, audit_id: str):
        for row in self._rows():
            if row.get("audit_id") == audit_id:
                return row
        return None

    def iter_by_correlation_id(self, correlation_id: str):
        return iter([row for row in self._rows()
                     if row.get("correlation_id") == correlation_id])

    def _rows(self):
        if not self._path.exists():
            return []
        with self._path.open("r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--audit-path", type=Path,
                        default=ROOT / "runtime" / "gateway_audit.jsonl")
    parser.add_argument("--stop-when-file-deleted", type=Path, default=None)
    parser.add_argument("--max-lifetime-seconds", type=int, default=600)
    args = parser.parse_args()

    args.audit_path.parent.mkdir(parents=True, exist_ok=True)

    audit = JsonlAuditRepository(args.audit_path)
    server = GatewayServer(audit)
    server.start()

    def route_to_ea(command):
        targets = server.sessions.by_type("OUR_EA")
        for session in targets:
            session.send({"type": "COMMAND", "command": command.command_type,
                          "request_id": command.request_id,
                          "payload": dict(command.payload)})
        return {"state": "COMPLETED", "delivered": len(targets)}

    for cmd in ROUTED_COMMANDS:
        server.commands.register_target(cmd, route_to_ea)

    ws = WebSocketTransport(handler=server.handle_raw,
                            host=args.host, port=args.port)
    ws.start()

    print(json.dumps({
        "ready": True, "endpoint": ws.endpoint, "pid": __import__("os").getpid(),
        "audit_path": str(args.audit_path),
    }), flush=True)

    deadline = time.time() + args.max_lifetime_seconds
    reason = "lifetime"
    try:
        while time.time() < deadline:
            if (args.stop_when_file_deleted is not None
                    and not args.stop_when_file_deleted.exists()):
                reason = "sentinel"
                break
            time.sleep(0.2)
    finally:
        ws.stop()
        server.stop()
        print(json.dumps({
            "stopped": True, "reason": reason,
            "counters": server.counters,
            "audit_records": audit.verify()["records"],
        }), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
