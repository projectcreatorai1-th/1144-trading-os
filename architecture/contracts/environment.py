"""Environment contract (SECTION 18).

Environments are an explicit enum. Every order/decision/action affecting
execution must carry an environment. Mismatches fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from architecture.contracts.errors import (
    ContractValidationError,
    EnvironmentMismatchError,
    EnvironmentTransitionError,
    StateTransitionError,
)
from architecture.contracts.registry import load_registry
from architecture.contracts.state_machine import build_state_machine_registry
from architecture.contracts.time import ensure_utc, utc_now

ENVIRONMENT_MACHINE = "environment"


class Environment(Enum):
    RESEARCH = "RESEARCH"
    SIMULATION = "SIMULATION"
    REPLAY = "REPLAY"
    PAPER = "PAPER"
    DEMO = "DEMO"
    LIVE = "LIVE"
    BACKTEST = "BACKTEST"


def parse_environment(value: object, *, location: str = "environment") -> Environment:
    """Parse an environment value; unknown or missing values fail closed."""
    if value is None:
        raise ContractValidationError(
            "environment is required (missing environment fails closed)",
            location=location,
            rule_id="ENV-002",
        )
    if isinstance(value, Environment):
        return value
    if isinstance(value, str):
        try:
            return Environment(value)
        except ValueError:
            pass
    raise ContractValidationError(
        f"Unknown environment {value!r}",
        location=location,
        rule_id="ENV-002",
        details={"supported": [e.value for e in Environment]},
    )


def supported_environments() -> list[str]:
    registry = load_registry("architecture.yaml")
    return list(registry["environments"]["supported"])


def assert_same_environment(
    left: object, right: object, *, context: str
) -> None:
    """Fail closed when two environments that must match do not match."""
    l = parse_environment(left, location=f"{context}.left")
    r = parse_environment(right, location=f"{context}.right")
    if l is not r:
        raise EnvironmentMismatchError(
            f"Environment mismatch in {context}: {l.value} != {r.value}",
            location=context,
            details={"left": l.value, "right": r.value, "context": context},
        )


def validate_environment_transition(current: Environment, target: Environment) -> None:
    """Validate an explicit environment transition against the registry.

    Only adjacent environment steps are valid transitions; everything else
    (e.g. RESEARCH -> LIVE) fails closed.
    """
    machines = build_state_machine_registry()
    try:
        machines.apply(
            ENVIRONMENT_MACHINE,
            current.value,
            target.value,
            reason="environment transition",
            actor="environment.contract",
        )
    except StateTransitionError as exc:
        raise EnvironmentTransitionError(
            f"Environment transition {current.value} -> {target.value} is not a "
            "validated transition (adjacent steps only)",
            location="environment.transition",
            details={
                "current": current.value,
                "target": target.value,
                "cause": exc.message,
            },
        ) from exc


@dataclass(frozen=True)
class EnvironmentDeclaration:
    """Declares the environment of a process/adapter/component."""

    environment: Environment
    declared_by: str
    declared_at: datetime
    capabilities: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()
    contract_version: str = "1.0.0"

    def validate(self) -> None:
        parse_environment(self.environment, location="environment")
        if not self.declared_by or not isinstance(self.declared_by, str):
            raise ContractValidationError(
                "declared_by must be a non-empty string",
                location="environment.declared_by",
            )
        ensure_utc(self.declared_at, location="environment.declared_at")
        for item in self.capabilities + self.restrictions:
            if not isinstance(item, str):
                raise ContractValidationError(
                    "capabilities/restrictions entries must be strings",
                    location="environment.capabilities",
                )

    def matches(self, other: object, *, context: str = "environment.declaration") -> None:
        assert_same_environment(self.environment, other, context=context)


def declare_environment(
    environment: Environment | str,
    *,
    declared_by: str,
    capabilities: tuple[str, ...] = (),
    restrictions: tuple[str, ...] = (),
    declared_at: datetime | None = None,
) -> EnvironmentDeclaration:
    declaration = EnvironmentDeclaration(
        environment=parse_environment(environment),
        declared_by=declared_by,
        declared_at=declared_at if declared_at is not None else utc_now(),
        capabilities=tuple(capabilities),
        restrictions=tuple(restrictions),
    )
    declaration.validate()
    return declaration
