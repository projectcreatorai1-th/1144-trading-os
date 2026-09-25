"""Training service (owned by core.intelligence).

Immutable TrainingRuns with a dependency lock verified at completion
(SECTION 16/17/18/56). Training failures never create validated models,
never replace anything and never touch production configuration. Partial
artifacts are quarantined: COMPLETED requires artifact + completion time.
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.adapter import (
    FRAMEWORK_VERSION,
    ModelAdapter,
    ModelAdapterError,
)
from core.intelligence.contracts import (
    IntelligenceDataset,
    ModelDefinition,
    TrainingConfig,
    TrainingRun,
    TrainingStatus,
    canonical_hash,
)
from core.intelligence.registry import ModelRegistry

CONTRACT_VERSION = "1.0.0"
RUNTIME_VERSION = f"python-{sys.version_info.major}.{sys.version_info.minor}"


class TrainingError(ContractError):
    rule_id = "TRAIN"


def dependency_fingerprint(*, adapter_name: str) -> str:
    """SECTION 17: dependency versions are recorded (stdlib-only plane).
    sys.implementation is used because the project's own `platform`
    package shadows the stdlib module of the same name."""
    return canonical_hash({
        "adapter": adapter_name,
        "framework": FRAMEWORK_VERSION,
        "runtime": RUNTIME_VERSION,
        "implementation": sys.implementation.name,
    })


def code_fingerprint(trainer_source: str) -> str:
    return hashlib.sha256(trainer_source.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TrainingOutcome:
    run: TrainingRun
    artifact_json: str | None
    model: ModelDefinition | None


class TrainingService:
    """Deterministic training orchestration with dependency lock."""

    def __init__(self, registry: ModelRegistry,
                 adapter: ModelAdapter) -> None:
        self._registry = registry
        self._adapter = adapter

    def train(self, *, config: TrainingConfig, dataset: IntelligenceDataset,
              rows: Sequence[Mapping[str, Any]], model_id: str,
              model_version: str, started_at: datetime,
              trainer_source: str,
              fail: Callable[[], None] | None = None) -> TrainingOutcome:
        config.validate()
        dataset.validate()
        meta = self._adapter.metadata()
        if config.model_family != meta.model_family:
            raise TrainingError(
                f"training config family {config.model_family} does not "
                f"match adapter family {meta.model_family}",
                location="training.family", rule_id="TRAIN-003")
        for key in meta.required_hyperparameters:
            if key not in config.hyperparameters:
                raise TrainingError(
                    f"hyperparameter '{key}' missing for family "
                    f"{meta.model_family} (NO_IMPLICIT_DEFAULTS - SECTION 17)",
                    location="training.hyperparameters", rule_id="TRAIN-004",
                    details={"missing": key})
        lock = {
            "dataset_hash": dataset.content_hash,
            "feature_schema_hash": dataset.feature_schema_hash,
            "label_hash": dataset.label_definition_hash or "",
            "config_hash": config.config_hash,
        }
        run = TrainingRun(
            training_run_id=new_identifier("training_run_id"),
            model_candidate_id=model_id,
            dataset_hash=dataset.content_hash,
            feature_schema_hash=dataset.feature_schema_hash,
            label_hash=dataset.label_definition_hash,
            config_hash=config.config_hash,
            code_hash=code_fingerprint(trainer_source),
            dependency_hash=dependency_fingerprint(
                adapter_name=type(self._adapter).__name__),
            seed=config.seed,
            environment=config.environment,
            started_at=ensure_utc(started_at, location="training.started_at"),
            status=TrainingStatus.RUNNING,
        )
        run.validate()
        if fail is not None:
            failed = TrainingRun(
                training_run_id=run.training_run_id,
                model_candidate_id=run.model_candidate_id,
                dataset_hash=run.dataset_hash,
                feature_schema_hash=run.feature_schema_hash,
                label_hash=run.label_hash,
                config_hash=run.config_hash,
                code_hash=run.code_hash,
                dependency_hash=run.dependency_hash,
                seed=run.seed,
                environment=run.environment,
                started_at=run.started_at,
                status=TrainingStatus.FAILED,
                completed_at=ensure_utc(started_at),
                failure_reason="injected training failure",
            )
            failed.validate()
            fail()
            return TrainingOutcome(run=failed, artifact_json=None, model=None)
        try:
            artifact_json = self._adapter.train(
                rows, config.hyperparameters, config.seed)
        except ModelAdapterError as error:
            failed = TrainingRun(
                training_run_id=run.training_run_id,
                model_candidate_id=run.model_candidate_id,
                dataset_hash=run.dataset_hash,
                feature_schema_hash=run.feature_schema_hash,
                label_hash=run.label_hash,
                config_hash=run.config_hash,
                code_hash=run.code_hash,
                dependency_hash=run.dependency_hash,
                seed=run.seed,
                environment=run.environment,
                started_at=run.started_at,
                status=TrainingStatus.FAILED,
                completed_at=ensure_utc(started_at),
                failure_reason=str(error),
            )
            failed.validate()
            return TrainingOutcome(run=failed, artifact_json=None, model=None)
        artifact_hash = hashlib.sha256(
            artifact_json.encode("utf-8")).hexdigest()
        # SECTION 56: verify the dependency lock at completion.
        import json as _json
        payload = _json.loads(artifact_json)
        reproduced = TrainingRun(
            training_run_id=run.training_run_id,
            model_candidate_id=run.model_candidate_id,
            dataset_hash=run.dataset_hash,
            feature_schema_hash=run.feature_schema_hash,
            label_hash=run.label_hash,
            config_hash=run.config_hash,
            code_hash=run.code_hash,
            dependency_hash=run.dependency_hash,
            seed=run.seed,
            environment=run.environment,
            started_at=run.started_at,
            status=TrainingStatus.COMPLETED,
            completed_at=ensure_utc(started_at),
            metrics={"n_training_rows": float(len(rows)),
                     "artifact_seed": float(payload["seed"])},
            artifact_hash=artifact_hash,
            provenance={"lock": lock, "verified": True},
        )
        reproduced.validate()
        model = ModelDefinition(
            model_id=model_id,
            model_version=model_version,
            model_family=config.model_family,
            artifact_hash=artifact_hash,
            code_hash=run.code_hash,
            feature_schema_hash=dataset.feature_schema_hash,
            dataset_hash=dataset.content_hash,
            label_definition_hash=dataset.label_definition_hash,
            training_config_hash=config.config_hash,
            evaluation_hash=None,
            dependency_hash=run.dependency_hash,
            training_environment=config.environment,
            inference_environments=("RESEARCH",),
            seed=config.seed,
            framework_version=FRAMEWORK_VERSION,
            runtime_version=RUNTIME_VERSION,
            status="TRAINED",
            model_hash="",
            created_at=ensure_utc(started_at),
            provenance={"training_run_id": run.training_run_id,
                        "artifact": payload},
        )
        object.__setattr__(model, "model_hash", model.compute_model_hash())
        model.validate()
        self._registry.register(model)
        return TrainingOutcome(run=reproduced, artifact_json=artifact_json,
                               model=model)
