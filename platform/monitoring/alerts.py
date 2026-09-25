"""SLO evaluation + audited alerting (platform.monitoring, Phase 11).

Every alert transition (RAISED / ESCALATED / RESOLVED) appends an audit
record through the EXISTING platform.audit repository - alerts without an
audit trail are refused at construction (fail closed). Dedup: one active
alert per rule; cooldown bounds re-raise frequency; repeats escalate the
severity ladder up to EMERGENCY.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc, utc_now

from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.monitoring.contracts import (
    Alert,
    AlertRule,
    AlertState,
    Severity,
    SLO,
    _compare,
)
from platform.monitoring.metrics import MetricsRegistry

#: deterministic compliance status for an SLO over a window
PASS = "PASS"
VIOLATED = "VIOLATED"
NO_DATA = "NO_DATA"   # no evidence is never PASS (UNKNOWN != SAFE)


def evaluate_slo(slo: SLO, registry: MetricsRegistry, *,
                 now: datetime | None = None,
                 min_samples: int = 1) -> dict:
    slo.validate()
    good, total = registry.sli_compliance(slo.sli, now=now)
    if total < min_samples:
        return {"slo_id": slo.slo_id, "status": NO_DATA,
                "good": good, "total": total, "ratio": None}
    ratio = good / total
    return {"slo_id": slo.slo_id,
            "status": PASS if ratio >= slo.target_ratio else VIOLATED,
            "good": good, "total": total, "ratio": round(ratio, 6),
            "target": slo.target_ratio}


class AlertManager:
    """Evaluates rules against the registry; owns alert lifecycle."""

    def __init__(self, registry: MetricsRegistry, audit: AuditRepository,
                 *, rules: tuple[AlertRule, ...] = ()) -> None:
        if not isinstance(audit, AuditRepository):
            raise ContractError(
                "AlertManager requires an AuditRepository (alerts without "
                "an audit trail are forbidden)",
                location="alerts.init", rule_id="OBS-005")
        self._registry = registry
        self._audit = audit
        self._rules: dict[str, AlertRule] = {}
        self._active: dict[str, Alert] = {}      # rule_id -> active alert
        for rule in rules:
            self.add_rule(rule)

    def add_rule(self, rule: AlertRule) -> None:
        rule.validate()
        if rule.rule_id in self._rules:
            raise ContractError(
                f"duplicate alert rule {rule.rule_id!r}",
                location="alerts.add_rule", rule_id="OBS-004")
        self._rules[rule.rule_id] = rule

    def rules(self) -> tuple[AlertRule, ...]:
        return tuple(self._rules.values())

    def active_alerts(self) -> tuple[Alert, ...]:
        return tuple(self._active.values())

    def evaluate(self, *, now: datetime | None = None) -> list[Alert]:
        """Evaluate every rule on the latest sample of its metric. Returns
        the alerts whose state changed this round (transitions audited)."""
        moment = ensure_utc(now) if now else utc_now()
        changed: list[Alert] = []
        for rule in self._rules.values():
            points = self._registry.points(rule.metric)
            latest = points[-1].value if points else None
            firing = latest is not None and \
                _compare(latest, rule.op, rule.threshold)
            alert = self._advance(rule, firing, latest, moment)
            if alert is not None:
                changed.append(alert)
        return changed

    # ------------------------------------------------------------------ #
    def _advance(self, rule: AlertRule, firing: bool, latest,
                 moment: datetime) -> Alert | None:
        current = self._active.get(rule.rule_id)
        if not firing:
            if current is None:
                return None
            resolved = Alert(
                alert_id=current.alert_id, rule_id=rule.rule_id,
                severity=current.severity, state=AlertState.RESOLVED,
                raised_at=current.raised_at, reason=current.reason,
                repeats=current.repeats, labels=current.labels)
            self._audit_transition(rule, resolved, moment,
                                   detail=f"value={latest}")
            del self._active[rule.rule_id]
            return resolved

        if current is None:
            alert = Alert(alert_id=new_identifier("alert_id"),
                          rule_id=rule.rule_id, severity=rule.severity,
                          state=AlertState.RAISED, raised_at=moment,
                          reason=(f"{rule.metric}={latest} "
                                  f"{rule.op.value} {rule.threshold}"),
                          labels={"owner": rule.owner})
            self._active[rule.rule_id] = alert
            self._audit_transition(rule, alert, moment,
                                   detail=f"value={latest}")
            return alert

        # active: honor cooldown; repeats escalate the severity ladder.
        # Only real transitions (state/severity change) are audited - a
        # repeat without change stays silent (dedup by design).
        since = (moment - current.raised_at).total_seconds()
        if since < rule.cooldown_seconds:
            return None
        repeats = current.repeats + 1
        escalated = repeats >= rule.escalation_after_repeats \
            and current.severity is not Severity.EMERGENCY
        if not escalated:
            kept = Alert(alert_id=current.alert_id,
                         rule_id=rule.rule_id,
                         severity=current.severity,
                         state=current.state,
                         raised_at=current.raised_at,
                         reason=current.reason, repeats=repeats,
                         labels=current.labels)
            self._active[rule.rule_id] = kept
            return None
        alert = Alert(alert_id=current.alert_id, rule_id=rule.rule_id,
                      severity=current.severity.escalate(),
                      state=AlertState.ESCALATED,
                      raised_at=current.raised_at, reason=current.reason,
                      repeats=repeats, labels=current.labels)
        self._active[rule.rule_id] = alert
        self._audit_transition(rule, alert, moment,
                               detail=f"value={latest} repeats={repeats}")
        return alert

    def _audit_transition(self, rule: AlertRule, alert: Alert,
                          moment: datetime, *, detail: str) -> None:
        alert.validate()
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.SYSTEM,
            actor_id="platform.monitoring.alerts",
            action=f"ALERT_{alert.state.value}",
            entity_type="alert", entity_id=alert.alert_id,
            event_time=ensure_utc(moment, location="alert.audit"),
            before=None,
            after={"rule": rule.rule_id, "severity": alert.severity.value,
                   "repeats": alert.repeats, "reason": alert.reason,
                   "detail": detail},
            reason=rule.description,
            source="platform.monitoring.alerts",
            environment="SIMULATION",   # observability plane, never trading
            correlation_id=alert.rule_id,
        ))
