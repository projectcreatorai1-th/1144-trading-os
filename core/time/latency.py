"""Latency measurement (owned by core.time).

Metrics: ingestion (received - event), processing (processed - received),
end-to-end (processed - event). Missing timestamps produce UNKNOWN (None) -
values are never guessed. Impossible (negative) metrics become UNKNOWN plus
a recorded anomaly (SECTION 15/16).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from architecture.contracts.time import ensure_utc, utc_now

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class LatencyReport:
    measured_at: datetime
    ingestion_latency_ms: float | None = None
    processing_latency_ms: float | None = None
    end_to_end_latency_ms: float | None = None
    event_id: str | None = None
    correlation_id: str | None = None
    anomalies: tuple[str, ...] = ()

    @property
    def unknown_metrics(self) -> tuple[str, ...]:
        names = []
        if self.ingestion_latency_ms is None:
            names.append("ingestion_latency_ms")
        if self.processing_latency_ms is None:
            names.append("processing_latency_ms")
        if self.end_to_end_latency_ms is None:
            names.append("end_to_end_latency_ms")
        return tuple(names)

    def to_dict(self) -> dict[str, object]:
        return {
            "measured_at": ensure_utc(self.measured_at).isoformat(),
            "ingestion_latency_ms": self.ingestion_latency_ms,
            "processing_latency_ms": self.processing_latency_ms,
            "end_to_end_latency_ms": self.end_to_end_latency_ms,
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "anomalies": list(self.anomalies),
        }


class LatencyCalculator:
    """Deterministic latency computation; UNKNOWN instead of guesses."""

    def __init__(self, large_latency_threshold_ms: float = 60_000.0) -> None:
        self._large_threshold = large_latency_threshold_ms

    def compute(
        self,
        *,
        event_time: datetime | None,
        received_time: datetime | None,
        processed_time: datetime | None,
        event_id: str | None = None,
        correlation_id: str | None = None,
        measured_at: datetime | None = None,
    ) -> LatencyReport:
        anomalies: list[str] = []
        ingestion = self._metric(event_time, received_time, "ingestion", anomalies)
        processing = self._metric(received_time, processed_time, "processing", anomalies)
        end_to_end = self._metric(event_time, processed_time, "end_to_end", anomalies)
        return LatencyReport(
            measured_at=measured_at or utc_now(),
            ingestion_latency_ms=ingestion,
            processing_latency_ms=processing,
            end_to_end_latency_ms=end_to_end,
            event_id=event_id,
            correlation_id=correlation_id,
            anomalies=tuple(anomalies),
        )

    def _metric(
        self,
        start: datetime | None,
        end: datetime | None,
        name: str,
        anomalies: list[str],
    ) -> float | None:
        if start is None or end is None:
            return None  # UNKNOWN - never guessed
        try:
            s = ensure_utc(start, location=f"latency.{name}.start")
            e = ensure_utc(end, location=f"latency.{name}.end")
        except Exception:
            return None
        delta_ms = (e - s).total_seconds() * 1000.0
        if delta_ms < 0:
            anomalies.append(f"NEGATIVE_LATENCY:{name}")
            return None  # impossible value -> UNKNOWN + anomaly, never a guess
        if delta_ms > self._large_threshold:
            anomalies.append(f"LARGE_LATENCY:{name}")
        return delta_ms
