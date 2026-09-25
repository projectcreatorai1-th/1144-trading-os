"""Data quality engine (owned by core.validation).

Fifteen structured checks (DQ-001..DQ-015) producing PASS/WARNING/FAIL
results with evidence. Classification: VALID (VALIDATED), DEGRADED, STALE,
INVALID, UNKNOWN. INVALID data never enters downstream production
processing; UNKNOWN never means valid (SECTION 8/9).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.errors import ContractValidationError
from core.validation.contracts import DataQualityLevel

CONTRACT_VERSION = "1.1.0"


class QualitySeverity(Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


@dataclass(frozen=True)
class QualityCheckResult:
    rule_id: str
    severity: QualitySeverity
    message: str
    field: str | None = None
    details: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def validate(self) -> None:
        known_rules = {f"DQ-{i:03d}" for i in range(1, 16)}
        if self.rule_id not in known_rules:
            raise ContractValidationError(
                f"quality.rule_id must be one of DQ-001..DQ-015, got '{self.rule_id}'",
                location="quality.rule_id",
            )
        if not isinstance(self.severity, QualitySeverity):
            raise ContractValidationError(
                f"quality.severity must be a QualitySeverity, got {self.severity!r}",
                location="quality.severity",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.message, str) or not self.message:
            raise ContractValidationError(
                "quality.message must be a non-empty string",
                location="quality.message",
            )
        if self.field is not None and (not isinstance(self.field, str) or not self.field):
            raise ContractValidationError(
                "quality.field must be a non-empty string or None",
                location="quality.field",
            )
        if not isinstance(self.details, Mapping):
            raise ContractValidationError(
                "quality.details must be a mapping",
                location="quality.details",
            )
        if self.severity is not QualitySeverity.PASS and not self.details:
            raise ContractValidationError(
                "WARNING/FAIL results must carry evidence in details",
                location="quality.details",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "message": self.message,
            "field": self.field,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class QualityEvaluation:

    """Aggregate quality verdict for one ingested record."""

    level: DataQualityLevel
    checks: tuple[QualityCheckResult, ...]
    judged_at: datetime

    @property
    def failed(self) -> tuple[QualityCheckResult, ...]:
        return tuple(c for c in self.checks if c.severity is QualitySeverity.FAIL)

    @property
    def warnings(self) -> tuple[QualityCheckResult, ...]:
        return tuple(c for c in self.checks if c.severity is QualitySeverity.WARNING)

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(
            f"{c.rule_id}:{c.message}" for c in self.failed + self.warnings
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "judged_at": self.judged_at.isoformat(),
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass(frozen=True)
class SequenceObservation:
    """Outcome of a sequence check (DQ-009/DQ-023 style stream checks)."""

    status: str  # OK | DUPLICATE | GAP | REGRESSION | UNKNOWN
    expected: int | None = None
    actual: int | None = None
    difference: int | None = None
    details: Mapping[str, Any] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True)
class QualityInput:
    """Everything the quality engine needs, gathered by the caller (core.data).

    The engine itself stays layer-pure: it never imports core.data/core.time;
    callers pass plain results in."""

    schema_issues: tuple[Mapping[str, Any], ...] = ()  # kernel ValidationIssue dicts
    schema_validation_executed: bool = False
    schema_resolved: bool = True
    source_registered: bool = True
    source_enabled: bool = True
    source_id: str | None = None
    time_anomalies: tuple[Mapping[str, Any], ...] = ()  # TimeAnomalyInstance.to_dict()-like
    time_classification: str = "CLEAN"
    naive_datetime_fields: tuple[str, ...] = ()
    duplicate: bool = False
    duplicate_of: str | None = None
    sequence: SequenceObservation | None = None
    out_of_order: bool = False
    order_details: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    symbol: str | None = None
    symbol_check_applicable: bool = False
    hash_matches: bool | None = None
    provenance_present: bool = True
    provenance_check_applicable: bool = False


_SCHEMA_RULE_MAP = {
    "SCHEMA-REQUIRED": ("DQ-001", "field"),
    "SCHEMA-TYPE": ("DQ-002", "type"),
    "SCHEMA-ENUM": ("DQ-003", "enum"),
    "SCHEMA-ENVIRONMENT": ("DQ-003", "enum"),
    "SCHEMA-TIMESTAMP": ("DQ-004", "timestamp"),
}

_TIME_ANOMALY_RULE_MAP = {
    "FUTURE_EVENT": ("DQ-006", True),
    "STALE_DATA": ("DQ-007", False),
    "LARGE_LATENCY": ("DQ-007", False),  # delayed data is a staleness form, never invalid
    "TIMESTAMP_REGRESSION": ("DQ-010", False),
}


class QualityEngine:
    """Deterministic DQ-001..DQ-015 evaluation; never a bare boolean."""

    def evaluate(self, data: QualityInput, *, judged_at: datetime) -> QualityEvaluation:
        checks: list[QualityCheckResult] = []

        for issue in data.schema_issues:
            rule, kind = _SCHEMA_RULE_MAP.get(str(issue.get("rule_id")), ("DQ-002", "type"))
            checks.append(
                QualityCheckResult(
                    rule_id=rule,
                    severity=QualitySeverity.FAIL,
                    message=f"Schema validation failed ({kind}): {issue.get('message', '')}",
                    field=str(issue.get("location", "")).split(".")[-1] or None,
                    details={"issue": dict(issue)},
                )
            )
        if data.schema_validation_executed:
            checks.append(
                QualityCheckResult(
                    rule_id="DQ-001",
                    severity=QualitySeverity.PASS,
                    message=(
                        "Schema validation passed"
                        if not data.schema_issues
                        else f"Schema validation failed with {len(data.schema_issues)} issue(s)"
                    ),
                )
            )

        # DQ-005 naive datetime (from time validation structural breakage)
        for field_name in data.naive_datetime_fields:
            checks.append(
                QualityCheckResult(
                    rule_id="DQ-005",
                    severity=QualitySeverity.FAIL,
                    message=f"Naive datetime without timezone in '{field_name}'",
                    field=field_name,
                    details={"field": field_name},
                )
            )

        # DQ-004 invalid timestamp / DQ-006 future / DQ-007 stale / DQ-010 out-of-order
        for anomaly in data.time_anomalies:
            anomaly_type = str(anomaly.get("anomaly", ""))
            details = dict(anomaly.get("details", {}))
            if anomaly_type in ("SOURCE_INCONSISTENCY",):
                checks.append(
                    QualityCheckResult(
                        rule_id="DQ-004",
                        severity=QualitySeverity.FAIL,
                        message=str(anomaly.get("message", "invalid timestamp")),
                        details=details,
                    )
                )
                continue
            rule, as_fail = _TIME_ANOMALY_RULE_MAP.get(anomaly_type, ("DQ-004", True))
            checks.append(
                QualityCheckResult(
                    rule_id=rule,
                    severity=QualitySeverity.FAIL if as_fail else QualitySeverity.WARNING,
                    message=str(anomaly.get("message", anomaly_type)),
                    details=details,
                )
            )

        # DQ-008 duplicate
        if data.duplicate:
            checks.append(
                QualityCheckResult(
                    rule_id="DQ-008",
                    severity=QualitySeverity.WARNING,
                    message="Duplicate data detected (recorded as reference, not stored twice)",
                    details={"duplicate_of": data.duplicate_of},
                )
            )

        # DQ-009 sequence gap / duplicate sequence / regression
        if data.sequence is not None:
            seq = data.sequence
            if seq.status == "OK":
                checks.append(
                    QualityCheckResult(
                        rule_id="DQ-009",
                        severity=QualitySeverity.PASS,
                        message="Sequence in order",
                        details={"actual": seq.actual},
                    )
                )
            elif seq.status == "UNKNOWN":
                checks.append(
                    QualityCheckResult(
                        rule_id="DQ-009",
                        severity=QualitySeverity.PASS,
                        message="Sequence explicitly UNKNOWN for this stream",
                        details={"explicit_absence": True},
                    )
                )
            else:
                rule = {
                    "DUPLICATE": "DQ-009",
                    "GAP": "DQ-009",
                    "REGRESSION": "DQ-009",
                }[seq.status]
                checks.append(
                    QualityCheckResult(
                        rule_id=rule,
                        severity=QualitySeverity.WARNING,
                        message=f"Sequence {seq.status.lower()} detected",
                        details={
                            "status": seq.status,
                            "expected": seq.expected,
                            "actual": seq.actual,
                            "difference": seq.difference,
                            **dict(seq.details),
                        },
                    )
                )

        # DQ-010 out-of-order (stream-level, from ordering monitor)
        if data.out_of_order:
            checks.append(
                QualityCheckResult(
                    rule_id="DQ-010",
                    severity=QualitySeverity.WARNING,
                    message="Out-of-order data detected (kept, flagged, never re-timestamped)",
                    details=dict(data.order_details),
                )
            )

        # DQ-011 invalid symbol
        if data.symbol_check_applicable:
            from core.validation.contracts import symbol_is_well_formed

            if data.symbol is None or not symbol_is_well_formed(data.symbol):
                checks.append(
                    QualityCheckResult(
                        rule_id="DQ-011",
                        severity=QualitySeverity.FAIL,
                        message="Invalid symbol (structural check, no trading semantics)",
                        field="symbol",
                        details={"symbol": data.symbol},
                    )
                )
            else:
                checks.append(
                    QualityCheckResult(
                        rule_id="DQ-011",
                        severity=QualitySeverity.PASS,
                        message="Symbol well formed",
                        details={"symbol": data.symbol},
                    )
                )

        # DQ-012 invalid source
        if not data.source_registered:
            checks.append(
                QualityCheckResult(
                    rule_id="DQ-012",
                    severity=QualitySeverity.FAIL,
                    message=f"Unknown source '{data.source_id}'",
                    details={"source_id": data.source_id},
                )
            )
        elif not data.source_enabled:
            checks.append(
                QualityCheckResult(
                    rule_id="DQ-012",
                    severity=QualitySeverity.FAIL,
                    message=f"Source '{data.source_id}' is disabled",
                    details={"source_id": data.source_id, "enabled": False},
                )
            )

        # DQ-013 invalid schema version
        if not data.schema_resolved:
            checks.append(
                QualityCheckResult(
                    rule_id="DQ-013",
                    severity=QualitySeverity.FAIL,
                    message="Schema id/version could not be resolved in the schema registry",
                    details={},
                )
            )

        # DQ-014 hash mismatch
        if data.hash_matches is False:
            checks.append(
                QualityCheckResult(
                    rule_id="DQ-014",
                    severity=QualitySeverity.FAIL,
                    message="Payload hash mismatch (integrity violation)",
                    details={},
                )
            )

        # DQ-015 missing provenance
        if data.provenance_check_applicable and not data.provenance_present:
            checks.append(
                QualityCheckResult(
                    rule_id="DQ-015",
                    severity=QualitySeverity.FAIL,
                    message="Missing provenance on derived record",
                    details={},
                )
            )

        if not checks:
            return QualityEvaluation(
                level=DataQualityLevel.UNKNOWN,
                checks=(),
                judged_at=judged_at,
            )

        level = self._classify(checks)
        return QualityEvaluation(level=level, checks=tuple(checks), judged_at=judged_at)

    @staticmethod
    def _classify(checks: list[QualityCheckResult]) -> DataQualityLevel:
        if any(c.severity is QualitySeverity.FAIL for c in checks):
            return DataQualityLevel.INVALID
        if any(c.rule_id == "DQ-007" for c in checks):
            return DataQualityLevel.STALE
        if any(c.severity is QualitySeverity.WARNING for c in checks):
            return DataQualityLevel.DEGRADED
        return DataQualityLevel.VALIDATED
