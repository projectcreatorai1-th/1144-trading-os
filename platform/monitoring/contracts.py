"""Observability contracts (owned by platform.monitoring, Phase 11).

Extends the kernel logging port with the Phase 11 measurement contract:
metrics, SLIs, SLOs, alert rules and alerts. Every SLO must carry an
explicit rationale (no numbers without a reason) and every alert transition
is auditable. UNKNOWN is never SAFE: health aggregation fails closed.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.errors import ContractError, ContractValidationError
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.time import ensure_utc

CONTRACT_VERSION = "1.0.0"

#: SLO/SLI/rule artifacts carry human-readable NAMES (design-time labels,
#: not system-generated ids); only runtime alerts get a generated alert_id.
_NAME_SLUG = re.compile(r"^[a-z][a-z0-9_]{3,63}$")


def _validate_name(kind: str, value: str, location: str) -> None:
    if not isinstance(value, str) or not _NAME_SLUG.match(value):
        raise ContractValidationError(
            f"{kind} must match {_NAME_SLUG.pattern!r}, got {value!r}",
            location=location, rule_id="OBS-006")

#: The mandated core metrics (master command 13.1). The registry is closed:
#: recording an unknown name fails closed (no silent metric sprawl).
CORE_METRICS: tuple[str, ...] = (
    "market_data_freshness",
    "market_data_gap",
    "event_bus_lag",
    "decision_latency",
    "risk_check_latency",
    "order_submission_latency",
    "broker_ack_latency",
    "fill_latency",
    "reconciliation_lag",
    "audit_append_latency",
    "audit_verify_latency",
    "ui_action_latency",
    "startup_latency",
    "gateway_health",
)


class Comparison(Enum):
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="


def _compare(value: float, op: Comparison, threshold: float) -> bool:
    return {
        Comparison.LT: value < threshold,
        Comparison.LE: value <= threshold,
        Comparison.GT: value > threshold,
        Comparison.GE: value >= threshold,
    }[op]


@dataclass(frozen=True)
class MetricPoint:
    """One measured sample. Values are finite numbers (NaN/inf rejected:
    a broken measurement must never enter an SLO)."""

    name: str
    value: float
    at: datetime
    labels: Mapping[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if self.name not in CORE_METRICS:
            raise ContractValidationError(
                f"metric {self.name!r} is not a registered core metric",
                location="metric.name", rule_id="OBS-001",
                details={"known": list(CORE_METRICS)})
        if not isinstance(self.value, (int, float)) \
                or isinstance(self.value, bool) \
                or not math.isfinite(self.value):
            raise ContractValidationError(
                f"metric {self.name} value must be finite, got {self.value!r}",
                location="metric.value", rule_id="OBS-002")
        ensure_utc(self.at, location="metric.at")
        for key, value in self.labels.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ContractValidationError(
                    "metric.labels must map str->str",
                    location="metric.labels", rule_id="OBS-002")


@dataclass(frozen=True)
class SLI:
    """Service level indicator measured from real metric points.

    good events = points satisfying (op, threshold) inside the window;
    total = all points of that metric in the window."""

    sli_id: str
    metric: str
    op: Comparison
    threshold: float
    window: timedelta
    measurement_source: str
    owner: str

    def validate(self) -> None:
        _validate_name("sli_id", self.sli_id, location="sli.sli_id")
        if self.metric not in CORE_METRICS:
            raise ContractValidationError(
                f"SLI {self.sli_id} references unknown metric "
                f"{self.metric!r}", location="sli.metric", rule_id="OBS-001")
        if not isinstance(self.op, Comparison):
            raise ContractValidationError("sli.op must be a Comparison",
                                          location="sli.op")
        if not isinstance(self.threshold, (int, float)) \
                or isinstance(self.threshold, bool):
            raise ContractValidationError("sli.threshold must be numeric",
                                          location="sli.threshold")
        if not isinstance(self.window, timedelta) or self.window <= timedelta(0):
            raise ContractValidationError(
                "sli.window must be a positive timedelta",
                location="sli.window")
        for name in ("measurement_source", "owner"):
            if not isinstance(getattr(self, name), str) \
                    or not getattr(self, name):
                raise ContractValidationError(
                    f"sli.{name} must be a non-empty string",
                    location=f"sli.{name}")


@dataclass(frozen=True)
class SLO:
    """Objective over an SLI. target_ratio and rationale are mandatory -
    numbers without a documented reason are rejected (rule: no unexplained
    thresholds)."""

    slo_id: str
    sli: SLI
    target_ratio: float
    rationale: str
    alert_severity: "Severity"

    def validate(self) -> None:
        _validate_name("slo_id", self.slo_id, location="slo.slo_id")
        self.sli.validate()
        if not (0.0 < self.target_ratio <= 1.0):
            raise ContractValidationError(
                f"slo.target_ratio must be in (0, 1], got {self.target_ratio}",
                location="slo.target_ratio", rule_id="OBS-003")
        if not isinstance(self.rationale, str) or len(self.rationale.strip()) < 20:
            raise ContractValidationError(
                "slo.rationale must explain the threshold (>= 20 chars)",
                location="slo.rationale", rule_id="OBS-003")


class Severity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"

    def escalate(self) -> "Severity":
        order = [Severity.INFO, Severity.WARNING,
                 Severity.CRITICAL, Severity.EMERGENCY]
        return order[min(order.index(self) + 1, len(order) - 1)]


@dataclass(frozen=True)
class AlertRule:
    """Condition -> visible, deduplicated, cooldown-bounded, escalating,
    audited alert."""

    rule_id: str
    metric: str
    op: Comparison
    threshold: float
    severity: Severity
    cooldown_seconds: float
    escalation_after_repeats: int
    owner: str
    description: str

    def validate(self) -> None:
        _validate_name("rule_id", self.rule_id, location="rule.rule_id")
        if self.metric not in CORE_METRICS:
            raise ContractValidationError(
                f"alert rule {self.rule_id} references unknown metric "
                f"{self.metric!r}", location="rule.metric", rule_id="OBS-001")
        if not isinstance(self.op, Comparison):
            raise ContractValidationError("rule.op must be a Comparison",
                                          location="rule.op")
        if not isinstance(self.severity, Severity):
            raise ContractValidationError("rule.severity must be a Severity",
                                          location="rule.severity")
        if self.cooldown_seconds <= 0:
            raise ContractValidationError(
                "rule.cooldown_seconds must be positive (dedup window)",
                location="rule.cooldown_seconds", rule_id="OBS-004")
        if self.escalation_after_repeats < 1:
            raise ContractValidationError(
                "rule.escalation_after_repeats must be >= 1",
                location="rule.escalation_after_repeats")
        for name in ("owner", "description"):
            if not isinstance(getattr(self, name), str) \
                    or not getattr(self, name):
                raise ContractValidationError(
                    f"rule.{name} must be a non-empty string",
                    location=f"rule.{name}")


class AlertState(Enum):
    RAISED = "RAISED"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class Alert:
    """One alert occurrence; transitions are audit records, never silent."""

    alert_id: str
    rule_id: str
    severity: Severity
    state: AlertState
    raised_at: datetime
    reason: str
    repeats: int = 0
    labels: Mapping[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        validate_identifier("alert_id", self.alert_id,
                            location="alert.alert_id")
        if not isinstance(self.severity, Severity) \
                or not isinstance(self.state, AlertState):
            raise ContractValidationError(
                "alert severity/state must be enums", location="alert")
        ensure_utc(self.raised_at, location="alert.raised_at")
        if not isinstance(self.reason, str) or not self.reason:
            raise ContractValidationError("alert.reason must be non-empty",
                                          location="alert.reason")


class HealthStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"
    DOWN = "DOWN"


#: UNKNOWN ranks below DEGRADED: an unmeasured component can never make the
#: aggregate look healthier than DEGRADED (UNKNOWN != SAFE).
_HEALTH_ORDER = {
    HealthStatus.HEALTHY: 3,
    HealthStatus.DEGRADED: 2,
    HealthStatus.UNKNOWN: 1,
    HealthStatus.DOWN: 0,
}


def worst_health(statuses: list[HealthStatus]) -> HealthStatus:
    if not statuses:
        return HealthStatus.UNKNOWN  # no evidence is never HEALTHY
    return min(statuses, key=lambda s: _HEALTH_ORDER[s])


__all__ = [
    "CORE_METRICS", "CONTRACT_VERSION", "Comparison", "MetricPoint",
    "SLI", "SLO", "Severity", "AlertRule", "AlertState", "Alert",
    "HealthStatus", "worst_health", "ContractError", "_compare",
]
