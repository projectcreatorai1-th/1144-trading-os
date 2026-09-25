"""Phase 11 - observability contracts, metrics, SLOs, alerts, health."""
from __future__ import annotations

import math
from datetime import timedelta

import pytest

from architecture.contracts.errors import ContractError, ContractValidationError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import utc_now
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.monitoring.alerts import (
    NO_DATA,
    PASS,
    VIOLATED,
    AlertManager,
    evaluate_slo,
)
from platform.monitoring.contracts import (
    CORE_METRICS,
    Alert,
    AlertRule,
    AlertState,
    Comparison,
    HealthStatus,
    MetricPoint,
    SLI,
    SLO,
    Severity,
    worst_health,
)
from platform.monitoring.health import (
    DEFAULT_SLOS,
    HealthAggregator,
)
from platform.monitoring.metrics import MetricsRegistry


class FakeAudit(AuditRepository):
    """In-memory audit sink implementing the repository port."""

    def __init__(self):
        self.records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        self.records.append(record)

    def verify(self) -> dict:
        return {"records": len(self.records), "tamper": False}

    def get_by_id(self, audit_id: str) -> AuditRecord | None:
        return next((r for r in self.records if r.audit_id == audit_id), None)

    def iter_by_correlation_id(self, correlation_id: str):
        return iter([r for r in self.records
                     if r.correlation_id == correlation_id])


def _rule(**overrides):
    base = dict(rule_id="rule_fresh_warn",
                metric="market_data_freshness",
                op=Comparison.GT, threshold=3000.0,
                severity=Severity.WARNING, cooldown_seconds=60.0,
                escalation_after_repeats=2, owner="data-plane",
                description="freshness age above DEGRADED bound")
    base.update(overrides)
    return AlertRule(**base)


class TestContracts:
    def test_core_metric_registry_closed(self):
        with pytest.raises(ContractValidationError):
            MetricPoint(name="not_a_metric", value=1.0, at=utc_now()) \
                .validate()

    def test_non_finite_value_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ContractValidationError):
                MetricPoint(name="decision_latency", value=bad,
                            at=utc_now()).validate()

    def test_slo_requires_rationale(self):
        sli = SLI("sli-x", "decision_latency", Comparison.LE, 10.0,
                  timedelta(minutes=1), "bench", "core")
        with pytest.raises(ContractValidationError):
            SLO("slo-x", sli, 0.9, "too short",
                Severity.WARNING).validate()

    def test_slo_target_bounds(self):
        sli = SLI("sli-x", "decision_latency", Comparison.LE, 10.0,
                  timedelta(minutes=1), "bench", "core")
        with pytest.raises(ContractValidationError):
            SLO("slo-x", sli, 0.0, "x" * 30, Severity.WARNING).validate()
        with pytest.raises(ContractValidationError):
            SLO("slo-x", sli, 1.5, "x" * 30, Severity.WARNING).validate()

    def test_default_slos_valid(self):
        for slo in DEFAULT_SLOS:
            slo.validate()

    def test_severity_escalation_ladder(self):
        s = Severity.INFO
        ladder = [s]
        for _ in range(5):
            s = s.escalate()
            ladder.append(s)
        assert ladder[-1] is Severity.EMERGENCY


class TestMetricsRegistry:
    def test_record_and_stats_percentiles(self):
        reg = MetricsRegistry()
        base = utc_now()
        for i in range(100):
            reg.record_value("risk_check_latency", float(i + 1),
                             at=base + timedelta(seconds=i))
        stats = reg.stats("risk_check_latency")
        assert stats.count == 100
        assert stats.min == 1.0 and stats.max == 100.0
        assert stats.p50 == 50.0
        assert stats.p95 == 95.0
        assert stats.p99 == 99.0

    def test_window_query_filters(self):
        reg = MetricsRegistry()
        base = utc_now()
        reg.record_value("decision_latency", 1.0, at=base - timedelta(minutes=5))
        reg.record_value("decision_latency", 2.0, at=base)
        recent = reg.points("decision_latency", since=base - timedelta(seconds=1))
        assert len(recent) == 1 and recent[0].value == 2.0

    def test_bounded_memory(self):
        reg = MetricsRegistry(maxlen=50)
        base = utc_now()
        for i in range(200):
            reg.record_value("event_bus_lag", float(i),
                             at=base + timedelta(microseconds=i))
        assert len(reg.points("event_bus_lag")) == 50

    def test_unknown_metric_query_rejected(self):
        reg = MetricsRegistry()
        with pytest.raises(ContractError):
            reg.points("nope")

    def test_timed_wrapper_measures_real_duration(self):
        reg = MetricsRegistry()
        import time

        class FakeClock:
            def __init__(self):
                self.t = 0.0

            def __call__(self):
                self.t += 0.150
                from datetime import datetime, timezone
                return datetime.fromtimestamp(1_700_000_000 + self.t,
                                              tz=timezone.utc)

        clock = FakeClock()
        with reg.timed("order_submission_latency", clock=clock):
            time.sleep(0.005)
        points = reg.points("order_submission_latency")
        assert len(points) == 1
        assert math.isclose(points[0].value, 150.0, abs_tol=1.0)


class TestSloEvaluation:
    def _registry_with(self, values, metric="market_data_freshness"):
        reg = MetricsRegistry()
        base = utc_now()
        for i, v in enumerate(values):
            reg.record_value(metric, float(v),
                             at=base - timedelta(minutes=len(values) - i))
        return reg

    def test_pass_violated_and_no_data(self):
        from platform.monitoring.health import SLO_FRESHNESS
        reg = self._registry_with([100, 200, 300, 4000])
        result = evaluate_slo(SLO_FRESHNESS, reg)
        assert result["status"] == VIOLATED  # 3/4 = 0.75 < 0.99
        reg2 = self._registry_with([100, 200, 300, 2900])
        assert evaluate_slo(SLO_FRESHNESS, reg2)["status"] == PASS
        empty = MetricsRegistry()
        assert evaluate_slo(SLO_FRESHNESS, empty)["status"] == NO_DATA


class TestAlertManager:
    def test_requires_audit_repository(self):
        with pytest.raises(ContractError):
            AlertManager(MetricsRegistry(), audit=object())

    def test_lifecycle_raised_escalated_resolved_with_audit(self):
        audit = FakeAudit()
        reg = MetricsRegistry()
        mgr = AlertManager(reg, audit, rules=(_rule(),))
        assert mgr.evaluate() == []          # no data -> no alert

        reg.record_value("market_data_freshness", 4000.0)
        raised = mgr.evaluate()[0]
        assert raised.state is AlertState.RAISED
        assert raised.severity is Severity.WARNING

        # within cooldown: deduplicated (no change, no extra audit)
        reg.record_value("market_data_freshness", 4100.0)
        assert mgr.evaluate() == []
        assert len(mgr.active_alerts()) == 1

        # past cooldown, still firing: repeats accumulate -> escalate at 2
        base = utc_now()
        for i, v in enumerate((4200.0, 4300.0)):
            reg.record_value("market_data_freshness", v,
                             at=base + timedelta(minutes=5 + i))
        assert mgr.evaluate(now=base + timedelta(minutes=11)) == []  # silent repeat
        reg.record_value("market_data_freshness", 4400.0,
                         at=base + timedelta(minutes=12))
        escalated = mgr.evaluate(now=base + timedelta(minutes=13))[0]
        assert escalated.state is AlertState.ESCALATED
        assert escalated.severity is Severity.CRITICAL

        # condition clears -> RESOLVED
        reg.record_value("market_data_freshness", 100.0)
        resolved = mgr.evaluate()[0]
        assert resolved.state is AlertState.RESOLVED
        assert mgr.active_alerts() == ()

        actions = [r.action for r in audit.records]
        assert actions == ["ALERT_RAISED", "ALERT_ESCALATED",
                           "ALERT_RESOLVED"]
        for record in audit.records:
            assert record.actor_type is ActorType.SYSTEM
            assert record.source == "platform.monitoring.alerts"

    def test_duplicate_rule_rejected(self):
        mgr = AlertManager(MetricsRegistry(), FakeAudit())
        mgr.add_rule(_rule())
        with pytest.raises(ContractError):
            mgr.add_rule(_rule())


class TestHealthAggregation:
    def test_worst_of_and_unknown_never_healthy(self):
        assert worst_health(
            [HealthStatus.HEALTHY, HealthStatus.DEGRADED]) \
            is HealthStatus.DEGRADED
        assert worst_health(
            [HealthStatus.HEALTHY, HealthStatus.UNKNOWN]) \
            is HealthStatus.UNKNOWN
        assert worst_health([]) is HealthStatus.UNKNOWN

    def test_snapshot_with_throwing_probe_is_unknown(self):
        agg = HealthAggregator({
            "ok": lambda: HealthStatus.HEALTHY,
            "broken": lambda: (_ for _ in ()).throw(RuntimeError("x")),
            "garbage": lambda: "not-a-status",
        })
        snap = agg.snapshot()
        assert snap["overall"] == "UNKNOWN"
        assert snap["sources"]["ok"] == "HEALTHY"
        assert snap["sources"]["broken"] == "UNKNOWN"
        assert snap["sources"]["garbage"] == "UNKNOWN"

    def test_requires_at_least_one_probe(self):
        with pytest.raises(ValueError):
            HealthAggregator({})
