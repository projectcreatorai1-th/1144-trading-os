"""Feature + model registries (owned by core.intelligence).

Content-addressed, append-only. Registry ids are handles; identity is the
content hash. Feature definitions and models are immutable: a changed
calculation is a new version, never a mutation (SECTION 5/12/55).
"""
from __future__ import annotations

from typing import Iterator, Mapping

from architecture.contracts.errors import ContractError

from core.intelligence.contracts import (
    FeatureDefinition,
    ModelDefinition,
)

CONTRACT_VERSION = "1.0.0"


class RegistryError(ContractError):
    rule_id = "IREGISTRY"


class FeatureRegistry:
    """Immutable feature definition registry."""

    def __init__(self) -> None:
        self._entries: dict[str, FeatureDefinition] = {}

    def register(self, definition: FeatureDefinition) -> FeatureDefinition:
        definition.validate()
        key = f"{definition.feature_id}@{definition.feature_version}"
        existing = self._entries.get(key)
        if existing is not None:
            if existing.content_hash != definition.content_hash:
                raise RegistryError(
                    f"feature {key} already registered with different "
                    "content - a changed calculation is a NEW VERSION, "
                    "never a mutation",
                    location="feature.registry.register", rule_id="FREG-001",
                    details={"feature": key})
            return existing
        self._entries[key] = definition
        return definition

    def get(self, feature_id: str, feature_version: str) -> FeatureDefinition:
        key = f"{feature_id}@{feature_version}"
        definition = self._entries.get(key)
        if definition is None:
            raise RegistryError(
                f"unknown feature {key}",
                location="feature.registry.get", rule_id="FREG-002",
                details={"feature": key})
        return definition

    def __iter__(self) -> Iterator[FeatureDefinition]:
        return iter(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)


class ModelRegistry:
    """Immutable model registry. Exactly one registry exists - candidates
    become StrategyCandidates through the Phase 6 research contract."""

    def __init__(self) -> None:
        self._by_handle: dict[str, ModelDefinition] = {}
        self._by_hash: dict[str, ModelDefinition] = {}

    def register(self, model: ModelDefinition) -> ModelDefinition:
        model.validate()
        existing_hash = self._by_hash.get(model.model_hash)
        if existing_hash is not None:
            raise RegistryError(
                "a model with identical content already exists "
                "(content identity collision)",
                location="model.registry.register", rule_id="MREG-001")
        key = f"{model.model_id}@{model.model_version}"
        existing = self._by_handle.get(key)
        if existing is not None:
            raise RegistryError(
                f"model handle {key} already registered - a changed model "
                "is a NEW VERSION",
                location="model.registry.register", rule_id="MREG-002",
                details={"model": key})
        self._by_handle[key] = model
        self._by_hash[model.model_hash] = model
        return model

    def get(self, model_id: str, model_version: str) -> ModelDefinition:
        """Explicit version lookup ONLY - 'latest model' does not exist as
        an operation (SECTION 59 / AI-026)."""
        key = f"{model_id}@{model_version}"
        model = self._by_handle.get(key)
        if model is None:
            raise RegistryError(
                f"unknown model {key} (model selection is explicit by "
                "version - never 'latest')",
                location="model.registry.get", rule_id="MREG-003",
                details={"model": key})
        return model

    def by_hash(self, model_hash: str) -> ModelDefinition:
        model = self._by_hash.get(model_hash)
        if model is None:
            raise RegistryError(
                "unknown model hash",
                location="model.registry.by_hash", rule_id="MREG-004")
        return model

    def __iter__(self) -> Iterator[ModelDefinition]:
        return iter(self._by_handle.values())

    def __len__(self) -> int:
        return len(self._by_handle)
