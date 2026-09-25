"""Event ordering monitor (owned by core.time).

Received order is NOT assumed to equal event order. The monitor tracks per
stream and reports OUT_OF_ORDER with expected/actual/difference. It never
rewrites timestamps (SECTION 14).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from architecture.contracts.time import ensure_utc

CONTRACT_VERSION = "1.1.0"


class OrderStatus(Enum):
    IN_ORDER = "IN_ORDER"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    FIRST = "FIRST"


@dataclass(frozen=True)
class OrderCheckResult:
    status: OrderStatus
    order_key: str
    expected: str | None = None
    actual: str | None = None
    difference_seconds: float | None = None

    @property
    def out_of_order(self) -> bool:
        return self.status is OrderStatus.OUT_OF_ORDER

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "order_key": self.order_key,
            "expected": self.expected,
            "actual": self.actual,
            "difference_seconds": self.difference_seconds,
        }


class OrderingMonitor:
    """Tracks event_time / received_time per stream key.

    check(...) both evaluates and advances the stream state, so callers see
    how the new observation relates to everything observed before it.
    """

    def __init__(self) -> None:
        self._last_event_time: dict[str, datetime] = {}
        self._last_received_time: dict[str, datetime] = {}

    def check(
        self,
        *,
        stream_key: str,
        event_time: datetime,
        received_time: datetime,
    ) -> OrderCheckResult:
        event_utc = ensure_utc(event_time, location="ordering.event_time")
        received_utc = ensure_utc(received_time, location="ordering.received_time")

        last_event = self._last_event_time.get(stream_key)
        last_received = self._last_received_time.get(stream_key)

        result = OrderCheckResult(status=OrderStatus.FIRST, order_key=stream_key)
        if last_event is not None and event_utc < last_event:
            result = OrderCheckResult(
                status=OrderStatus.OUT_OF_ORDER,
                order_key=stream_key,
                expected=last_event.isoformat(),
                actual=event_utc.isoformat(),
                difference_seconds=(last_event - event_utc).total_seconds(),
            )
        elif last_event is not None:
            result = OrderCheckResult(
                status=OrderStatus.IN_ORDER,
                order_key=stream_key,
                expected=last_event.isoformat(),
                actual=event_utc.isoformat(),
                difference_seconds=(event_utc - last_event).total_seconds(),
            )

        if last_received is not None and received_utc < last_received:
            # Ingestion order regressed as well - recorded via received view.
            result = OrderCheckResult(
                status=OrderStatus.OUT_OF_ORDER,
                order_key=stream_key,
                expected=last_received.isoformat(),
                actual=received_utc.isoformat(),
                difference_seconds=(last_received - received_utc).total_seconds(),
            )

        if last_event is None or event_utc >= last_event:
            self._last_event_time[stream_key] = event_utc
        if last_received is None or received_utc >= last_received:
            self._last_received_time[stream_key] = received_utc
        return result

    def last_event_time(self, stream_key: str) -> datetime | None:
        return self._last_event_time.get(stream_key)
