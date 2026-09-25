"""Position contract (owned by core.portfolio).

Positions are per (account, strategy): one account may host multiple EAs /
strategies. No assumption that one account equals one strategy.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_any_identifier, validate_identifier
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    TransitionRecord,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_not_before, ensure_utc
from architecture.contracts.versioning import SemVer
from core.execution.contracts import to_decimal

CONTRACT_VERSION = "1.0.0"
POSITION_STATUS_MACHINE = "position_status"


class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(Enum):
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class Position:
    position_id: str
    account_id: str
    symbol: str
    side: PositionSide
    quantity: Decimal
    average_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    margin: Decimal
    exposure: Decimal
    strategy_id: str
    environment: str
    opened_at: datetime
    updated_at: datetime
    status: PositionStatus
    correlation_id: str
    causation_id: str | None = None
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "quantity",
            "average_price",
            "unrealized_pnl",
            "realized_pnl",
            "margin",
            "exposure",
        ):
            object.__setattr__(self, name, to_decimal(getattr(self, name), f"position.{name}"))

    def validate(self) -> None:
        validate_identifier("position_id", self.position_id, location="position.position_id")
        for name in ("account_id", "symbol"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ContractValidationError(
                    f"position.{name} must be a non-empty string",
                    location=f"position.{name}",
                )
        if not isinstance(self.side, PositionSide):
            raise ContractValidationError(
                f"position.side must be a PositionSide, got {self.side!r}",
                location="position.side",
                rule_id="SCHEMA-ENUM",
            )
        open_states = (PositionStatus.OPEN, PositionStatus.PARTIALLY_CLOSED)
        if self.status in open_states and self.quantity <= 0:
            raise ContractValidationError(
                f"position.quantity must be > 0 while {self.status.value}",
                location="position.quantity",
            )
        if self.status is PositionStatus.CLOSED and self.quantity != 0:
            raise ContractValidationError(
                "position.quantity must be 0 when CLOSED",
                location="position.quantity",
            )
        if self.average_price <= 0:
            raise ContractValidationError(
                "position.average_price must be > 0",
                location="position.average_price",
            )
        if self.margin < 0 or self.exposure < 0:
            raise ContractValidationError(
                "position.margin and position.exposure must be >= 0",
                location="position.margin",
            )
        validate_identifier("strategy_id", self.strategy_id, location="position.strategy_id")
        parse_environment(self.environment, location="position.environment")
        opened_at = ensure_utc(self.opened_at, location="position.opened_at")
        ensure_not_before(self.updated_at, not_before=opened_at, location="position.updated_at")
        if not isinstance(self.status, PositionStatus):
            raise ContractValidationError(
                f"position.status must be a PositionStatus, got {self.status!r}",
                location="position.status",
                rule_id="SCHEMA-ENUM",
            )
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="position.causation_id")
        validate_any_identifier(self.correlation_id, location="position.correlation_id")
        SemVer.parse(self.contract_version, location="position.contract_version")

    def transition_status(
        self,
        target: PositionStatus,
        *,
        reason: str,
        actor: str,
        quantity: Decimal | None = None,
        machines: StateMachineRegistry | None = None,
    ) -> tuple["Position", TransitionRecord]:
        """Apply a position-status transition; CLOSED sets quantity to zero."""
        registry = machines or build_state_machine_registry()
        record = registry.apply(
            POSITION_STATUS_MACHINE,
            self.status.value,
            target.value,
            reason=reason,
            actor=actor,
            correlation_id=self.correlation_id or None,
        )
        new_quantity = self.quantity if target is not PositionStatus.CLOSED else Decimal("0")
        if target is PositionStatus.CLOSED and quantity is not None:
            raise ContractValidationError(
                "Closing quantity is not contractible; a CLOSED position has quantity 0",
                location="position.transition",
            )
        updated = replace(self, status=target, quantity=new_quantity, updated_at=self.updated_at)
        updated.validate()
        return updated, record
