"""Observability contract (kernel port).

Structured logging for Phase 1: timestamp, level, component, operation,
correlation_id, event_id, source_id, duration, status, error_code.
Logs are observability output only - NEVER a source of truth (the event
store is). Secrets and sensitive fields are redacted before emission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.time import ensure_utc

SECRET_LOG_MARKERS = ("password", "secret", "token", "api_key", "apikey", "credential")


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class LogRecord:
    """One structured log entry (contract fields per SECTION 32)."""

    timestamp: datetime
    level: LogLevel
    component: str
    operation: str
    status: str
    correlation_id: str | None = None
    event_id: str | None = None
    source_id: str | None = None
    duration_ms: float | None = None
    error_code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        ensure_utc(self.timestamp, location="log.timestamp")
        if not isinstance(self.level, LogLevel):
            raise ContractValidationError(
                f"log.level must be a LogLevel, got {self.level!r}",
                location="log.level",
            )
        for name in ("component", "operation", "status"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"log.{name} must be a non-empty string",
                    location=f"log.{name}",
                )
        if self.duration_ms is not None and (
            not isinstance(self.duration_ms, (int, float))
            or isinstance(self.duration_ms, bool)
            or self.duration_ms < 0
        ):
            raise ContractValidationError(
                "log.duration_ms must be a non-negative number or None",
                location="log.duration_ms",
            )
        for key in self.details:
            lowered = str(key).lower()
            if any(marker in lowered for marker in SECRET_LOG_MARKERS):
                raise ContractValidationError(
                    f"Log details must not contain secret-like keys (found '{key}')",
                    location="log.details",
                    rule_id="SEC-001",
                )

    def redacted(self, sensitive_fields: tuple[str, ...] = ()) -> "LogRecord":
        """Return a copy with configured sensitive field values redacted."""
        import dataclasses

        safe_details = {
            key: ("[redacted]" if str(key) in sensitive_fields else value)
            for key, value in self.details.items()
        }
        return dataclasses.replace(self, details=safe_details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": ensure_utc(self.timestamp).isoformat(),
            "level": self.level.value,
            "component": self.component,
            "operation": self.operation,
            "status": self.status,
            "correlation_id": self.correlation_id,
            "event_id": self.event_id,
            "source_id": self.source_id,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "details": dict(self.details),
        }


class Logger(Protocol):
    """Structured logging port. Implementations live in platform.monitoring."""

    def log(self, record: LogRecord) -> None: ...  # noqa: E704


def redact_payload_view(
    payload: Mapping[str, Any], sensitive_fields: tuple[str, ...]
) -> dict[str, Any]:
    """Redacted VIEW of a payload for logs/derived views. Raw payloads are
    never transformed (SECTION 45); only views are redacted."""
    return {
        key: ("[redacted]" if str(key) in sensitive_fields else value)
        for key, value in payload.items()
    }
