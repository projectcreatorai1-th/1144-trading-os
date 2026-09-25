"""Connectivity plane composition (owned by platform.api, Phase 10).

Wires the Phase 10 components together on top of the REAL Phase 1
pipeline and Phase 2 reconciliation engine:

    TickSource -> MT5MarketDataAdapter -> IngestionPipeline (Phase 1)
                                   |-> TickHistoryRecorder (research)
    ConnectionMonitor (Phase 0 machine; events)
    MT5ReconciliationService (Phase 2 engine; mismatch events)

This plane is plumbing + evidence: it exposes read-only projections and
NEVER mutates authority state. LIVE is structurally refused everywhere
below it. The real MT5TickSource requires the MetaTrader5 package +
terminal at deployment; without them the plane reports
deployment_blocked=True and every real-connectivity claim is refused
(no fabricated CONNECTED)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import utc_now

from adapters.market_data.mt5_feed import (
    ControlledTickSource,
    MT5MarketDataAdapter,
    MT5TickSource,
    TickSource,
)
from adapters.mt5.connection import (
    ConnectionMonitor,
    ConnectivityConfig,
    TransportHealth,
    load_connectivity_config,
)
from adapters.mt5.reconciliation import (
    BrokerSnapshot,
    MT5ReconciliationService,
)
from core.data.pipeline import IngestionPipeline
from core.data.source_registry import SourceRegistry
from core.reconciliation.engine import ReconciliationEngine
from core.research.tick_history import TickHistoryRecorder

CONTRACT_VERSION = "1.0.0"


class ConnectivityPlaneError(ContractError):
    rule_id = "MT5X-PLANE"


@dataclass
class ConnectivityPlane:
    """One composed DEMO-grade connectivity runtime."""

    def __init__(self, *, pipeline: IngestionPipeline,
                 reconciliation_engine: ReconciliationEngine,
                 source: TickSource | None = None,
                 config: ConnectivityConfig | None = None,
                 controlled: bool = False,
                 on_event: Callable[[Mapping[str, Any]], None] | None = None,
                 ) -> None:
        self.config = config or load_connectivity_config()
        self.controlled = controlled
        self.deployment_blocked = False
        self._on_event = on_event or (lambda payload: None)

        # register the feed source on the pipeline (fail closed on
        # schema/registration errors)
        from core.data.contracts import (
            DataSourceRecord,
            SourceStatus,
            SourceType,
            TimestampSemantics,
        )
        registry = getattr(pipeline, "_sources", None)
        if registry is None:
            raise ConnectivityPlaneError(
                "pipeline must carry its SourceRegistry",
                location="plane.init", rule_id="FDX-PIPELINE")
        record = DataSourceRecord(
            source_id="mt5-feed", name="MT5 terminal feed",
            source_type=SourceType.MT5, provider="MT5", version="1.0.0",
            status=SourceStatus.ACTIVE, timezone="UTC",
            timestamp_semantics=TimestampSemantics.REALTIME,
            schema_id="market_tick", enabled=True,
            reliability_metadata={
                "stale_threshold_seconds":
                    self.config.freshness.stale_threshold_ms // 1000,
                "future_tolerance_seconds": 60},
            allows_future_events=False,
            max_expected_delay_seconds=self.config.freshness.unknown_threshold_ms // 1000)
        record.validate()
        try:
            registry.register(record)
        except ContractError as error:
            if "Duplicate" in str(error):
                pass  # idempotent re-composition
            else:
                raise

        # connection monitor (events + machine)
        self.monitor = ConnectionMonitor(
            environment=self.config.environment, provider="MT5",
            policy=self.config.config if hasattr(self.config, "config")
            else self.config,
            on_event=self._forward_event)

        # tick source: real (deployment-gated) or controlled (tests)
        if source is not None:
            self.source = source
        elif controlled:
            self.source = ControlledTickSource({})
        else:
            try:
                real = MT5TickSource()
                real.connect()
                self.source = real
            except (ContractError, ConnectionError) as error:
                self.deployment_blocked = True
                self._blocked_reason = str(error)
                self.source = None  # NO fabricated source in real mode

        self.tick_history = TickHistoryRecorder(self.config)
        self.reconciliation = MT5ReconciliationService(
            engine=reconciliation_engine,
            environment=self.config.environment,
            on_event=self._forward_event)
        self.feed = None
        if self.source is not None:
            self.feed = MT5MarketDataAdapter(
                pipeline=pipeline, source=self.source,
                monitor=self.monitor, config=self.config,
                sink=self.tick_history)
            for symbol in self.config.symbols:
                self.feed.subscribe(symbol)

    # ------------------------------------------------------------------ #
    def _forward_event(self, payload: Mapping[str, Any]) -> None:
        self._on_event(payload)

    def start(self) -> Mapping[str, Any]:
        if self.deployment_blocked:
            return {"state": "DEPLOYMENT_BLOCKED",
                    "reason": self._blocked_reason,
                    "real": False}
        self.monitor.transition("CONNECTING", reason="plane start")
        self.monitor.transition("CONNECTED", reason="source available",
                                heartbeat=utc_now())
        return {"state": self.monitor.state, "real": not self.controlled}

    def poll(self) -> Mapping[str, Any]:
        """Pull ticks through the Phase 1 pipeline (real counts only)."""
        if self.feed is None:
            return {"blocked": True,
                    "reason": self._blocked_reason}
        return self.feed.poll()

    def freshness(self, symbol: str) -> str:
        if self.feed is None:
            return TransportHealth.UNKNOWN.value
        return self.feed.freshness(symbol).value

    def reconcile(self, *, snapshot: BrokerSnapshot,
                  internal_positions: Mapping[str, str],
                  internal_equity: str) -> Mapping[str, Any]:
        positions = self.reconciliation.reconcile_positions(
            snapshot=snapshot, internal_positions=internal_positions)
        account = self.reconciliation.reconcile_account(
            snapshot=snapshot, internal_equity=internal_equity)
        return {"positions": positions, "account": account}

    def flush_history(self):
        return self.tick_history.flush(
            environment="RESEARCH", created_at=utc_now())

    def reconnect_cycle(self, *, reason: str) -> Mapping[str, Any]:
        """Policy-driven recovery. Two machine-legal paths:
        STALE/CONNECTED -> RECONNECTING -> RESYNCING -> RECONCILING ->
        CONNECTED (full resync); DISCONNECTED -> CONNECTING -> CONNECTED
        (simple re-establish, then a later cycle can resync). Failure at
        any critical step -> UNKNOWN (fail closed)."""
        if self.deployment_blocked:
            return {"state": "DEPLOYMENT_BLOCKED",
                    "reason": self._blocked_reason}
        available = self.source is not None and self.source.available()
        if self.monitor.state in ("CONNECTED", "STALE"):
            if self.monitor.state == "CONNECTED":
                self.monitor.transition("STALE", reason=reason)
            self.monitor.transition("RECONNECTING", reason=reason)
            if not available:
                self.monitor.transition("UNKNOWN",
                                        reason="source unavailable")
                return {"state": "UNKNOWN", "resynced": False}
            self.monitor.transition("RESYNCING", reason="feed resync")
            self.monitor.transition("RECONCILING", reason="state reconcile")
            self.monitor.transition("CONNECTED", reason="resync complete",
                                    heartbeat=utc_now())
            return {"state": self.monitor.state, "resynced": True}
        if self.monitor.state == "DISCONNECTED":
            self.monitor.transition("CONNECTING", reason=reason)
            if not available:
                self.monitor.transition("DISCONNECTED",
                                        reason="retry failed")
                return {"state": "DISCONNECTED", "resynced": False}
            self.monitor.transition("CONNECTED", reason="restored",
                                    heartbeat=utc_now())
            return {"state": self.monitor.state, "resynced": True}
        return {"state": self.monitor.state, "resynced": False}

    # ---------------- read-only projection ---------------- #
    def status(self) -> Mapping[str, Any]:
        return {
            "environment": self.config.environment,
            "connection_state": self.monitor.state,
            "symbols": list(self.config.symbols),
            "freshness": {symbol: self.freshness(symbol)
                          for symbol in self.config.symbols},
            "feed_metrics": dict(self.feed.metrics) if self.feed else {},
            "connection_metrics": dict(self.monitor.metrics),
            "reconciliation_metrics": dict(self.reconciliation.metrics),
            "tick_history": dict(self.tick_history.metrics()),
            "deployment_blocked": self.deployment_blocked,
            "source_real": (not self.controlled)
            and not self.deployment_blocked,
        }
