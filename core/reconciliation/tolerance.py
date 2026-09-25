"""Tolerance policy (owned by core.reconciliation).

Single source of truth: architecture/tolerances.yaml. Tolerances are
explicit decimal strings; modules never hard-code them (SECTION 26).
Comparison semantics: within BOTH absolute and relative tolerance = match
(see registry rules)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.registry import load_registry
from core.reconciliation.contracts import Difference, DifferenceSeverity
from core.ledger.money import parse_decimal

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class Tolerance:
    absolute: Decimal
    relative: Decimal

    def to_dict(self) -> dict[str, str]:
        return {"absolute": str(self.absolute), "relative": str(self.relative)}


@lru_cache(maxsize=None)
def _tolerances() -> dict[str, dict[str, str]]:
    data = load_registry("tolerances.yaml")
    table: dict[str, dict[str, str]] = {}
    for scope, spec in data.get("by_scope", {}).items():
        table[str(scope)] = dict(spec)
    return table


@lru_cache(maxsize=None)
def _defaults() -> dict[str, str]:
    return dict(load_registry("tolerances.yaml")["defaults"])


def tolerance_for(scope: str) -> Tolerance:
    spec = _tolerances().get(scope, _defaults())
    return Tolerance(
        absolute=parse_decimal(spec["absolute"], location=f"tolerance.{scope}.absolute"),
        relative=parse_decimal(spec["relative"], location=f"tolerance.{scope}.relative"),
    )


def compare_values(
    *,
    field: str,
    internal: Decimal,
    external: Decimal,
    tolerance: Tolerance,
) -> Difference:
    """Deterministic decimal comparison with tolerance semantics. Never float
    equality (SECTION 25)."""
    difference = internal - external
    absolute_difference = abs(difference)
    if external != 0:
        relative_difference = absolute_difference / abs(external)
        relative_text = str(relative_difference)
    elif internal != 0:
        relative_difference = Decimal("Infinity")
        relative_text = "undefined"
    else:
        relative_difference = Decimal("0")
        relative_text = "0"
    within = (
        absolute_difference <= tolerance.absolute
        and relative_difference <= tolerance.relative
    )
    return Difference(
        field=field,
        internal_value=str(internal),
        external_value=str(external),
        difference=str(difference),
        absolute_difference=str(absolute_difference),
        relative_difference=relative_text,
        tolerance=str(tolerance.absolute),
        severity=DifferenceSeverity.INFO if within else DifferenceSeverity.ERROR,
        reason="within tolerance" if within else "beyond tolerance",
    )
