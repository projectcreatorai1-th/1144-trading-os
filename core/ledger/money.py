"""Canonical money policy (owned by core.ledger).

Single source of truth for currencies, precision and rounding:
architecture/currencies.yaml. Money is Decimal-based; binary floats are
rejected as canonical values. Cross-currency arithmetic is refused (FX is a
later-phase boundary) (SECTIONS 40/41).
"""
from __future__ import annotations

import decimal
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.registry import load_registry

CONTRACT_VERSION = "1.0.0"

_ROUNDING_MODES = {
    "ROUND_HALF_EVEN": decimal.ROUND_HALF_EVEN,
    "ROUND_HALF_UP": decimal.ROUND_HALF_UP,
    "ROUND_DOWN": decimal.ROUND_DOWN,
    "ROUND_UP": decimal.ROUND_UP,
}


@lru_cache(maxsize=None)
def _currency_table() -> dict[str, dict[str, Any]]:
    data = load_registry("currencies.yaml")
    return {entry["code"]: dict(entry) for entry in data["currencies"]}


@lru_cache(maxsize=None)
def _default_rounding_name() -> str:
    return str(load_registry("currencies.yaml")["default_rounding"])


def registered_currencies() -> tuple[str, ...]:
    return tuple(sorted(_currency_table()))


def precision_for(currency: str) -> int:
    entry = _currency_table().get(currency)
    if entry is None:
        raise ContractValidationError(
            f"Unknown currency '{currency}' (must be registered in currencies.yaml)",
            location="money.currency",
            details={"registered": list(registered_currencies())},
        )
    return int(entry["precision"])


def rounding_mode_for(currency: str) -> str:
    entry = _currency_table().get(currency)
    if entry is None:
        raise ContractValidationError(
            f"Unknown currency '{currency}'",
            location="money.currency",
        )
    return str(entry.get("rounding", _default_rounding_name()))


def parse_decimal(value: Any, *, location: str) -> Decimal:
    """Parse a canonical decimal. Binary floats are REJECTED (LEDGER-004)."""
    if isinstance(value, bool):
        raise ContractValidationError(
            f"{location} must be a decimal value, got bool",
            location=location,
            rule_id="LEDGER-004",
        )
    if isinstance(value, float):
        raise ContractValidationError(
            f"{location} is a binary float; canonical money values are decimal strings/integers",
            location=location,
            rule_id="LEDGER-004",
            details={"value": repr(value)},
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ContractValidationError(
                f"{location} is not a valid decimal string: {value!r}",
                location=location,
            ) from exc
    raise ContractValidationError(
        f"{location} must be a decimal, int or decimal string, got {type(value).__name__}",
        location=location,
    )


@dataclass(frozen=True)
class Money:
    """Canonical monetary value: Decimal amount + registered currency."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", parse_decimal(self.amount, location="money.amount"))
        precision_for(self.currency)  # validates registration

    @classmethod
    def from_value(cls, value: Any, currency: str) -> "Money":
        return cls(amount=parse_decimal(value, location="money.amount"), currency=currency)

    def quantized(self) -> "Money":
        precision = precision_for(self.currency)
        mode = _ROUNDING_MODES[rounding_mode_for(self.currency)]
        return Money(
            amount=self.amount.quantize(Decimal(1).scaleb(-precision), rounding=mode),
            currency=self.currency,
        )

    def __add__(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __neg__(self) -> "Money":
        return Money(amount=-self.amount, currency=self.currency)

    def _require_same_currency(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise ContractValidationError(
                "Money arithmetic requires Money operands",
                location="money.arithmetic",
            )
        if other.currency != self.currency:
            raise ContractValidationError(
                f"Cross-currency arithmetic is not implemented in Phase 2 "
                f"({self.currency} vs {other.currency}); FX conversion is a later-phase boundary",
                location="money.arithmetic",
                rule_id="FX-BOUNDARY",
                details={"left": self.currency, "right": other.currency},
            )

    def __str__(self) -> str:
        return f"{self.quantized().amount}"

    def canonical_string(self) -> str:
        return str(self.quantized().amount)
