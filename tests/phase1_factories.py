"""Phase 1 test fixtures: deterministic, test-scope only (SECTION 38).

Covers: valid market data, invalid market data, duplicate data,
out-of-order data, late data, stale data, sequence gap, timestamp anomaly,
schema mismatch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from architecture.contracts.identifiers import new_identifier
from core.data.contracts import (
    DataSourceRecord,
    SourceStatus,
    SourceType,
    TimestampSemantics,
    compute_payload_hash,
)
from core.time.validation import SourceTimeSemantics

UTC = timezone.utc
T0 = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return T0.replace(hour=hour, minute=minute, second=second)


def make_source(**overrides) -> DataSourceRecord:
    defaults: dict[str, Any] = dict(
        source_id="feed-xauusd",
        name="XAUUSD demo feed",
        source_type=SourceType.MARKET_DATA,
        provider="in-process-provider",
        version="1.0.0",
        status=SourceStatus.ACTIVE,
        timezone="UTC",
        timestamp_semantics=TimestampSemantics.REALTIME,
        schema_id="market_tick",
        enabled=True,
        reliability_metadata={
            "stale_threshold_seconds": 300,
            "future_tolerance_seconds": 60,
        },
        allows_future_events=False,
        max_expected_delay_seconds=30,
    )
    defaults.update(overrides)
    return DataSourceRecord(**defaults)


def make_calendar_source(**overrides) -> DataSourceRecord:
    return make_source(
        source_id="calendar-eco",
        name="Economic calendar",
        source_type=SourceType.CALENDAR,
        timestamp_semantics=TimestampSemantics.HISTORICAL,
        schema_id="calendar_event",
        allows_future_events=True,
        reliability_metadata={
            "stale_threshold_seconds": 31_536_000,
            "future_tolerance_seconds": 0,
        },
        max_expected_delay_seconds=None,
        **overrides,
    )


def make_time_semantics(**overrides) -> SourceTimeSemantics:
    defaults: dict[str, Any] = dict(
        source_id="feed-xauusd",
        timestamp_semantics="REALTIME",
        allows_future_events=False,
        future_tolerance_seconds=60,
        stale_threshold_seconds=300,
        max_expected_delay_seconds=30,
    )
    defaults.update(overrides)
    return SourceTimeSemantics(**defaults)


def market_tick_payload(symbol: str = "XAUUSD", bid: str = "2650.10", ask: str = "2650.30") -> dict[str, Any]:
    return {"symbol": symbol, "bid": bid, "ask": ask}


def make_ingestion_request(**overrides) -> dict[str, Any]:
    """Plain-dict ingestion request (as the pipeline API accepts).

    Deterministic by default: fixed processing time `now`."""
    payload = market_tick_payload()
    defaults: dict[str, Any] = dict(
        source_id="feed-xauusd",
        schema_id="market_tick",
        schema_version="1.0.0",
        payload=payload,
        source_timestamp=at(11, 59, 58),
        received_time=at(12, 0, 0),
        environment="SIMULATION",
        event_type="MARKET_DATA_RECEIVED",
        correlation_id=new_identifier("correlation_id"),
        source_event_ref="tick-000001",
        sequence_number=1,
        now=at(12, 0, 2),
    )
    defaults.update(overrides)
    return defaults


def hash_payload(payload: dict[str, Any]) -> str:
    return compute_payload_hash(payload)
