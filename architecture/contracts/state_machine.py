"""State machine engine (SECTION 10).

All valid states and transitions are declared in
architecture/state-machines.yaml. State never changes via arbitrary strings;
every transition is validated against the registry and recorded with reason,
actor and timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, Mapping

from architecture.contracts.errors import (
    RequirementNotMetError,
    StateTransitionError,
    UnknownStateError,
)
from architecture.contracts.registry import load_registry
from architecture.contracts.time import ensure_utc, utc_now

TRANSITION_REQUIREMENTS = ("VALID_RISK_DECISION", "POLICY_APPROVED", "HUMAN_APPROVAL")


@dataclass(frozen=True)
class TransitionSpec:
    from_state: str
    to_state: str
    requirement: str | None = None


@dataclass(frozen=True)
class StateMachineDefinition:
    name: str
    description: str
    initial: str
    states: frozenset[str]
    terminal: frozenset[str]
    transitions: frozenset[TransitionSpec]

    def transition_spec(self, from_state: str, to_state: str) -> TransitionSpec | None:
        for spec in self.transitions:
            if spec.from_state == from_state and spec.to_state == to_state:
                return spec
        return None

    def is_valid_transition(self, from_state: str, to_state: str) -> bool:
        return self.transition_spec(from_state, to_state) is not None


@dataclass(frozen=True)
class TransitionRecord:
    """Structured evidence of one applied transition (RULE 017/018)."""

    machine: str
    previous_state: str
    new_state: str
    reason: str
    actor: str
    timestamp: datetime
    requirement: str | None = None
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "machine": self.machine,
            "previous_state": self.previous_state,
            "new_state": self.new_state,
            "reason": self.reason,
            "actor": self.actor,
            "timestamp": ensure_utc(self.timestamp).isoformat(),
            "requirement": self.requirement,
            "correlation_id": self.correlation_id,
        }


class StateMachineRegistry:
    """Loads and enforces the state machine registry."""

    def __init__(self, machines: Mapping[str, StateMachineDefinition]) -> None:
        self._machines: dict[str, StateMachineDefinition] = dict(machines)

    def machine(self, name: str) -> StateMachineDefinition:
        machine = self._machines.get(name)
        if machine is None:
            raise StateTransitionError(
                f"Unknown state machine '{name}'",
                rule_id="SM-003",
                location="state_machine",
                details={"known_machines": sorted(self._machines)},
            )
        return machine

    @property
    def machine_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._machines))

    def is_known_state(self, machine_name: str, state: str) -> bool:
        return state in self.machine(machine_name).states

    def validate_states(self, machine_name: str, *states: str) -> None:
        machine = self.machine(machine_name)
        for state in states:
            if state not in machine.states:
                raise UnknownStateError(
                    f"Unknown state '{state}' for machine '{machine_name}'",
                    location=machine_name,
                    details={"valid_states": sorted(machine.states)},
                )

    def apply(
        self,
        machine_name: str,
        current: str,
        target: str,
        *,
        reason: str,
        actor: str,
        context: Mapping[str, Any] | None = None,
        timestamp: datetime | None = None,
        correlation_id: str | None = None,
    ) -> TransitionRecord:
        """Validate and record a transition; raise on any invalid attempt."""
        machine = self.machine(machine_name)
        self.validate_states(machine_name, current, target)
        if not isinstance(reason, str) or not reason:
            raise StateTransitionError(
                "Transition reason is required",
                rule_id="SM-005",
                location=machine_name,
                details={"from": current, "to": target},
            )
        if not isinstance(actor, str) or not actor:
            raise StateTransitionError(
                "Transition actor is required",
                rule_id="SM-006",
                location=machine_name,
                details={"from": current, "to": target},
            )
        spec = machine.transition_spec(current, target)
        if spec is None:
            raise StateTransitionError(
                f"Invalid transition '{current}' -> '{target}' for machine '{machine_name}'",
                location=machine_name,
                details={
                    "from": current,
                    "to": target,
                    "valid_targets_from": sorted(
                        t.to_state for t in machine.transitions if t.from_state == current
                    ),
                },
            )
        if spec.requirement is not None:
            self._check_requirement(machine_name, spec, context or {})
        ts = ensure_utc(timestamp, location="transition.timestamp") if timestamp else utc_now()
        return TransitionRecord(
            machine=machine_name,
            previous_state=current,
            new_state=target,
            reason=reason,
            actor=actor,
            timestamp=ts,
            requirement=spec.requirement,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _check_requirement(
        machine_name: str, spec: TransitionSpec, context: Mapping[str, Any]
    ) -> None:
        if spec.requirement not in TRANSITION_REQUIREMENTS:
            raise StateTransitionError(
                f"Unknown transition requirement '{spec.requirement}'",
                rule_id="SM-007",
                location=machine_name,
            )
        satisfied = context.get(_requirement_context_key(spec.requirement))
        if satisfied is not True:
            raise RequirementNotMetError(
                f"Transition requirement '{spec.requirement}' not satisfied "
                f"for '{spec.from_state}' -> '{spec.to_state}'",
                location=machine_name,
                details={"requirement": spec.requirement},
            )


def _requirement_context_key(requirement: str) -> str:
    return {
        "VALID_RISK_DECISION": "risk_decision_validated",
        "POLICY_APPROVED": "policy_approved",
        "HUMAN_APPROVAL": "human_approval",
    }[requirement]


def _build_registry(data: Mapping[str, Any]) -> StateMachineRegistry:
    machines: dict[str, StateMachineDefinition] = {}
    for name, spec in data["machines"].items():
        transitions = frozenset(
            TransitionSpec(
                from_state=t["from"],
                to_state=t["to"],
                requirement=t.get("requirement"),
            )
            for t in spec.get("transitions", [])
        )
        machines[name] = StateMachineDefinition(
            name=name,
            description=spec.get("description", ""),
            initial=spec["initial"],
            states=frozenset(spec["states"]),
            terminal=frozenset(spec.get("terminal", [])),
            transitions=transitions,
        )
    return StateMachineRegistry(machines)


@lru_cache(maxsize=None)
def build_state_machine_registry() -> StateMachineRegistry:
    return _build_registry(load_registry("state-machines.yaml"))
