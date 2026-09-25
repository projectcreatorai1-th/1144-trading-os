"""Risk state service (owned by core.risk).

Risk-state transitions ride the Phase 2 state machinery (RISK_STATE category
-> Phase 0 risk_state machine): immutable, versioned, event-linked,
auditable. Escalation follows the engine outcome; recovery is stepwise and
operator-driven - never automatic (SECTION 11)."""
from __future__ import annotations

from dataclasses import dataclass

from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc
from core.events.contracts import Event
from core.state.contracts import StateCategory, StateRecord
from core.state.engine import StateEngine, StateApplication
from core.state.stores import StateStore
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"

#: Deterministic mapping from decision permission to risk-state target.
#: De-escalation is NOT mapped - recovery is an explicit operator transition.
ESCALATION_BY_PERMISSION = {
    "EMERGENCY": "EMERGENCY",
    "CLOSE_ONLY": "LIMITED",
    "BLOCK": "CAUTION",
}


class RiskStateService:
    def __init__(self, states: StateStore, audit: AuditRepository,
                 engine: StateEngine | None = None) -> None:
        self._states = states
        self._audit = audit
        self._engine = engine or StateEngine()

    def current(self, entity_id: str = "risk-engine") -> str | None:
        state = self._states.get_current_state(StateCategory.RISK_STATE.value, entity_id)
        return state.status if state is not None else None

    def escalate_on_decision(self, permission: str, *, event: Event,
                             entity_id: str = "risk-engine") -> StateApplication | None:
        """Escalate the risk state when a decision warrants it. The Phase 0
        machine enforces legal transitions (escalation may jump; invalid
        transitions fail closed)."""
        target = ESCALATION_BY_PERMISSION.get(permission)
        if target is None:
            return None
        return self._apply(target, event=event, reason=f"decision {permission}", entity_id=entity_id)

    def operator_transition(self, target: str, *, event: Event, reason: str,
                            entity_id: str = "risk-engine") -> StateApplication:
        """Explicit operator-driven transition (e.g. stepwise recovery
        EMERGENCY -> PAUSE -> ... ). Audited; machine-validated."""
        return self._apply(target, event=event, reason=reason, entity_id=entity_id)

    def _apply(self, target: str, *, event: Event, reason: str,
               entity_id: str) -> StateApplication | None:
        current = self._states.get_current_state(StateCategory.RISK_STATE.value, entity_id)
        if current is not None and current.status == target:
            return None  # already at the target - idempotent no-op
        application = self._engine.apply_event(
            entity_type=StateCategory.RISK_STATE,
            entity_id=entity_id,
            current=current,
            new_status=target,
            event=event,
            payload={"trigger": reason},
            reason=reason,
            actor="core.risk.state_service",
            effective_time=event.event_time,
            observed_time=event.received_time,
            processed_time=event.received_time,
        )
        self._states.save_state(application.state, application.transition)
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.SYSTEM,
            actor_id="core.risk.state_service",
            action="RISK_STATE_TRANSITION",
            entity_type="risk_state",
            entity_id=entity_id,
            event_time=application.transition.timestamp,
            before={"status": application.transition.previous_state},
            after={"status": application.state.status,
                   "version": application.state.state_version},
            reason=reason,
            source="core.risk.state_service",
            environment=event.environment,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
        ))
        return application
