"""Phase 1 time tests: validation, latency, ordering (SECTIONS 12-16)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.time.latency import LatencyCalculator
from core.time.ordering import OrderStatus, OrderingMonitor
from core.time.validation import (
    TimeAnomaly,
    TimeClassification,
    TimeValidator,
)
from tests.phase1_factories import at, make_time_semantics

VALIDATOR = TimeValidator()
SEMANTICS = make_time_semantics()


class TestTimeValidation:
    def test_clean_realtime_tick(self):
        result = VALIDATOR.validate_timestamps(
            event_time=at(11, 59, 58),
            received_time=at(12, 0, 0),
            processed_time=at(12, 0, 1),
            now=at(12, 0, 2),
            semantics=SEMANTICS,
        )
        assert result.classification is TimeClassification.CLEAN
        assert result.anomalies == ()

    def test_naive_datetime_is_invalid(self):
        naive = datetime(2026, 9, 23, 11, 59, 58)
        result = VALIDATOR.validate_timestamps(
            event_time=naive, received_time=at(12, 0), now=at(12, 0, 2), semantics=SEMANTICS
        )
        assert result.classification is TimeClassification.INVALID
        assert any(a.anomaly is TimeAnomaly.SOURCE_INCONSISTENCY for a in result.anomalies)

    def test_missing_timezone_received_time_is_invalid(self):
        result = VALIDATOR.validate_timestamps(
            event_time=at(11, 59, 58),
            received_time=datetime(2026, 9, 23, 12, 0),
            now=at(12, 0, 2),
            semantics=SEMANTICS,
        )
        assert result.classification is TimeClassification.INVALID

    def test_impossible_timestamp_is_invalid(self):
        result = VALIDATOR.validate_timestamps(
            event_time=datetime(1971, 1, 1, tzinfo=at(12).tzinfo),
            received_time=at(12, 0),
            now=at(12, 0, 2),
            semantics=SEMANTICS,
        )
        assert result.classification is TimeClassification.INVALID

    def test_future_event_beyond_tolerance_is_invalid(self):
        result = VALIDATOR.validate_timestamps(
            event_time=at(12, 5),
            received_time=at(12, 5),
            now=at(12, 0),
            semantics=SEMANTICS,
        )
        assert result.classification is TimeClassification.INVALID
        assert any(a.anomaly is TimeAnomaly.FUTURE_EVENT for a in result.anomalies)

    def test_future_within_tolerance_is_clock_skew_not_invalid(self):
        result = VALIDATOR.validate_timestamps(
            event_time=at(12, 0, 30),
            received_time=at(12, 0, 31),
            now=at(12, 0),
            semantics=SEMANTICS,
        )
        assert result.classification is TimeClassification.CLEAN
        assert any(a.anomaly is TimeAnomaly.CLOCK_SKEW for a in result.anomalies)

    def test_calendar_future_events_are_legitimate(self):
        calendar = make_time_semantics(
            timestamp_semantics="HISTORICAL", allows_future_events=True
        )
        result = VALIDATOR.validate_timestamps(
            event_time=at(20, 0),
            received_time=at(12, 0),
            now=at(12, 0),
            semantics=calendar,
        )
        assert result.classification is TimeClassification.CLEAN

    def test_stale_data_classified_stale_not_invalid(self):
        result = VALIDATOR.validate_timestamps(
            event_time=at(11, 0),
            received_time=at(12, 0),
            now=at(12, 0),
            semantics=SEMANTICS,
        )
        assert result.classification is TimeClassification.STALE
        staleness = [a for a in result.anomalies if a.anomaly is TimeAnomaly.STALE_DATA]
        assert staleness and staleness[0].details["stale_threshold_seconds"] == 300
        assert "judged_at" in staleness[0].details

    def test_late_data_is_late_not_invalid(self):
        result = VALIDATOR.validate_timestamps(
            event_time=at(11, 59, 0),
            received_time=at(12, 0),
            now=at(12, 0, 1),
            semantics=SEMANTICS,
        )
        assert result.classification is TimeClassification.LATE
        assert any(a.anomaly is TimeAnomaly.LARGE_LATENCY for a in result.anomalies)

    def test_out_of_order_regression(self):
        result = VALIDATOR.validate_timestamps(
            event_time=at(11, 59, 58),
            received_time=at(12, 0),
            now=at(12, 0, 1),
            semantics=SEMANTICS,
            last_event_time=at(11, 59, 59),
        )
        assert result.classification is TimeClassification.OUT_OF_ORDER

    def test_event_after_received_is_invalid(self):
        result = VALIDATOR.validate_timestamps(
            event_time=at(12, 2),
            received_time=at(12, 0),
            now=at(12, 0, 1),
            semantics=SEMANTICS,
        )
        assert result.classification is TimeClassification.INVALID
        assert any(a.anomaly is TimeAnomaly.NEGATIVE_LATENCY for a in result.anomalies)

    def test_received_after_processed_is_invalid(self):
        result = VALIDATOR.validate_timestamps(
            event_time=at(11, 59, 58),
            received_time=at(12, 0),
            processed_time=at(11, 59, 59),
            now=at(12, 0, 1),
            semantics=SEMANTICS,
        )
        assert result.classification is TimeClassification.INVALID
        assert any(a.anomaly is TimeAnomaly.RECEIVED_AFTER_PROCESSED for a in result.anomalies)

    def test_invalid_worse_than_stale(self):
        assert TimeClassification.INVALID.worse_than(TimeClassification.STALE)
        assert TimeClassification.STALE.worse_than(TimeClassification.LATE)
        assert TimeClassification.LATE.worse_than(TimeClassification.OUT_OF_ORDER)
        assert not TimeClassification.CLEAN.worse_than(TimeClassification.INVALID)


class TestLatency:
    CALC = LatencyCalculator()

    def test_all_metrics_computed(self):
        report = self.CALC.compute(
            event_time=at(12, 0, 0),
            received_time=at(12, 0, 1),
            processed_time=at(12, 0, 3),
        )
        assert report.ingestion_latency_ms == pytest.approx(1000.0)
        assert report.processing_latency_ms == pytest.approx(2000.0)
        assert report.end_to_end_latency_ms == pytest.approx(3000.0)
        assert report.unknown_metrics == ()

    def test_missing_timestamps_are_unknown_never_guessed(self):
        report = self.CALC.compute(
            event_time=at(12, 0), received_time=None, processed_time=None
        )
        assert report.ingestion_latency_ms is None
        assert report.processing_latency_ms is None
        assert report.end_to_end_latency_ms is None
        assert set(report.unknown_metrics) == {
            "ingestion_latency_ms", "processing_latency_ms", "end_to_end_latency_ms"
        }

    def test_negative_latency_becomes_unknown_with_anomaly(self):
        report = self.CALC.compute(
            event_time=at(12, 0, 5),
            received_time=at(12, 0, 1),
            processed_time=at(12, 0, 6),
        )
        assert report.ingestion_latency_ms is None
        assert "NEGATIVE_LATENCY:ingestion" in report.anomalies

    def test_large_latency_flagged(self):
        report = LatencyCalculator(large_latency_threshold_ms=1000).compute(
            event_time=at(11, 0), received_time=at(12, 0), processed_time=at(12, 0, 1)
        )
        assert "LARGE_LATENCY:ingestion" in report.anomalies
        assert report.ingestion_latency_ms == pytest.approx(3_600_000.0)


class TestOrdering:
    def test_first_observation(self):
        monitor = OrderingMonitor()
        result = monitor.check(
            stream_key="feed-xauusd",
            event_time=at(11, 59, 58),
            received_time=at(12, 0),
        )
        assert result.status is OrderStatus.FIRST

    def test_in_order_progression(self):
        monitor = OrderingMonitor()
        monitor.check(stream_key="s", event_time=at(11, 59, 58), received_time=at(12, 0))
        result = monitor.check(stream_key="s", event_time=at(11, 59, 59), received_time=at(12, 0, 1))
        assert result.status is OrderStatus.IN_ORDER

    def test_out_of_order_reports_expected_actual_difference(self):
        monitor = OrderingMonitor()
        monitor.check(stream_key="s", event_time=at(11, 59, 59), received_time=at(12, 0))
        result = monitor.check(stream_key="s", event_time=at(11, 59, 57), received_time=at(12, 0, 1))
        assert result.out_of_order
        assert result.expected is not None and "11:59:59" in result.expected
        assert result.actual is not None and "11:59:57" in result.actual
        assert result.difference_seconds == pytest.approx(2.0)

    def test_streams_are_independent(self):
        monitor = OrderingMonitor()
        monitor.check(stream_key="a", event_time=at(11, 59), received_time=at(12, 0))
        result = monitor.check(stream_key="b", event_time=at(11, 0), received_time=at(12, 0))
        assert result.status is OrderStatus.FIRST

    def test_received_order_regression_detected(self):
        monitor = OrderingMonitor()
        monitor.check(stream_key="s", event_time=at(11, 59, 58), received_time=at(12, 0, 5))
        result = monitor.check(stream_key="s", event_time=at(12, 0, 10), received_time=at(12, 0, 1))
        assert result.out_of_order

    def test_monitor_does_not_mutate_timestamps(self):
        monitor = OrderingMonitor()
        event_time = at(11, 59, 58)
        received = at(12, 0)
        monitor.check(stream_key="s", event_time=event_time, received_time=received)
        assert event_time == at(11, 59, 58)
        assert received == at(12, 0)
