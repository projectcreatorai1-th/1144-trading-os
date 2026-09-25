"""AI interface contracts (owned by adapters.ai).

Phase 0 defines interfaces only - NO AI trading behavior (SECTION 35).
AI output flows: AI -> Analysis -> Decision/Policy -> Risk -> Execution.
AI NEVER connects to MT5/broker and NEVER bypasses policy/risk (RULE 006-008).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.time import ensure_utc

CONTRACT_VERSION = "1.0.0"


class AIAnalysisType(Enum):
    MARKET_REGIME = "MARKET_REGIME"
    NEWS_SENTIMENT = "NEWS_SENTIMENT"
    STRATEGY_SUPPORT = "STRATEGY_SUPPORT"
    RISK_CONTEXT = "RISK_CONTEXT"


@dataclass(frozen=True)
class ModelVersion:
    """Immutable model version record (models are registered, versioned, traceable)."""

    model_version_id: str
    model_id: str
    version: str
    registered_at: datetime
    description: str = ""

    def validate(self) -> None:
        validate_identifier(
            "model_version_id", self.model_version_id, location="model_version.id"
        )
        validate_identifier("model_id", self.model_id, location="model_version.model_id")
        if not isinstance(self.version, str) or not self.version:
            raise ContractValidationError(
                "model_version.version must be a non-empty string",
                location="model_version.version",
            )
        ensure_utc(self.registered_at, location="model_version.registered_at")


@dataclass(frozen=True)
class Model:
    """Registered AI/quant model identity."""

    model_id: str
    name: str
    active_version_id: str | None = None

    def validate(self) -> None:
        validate_identifier("model_id", self.model_id, location="model.model_id")
        if not isinstance(self.name, str) or not self.name:
            raise ContractValidationError(
                "model.name must be a non-empty string",
                location="model.name",
            )
        if self.active_version_id is not None:
            validate_identifier(
                "model_version_id", self.active_version_id, location="model.active_version_id"
            )


@dataclass(frozen=True)
class AIAnalysis:
    """Analysis output contract. Analysis is INPUT to decisions - never executable output."""

    analysis_id: str
    model_version_id: str
    analysis_type: AIAnalysisType
    environment: str
    created_at: datetime
    output: Mapping[str, Any]
    confidence: float
    correlation_id: str

    def validate(self) -> None:
        validate_identifier("model_version_id", self.model_version_id, location="ai.model_version_id")
        if not isinstance(self.analysis_type, AIAnalysisType):
            raise ContractValidationError(
                f"ai.analysis_type must be an AIAnalysisType, got {self.analysis_type!r}",
                location="ai.analysis_type",
                rule_id="SCHEMA-ENUM",
            )
        parse_environment(self.environment, location="ai.environment")
        ensure_utc(self.created_at, location="ai.created_at")
        if not isinstance(self.output, Mapping):
            raise ContractValidationError(
                "ai.output must be a mapping",
                location="ai.output",
            )
        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise ContractValidationError(
                "ai.confidence must be within [0.0, 1.0]; confidence is analysis input, "
                "never a risk permission (RULE 008)",
                location="ai.confidence",
            )


class ModelRegistry(ABC):
    """Registry port for models and versions (implementations in later phases)."""

    @abstractmethod
    def register_version(self, model: Model, version: ModelVersion) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def active_version(self, model_id: str) -> ModelVersion:  # pragma: no cover
        ...
