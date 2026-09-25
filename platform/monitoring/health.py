"""Health aggregation + the Phase 11 default SLI/SLO set.

The default objectives encode the master command 13.1/13.2 metrics with
explicit rationale for every number (no unexplained thresholds). Sources of
measurement are existing surfaces only (feed freshness, gateway, audit).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Callable, Mapping

from platform.monitoring.contracts import (
    Comparison,
    HealthStatus,
    SLI,
    SLO,
    Severity,
    worst_health,
)

# --------------------------------------------------------------------- #
# Default SLIs / SLOs (rationale inline - required by contract)
# --------------------------------------------------------------------- #
SLI_FRESHNESS = SLI(
    sli_id="market_data_freshness_sli",
    metric="market_data_freshness",
    op=Comparison.LE, threshold=3000.0,
    window=timedelta(minutes=10),
    measurement_source="MT5MarketDataAdapter.freshness age (ms)",
    owner="data-plane",
)
SLO_FRESHNESS = SLO(
    slo_id="market_data_freshness_slo",
    sli=SLI_FRESHNESS, target_ratio=0.99,
    rationale="Connectivity config sets stale_threshold at 15s and degraded "
              "at 3s; requiring 99% of freshness ages <= 3s over 10 minutes "
              "matches the DEGRADED bound with operational margin.",
    alert_severity=Severity.WARNING,
)

SLI_AUDIT = SLI(
    sli_id="audit_append_sli",
    metric="audit_append_latency",
    op=Comparison.LE, threshold=50.0,
    window=timedelta(minutes=10),
    measurement_source="timed wrapper over AuditRepository.append",
    owner="platform",
)
SLO_AUDIT = SLO(
    slo_id="audit_append_slo",
    sli=SLI_AUDIT, target_ratio=0.99,
    rationale="Audit is on the order path (permission and risk checks "
              "append evidence synchronously); local SQLite appends measure "
              "single-digit ms, so 50ms bounds pathological IO while keeping "
              "99% of the window compliant.",
    alert_severity=Severity.CRITICAL,
)

SLI_UI = SLI(
    sli_id="ui_responsiveness_sli",
    metric="ui_action_latency",
    op=Comparison.LE, threshold=250.0,
    window=timedelta(minutes=10),
    measurement_source="DesktopGateway action wrapper (dispatch duration)",
    owner="desktop",
)
SLO_UI = SLO(
    slo_id="ui_responsiveness_slo",
    sli=SLI_UI, target_ratio=0.95,
    rationale="The GUI is a control plane, not a trading hot path; 250ms is "
              "the perceived-instant threshold for control actions and 95% "
              "allows for first-render and GC pauses without alert noise.",
    alert_severity=Severity.INFO,
)

SLI_GATEWAY = SLI(
    sli_id="gateway_availability_sli",
    metric="gateway_health",
    op=Comparison.GE, threshold=1.0,      # 1 = reachable, 0 = not
    window=timedelta(minutes=5),
    measurement_source="gateway heartbeat probe",
    owner="platform",
)
SLO_GATEWAY = SLO(
    slo_id="gateway_availability_slo",
    sli=SLI_GATEWAY, target_ratio=0.999,
    rationale="A control-plane outage blinds the operator: 5-minute window "
              "at 0.999 tolerates a single lost probe (~300ms) per window.",
    alert_severity=Severity.EMERGENCY,
)

DEFAULT_SLOS: tuple[SLO, ...] = (
    SLO_FRESHNESS, SLO_AUDIT, SLO_UI, SLO_GATEWAY,
)


# --------------------------------------------------------------------- #
# Health aggregation (UNKNOWN != SAFE: worst-of, never assume healthy)
# --------------------------------------------------------------------- #
class HealthAggregator:
    """Polls named probes and aggregates with worst-of semantics. A missing
    or throwing probe reports UNKNOWN (which can never aggregate up to
    HEALTHY)."""

    def __init__(self, probes: Mapping[str, Callable[[], HealthStatus]]):
        if not probes:
            raise ValueError("HealthAggregator needs at least one probe "
                             "(no probes is UNKNOWN, not HEALTHY)")
        self._probes = dict(probes)

    def snapshot(self) -> dict:
        per_source: dict[str, str] = {}
        statuses = []
        for name, probe in self._probes.items():
            try:
                status = probe()
                if not isinstance(status, HealthStatus):
                    status = HealthStatus.UNKNOWN
            except Exception:
                status = HealthStatus.UNKNOWN
            per_source[name] = status.value
            statuses.append(status)
        overall = worst_health(statuses)
        return {"overall": overall.value, "sources": per_source}
