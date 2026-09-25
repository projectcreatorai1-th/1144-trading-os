"""Time contract tests (SECTION 8) - including failure tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from architecture.contracts.errors import TimeValidationError
from architecture.contracts.time import (
    TimestampType,
    canonical,
    ensure_not_before,
    ensure_utc,
    latency_ms,
    parse_canonical,
    utc_now,
)
from core.time.contracts import (
    DeterministicClock,
    LatencyMeasurement,
    SystemClock,
    Timestamp,
)
from tests.factories import T0, at

UTC = timezone.utc


class TestCanonicalTime:
    def test_utc_now_is_timezone_aware(self):
        now = utc_now()
        assert now.tzinfo is not None

    def test_ensure_utc_converts_timezone(self):
        bangkok = timezone(__import__("datetime").timedelta(hours=7))
        local = datetime(2026, 9, 23, 19, 0, tzinfo=bangkok)
        converted = ensure_utc(local)
        assert converted.utcoffset().total_seconds() == 0
        assert converted.hour == 12

    def test_canonical_string_carries_offset(self):
        assert canonical(T0).endswith("+00:00")

    def test_parse_canonical_roundtrip(self):
        parsed = parse_canonical(canonical(at(12, 30)))
        assert parsed == at(12, 30)

    def test_latency_ms_computes(self):
        assert latency_ms(at(12, 0), at(12, 0, )) == 0.0
        value = latency_ms(at(12, 0), at(12, 1))
        assert value == pytest.approx(60000.0)


class TestTimeFailures:
    def test_naive_datetime_rejected(self):
        naive = datetime(2026, 9, 23, 12, 0)
        with pytest.raises(TimeValidationError):
            ensure_utc(naive)

    def test_non_datetime_rejected(self):
        with pytest.raises(TimeValidationError):
            ensure_utc("2026-09-23T12:00:00+00:00")

    def test_naive_iso_string_rejected(self):
        with pytest.raises(TimeValidationError):
            parse_canonical("2026-09-23T12:00:00")

    def test_invalid_iso_string_rejected(self):
        with pytest.raises(TimeValidationError):
            parse_canonical("not-a-timestamp")

    def test_not_before_violation_rejected(self):
        with pytest.raises(TimeValidationError):
            ensure_not_before(at(11), not_before=at(12), location="x")

    def test_latency_end_before_start_rejected(self):
        with pytest.raises(TimeValidationError):
            latency_ms(at(13), at(12))


class TestClocks:
    def test_system_clock_aware(self):
        assert SystemClock().now().tzinfo is not None

    def test_deterministic_clock_fixed(self):
        clock = DeterministicClock(at(9, 30))
        assert clock.now() == at(9, 30)

    def test_deterministic_clock_advances(self):
        clock = DeterministicClock(at(9, 30))
        clock.advance_to(at(10, 0))
        assert clock.now() == at(10, 0)

    def test_deterministic_clock_refuses_backwards(self):
        clock = DeterministicClock(at(10, 0))
        with pytest.raises(TimeValidationError):
            clock.advance_to(at(9, 0))

    def test_deterministic_clock_rejects_naive_init(self):
        with pytest.raises(TimeValidationError):
            DeterministicClock(datetime(2026, 9, 23, 10, 0))


class TestTimestampValueObject:
    def test_valid_timestamp(self):
        ts = Timestamp(value=at(12, 0), type=TimestampType.DECISION_TIME)
        ts.validate()
        assert ts.canonical.endswith("+00:00")

    def test_timestamp_type_enum_complete(self):
        assert {t.value for t in TimestampType} == {
            "EVENT_TIME", "RECEIVED_TIME", "PROCESSED_TIME", "DECISION_TIME", "EXECUTION_TIME"
        }

    def test_naive_timestamp_rejected(self):
        ts = Timestamp(value=datetime(2026, 9, 23, 12, 0), type=TimestampType.EVENT_TIME)
        with pytest.raises(TimeValidationError):
            ts.validate()

    def test_latency_measurement(self):
        start = Timestamp(at(12, 0), TimestampType.DECISION_TIME)
        end = Timestamp(at(12, 0, ), TimestampType.EXECUTION_TIME)
        measurement = LatencyMeasurement(start=start, end=end, label="decision_to_execution")
        assert measurement.milliseconds >= 0.0
