"""Configuration envelope contract (SECTION 37).

System, environment, strategy, risk and user-preference configurations are
separated; every decision/risk/execution-affecting configuration is versioned.
Secrets never appear in configuration data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.time import ensure_utc
from architecture.contracts.versioning import SemVer

SECRET_KEY_MARKERS = ("password", "secret", "token", "api_key", "apikey", "credential")


class ConfigKind(Enum):
    SYSTEM = "SYSTEM"
    ENVIRONMENT = "ENVIRONMENT"
    STRATEGY = "STRATEGY"
    RISK = "RISK"
    USER_PREFERENCES = "USER_PREFERENCES"


@dataclass(frozen=True)
class ConfigContract:
    config_id: str
    kind: ConfigKind
    version: str
    data: Mapping[str, Any]
    created_by: str
    created_at: datetime
    environment: str | None = None
    contract_version: str = "1.0.0"

    def validate(self) -> None:
        validate_identifier("config_id", self.config_id, location="config.config_id")
        if not isinstance(self.kind, ConfigKind):
            raise ContractValidationError(
                f"config.kind must be a ConfigKind, got {self.kind!r}",
                location="config.kind",
            )
        SemVer.parse(self.version, location="config.version")
        if not isinstance(self.data, Mapping):
            raise ContractValidationError(
                "config.data must be a mapping",
                location="config.data",
            )
        validate_identifier("user_id", self.created_by, location="config.created_by")
        ensure_utc(self.created_at, location="config.created_at")
        if self.environment is not None:
            parse_environment(self.environment, location="config.environment")
        if self.kind in (ConfigKind.STRATEGY, ConfigKind.RISK) and self.environment is None:
            raise ContractValidationError(
                f"config kind {self.kind.value} requires an explicit environment scope",
                location="config.environment",
            )
        _reject_secrets(self.data, location="config.data")

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "kind": self.kind.value,
            "version": self.version,
            "environment": self.environment,
            "data": dict(self.data),
            "created_by": self.created_by,
            "created_at": ensure_utc(self.created_at).isoformat(),
        }


def _reject_secrets(data: Mapping[str, Any], location: str) -> None:
    for key in data:
        lowered = str(key).lower()
        if any(marker in lowered for marker in SECRET_KEY_MARKERS):
            raise ContractValidationError(
                f"Configuration must not contain secret-like keys (found '{key}'); "
                "secrets are never stored in configuration or source code",
                location=location,
                rule_id="SEC-001",
                details={"key": str(key)},
            )
