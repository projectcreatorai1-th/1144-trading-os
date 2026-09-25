"""Data quality contract (owned by core.validation).

Risk decisions reference a data quality level. UNKNOWN and DEGRADED never
mean "no risk" (RULE 010/011): the risk contract fails closed on them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.time import ensure_utc

CONTRACT_VERSION = "1.1.0"

SYMBOL_ALLOWED_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789./_-")


def symbol_is_well_formed(value: object) -> bool:
    """Structural symbol check only (no trading semantics): non-empty string
    of letters, digits and . / _ - separators; case-insensitive because
    casing is a normalization concern (quality rule DQ-011)."""
    if not isinstance(value, str) or not value:
        return False
    return all(ch in SYMBOL_ALLOWED_CHARS for ch in value.upper())


class DataQualityLevel(Enum):
    VERIFIED = "VERIFIED"
    VALIDATED = "VALIDATED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"

    @property
    def blocks_risk_allowance(self) -> bool:
        """Levels on which risk must not return ALLOW/LIMITED (fail closed)."""
        return self in (
            DataQualityLevel.DEGRADED,
            DataQualityLevel.STALE,
            DataQualityLevel.INVALID,
            DataQualityLevel.UNKNOWN,
        )

    @property
    def allows_downstream_processing(self) -> bool:
        """INVALID data must never enter downstream production processing."""
        return self is not DataQualityLevel.INVALID


@dataclass(frozen=True)
class DataQualityReport:
    level: DataQualityLevel
    checked_at: datetime
    missing_fields: tuple[str, ...] = ()
    stale_data: bool = False
    details: Mapping[str, Any] | None = None

    def validate(self) -> None:
        if not isinstance(self.level, DataQualityLevel):
            raise ContractValidationError(
                f"data_quality.level must be a DataQualityLevel, got {self.level!r}",
                location="data_quality.level",
                rule_id="SCHEMA-ENUM",
            )
        ensure_utc(self.checked_at, location="data_quality.checked_at")
        for item in self.missing_fields:
            if not isinstance(item, str):
                raise ContractValidationError(
                    "data_quality.missing_fields entries must be strings",
                    location="data_quality.missing_fields",
                )
        if not isinstance(self.stale_data, bool):
            raise ContractValidationError(
                "data_quality.stale_data must be a boolean",
                location="data_quality.stale_data",
            )
        if self.details is not None and not isinstance(self.details, Mapping):
            raise ContractValidationError(
                "data_quality.details must be a mapping",
                location="data_quality.details",
            )

    @classmethod
    def unknown(cls, checked_at: datetime, reason: str) -> "DataQualityReport":
        """A fail-closed UNKNOWN report: missing data is risk, not safety."""
        report = cls(
            level=DataQualityLevel.UNKNOWN,
            checked_at=checked_at,
            missing_fields=(f"unknown:{reason}",),
            stale_data=True,
            details={"reason": reason},
        )
        report.validate()
        return report

    @property
    def level_name(self) -> str:
        return self.level.value
