"""SNIPER Gateway SERVER contracts (platform.gateway).

Field-for-field compatible with SNIPER Gateway contract v1.0.0 whose
authorities are the Analyzer's core/gateway/contracts.py (contract
authority) and OUR-EA's our_ea/gateway/contracts.py (client SSOT). Per the
EA's own pattern this module copy-adapts the shapes; it NEVER imports the
sibling projects and NEVER changes their semantics.

The gateway is TRANSPORT/ROUTING ONLY (ownership boundary §4): no strategy,
risk, OMS or research interpretation lives here.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

CONTRACT_VERSION = "1.0.0"          # SNIPER Gateway contract
PROTOCOL_VERSION = "1.0"            # wire protocol major.minor
EVENT_SCHEMA_VERSION = "1.0.0"
SIGNAL_SCHEMA_VERSION = "1.0.0"
GATEWAY_ID = "1144-GATEWAY"

#: wire message types (request -> expected response)
MESSAGE_TYPES = (
    "CONTRACT_REQUEST", "CONTRACT_RESPONSE",
    "HEARTBEAT", "HEARTBEAT_ACK",
    "EVENT_PUBLISH", "EVENT_ACK",
    "EXECUTION_FEEDBACK", "FEEDBACK_ACK",
    "SIGNAL_PUBLISH", "SIGNAL_ACK",
    "COMMAND", "COMMAND_RESPONSE",
    "STATE_SNAPSHOT", "ERROR",
)

#: client types the server accepts (§7)
CLIENT_TYPES = ("OUR_EA", "ANALYZER", "MT5", "TRADING_OS")

#: OS control commands routed to the EA (§11 - gateway never interprets).
#: UPDATE_CONFIG is the EA-client-SSOT spelling of the §11
#: UPDATE_STRATEGY/UPDATE_PARAMETER family (documented contract conflict
#: resolution: the gateway routes both spellings without interpreting).
ROUTED_COMMANDS = (
    "REGISTER", "INITIALIZE", "START", "STOP", "PAUSE", "RESUME",
    "HALT", "KILL", "SYNC", "RECONCILE", "DEPLOY", "ROLLBACK",
    "UPDATE_STRATEGY", "UPDATE_PARAMETER", "UPDATE_CONFIG",
    "REQUEST_HEALTH", "REQUEST_STATUS",
)

#: analyzer request commands routed to the ANALYZER (§25)
ANALYZER_COMMANDS = (
    "REQUEST_ANALYSIS", "REQUEST_BACKTEST", "REQUEST_REPLAY",
    "REQUEST_VALIDATION", "REQUEST_CONFORMANCE", "REQUEST_DRIFT_ANALYSIS",
    "REQUEST_TCA", "REQUEST_STRESS_TEST", "REQUEST_REPORT",
)

#: event classes routed without semantic change (§10)
EVENT_CLASSES = (
    "MARKET_EVENT", "SIGNAL", "DECISION", "RISK_DECISION", "ORDER_INTENT",
    "ORDER", "EXECUTION", "DEAL", "POSITION", "BASKET", "GRID", "STATE",
    "RECOVERY", "INCIDENT", "HEALTH", "AUDIT",
    # EA publishable concrete types (contract v1.0.0)
    "EA_CONNECTED", "EA_DISCONNECTED", "EA_HEARTBEAT",
    "ORDER_CREATED", "ORDER_SENT", "ORDER_FILLED", "ORDER_REJECTED",
    "ORDER_CANCELLED", "ORDER_PARTIALLY_FILLED",
    "POSITION_OPENED", "POSITION_CLOSED",
    "RISK_UPDATED", "SIGNAL_ACCEPTED", "SIGNAL_REJECTED",
    "EXECUTION_RESULT",
    "RECONCILIATION_STARTED", "RECONCILIATION_COMPLETED",
    "RECONCILIATION_FAILED",
)

#: request/response lifecycle (§12)
REQUEST_STATES = ("REQUESTED", "ACCEPTED", "PROCESSING", "COMPLETED",
                  "REJECTED", "TIMEOUT", "FAILED")

#: idempotency states (§13)
IDEMPOTENCY_STATES = ("NEW", "PROCESSING", "COMPLETED", "FAILED", "UNKNOWN")

#: kill switch levels (§21) - gateway propagates, never decides
KILL_LEVELS = ("GLOBAL", "PORTFOLIO", "ACCOUNT", "STRATEGY", "EA", "SYMBOL")

#: error taxonomy (§19)
ERROR_CODES = (
    "CONNECTION_ERROR", "AUTH_ERROR", "CONTRACT_ERROR", "SCHEMA_ERROR",
    "ROUTING_ERROR", "TIMEOUT", "DUPLICATE", "OUT_OF_ORDER", "STALE",
    "CAPACITY", "UNAUTHORIZED", "INTERNAL_ERROR", "RECOVERY_ERROR",
)

#: negotiation outcomes (§8)
NEGOTIATION_RESULTS = ("ACCEPT", "DEGRADED", "REJECT")

_SESSION_STATES = ("CONNECT", "AUTHENTICATE", "REGISTER", "READY", "ACTIVE",
                   "DEGRADED", "DISCONNECTED", "RECONNECTING", "SYNC",
                   "RECONCILE", "CLOSED")

_ID = re.compile(r"^[A-Z]{2,6}-[0-9A-F]{12}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def canonical_hash(data) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str).encode()).hexdigest().upper()


def is_wire_id(value: str) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


@dataclass(frozen=True)
class EventEnvelope:
    """Wire envelope - identical fields/meaning to contract v1.0.0."""

    event_id: str
    event_type: str
    schema_version: str
    created_at: str
    source: str
    target: str
    sequence: int
    correlation_id: str
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "event_type": self.event_type,
            "schema_version": self.schema_version,
            "created_at": self.created_at, "source": self.source,
            "target": self.target, "sequence": self.sequence,
            "correlation_id": self.correlation_id,
            "payload": dict(self.payload)}

    @staticmethod
    def from_dict(d: dict) -> "EventEnvelope":
        return EventEnvelope(
            event_id=d["event_id"], event_type=d["event_type"],
            schema_version=d.get("schema_version", EVENT_SCHEMA_VERSION),
            created_at=d["created_at"], source=d.get("source", ""),
            target=d.get("target", ""), sequence=int(d.get("sequence", 0)),
            correlation_id=d.get("correlation_id", ""),
            payload=dict(d.get("payload", {})))


@dataclass(frozen=True)
class ContractRequest:
    """First message a client sends (field-compatible with the EA client)."""

    protocol_version: str
    contract_version: str
    client_version: str
    client_capabilities: tuple[str, ...]
    supported_features: tuple[str, ...]
    accepted_commands: tuple[str, ...]
    client_type: str = "OUR_EA"
    client_id: str = ""

    def validate(self) -> list[str]:
        errors = []
        if self.protocol_version != PROTOCOL_VERSION:
            errors.append(f"protocol_version {self.protocol_version!r}")
        if not self.contract_version.startswith("1.0"):
            errors.append(f"contract_version {self.contract_version!r}")
        if self.client_type not in CLIENT_TYPES:
            errors.append(f"client_type {self.client_type!r}")
        return errors

    @staticmethod
    def from_dict(d: dict) -> "ContractRequest":
        return ContractRequest(
            protocol_version=str(d.get("protocol_version", "")),
            contract_version=str(d.get("contract_version", "")),
            client_version=str(d.get("client_version", "")),
            client_capabilities=tuple(d.get("client_capabilities", ())),
            supported_features=tuple(d.get("supported_features", ())),
            accepted_commands=tuple(d.get("accepted_commands", ())),
            client_type=str(d.get("client_type",
                                  d.get("client_version", "OUR_EA"))),
            client_id=str(d.get("client_id", "")))


@dataclass(frozen=True)
class ContractResponse:
    type: str = "CONTRACT_RESPONSE"
    contract_version: str = CONTRACT_VERSION
    gateway_version: str = "1.0.0"
    protocol_version: str = PROTOCOL_VERSION
    accepted: bool = False
    result: str = "REJECT"          # ACCEPT | DEGRADED | REJECT
    session_id: str = ""
    reason: str = ""
    gateway_capabilities: tuple[str, ...] = (
        "signal.routing", "event.routing", "command.routing",
        "correlation", "idempotency", "ordering", "heartbeat",
        "backpressure", "kill-switch.propagation", "integration-audit")

    def to_dict(self) -> dict:
        return {"type": self.type, "contract_version": self.contract_version,
                "gateway_version": self.gateway_version,
                "protocol_version": self.protocol_version,
                "accepted": self.accepted, "result": self.result,
                "session_id": self.session_id, "reason": self.reason,
                "gateway_capabilities": list(self.gateway_capabilities)}


def negotiate(request: ContractRequest) -> tuple[str, str]:
    """§8 contract negotiation. Returns (result, reason).
    MAJOR.MINOR must match; unknown commands degrade (accepted with a
    reduced command set), incompatible protocol/contract REJECT."""
    errors = request.validate()
    if any("protocol_version" in e for e in errors):
        return "REJECT", "; ".join(errors)
    if any("contract_version" in e for e in errors):
        return "REJECT", "; ".join(errors)
    if request.client_type not in CLIENT_TYPES:
        return "REJECT", "; ".join(errors)
    unknown = [c for c in request.accepted_commands
               if c not in ROUTED_COMMANDS]
    if unknown:
        # command set mismatch = degraded session, never silent
        return "DEGRADED", f"unknown commands not routed: {unknown}"
    return "ACCEPT", ""


@dataclass(frozen=True)
class GatewayCommand:
    """§11 command envelope the OS sends through the gateway."""

    command_type: str
    request_id: str
    correlation_id: str
    trace_id: str
    target: str                      # "OUR_EA" | "ANALYZER" | "MT5"
    payload: dict = field(default_factory=dict)
    idempotency_key: str = ""
    created_at: str = ""
    expires_at: str = ""

    def validate(self) -> list[str]:
        errors = []
        if self.command_type not in ROUTED_COMMANDS + ANALYZER_COMMANDS:
            errors.append(f"command_type {self.command_type!r}")
        if not self.request_id:
            errors.append("request_id empty")
        if self.target not in ("OUR_EA", "ANALYZER", "MT5"):
            errors.append(f"target {self.target!r}")
        return errors

    @staticmethod
    def from_dict(d: dict) -> "GatewayCommand":
        return GatewayCommand(
            command_type=str(d.get("command_type",
                                   d.get("command", ""))),
            request_id=str(d.get("request_id", "")),
            correlation_id=str(d.get("correlation_id", "")),
            trace_id=str(d.get("trace_id", "")),
            target=str(d.get("target", "OUR_EA")),
            payload=dict(d.get("payload", {})),
            idempotency_key=str(d.get("idempotency_key", "")),
            created_at=str(d.get("created_at", "")),
            expires_at=str(d.get("expires_at", "")))
