"""Time validation and timestamp anomaly detection (owned by core.time).

Separates INVALID (impossible/broken timestamps) from LATE (delayed but real
data), STALE (beyond staleness threshold) and OUT_OF_ORDER (regression vs
the stream). Rules respect each source's timestamp semantics - no blind
global thresholds (SECTION 13/16). Original timestamps are never fixed here;
anomalies are marked only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from architecture.contracts.time import UTC, ensure_utc

CONTRACT_VERSION = "1.1.0"

#: Outside this window a timestamp is impossible (clock corruption).
MIN_REASONABLE_TIME = datetime(2000, 1, 1, tzinfo=UTC)
MAX_REASONABLE_TIME = datetime(2100, 1, 1, tzinfo=UTC)


class TimeAnomaly(Enum):
    CLOCK_SKEW = "CLOCK_SKEW"
    FUTURE_EVENT = "FUTURE_EVENT"
    TIMESTAMP_REGRESSION = "TIMESTAMP_REGRESSION"
    STALE_DATA = "STALE_DATA"
    NEGATIVE_LATENCY = "NEGATIVE_LATENCY"
    LARGE_LATENCY = "LARGE_LATENCY"
    RECEIVED_AFTER_PROCESSED = "RECEIVED_AFTER_PROCESSED"
    SOURCE_INCONSISTENCY = "SOURCE_INCONSISTENCY"


class TimeClassification(Enum):
    CLEAN = "CLEAN"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    LATE = "LATE"
    STALE = "STALE"
    INVALID = "INVALID"

    def worse_than(self, other: "TimeClassification") -> bool:
        order = {
            TimeClassification.CLEAN: 0,
            TimeClassification.OUT_OF_ORDER: 1,
            TimeClassification.LATE: 2,
            TimeClassification.STALE: 3,
            TimeClassification.INVALID: 4,
        }
        return order[self] > order[other]


@dataclass(frozen=True)
class TimeAnomalyInstance:
    anomaly: TimeAnomaly
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimeValidationResult:
    classification: TimeClassification
    anomalies: tuple[TimeAnomalyInstance, ...] = ()
    checked_fields: tuple[str, ...] = ()

    @property
    def invalid(self) -> bool:
        return self.classification is TimeClassification.INVALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "anomalies": [
                {"anomaly": a.anomaly.value, "message": a.message, "details": a.details}
                for a in self.anomalies
            ],
            "checked_fields": list(self.checked_fields),
        }


@dataclass(frozen=True)
class SourceTimeSemantics:
    """Time expectations of one source (from the data source registry)."""

    source_id: str
    timestamp_semantics: str  # REALTIME / DELAYED / BATCH / HISTORICAL
    allows_future_events: bool = False
    future_tolerance_seconds: float = 60.0
    stale_threshold_seconds: float = 300.0
    max_expected_delay_seconds: float | None = None

    @property
    def historical(self) -> bool:
        return self.timestamp_semantics == "HISTORICAL" or self.allows_future_events


class TimeValidator:
    """Classifies timestamps; never mutates them."""

    def validate_timestamps(
        self,
        *,
        event_time: datetime | None,
        received_time: datetime | None,
        processed_time: datetime | None = None,
        now: datetime,
        semantics: SourceTimeSemantics,
        last_event_time: datetime | None = None,
    ) -> TimeValidationResult:
        anomalies: list[TimeAnomalyInstance] = []
        classification = TimeClassification.CLEAN
        checked: list[str] = []

        def worsen(candidate: TimeClassification) -> None:
            nonlocal classification
            if candidate.worse_than(classification):
                classification = candidate

        event_utc, event_broken = self._as_utc(event_time, "event_time", anomalies, checked)
        received_utc, received_broken = self._as_utc(received_time, "received_time", anomalies, checked)
        processed_utc, processed_broken = self._as_utc(processed_time, "processed_time", anomalies, checked)
        for broken in (event_broken, received_broken, processed_broken):
            if broken:
                worsen(TimeClassification.INVALID)
        for value, name in ((event_utc, "event_time"), (received_utc, "received_time"), (processed_utc, "processed_time")):
            if value is not None and not (MIN_REASONABLE_TIME <= value <= MAX_REASONABLE_TIME):
                anomalies.append(
                    TimeAnomalyInstance(
                        TimeAnomaly.SOURCE_INCONSISTENCY,
                        f"{name} is outside the reasonable time window",
                        {"value": value.isoformat(), "window": "2000-01-01..2100-01-01"},
                    )
                )
                worsen(TimeClassification.INVALID)

        now_utc = ensure_utc(now, location="time.now")

        if event_utc is not None and not semantics.historical:
            lead = (event_utc - now_utc).total_seconds()
            if lead > semantics.future_tolerance_seconds:
                anomalies.append(
                    TimeAnomalyInstance(
                        TimeAnomaly.FUTURE_EVENT,
                        "event_time lies in the future beyond the source tolerance",
                        {
                            "event_time": event_utc.isoformat(),
                            "now": now_utc.isoformat(),
                            "lead_seconds": lead,
                            "tolerance_seconds": semantics.future_tolerance_seconds,
                        },
                    )
                )
                worsen(TimeClassification.INVALID)
            elif lead > 0:
                anomalies.append(
                    TimeAnomalyInstance(
                        TimeAnomaly.CLOCK_SKEW,
                        "event_time slightly ahead of now (within tolerance)",
                        {"lead_seconds": lead, "tolerance_seconds": semantics.future_tolerance_seconds},
                    )
                )

        if event_utc is not None and received_utc is not None and not semantics.historical:
            lead = (event_utc - received_utc).total_seconds()
            if lead > semantics.future_tolerance_seconds:
                anomalies.append(
                    TimeAnomalyInstance(
                        TimeAnomaly.NEGATIVE_LATENCY,
                        "event_time after received_time beyond tolerance (impossible)",
                        {
                            "event_time": event_utc.isoformat(),
                            "received_time": received_utc.isoformat(),
                            "lead_seconds": lead,
                            "tolerance_seconds": semantics.future_tolerance_seconds,
                        },
                    )
                )
                worsen(TimeClassification.INVALID)
            elif lead > 0:
                anomalies.append(
                    TimeAnomalyInstance(
                        TimeAnomaly.CLOCK_SKEW,
                        "event_time after received_time within tolerance",
                        {"lead_seconds": lead},
                    )
                )
            elif semantics.max_expected_delay_seconds is not None:
                delay = -lead
                if delay > semantics.max_expected_delay_seconds:
                    anomalies.append(
                        TimeAnomalyInstance(
                            TimeAnomaly.LARGE_LATENCY,
                            "delay from event to receipt exceeds the source expectation",
                            {
                                "delay_seconds": delay,
                                "max_expected_delay_seconds": semantics.max_expected_delay_seconds,
                            },
                        )
                    )
                    worsen(TimeClassification.LATE)

        if received_utc is not None and processed_utc is not None and processed_utc < received_utc:
            anomalies.append(
                TimeAnomalyInstance(
                    TimeAnomaly.RECEIVED_AFTER_PROCESSED,
                    "processed_time before received_time (impossible)",
                    {"received_time": received_utc.isoformat(), "processed_time": processed_utc.isoformat()},
                )
            )
            worsen(TimeClassification.INVALID)

        if event_utc is not None and not semantics.historical:
            age = (now_utc - event_utc).total_seconds()
            if age > semantics.stale_threshold_seconds:
                anomalies.append(
                    TimeAnomalyInstance(
                        TimeAnomaly.STALE_DATA,
                        "data is stale: event_time beyond the staleness threshold",
                        {
                            "age_seconds": age,
                            "stale_threshold_seconds": semantics.stale_threshold_seconds,
                            "judged_at": now_utc.isoformat(),
                        },
                    )
                )
                worsen(TimeClassification.STALE)

        if event_utc is not None and last_event_time is not None and event_utc < last_event_time:
            anomalies.append(
                TimeAnomalyInstance(
                    TimeAnomaly.TIMESTAMP_REGRESSION,
                    "event_time regressed against the previous event of the stream",
                    {
                        "expected_after": last_event_time.isoformat(),
                        "actual": event_utc.isoformat(),
                        "difference_seconds": (last_event_time - event_utc).total_seconds(),
                    },
                )
            )
            worsen(TimeClassification.OUT_OF_ORDER)

        return TimeValidationResult(
            classification=classification,
            anomalies=tuple(anomalies),
            checked_fields=tuple(checked),
        )

    @staticmethod
    def _as_utc(
        value: datetime | None,
        name: str,
        anomalies: list[TimeAnomalyInstance],
        checked: list[str],
    ) -> tuple[datetime | None, bool]:
        """Normalize to UTC; returns (value, broken) where broken means the
        input was structurally invalid (non-datetime or naive)."""
        if value is None:
            return None, False
        checked.append(name)
        if not isinstance(value, datetime):
            anomalies.append(
                TimeAnomalyInstance(
                    TimeAnomaly.SOURCE_INCONSISTENCY,
                    f"{name} is not a datetime",
                    {"actual_type": type(value).__name__},
                )
            )
            return None, True
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            anomalies.append(
                TimeAnomalyInstance(
                    TimeAnomaly.SOURCE_INCONSISTENCY,
                    f"{name} is a naive datetime (missing timezone)",
                    {"value": str(value)},
                )
            )
            return None, True
        return value.astimezone(UTC), False
