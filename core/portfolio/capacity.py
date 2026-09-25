"""Capacity + liquidity boundary (owned by core.portfolio).

Capacity separates THEORETICAL / OBSERVED / ESTIMATED / UNKNOWN - estimates
never masquerade as observed facts. Liquidity budgets are UNKNOWN when data
is missing (SECTION 25/26)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.time import ensure_utc
from core.ledger.money import parse_decimal

CONTRACT_VERSION = "1.0.0"


class CapacitySubject(Enum):
    STRATEGY = "STRATEGY"
    SYMBOL = "SYMBOL"
    MARKET = "MARKET"
    LIQUIDITY = "LIQUIDITY"
    RISK = "RISK"
    CAPITAL = "CAPITAL"


class CapacityKind(Enum):
    THEORETICAL = "THEORETICAL"
    OBSERVED = "OBSERVED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CapacityAssessment:
    subject_type: CapacitySubject
    subject_id: str
    capacity_kind: CapacityKind
    unit: str
    provenance: Mapping[str, Any]
    as_of: datetime
    environment: str
    value: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.subject_type, CapacitySubject):
            raise ContractValidationError(
                "capacity.subject_type must be a CapacitySubject",
                location="capacity.subject_type", rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.subject_id, str) or not self.subject_id:
            raise ContractValidationError(
                "capacity.subject_id must be a non-empty string", location="capacity.subject_id",
            )
        if not isinstance(self.capacity_kind, CapacityKind):
            raise ContractValidationError(
                "capacity.capacity_kind must be a CapacityKind",
                location="capacity.capacity_kind", rule_id="SCHEMA-ENUM",
            )
        if self.capacity_kind is CapacityKind.UNKNOWN and self.value is not None:
            raise ContractValidationError(
                "UNKNOWN capacity carries no value (never guessed)",
                location="capacity.value",
            )
        if self.value is not None:
            parse_decimal(self.value, location="capacity.value")
        if not isinstance(self.unit, str) or not self.unit:
            raise ContractValidationError(
                "capacity.unit must be a non-empty string", location="capacity.unit",
            )
        if not isinstance(self.provenance, Mapping) or not self.provenance:
            raise ContractValidationError(
                "capacity.provenance is required (values are traceable facts or labelled estimates)",
                location="capacity.provenance", rule_id="PROV-001",
            )
        ensure_utc(self.as_of, location="capacity.as_of")

    def is_exceeded_by(self, requested: Any) -> bool | None:
        """None when capacity is UNKNOWN (callers fail closed on None)."""
        if self.value is None or self.capacity_kind is CapacityKind.UNKNOWN:
            return None
        return parse_decimal(requested, location="capacity.requested") > \
            parse_decimal(self.value, location="capacity.value")


class LiquidityState(Enum):
    NORMAL = "NORMAL"
    REDUCED = "REDUCED"
    INSUFFICIENT = "INSUFFICIENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiquidityBudget:
    symbol: str
    liquidity_state: LiquidityState
    as_of: datetime
    environment: str
    available_liquidity: str | None = None
    required_liquidity: str | None = None
    utilization: str | None = None
    remaining_liquidity: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ContractValidationError(
                "liquidity.symbol must be a non-empty string", location="liquidity.symbol",
            )
        if not isinstance(self.liquidity_state, LiquidityState):
            raise ContractValidationError(
                "liquidity.liquidity_state must be a LiquidityState",
                location="liquidity.liquidity_state", rule_id="SCHEMA-ENUM",
            )
        for name in ("available_liquidity", "required_liquidity", "utilization",
                     "remaining_liquidity"):
            value = getattr(self, name)
            if value is not None:
                parse_decimal(value, location=f"liquidity.{name}")
        ensure_utc(self.as_of, location="liquidity.as_of")

    @staticmethod
    def unknown(symbol: str, as_of: datetime, environment: str) -> "LiquidityBudget":
        budget = LiquidityBudget(symbol=symbol, liquidity_state=LiquidityState.UNKNOWN,
                                 as_of=as_of, environment=environment)
        budget.validate()
        return budget

    def covers(self, required: Any) -> bool | None:
        """None when UNKNOWN (never guessed)."""
        if self.liquidity_state is LiquidityState.UNKNOWN or self.remaining_liquidity is None:
            return None
        return parse_decimal(required, location="liquidity.required") <= \
            parse_decimal(self.remaining_liquidity, location="liquidity.remaining")
