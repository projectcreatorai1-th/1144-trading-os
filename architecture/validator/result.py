"""Validator result types: PASS / WARNING / FAIL with structured items."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ValidationStatus:
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


_SEVERITY_RANK = {ValidationStatus.PASS: 0, ValidationStatus.WARNING: 1, ValidationStatus.FAIL: 2}


@dataclass(frozen=True)
class ValidationItem:
    rule_id: str
    severity: str
    location: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "location": self.location,
            "message": self.message,
            "details": self.details,
        }


class ValidationResult:
    def __init__(self, items: list[ValidationItem]) -> None:
        self._items = list(items)

    @property
    def items(self) -> list[ValidationItem]:
        return list(self._items)

    @property
    def status(self) -> str:
        worst = ValidationStatus.PASS
        for item in self._items:
            if _SEVERITY_RANK.get(item.severity, 2) > _SEVERITY_RANK[worst]:
                worst = item.severity
        return worst

    @property
    def failures(self) -> list[ValidationItem]:
        return [i for i in self._items if i.severity == ValidationStatus.FAIL]

    @property
    def warnings(self) -> list[ValidationItem]:
        return [i for i in self._items if i.severity == ValidationStatus.WARNING]

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total_items": len(self._items),
            "failures": len(self.failures),
            "warnings": len(self.warnings),
            "rules_triggered": sorted({i.rule_id for i in self._items}),
        }

    def render(self) -> str:
        lines = [
            "=" * 60,
            "1144 TRADING OS - ARCHITECTURE VALIDATION",
            "=" * 60,
        ]
        summary = self.summary()
        lines.append(
            f"STATUS: {summary['status']}  "
            f"(failures={summary['failures']}, warnings={summary['warnings']})"
        )
        if self._items:
            lines.append("-" * 60)
            for item in self._items:
                lines.append(
                    f"[{item.severity}] {item.rule_id} {item.location}: {item.message}"
                )
                if item.details:
                    lines.append(f"          details: {item.details}")
        else:
            lines.append("No violations found.")
        lines.append("=" * 60)
        return "\n".join(lines)
