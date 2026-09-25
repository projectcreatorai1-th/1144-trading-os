"""Bounded metrics registry + window analytics (platform.monitoring).

Records MetricPoint samples per core metric in bounded deques (memory cap:
the disk-full incident proved unbounded buffers are an operational risk)
and answers window queries with count/sum/min/max and nearest-rank
percentiles. This is measurement infrastructure only - never authority.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.time import ensure_utc, utc_now

from platform.monitoring.contracts import (
    CORE_METRICS,
    Comparison,
    MetricPoint,
    SLI,
    _compare,
)

MAX_POINTS_PER_METRIC = 10_000


@dataclass(frozen=True)
class WindowStats:
    count: int
    sum: float
    min: float | None
    max: float | None
    p50: float | None
    p95: float | None
    p99: float | None

    def as_dict(self) -> dict:
        return {"count": self.count, "sum": round(self.sum, 6),
                "min": self.min, "max": self.max,
                "p50": self.p50, "p95": self.p95, "p99": self.p99}


class MetricsRegistry:
    def __init__(self, maxlen: int = MAX_POINTS_PER_METRIC) -> None:
        self._series: dict[str, deque] = {
            name: deque(maxlen=maxlen) for name in CORE_METRICS}
        self._maxlen = maxlen

    def record(self, point: MetricPoint) -> None:
        point.validate()
        self._series[point.name].append(point)

    def record_value(self, name: str, value: float, *, at: datetime | None = None,
                     labels: Mapping[str, str] | None = None) -> MetricPoint:
        point = MetricPoint(name=name, value=float(value),
                            at=ensure_utc(at) if at else utc_now(),
                            labels=dict(labels or {}))
        self.record(point)
        return point

    def points(self, name: str, *, since: datetime | None = None,
               until: datetime | None = None) -> list[MetricPoint]:
        if name not in self._series:
            raise ContractError(f"unknown metric {name!r}",
                                location="metrics.query", rule_id="OBS-001")
        lo = ensure_utc(since) if since else None
        hi = ensure_utc(until) if until else None
        return [p for p in self._series[name]
                if (lo is None or p.at >= lo) and (hi is None or p.at <= hi)]

    def stats(self, name: str, *, since: datetime | None = None,
              until: datetime | None = None) -> WindowStats:
        values = sorted(p.value for p in self.points(name, since=since,
                                                     until=until))
        if not values:
            return WindowStats(0, 0.0, None, None, None, None, None)

        def pct(q: float) -> float:
            # nearest-rank percentile on the sorted window
            rank = max(1, min(len(values), int(round(q * len(values)))))
            return values[rank - 1]

        return WindowStats(len(values), float(sum(values)),
                           values[0], values[-1],
                           pct(0.50), pct(0.95), pct(0.99))

    def memory_bound_points(self) -> int:
        return self._maxlen * len(self._series)

    # ------------------------------------------------------------------ #
    # instrumentation helpers (composition layer only - they wrap EXISTING
    # surfaces; core engines are never modified for instrumentation)
    # ------------------------------------------------------------------ #
    def timed(self, metric: str, *, labels: Mapping[str, str] | None = None,
              clock: Callable[[], datetime] = utc_now):
        """Context manager measuring a duration in ms into `metric`."""
        import contextlib

        @contextlib.contextmanager
        def _timer():
            start = clock()
            try:
                yield
            finally:
                end = clock()
                ms = (end - start).total_seconds() * 1000.0
                if ms >= 0:
                    self.record_value(metric, ms, at=end, labels=labels)

        return _timer()

    def sli_compliance(self, sli: SLI, *, now: datetime | None = None) \
            -> tuple[int, int]:
        """(good, total) for the SLI's window ending at `now`."""
        sli.validate()
        moment = ensure_utc(now) if now else utc_now()
        window_points = self.points(
            sli.metric, since=moment - sli.window, until=moment)
        good = sum(1 for p in window_points
                   if _compare(p.value, sli.op, sli.threshold))
        return good, len(window_points)
