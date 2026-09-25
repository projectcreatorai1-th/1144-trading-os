"""State engine (owned by core.state).

Applies events to state with full traceability. Machine-backed categories
transition through the Phase 0 state machine registry (no parallel logic);
invalid transitions fail closed. Every application produces an immutable
StateRecord + StateTransitionRecord (SECTIONS 5-8).

Determinism: all timestamps are explicit inputs - the engine never reads a
clock (SECTION 11/39)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from architecture.contracts.errors import ContractValidationError, StateTransitionError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    TransitionRecord,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_utc
from core.events.contracts import Event
from core.state.contracts import (
    MACHINE_BY_CATEGORY,
    CONTRACT_VERSION,
    StateCategory,
    StateRecord,
    StateTransitionRecord,
)


@dataclass(frozen=True)
class StateApplication:
    state: StateRecord
    transition: StateTransitionRecord
    machine_transition: TransitionRecord | None  # set for machine-backed categories

    @property
    def previous_state(self) -> str | None:
        return self.transition.previous_state


class StateEngine:
    def __init__(self, machines: StateMachineRegistry | None = None) -> None:
        self._machines = machines or build_state_machine_registry()

    def apply_event(
        self,
        *,
        entity_type: StateCategory,
        entity_id: str,
        current: StateRecord | None,
        new_status: str,
        event: Event,
        payload: Mapping[str, Any],
        reason: str,
        actor: str,
        effective_time: datetime,
        observed_time: datetime,
        processed_time: datetime,
        payload_merge: bool = True,
    ) -> StateApplication:
        """Derive the next state from an event. Fails closed on invalid
        transitions, version regressions and environment mismatches."""
        if current is not None:
            if current.entity_type is not entity_type or current.entity_id != entity_id:
                raise ContractValidationError(
                    "Current state does not belong to the target entity",
                    location="state.apply",
                )
            assert_same_environment(current.environment, event.environment, "state.apply")

        machine_name = MACHINE_BY_CATEGORY.get(entity_type)
        machine_record: TransitionRecord | None = None
        if machine_name is not None:
            machine = self._machines.machine(machine_name)
            previous_status = current.status if current is not None else machine.initial
            if current is None and new_status == machine.initial:
                pass  # genesis asserting the initial condition is not a transition
            else:
                machine_record = self._machines.apply(
                    machine_name,
                    previous_status,
                    new_status,
                    reason=reason,
                    actor=actor,
                    correlation_id=event.correlation_id,
                    timestamp=processed_time,
                )
        elif not isinstance(new_status, str) or not new_status:
            raise ContractValidationError(
                "Non-machine categories still require a non-empty status",
                location="state.apply.new_status",
            )

        previous_version = current.state_version if current is not None else 0
        previous_state = current.status if current is not None else None
        merged_payload: dict[str, Any] = {}
        if current is not None and payload_merge:
            merged_payload.update(current.payload)
        merged_payload.update(payload)

        state = StateRecord(
            state_id=new_identifier("state_id"),
            entity_type=entity_type,
            entity_id=entity_id,
            state_version=previous_version + 1,
            status=new_status,
            payload=merged_payload,
            effective_time=ensure_utc(effective_time, location="state.effective_time"),
            observed_time=ensure_utc(observed_time, location="state.observed_time"),
            processed_time=ensure_utc(processed_time, location="state.processed_time"),
            source_event_id=event.event_id,
            correlation_id=event.correlation_id,
            environment=event.environment,
            schema_version=CONTRACT_VERSION,
            causation_id=event.causation_id,
        )
        transition = StateTransitionRecord(
            transition_id=new_identifier("transition_id"),
            entity_type=entity_type,
            entity_id=entity_id,
            new_state=new_status,
            previous_version=previous_version,
            new_version=previous_version + 1,
            event_id=event.event_id,
            reason=reason,
            actor=actor,
            timestamp=ensure_utc(processed_time, location="state.processed_time"),
            environment=event.environment,
            correlation_id=event.correlation_id,
            previous_state=previous_state,
        )
        state.validate()
        transition.validate()
        return StateApplication(state=state, transition=transition, machine_transition=machine_record)


def assert_same_environment(left: str, right: str, context: str) -> None:
    from architecture.contracts.environment import assert_same_environment as _assert

    _assert(left, right, context=context)


__all__ = ["StateEngine", "StateApplication", "StateTransitionError"]
