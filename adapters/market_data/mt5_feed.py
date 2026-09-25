"""MT5 market-data feed (owned by adapters.market_data, Phase 10).

The ONLY path market data enters the system: TickSource -> ticks ->
IngestionRequest -> the existing Phase 1 IngestionPipeline (raw ->
normalized -> event). There is no second pipeline: this adapter never
writes stores directly.

Freshness is stamped from tick age + connection state (CURRENT/STALE/
UNKNOWN/DISCONNECTED); a missing tick is never CURRENT and a failed
connection is never SAFE.

D8.1: the tick source is the same MT5 terminal used by the DEMO
execution transport. The real source is deployment-gated; tests inject
a controlled deterministic source.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterator, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc, utc_now

from adapters.mt5.connection import (
    ConnectionMonitor,
    ConnectivityConfig,
    TransportHealth,
    load_connectivity_config,
)
from core.data.pipeline import IngestionPipeline, IngestionRequest

CONTRACT_VERSION = "1.1.0"
SOURCE_ID = "mt5-feed"

# Phase 10 finding 1 evidence: with the server-local epoch fixed, real DEMO
# ticks still land up to ~1.03s AFTER local ingestion (inter-clock drift
# between the terminal clock and this host; measured 2026-09-25). The tick
# contract therefore tolerates a bounded future skew of 2s (NTP-grade drift
# margin) while still rejecting gross future timestamps (the original +3h
# server-local-epoch defect). Backward compatible: every tick valid under
# 1.0.0 remains valid.
CLOCK_SKEW_TOLERANCE = timedelta(seconds=2)


class FeedError(ContractError):
    rule_id = "FDX-FEED"


# --------------------------------------------------------------------- #
# Tick source port (D8.1: the DEMO execution terminal is the provider)  #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class Tick:
    symbol: str
    event_time: datetime          # provider/terminal timestamp
    ingestion_time: datetime      # when we saw it
    bid: str
    ask: str
    last: str
    volume: str
    sequence: int | None = None   # MT5 time_msc where available

    def validate(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol:
            raise FeedError("tick.symbol must be non-empty",
                            location="tick.symbol")
        ensure_utc(self.event_time, location="tick.event_time")
        ensure_utc(self.ingestion_time, location="tick.ingestion_time")
        if self.ingestion_time + CLOCK_SKEW_TOLERANCE < self.event_time:
            raise FeedError(
                "tick.event_time beyond ingestion + clock-skew tolerance "
                "(provider timestamp must not postdate ingestion by more "
                f"than {CLOCK_SKEW_TOLERANCE.total_seconds():.0f}s)",
                location="tick.ingestion_time", rule_id="FDX-TIME")
        for name in ("bid", "ask", "last", "volume"):
            value = getattr(self, name)
            try:
                float(value)
            except (TypeError, ValueError) as error:
                raise FeedError(
                    f"tick.{name} must be numeric, got {value!r} "
                    "(malformed data rejected)",
                    location=f"tick.{name}", rule_id="FDX-MALFORMED") \
                    from error


class TickSource(ABC):
    """Deployment boundary: the real source is the MT5 terminal."""

    @abstractmethod
    def ticks(self, symbol: str) -> Iterator[Tick]:  # pragma: no cover
        ...

    @abstractmethod
    def available(self) -> bool:  # pragma: no cover
        ...


class ControlledTickSource(TickSource):
    """Deterministic scripted source for tests and simulated-demo
    deployments. Clearly NOT real connectivity (never claimed as such)."""

    def __init__(self, ticks_by_symbol: Mapping[str, list[Tick]]) -> None:
        self._ticks = {s: list(t) for s, t in ticks_by_symbol.items()}
        self._cursors = {s: 0 for s in self._ticks}

    def ticks(self, symbol: str) -> Iterator[Tick]:
        rows = self._ticks.get(symbol, [])
        while self._cursors.get(symbol, 0) < len(rows):
            index = self._cursors[symbol]
            self._cursors[symbol] = index + 1
            yield rows[index]

    def available(self) -> bool:
        return True


def _mt5_available() -> bool:
    try:
        import MetaTrader5  # noqa: F401  deployment-gated
        return True
    except ImportError:
        return False


class MT5TickSource(TickSource):
    """Real terminal tick stream (deployment-gated: requires the
    MetaTrader5 package + a running DEMO terminal). Credentials are
    terminal-side; none live in this process.

    Phase 10 finding 1 (server-local epoch): MT5 tick.time is the SERVER
    wall clock expressed as an epoch, not true UTC (measured skew vs the
    DEMO terminal: +3h). This source measures the server-clock offset from
    every received tick (server epoch minus UTC receipt time, snapped to
    the nearest half hour like real timezone offsets) and converts
    event_time to true UTC. Absurd or non-timezone-like offsets fail closed
    (FDX-TIME) instead of producing future-dated events.
    """

    MAX_ABS_OFFSET_SECONDS = 14 * 3600   # valid timezone offsets
    OFFSET_SNAP_SECONDS = 1800           # whole/half-hour tz offsets
    OFFSET_RESIDUAL_TOLERANCE = 300      # transit delay + clock noise

    def __init__(self, module: Any | None = None) -> None:
        if module is None and not _mt5_available():
            raise ContractError(
                "MetaTrader5 package not installed - the real tick source "
                "is unavailable at this deployment (report the runtime "
                "gate as BLOCKED; do not substitute and claim real)",
                location="mt5_tick_source.init", rule_id="MT5X-DEPLOYMENT")
        self._mt5 = module if module is not None \
            else __import__("MetaTrader5")
        self._connected = False
        self.server_offset_seconds: int | None = None  # measured evidence
        # symbol_info_tick is a POLL api (returns the latest tick on every
        # call). ticks() therefore yields only ticks NOT yet delivered for
        # the symbol and then returns - a bounded, non-blocking poll. (The
        # previous `while True` re-yielded the same tick forever once
        # validation passed, hanging the feed loop.)
        self._last_tick_key: dict[str, int] = {}

    def connect(self) -> None:
        if not self._mt5.initialize():
            raise ConnectionError("MT5 terminal initialize failed")
        self._connected = True

    def available(self) -> bool:
        return self._connected

    def _measure_offset(self, server_epoch: int, now_epoch: int) -> int:
        """Measured server timezone offset (seconds). Fail closed when the
        skew is not timezone-like (FDX-TIME: never emit future events)."""
        raw = server_epoch - now_epoch
        snapped = round(raw / self.OFFSET_SNAP_SECONDS) \
            * self.OFFSET_SNAP_SECONDS
        if abs(snapped) > self.MAX_ABS_OFFSET_SECONDS:
            raise ContractError(
                f"Server clock skew {snapped}s exceeds timezone bounds - "
                "refusing to convert tick time (fail closed)",
                location="mt5_tick_source.offset", rule_id="FDX-TIME",
                details={"raw_skew_seconds": raw})
        if abs(raw - snapped) > self.OFFSET_RESIDUAL_TOLERANCE:
            raise ContractError(
                f"Server clock skew {raw}s is not timezone-like - refusing "
                "to convert tick time (fail closed)",
                location="mt5_tick_source.offset", rule_id="FDX-TIME",
                details={"raw_skew_seconds": raw,
                         "snapped_seconds": snapped})
        return snapped

    def ticks(self, symbol: str) -> Iterator[Tick]:
        if not self._connected:
            raise ConnectionError("tick stream requires connect()")
        info = self._mt5.symbol_info_tick(symbol)
        if info is None:
            return
        key = int(getattr(info, "time_msc", 0) or info.time * 1000)
        if self._last_tick_key.get(symbol) == key:
            return  # no NEW tick since the previous poll
        self._last_tick_key[symbol] = key
        now = utc_now()
        offset = self._measure_offset(int(info.time), int(now.timestamp()))
        self.server_offset_seconds = offset
        yield Tick(
            symbol=symbol,
            event_time=datetime.fromtimestamp(
                int(info.time) - offset, tz=now.tzinfo),
            ingestion_time=now,
            bid=str(info.bid), ask=str(info.ask),
            last=str(info.last), volume=str(info.volume),
            sequence=int(info.time_msc) if hasattr(info, "time_msc")
            else None)


# --------------------------------------------------------------------- #
# The feed adapter: ticks -> Phase 1 pipeline                            #
# --------------------------------------------------------------------- #
class MT5MarketDataAdapter:
    """Implements the adapters.market_data boundary. Every tick becomes
    an IngestionRequest on the EXISTING pipeline; the pipeline owns
    dedup (event ids), ordering, quality and lineage."""

    def __init__(self, *, pipeline: IngestionPipeline,
                 source: TickSource,
                 monitor: ConnectionMonitor | None = None,
                 config: ConnectivityConfig | None = None,
                 sink=None) -> None:
        self._pipeline = pipeline
        self._source = source
        self._config = config or load_connectivity_config()
        self._monitor = monitor
        self._sink = sink  # TickHistoryRecorder (research plane)
        self._last_tick_at: dict[str, datetime] = {}
        self._last_event_time: dict[str, datetime] = {}
        self._dedup_cache: deque[tuple[str, int]] = deque(maxlen=10000)
        self._out_of_order = 0
        self._duplicate = 0
        self._ingested = 0
        self._rejected = 0
        self._subscriptions: dict[str, bool] = {}

    # ------- subscription management (explicit, validated, auditable) --- #
    def subscribe(self, symbol: str) -> None:
        if not self._config.symbol_allowed(symbol):
            raise FeedError(
                f"symbol {symbol!r} is not in the configured subscription "
                "list (REJECTED/NOT_SUPPORTED; never silently subscribed)",
                location="feed.subscribe", rule_id="FDX-SYMBOLS",
                details={"allowed": list(self._config.symbols)})
        self._subscriptions[symbol] = True

    def unsubscribe(self, symbol: str) -> None:
        self._subscriptions.pop(symbol, None)

    @property
    def subscriptions(self) -> tuple[str, ...]:
        return tuple(sorted(self._subscriptions))

    # ------- ingestion -------------------------------------------------- #
    def poll_symbol(self, symbol: str, *, now: datetime | None = None) \
            -> Mapping[str, Any]:
        """Pull available ticks for one subscribed symbol through the
        Phase 1 pipeline. Returns per-call evidence (never fabricated success:
        counts come from real pipeline outcomes)."""
        if symbol not in self._subscriptions:
            raise FeedError(
                f"symbol {symbol!r} is not subscribed",
                location="feed.poll", rule_id="FDX-SYMBOLS")
        moment = ensure_utc(now) if now else utc_now()
        accepted = 0
        duplicates = 0
        rejected = 0
        for tick in self._source.ticks(symbol):
            tick.validate()
            sequence = tick.sequence if tick.sequence is not None else \
                int(tick.event_time.timestamp() * 1000)
            key = (symbol, sequence)
            if key in self._dedup_cache:
                self._duplicate += 1
                duplicates += 1
                continue  # DO NOT PROCESS TWICE
            self._dedup_cache.append(key)
            previous = self._last_event_time.get(symbol)
            out_of_order = previous is not None and \
                tick.event_time < previous
            if out_of_order:
                # DO NOT SILENTLY REWRITE HISTORY: ingest with evidence
                # flag; ordering authority stays with the pipeline/store
                self._out_of_order += 1
            self._last_event_time[symbol] = max(
                tick.event_time, previous or tick.event_time)
            self._last_tick_at[symbol] = tick.ingestion_time
            request = IngestionRequest(
                source_id=SOURCE_ID,
                schema_id="market_tick",
                schema_version="1.0.0",
                payload={
                    "symbol": symbol,
                    "bid": tick.bid, "ask": tick.ask, "last": tick.last,
                    "volume": tick.volume,
                    "event_time": tick.event_time.isoformat(),
                    "ingestion_time": tick.ingestion_time.isoformat(),
                    "provider": "MT5",
                    "sequence": sequence,
                    "out_of_order": out_of_order,
                },
                source_timestamp=tick.event_time,
                environment=self._config.environment,
                event_type="MARKET_DATA_RECEIVED",
                correlation_id=new_identifier("correlation_id"),
                sequence_number=sequence,
                received_time=tick.ingestion_time,
                metadata={"source": "MT5", "synthetic": False},
                now=moment)
            outcome = self._pipeline.ingest(request)
            if outcome.accepted:
                accepted += 1
                self._ingested += 1
                if self._sink is not None:
                    self._sink.record_tick(tick)
            elif outcome.duplicate:
                duplicates += 1
                self._duplicate += 1
            else:
                rejected += 1
                self._rejected += 1
        return {"symbol": symbol, "accepted": accepted,
                "duplicates": duplicates, "rejected": rejected,
                "out_of_order_total": self._out_of_order}

    def poll(self, *, now: datetime | None = None) \
            -> Mapping[str, Mapping[str, Any]]:
        return {symbol: self.poll_symbol(symbol, now=now)
                for symbol in self.subscriptions}

    # ------- freshness --------------------------------------------------- #
    def freshness(self, symbol: str, *, now: datetime | None = None) \
            -> TransportHealth:
        moment = ensure_utc(now) if now else utc_now()
        connected = self._source.available() and (
            self._monitor is None or self._monitor.state == "CONNECTED")
        if self._monitor is not None and \
                self._monitor.state in ("RECONNECTING", "RESYNCING",
                                        "RECONCILING", "UNKNOWN"):
            return TransportHealth.UNKNOWN
        if not connected:
            return TransportHealth.DISCONNECTED
        last = self._last_tick_at.get(symbol)
        if last is None:
            return TransportHealth.UNKNOWN
        if self._monitor is not None:
            return self._monitor.health_for_tick_age(moment - last, True)
        # Phase 13 chaos finding (chaos_market_feed_stale): without a
        # monitor this used to return CURRENT unconditionally - a stale
        # feed was reported healthy (fail-open). Compute from the SAME
        # policy thresholds the monitor uses (never CURRENT past the
        # tick-stream timeout; never SAFE when stale).
        age = moment - last
        freshness = self._config.freshness
        if age <= timedelta(milliseconds=freshness.tick_stream_timeout_ms):
            return TransportHealth.CURRENT
        if age <= timedelta(milliseconds=freshness.stale_threshold_ms):
            return TransportHealth.STALE
        return TransportHealth.UNKNOWN

    @property
    def metrics(self) -> Mapping[str, int]:
        return {"ticks_ingested": self._ingested,
                "duplicates": self._duplicate,
                "rejected": self._rejected,
                "out_of_order": self._out_of_order}
