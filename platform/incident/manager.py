"""Operational incident management (platform.incident, Phase 12).

Contract-first incident records with an audited lifecycle
(DETECTED -> ... -> PREVENTED) driven by the registered
`operational_incident_lifecycle` state machine, plus the runbook registry
covering the mandated failure scenarios. Detection sources are Phase 11
alerts; evidence references are immutable ids (audit/events/alerts).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.registry import load_registry
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_utc, utc_now
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.monitoring.contracts import Alert, Severity

CONTRACT_VERSION = "1.0.0"
INCIDENT_MACHINE = "operational_incident_lifecycle"

RUNBOOKS_FILE = Path(__file__).resolve().parent / "runbooks.yaml"


@dataclass(frozen=True)
class TimelineEntry:
    at: datetime
    actor: str
    action: str
    detail: str = ""

    def validate(self) -> None:
        ensure_utc(self.at, location="incident.timeline.at")
        for name in ("actor", "action"):
            if not isinstance(getattr(self, name), str) \
                    or not getattr(self, name):
                raise ContractError(
                    f"timeline.{name} must be non-empty",
                    location="incident.timeline")


@dataclass(frozen=True)
class OperationalIncident:
    """§14.1 incident record. `state` advances only via IncidentManager
    (state-machine + audit); evidence ids are references, never copies."""

    incident_id: str
    severity: Severity
    detected_at: datetime
    affected_component: str
    state: str
    operator: str | None = None
    timeline: tuple[TimelineEntry, ...] = ()
    root_cause: str | None = None
    resolution: str | None = None
    evidence: tuple[str, ...] = ()
    runbook_id: str | None = None

    def validate(self) -> None:
        if not self.incident_id.startswith("inc_"):
            raise ContractError("incident_id must be an operational_incident_id",
                                location="incident.incident_id",
                                rule_id="IDENT-001")
        if not isinstance(self.severity, Severity):
            raise ContractError("severity must be a monitoring Severity",
                                location="incident.severity")
        ensure_utc(self.detected_at, location="incident.detected_at")
        if not isinstance(self.affected_component, str) \
                or not self.affected_component:
            raise ContractError("affected_component must be non-empty",
                                location="incident.affected_component")
        if self.state == "ROOT_CAUSED" and not (self.root_cause or "").strip():
            raise ContractError(
                "ROOT_CAUSED requires a root_cause statement",
                location="incident.root_cause", rule_id="INC-001")
        for entry in self.timeline:
            entry.validate()


class IncidentManager:
    def __init__(self, audit: AuditRepository,
                 machines: StateMachineRegistry | None = None) -> None:
        if not isinstance(audit, AuditRepository):
            raise ContractError(
                "IncidentManager requires an AuditRepository",
                location="incident.init", rule_id="INC-002")
        self._audit = audit
        self._machines = machines or build_state_machine_registry()
        self._incidents: dict[str, OperationalIncident] = {}

    # ------------------------------------------------------------------ #
    def open_from_alert(self, alert: Alert, component: str, *,
                        operator: str | None = None,
                        evidence: tuple[str, ...] = (),
                        at: datetime | None = None) -> OperationalIncident:
        if not isinstance(alert, Alert):
            raise ContractError("detection source must be an Alert",
                                location="incident.open", rule_id="INC-002")
        moment = ensure_utc(at) if at else utc_now()
        incident = OperationalIncident(
            incident_id=new_identifier("operational_incident_id"),
            severity=alert.severity, detected_at=moment,
            affected_component=component, state="DETECTED",
            operator=operator,
            timeline=(TimelineEntry(moment, "platform.incident",
                                    "DETECTED",
                                    f"from alert {alert.alert_id}"),),
            evidence=tuple(evidence) + (alert.alert_id,))
        incident.validate()
        runbook = RunbookRegistry().match(component)
        if runbook is not None:
            incident = _replace(incident, runbook_id=runbook["runbook_id"])
        self._incidents[incident.incident_id] = incident
        self._audit_incident(incident, "INCIDENT_DETECTED", moment,
                             detail=f"component={component}")
        return incident

    def transition(self, incident_id: str, target: str, *, actor: str,
                   reason: str, at: datetime | None = None,
                   root_cause: str | None = None,
                   human_approval: bool = False) -> OperationalIncident:
        incident = self._get(incident_id)
        moment = ensure_utc(at) if at else utc_now()
        self._machines.apply(INCIDENT_MACHINE, incident.state, target,
                             reason=reason, actor=actor,
                             timestamp=moment,
                             context={"human_approval": human_approval})
        updated = _replace(
            incident, state=target,
            root_cause=root_cause or incident.root_cause,
            timeline=incident.timeline + (TimelineEntry(
                moment, actor, target, reason),))
        if target == "ROOT_CAUSED":
            updated = _replace(updated, root_cause=root_cause or
                               (updated.root_cause or ""))
        updated.validate()
        self._incidents[incident_id] = updated
        self._audit_incident(updated, f"INCIDENT_{target}", moment,
                             detail=reason)
        return updated

    def get(self, incident_id: str) -> OperationalIncident:
        return self._get(incident_id)

    def _get(self, incident_id: str) -> OperationalIncident:
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise ContractError(f"unknown incident {incident_id!r}",
                                location="incident.lookup", rule_id="INC-003")
        return incident

    def _audit_incident(self, incident: OperationalIncident, action: str,
                        moment: datetime, *, detail: str) -> None:
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.SYSTEM, actor_id="platform.incident",
            action=action, entity_type="operational_incident",
            entity_id=incident.incident_id,
            event_time=ensure_utc(moment, location="incident.audit"),
            before=None,
            after={"state": incident.state,
                   "severity": incident.severity.value,
                   "component": incident.affected_component,
                   "runbook": incident.runbook_id},
            reason=detail, source="platform.incident",
            environment="SIMULATION",
            correlation_id=incident.incident_id))


def _replace(incident: OperationalIncident, **changes) -> OperationalIncident:
    from dataclasses import replace
    return replace(incident, **changes)


# --------------------------------------------------------------------- #
# Runbook registry (13 mandated scenarios; data is the source of truth)
# --------------------------------------------------------------------- #
class RunbookRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or RUNBOOKS_FILE
        data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        self._books: list[dict[str, Any]] = list(data.get("runbooks", []))
        ids = [b["runbook_id"] for b in self._books]
        if len(ids) != len(set(ids)):
            raise ContractError("duplicate runbook_id",
                                location="runbooks.registry", rule_id="INC-004")

    def all_runbooks(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._books)

    def match(self, component_or_symptom: str) -> Mapping[str, Any] | None:
        needle = component_or_symptom.lower()
        for book in self._books:
            if needle in str(book.get("applies_to", "")).lower():
                return book
        return None

    def get(self, runbook_id: str) -> Mapping[str, Any]:
        for book in self._books:
            if book["runbook_id"] == runbook_id:
                return book
        raise ContractError(f"unknown runbook {runbook_id!r}",
                            location="runbooks.get", rule_id="INC-004")
