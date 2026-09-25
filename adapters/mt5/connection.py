"""Connection state plane (owned by adapters.mt5, Phase 10).

ConnectionStateRecord + ConnectionMonitor: evidence about broker/feed
connectivity. This plane is NEVER an authority - it reports transport
health, emits CONNECTION_STATE_CHANGED evidence and drives fail-closed
behavior through the existing risk path (data quality / execution
state), never around it.

LIVE is structurally refused: the monitor refuses to construct for any
environment outside {DEMO, SIMULATION}.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.registry import load_registry
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_utc

CONTRACT_VERSION = "1.0.0"
CONNECTION_MACHINE = "connection_state"
#: Phase 10 connectivity is DEMO-grade (SIMULATION allowed for the
#: controlled plane). LIVE cannot appear here - and there is no flag.
ALLOWED_ENVIRONMENTS = frozenset({"DEMO", "SIMULATION"})


class TransportHealth(Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    DISCONNECTED = "DISCONNECTED"


def canonical_hash(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConnectionStateRecord:
    """Contract `connection_state` 1.0.0 (owner adapters.mt5)."""
    connection_id: str
    environment: str
    provider: str
    state: str
    changed_at: datetime
    reconnect_policy_version: str
    secret_id: str | None = None
    last_heartbeat: datetime | None = None
    content_hash: str = ""
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.connection_id, str) or \
                not self.connection_id.startswith("cnn_"):
            raise ContractError(
                "connection.connection_id must be a cnn_ identifier",
                location="connection.connection_id", rule_id="IDENT-001")
        if self.environment not in ALLOWED_ENVIRONMENTS:
            raise ContractError(
                f"connection.environment {self.environment!r} refused: "
                "Phase 10 connectivity is DEMO/SIMULATION only (LIVE is "
                "structurally refused)",
                location="connection.environment", rule_id="MT5-LIVE-REFUSED")
        if not isinstance(self.provider, str) or not self.provider:
            raise ContractError(
                "connection.provider must be a non-empty string",
                location="connection.provider")
        if self.state not in ("DISCONNECTED", "CONNECTING", "CONNECTED",
                              "DEGRADED", "STALE", "RECONNECTING",
                              "RESYNCING", "RECONCILING", "UNKNOWN"):
            raise ContractError(
                f"connection.state {self.state!r} is not a connection_state "
                "machine state",
                location="connection.state", rule_id="SCHEMA-ENUM")
        ensure_utc(self.changed_at, location="connection.changed_at")
        if self.last_heartbeat is not None:
            ensure_utc(self.last_heartbeat,
                       location="connection.last_heartbeat")
        if not isinstance(self.reconnect_policy_version, str) or \
                not self.reconnect_policy_version:
            raise ContractError(
                "connection.reconnect_policy_version is required (versioned "
                "policy evidence)",
                location="connection.reconnect_policy_version",
                rule_id="MT5X-CONFIG")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractError(
                "connection.content_hash mismatch (corrupted record)",
                location="connection.content_hash", rule_id="MT5X-INTEGRITY",
                details={"expected": expected})

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "environment": self.environment, "provider": self.provider,
            "state": self.state,
            "changed_at": ensure_utc(self.changed_at).isoformat(),
            "last_heartbeat": ensure_utc(self.last_heartbeat).isoformat()
            if self.last_heartbeat else None,
            "reconnect_policy_version": self.reconnect_policy_version,
        })


class ConnectionMonitor:
    """Drives the Phase 0 connection_state machine; every transition is
    machine-validated, evented (CONNECTION_STATE_CHANGED) and recorded
    immutably. UNKNOWN is a first-class state."""

    def __init__(self, *, environment: str, provider: str,
                 policy: "ConnectivityConfig | None" = None,
                 machines: StateMachineRegistry | None = None,
                 on_event: Callable[[Mapping[str, Any]], None] | None = None,
                 now: datetime | None = None) -> None:
        if environment not in ALLOWED_ENVIRONMENTS:
            raise ContractError(
                f"ConnectionMonitor refused for environment {environment!r} "
                "(LIVE is structurally refused; DEMO/SIMULATION only)",
                location="connection.monitor", rule_id="MT5-LIVE-REFUSED")
        self._environment = environment
        self._provider = provider
        self._policy = policy or load_connectivity_config()
        self._machines = machines or build_state_machine_registry()
        self._on_event = on_event
        self._state = "DISCONNECTED"
        initial = ConnectionStateRecord(
            connection_id=new_identifier("connection_id"),
            environment=environment, provider=provider, state=self._state,
            changed_at=ensure_utc(now) if now else _utc_now(),
            reconnect_policy_version=self._policy.version)
        object.__setattr__(initial, "content_hash",
                           initial.compute_content_hash())
        initial.validate()
        self._record = initial
        self._history: list[ConnectionStateRecord] = [self._record]
        self._retry_count = 0
        self._reconnect_failures = 0

    @property
    def state(self) -> str:
        return self._state

    @property
    def record(self) -> ConnectionStateRecord:
        return self._record

    @property
    def history(self) -> tuple[ConnectionStateRecord, ...]:
        return tuple(self._history)

    @property
    def metrics(self) -> Mapping[str, int]:
        return {"reconnect_count": self._retry_count,
                "reconnect_failures": self._reconnect_failures,
                "transitions": len(self._history) - 1}

    def transition(self, target: str, *, at: datetime | None = None,
                   reason: str, actor: str = "system",
                   heartbeat: datetime | None = None) \
            -> ConnectionStateRecord:
        self._machines.apply(CONNECTION_MACHINE, self._state, target,
                             reason=reason, actor=actor,
                             timestamp=ensure_utc(at) if at else _utc_now())
        previous = self._state
        self._state = target
        record = ConnectionStateRecord(
            connection_id=self._record.connection_id,
            environment=self._environment, provider=self._provider,
            state=target,
            changed_at=ensure_utc(at) if at else _utc_now(),
            reconnect_policy_version=self._policy.version,
            secret_id=self._record.secret_id,
            last_heartbeat=heartbeat or self._record.last_heartbeat)
        object.__setattr__(record, "content_hash",
                           record.compute_content_hash())
        record.validate()
        self._history.append(record)
        self._record = record
        if previous in ("RECONNECTING", "RESYNCING", "RECONCILING") and \
                target == "CONNECTED":
            self._retry_count += 1
        if previous == "RECONNECTING" and target == "DISCONNECTED":
            self._reconnect_failures += 1
        if self._on_event is not None:
            self._on_event({
                "event_type": "CONNECTION_STATE_CHANGED",
                "connection_id": record.connection_id,
                "provider": self._provider,
                "environment": self._environment,
                "from": previous, "to": target, "reason": reason,
                "changed_at": record.changed_at.isoformat(),
            })
        return record

    # ------- policy-driven helpers (numbers come from versioned config) --- #
    def retry_delay(self, attempt: int) -> timedelta:
        ms = min(self._policy.reconnect.initial_retry_delay_ms *
                 (self._policy.reconnect.backoff_multiplier ** max(attempt - 1, 0)),
                 self._policy.reconnect.max_retry_delay_ms)
        return timedelta(milliseconds=ms)

    def retries_exhausted(self, attempt: int) -> bool:
        return attempt >= self._policy.reconnect.max_retry_attempts

    def health_for_tick_age(self, age: timedelta | None,
                            connected: bool) -> TransportHealth:
        if not connected:
            return TransportHealth.DISCONNECTED
        if age is None:
            return TransportHealth.UNKNOWN
        if age <= timedelta(
                milliseconds=self._policy.freshness.tick_stream_timeout_ms):
            return TransportHealth.CURRENT
        if age <= timedelta(
                milliseconds=self._policy.freshness.stale_threshold_ms):
            return TransportHealth.STALE
        return TransportHealth.UNKNOWN


def _utc_now() -> datetime:
    from architecture.contracts.time import utc_now
    return utc_now()


# --------------------------------------------------------------------- #
# Versioned connectivity configuration (fail-closed loader)             #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class ReconnectPolicy:
    initial_retry_delay_ms: int
    max_retry_delay_ms: int
    backoff_multiplier: float
    max_retry_attempts: int
    connection_timeout_ms: int


@dataclass(frozen=True)
class FreshnessPolicy:
    tick_stream_timeout_ms: int
    stale_threshold_ms: int
    unknown_threshold_ms: int


@dataclass(frozen=True)
class ResyncPolicy:
    batch_size: int
    reconcile_positions: bool
    reconcile_account: bool


@dataclass(frozen=True)
class TickHistoryPolicy:
    max_ticks: int
    max_age_days: int


@dataclass(frozen=True)
class ConnectivityConfig:
    """Contract-backed view of architecture/connectivity.yaml. Invalid
    configuration is STARTUP FAILURE - never fallback defaults."""
    version: str
    environment: str
    symbols: tuple[str, ...]
    reconnect: ReconnectPolicy
    freshness: FreshnessPolicy
    resync: ResyncPolicy
    tick_history: TickHistoryPolicy
    adapter_environment: str
    content_hash: str = ""
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if self.environment not in ALLOWED_ENVIRONMENTS:
            raise ContractError(
                f"connectivity.environment {self.environment!r} refused "
                "(DEMO/SIMULATION only; LIVE is structurally refused and "
                "cannot be enabled by configuration)",
                location="connectivity.environment",
                rule_id="MT5-LIVE-REFUSED")
        if self.adapter_environment != self.environment:
            raise ContractError(
                "connectivity.execution.adapter_environment must equal the "
                "config environment (no cross-environment routing)",
                location="connectivity.adapter_environment",
                rule_id="MT5-ENV")
        if not self.symbols or len(self.symbols) > 5:
            raise ContractError(
                "connectivity.symbols must be an explicit list of 1-5 "
                "symbols (D8.2: small controlled subset; never "
                "auto-subscribe)",
                location="connectivity.symbols", rule_id="FDX-SYMBOLS")
        if len(set(self.symbols)) != len(self.symbols):
            raise ContractError("connectivity.symbols contains duplicates",
                                location="connectivity.symbols")
        for symbol in self.symbols:
            if not isinstance(symbol, str) or not symbol:
                raise ContractError(
                    "connectivity.symbols entries must be non-empty strings",
                    location="connectivity.symbols")
        r = self.reconnect
        if not (0 < r.initial_retry_delay_ms <= r.max_retry_delay_ms
                <= 60000):
            raise ContractError(
                "reconnect delays must satisfy 0 < initial <= max <= 60000ms",
                location="connectivity.reconnect", rule_id="MT5X-CONFIG")
        if not (1.0 <= r.backoff_multiplier <= 10.0):
            raise ContractError(
                "reconnect.backoff_multiplier must be within [1, 10]",
                location="connectivity.reconnect", rule_id="MT5X-CONFIG")
        if not (1 <= r.max_retry_attempts <= 100):
            raise ContractError(
                "reconnect.max_retry_attempts must be within [1, 100]",
                location="connectivity.reconnect", rule_id="MT5X-CONFIG")
        if r.connection_timeout_ms <= 0:
            raise ContractError("connection_timeout_ms must be positive",
                                location="connectivity.reconnect")
        f = self.freshness
        if not (0 < f.tick_stream_timeout_ms < f.stale_threshold_ms
                < f.unknown_threshold_ms):
            raise ContractError(
                "freshness thresholds must satisfy 0 < tick_timeout < "
                "stale < unknown",
                location="connectivity.freshness", rule_id="FDX-FRESHNESS")
        if self.resync.batch_size <= 0:
            raise ContractError("resync.batch_size must be positive",
                                location="connectivity.resync")
        if self.tick_history.max_ticks <= 0 or \
                self.tick_history.max_age_days <= 0:
            raise ContractError(
                "tick_history limits must be positive (bounded storage)",
                location="connectivity.tick_history", rule_id="FDX-BOUNDED")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractError(
                "connectivity.content_hash mismatch (corrupted config)",
                location="connectivity.content_hash", rule_id="MT5X-CONFIG")

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "version": self.version, "environment": self.environment,
            "symbols": list(self.symbols),
            "reconnect": vars(self.reconnect),
            "freshness": vars(self.freshness),
            "resync": vars(self.resync),
            "tick_history": vars(self.tick_history),
            "adapter_environment": self.adapter_environment,
        })

    def symbol_allowed(self, symbol: str) -> bool:
        return symbol in self.symbols


def load_connectivity_config(root: str | None = None) -> ConnectivityConfig:
    """Fail-closed loader: any malformed/missing value is a startup
    error (no fallback to safe-looking defaults)."""
    data = load_registry("connectivity.yaml", project_root=root)
    try:
        config = ConnectivityConfig(
            version=str(data["version"]),
            environment=data["environment"],
            symbols=tuple(data["symbols"]),
            reconnect=ReconnectPolicy(**data["reconnect"]),
            freshness=FreshnessPolicy(**data["freshness"]),
            resync=ResyncPolicy(**data["resync"]),
            tick_history=TickHistoryPolicy(**data["tick_history"]),
            adapter_environment=data["execution"]["adapter_environment"])
    except (KeyError, TypeError) as error:
        raise ContractError(
            f"connectivity config malformed: {error}",
            location="connectivity.load", rule_id="MT5X-CONFIG") from error
    object.__setattr__(config, "content_hash",
                       config.compute_content_hash())
    config.validate()
    return config
