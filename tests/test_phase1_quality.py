"""Phase 1 quality engine tests (SECTION 8/9) - all 15 rules + classification."""
from __future__ import annotations

from datetime import datetime

import pytest

from core.validation.contracts import DataQualityLevel
from core.validation.engine import (
    QualityEngine,
    QualityCheckResult,
    QualityInput,
    QualitySeverity,
    SequenceObservation,
)
from tests.factories import at

ENGINE = QualityEngine()


def evaluate(**overrides):
    defaults = dict(
        schema_issues=(),
        schema_validation_executed=True,
        schema_resolved=True,
        source_registered=True,
        source_enabled=True,
        source_id="feed-xauusd",
        judged_at=at(12, 0),
    )
    judged = defaults.pop("judged_at")
    defaults.update(overrides)
    return ENGINE.evaluate(QualityInput(**defaults), judged_at=judged)


class TestQualityCheckResultContract:
    def test_pass_result_valid(self):
        QualityCheckResult(
            rule_id="DQ-001", severity=QualitySeverity.PASS, message="ok"
        ).validate()

    def test_warning_requires_evidence(self):
        with pytest.raises(Exception):
            QualityCheckResult(
                rule_id="DQ-007", severity=QualitySeverity.WARNING, message="stale"
            ).validate()

    def test_rule_id_out_of_range_rejected(self):
        with pytest.raises(Exception):
            QualityCheckResult(
                rule_id="DQ-099", severity=QualitySeverity.PASS, message="x"
            ).validate()

    def test_invalid_severity_rejected(self):
        with pytest.raises(Exception):
            QualityCheckResult(
                rule_id="DQ-001", severity="MAYBE", message="x"
            ).validate()


class TestSchemaDrivenChecks:
    def test_missing_field_maps_to_dq001(self):
        result = evaluate(
            schema_issues=({"rule_id": "SCHEMA-REQUIRED", "location": "market_tick.symbol",
                            "message": "Missing required field 'symbol'", "details": {}},)
        )
        assert result.level is DataQualityLevel.INVALID
        assert any(c.rule_id == "DQ-001" and c.severity is QualitySeverity.FAIL for c in result.checks)

    def test_invalid_type_maps_to_dq002(self):
        result = evaluate(
            schema_issues=({"rule_id": "SCHEMA-TYPE", "location": "market_tick.bid",
                            "message": "expected string", "details": {}},)
        )
        assert any(c.rule_id == "DQ-002" for c in result.checks)

    def test_invalid_enum_maps_to_dq003(self):
        result = evaluate(
            schema_issues=({"rule_id": "SCHEMA-ENUM", "location": "x.y",
                            "message": "not in enum", "details": {}},)
        )
        assert any(c.rule_id == "DQ-003" for c in result.checks)

    def test_naive_datetime_maps_to_dq005(self):
        result = evaluate(naive_datetime_fields=("event_time",))
        assert result.level is DataQualityLevel.INVALID
        assert any(c.rule_id == "DQ-005" for c in result.checks)


class TestTimeDrivenChecks:
    def _anomaly(self, kind: str, details: dict | None = None):
        return {"anomaly": kind, "message": kind, "details": details or {}}

    def test_future_event_is_invalid(self):
        result = evaluate(time_anomalies=(self._anomaly("FUTURE_EVENT", {"lead_seconds": 300}),))
        assert result.level is DataQualityLevel.INVALID
        assert any(c.rule_id == "DQ-006" for c in result.checks)

    def test_stale_is_stale_not_invalid(self):
        result = evaluate(
            time_anomalies=(self._anomaly("STALE_DATA", {"stale_threshold_seconds": 300}),)
        )
        assert result.level is DataQualityLevel.STALE
        assert any(c.rule_id == "DQ-007" for c in result.checks)

    def test_timestamp_regression_is_degraded(self):
        result = evaluate(time_anomalies=(self._anomaly("TIMESTAMP_REGRESSION", {"difference_seconds": 2}),))
        assert result.level is DataQualityLevel.DEGRADED

    def test_invalid_timestamp_anomaly(self):
        result = evaluate(time_anomalies=(self._anomaly("SOURCE_INCONSISTENCY", {"value": "x"}),))
        assert result.level is DataQualityLevel.INVALID
        assert any(c.rule_id == "DQ-004" for c in result.checks)


class TestStreamChecks:
    def test_duplicate_is_warning_not_invalid(self):
        result = evaluate(duplicate=True, duplicate_of="raw_x")
        assert result.level is DataQualityLevel.DEGRADED
        assert any(c.rule_id == "DQ-008" for c in result.checks)

    def test_sequence_gap(self):
        result = evaluate(sequence=SequenceObservation(status="GAP", expected=4, actual=6, difference=2))
        assert any(c.rule_id == "DQ-009" and "gap" in c.message for c in result.checks)
        assert result.level is DataQualityLevel.DEGRADED

    def test_sequence_regression(self):
        result = evaluate(sequence=SequenceObservation(status="REGRESSION", expected=5, actual=3))
        assert any("regression" in c.message for c in result.checks)

    def test_sequence_duplicate(self):
        result = evaluate(sequence=SequenceObservation(status="DUPLICATE", expected=5, actual=5))
        assert any("duplicate" in c.message for c in result.checks)

    def test_sequence_explicit_unknown_is_not_failure(self):
        result = evaluate(sequence=SequenceObservation(status="UNKNOWN"))
        assert result.level is DataQualityLevel.VALIDATED

    def test_out_of_order_warning(self):
        result = evaluate(out_of_order=True, order_details={"expected": "a", "actual": "b"})
        assert any(c.rule_id == "DQ-010" for c in result.checks)
        assert result.level is DataQualityLevel.DEGRADED


class TestSourceAndIntegrityChecks:
    def test_invalid_symbol(self):
        result = evaluate(symbol="xau-usd!!", symbol_check_applicable=True)
        assert result.level is DataQualityLevel.INVALID
        assert any(c.rule_id == "DQ-011" for c in result.checks)

    def test_valid_symbol(self):
        result = evaluate(symbol="XAUUSD", symbol_check_applicable=True)
        assert result.level is DataQualityLevel.VALIDATED

    def test_unknown_source_rejected(self):
        result = evaluate(source_registered=False)
        assert result.level is DataQualityLevel.INVALID
        assert any(c.rule_id == "DQ-012" and "Unknown" in c.message for c in result.checks)

    def test_disabled_source_rejected(self):
        result = evaluate(source_enabled=False)
        assert result.level is DataQualityLevel.INVALID
        assert any(c.rule_id == "DQ-012" and "disabled" in c.message for c in result.checks)

    def test_unresolved_schema_rejected(self):
        result = evaluate(schema_resolved=False)
        assert result.level is DataQualityLevel.INVALID
        assert any(c.rule_id == "DQ-013" for c in result.checks)

    def test_hash_mismatch_rejected(self):
        result = evaluate(hash_matches=False)
        assert result.level is DataQualityLevel.INVALID
        assert any(c.rule_id == "DQ-014" for c in result.checks)

    def test_missing_provenance_on_derived(self):
        result = evaluate(provenance_check_applicable=True, provenance_present=False)
        assert result.level is DataQualityLevel.INVALID
        assert any(c.rule_id == "DQ-015" for c in result.checks)


class TestClassification:
    def test_clean_data_is_validated(self):
        result = evaluate()
        assert result.level is DataQualityLevel.VALIDATED
        assert result.reasons == ()

    def test_invalid_blocks_downstream(self):
        result = evaluate(hash_matches=False)
        assert not result.level.allows_downstream_processing

    def test_fail_beats_stale_and_warning(self):
        result = evaluate(
            time_anomalies=({"anomaly": "STALE_DATA", "message": "stale", "details": {}},),
            hash_matches=False,
        )
        assert result.level is DataQualityLevel.INVALID

    def test_empty_checks_unknown_never_valid(self):
        result = ENGINE.evaluate(
            QualityInput(), judged_at=at(12, 0)
        )
        assert result.level is DataQualityLevel.UNKNOWN
