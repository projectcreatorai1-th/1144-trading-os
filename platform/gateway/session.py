"""Gateway sessions + registry (platform.gateway, §7/§8/§15/§16).

Session states follow the registered `gateway_session` state machine
(CONNECT -> AUTHENTICATE -> REGISTER -> READY -> ACTIVE -> ... -> CLOSED);
order flow only resumes through SYNC -> RECONCILE after a reconnect.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    build_state_machine_registry,
)

from platform.gateway.contracts import (
    ContractRequest,
    utc_now_iso,
)

SESSION_MACHINE = "gateway_session"


class GatewaySessionError(ContractError):
    rule_id = "GWS-001"


@dataclass
class GatewaySession:
    """One connected client. Metadata per §7; heartbeat per §15."""

    session_id: str
    client_id: str
    client_type: str
    client_version: str
    contract_version: str
    capabilities: tuple[str, ...]
    state: str = "CONNECT"
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    heartbeat_at: float = 0.0
    send: Callable[[dict], None] = field(default=lambda _m: None)
    sequence: int = 0

    # ------------------------------------------------------------------ #
    def metadata(self) -> dict:
        return {
            "session_id": self.session_id, "client_id": self.client_id,
            "client_type": self.client_type,
            "client_version": self.client_version,
            "contract_version": self.contract_version,
            "connected_at": self.connected_at, "last_seen": self.last_seen,
            "heartbeat_at": self.heartbeat_at, "state": self.state,
            "capabilities": list(self.capabilities)}

    def touch(self) -> None:
        self.last_seen = time.time()

    def heartbeat_status(self, *, timeout: float,
                         stale_after: float) -> str:
        """HEALTHY / DEGRADED / STALE / DISCONNECTED - never assume a live
        process equals a healthy connection (process alive != connected)."""
        if self.state in ("CLOSED", "DISCONNECTED"):
            return "DISCONNECTED"
        age = time.time() - max(self.last_seen, self.heartbeat_at
                                or self.connected_at)
        if age <= timeout:
            return "HEALTHY"
        if age <= stale_after:
            return "DEGRADED"
        return "STALE"


class SessionRegistry:
    """Owns session lifecycle through the registered state machine."""

    def __init__(self,
                 machines: StateMachineRegistry | None = None) -> None:
        self._machines = machines or build_state_machine_registry()
        self._by_id: dict[str, GatewaySession] = {}
        self._by_client: dict[str, str] = {}

    def create(self, request: ContractRequest, *,
               send: Callable[[dict], None],
               negotiation: str = "ACCEPT") -> GatewaySession:
        if negotiation == "REJECT":
            raise GatewaySessionError(
                "cannot create a session for a rejected contract",
                location="gateway.session.create")
        client_key = request.client_id or request.client_version
        existing_id = self._by_client.get(client_key)
        if existing_id is not None:
            # one live session per client identity: the old one closes
            existing = self._by_id.get(existing_id)
            if existing is not None:
                self.force_close(existing)
        session = GatewaySession(
            session_id=new_identifier("gateway_session_id"),
            client_id=client_key, client_type=request.client_type,
            client_version=request.client_version,
            contract_version=request.contract_version,
            capabilities=request.supported_features,
            state="READY", send=send)
        self._by_id[session.session_id] = session
        self._by_client[client_key] = session.session_id
        return session

    def transition(self, session: GatewaySession, target: str, *,
                   reason: str = "") -> GatewaySession:
        self._machines.apply(SESSION_MACHINE, session.state, target,
                             reason=reason or f"->{target}",
                             actor="gateway.server")
        session.state = target
        return session

    def get(self, session_id: str) -> GatewaySession | None:
        return self._by_id.get(session_id)

    def by_client(self, client_id: str) -> GatewaySession | None:
        sid = self._by_client.get(client_id)
        return self._by_id.get(sid) if sid else None

    def by_type(self, client_type: str) -> list[GatewaySession]:
        return [s for s in self._by_id.values()
                if s.client_type == client_type
                and s.state not in ("CLOSED",)]

    def active(self) -> list[GatewaySession]:
        return [s for s in self._by_id.values() if s.state == "ACTIVE"]

    def all_sessions(self) -> list[GatewaySession]:
        return list(self._by_id.values())

    def force_close(self, session: GatewaySession) -> None:
        if session.state != "CLOSED":
            session.state = "DISCONNECTED" \
                if session.state not in ("DISCONNECTED", "RECONNECTING") \
                else session.state
            # terminal close is always legal from a connectivity state
            session.state = "CLOSED"
        self._by_client.pop(session.client_id, None)

    def sweep(self, *, timeout: float, stale_after: float) -> list[str]:
        """Mark stale sessions DISCONNECTED; returns closed session ids."""
        closed = []
        for session in self._by_id.values():
            if session.state in ("ACTIVE", "DEGRADED"):
                if session.heartbeat_status(
                        timeout=timeout, stale_after=stale_after) == "STALE":
                    try:
                        self.transition(session, "DISCONNECTED",
                                        reason="heartbeat stale")
                    except ContractError:
                        pass
        for session in list(self._by_id.values()):
            if session.state == "CLOSED":
                self._by_id.pop(session.session_id, None)
                closed.append(session.session_id)
        return closed
