"""Intelligence contracts (owned by core.intelligence).

Advisory/proposal intelligence plane (Phase 7). AI observes, classifies,
estimates, predicts and proposes. It NEVER decides risk, NEVER creates
orders and NEVER executes (SECTION: AI is not the risk/execution/policy/
permission/portfolio authority).

Point-in-time semantics reuse the Phase 6 research dataset (visible_at);
there is exactly one PIT implementation in this system.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Tuple

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import new_identifier, validate_identifier
from architecture.contracts.time import ensure_utc
from architecture.contracts.versioning import SemVer
from core.research.contracts import RESEARCH_ENVIRONMENTS

CONTRACT_VERSION = "1.0.0"

#: Environments where inference may run (Phase 0 environments remain
#: authoritative; a model must be explicitly approved per environment).
INFERENCE_ENVIRONMENTS = frozenset(
    {"RESEARCH", "BACKTEST", "REPLAY", "SIMULATION", "PAPER", "DEMO", "LIVE"}
)


def canonical_hash(value: Any) -> str:
    """Deterministic content hash (same canonical form as the research plane)."""
    material = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _require_sha256(value: str, location: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        c not in "0123456789abcdef" for c in value
    ):
        raise ContractValidationError(
            f"{location} must be a sha-256 hex string", location=location)


def _require_research_environment(environment: str, location: str) -> None:
    parse_environment(environment, location=location)
    if environment not in RESEARCH_ENVIRONMENTS:
        raise ContractValidationError(
            f"{location} must be a research-plane environment "
            f"({sorted(RESEARCH_ENVIRONMENTS)}); intelligence data/training "
            "objects never execute in SIMULATION/PAPER/DEMO/LIVE",
            location=location, rule_id="ENV-006",
            details={"value": environment})


def _require_inference_environment(environment: str, location: str) -> None:
    parse_environment(environment, location=location)
    if environment not in INFERENCE_ENVIRONMENTS:
        raise ContractValidationError(
            f"{location} must be a known environment "
            f"({sorted(INFERENCE_ENVIRONMENTS)})",
            location=location, rule_id="ENV-007",
            details={"value": environment})


# --------------------------------------------------------------------- #
# Feature definitions                                                    #
# --------------------------------------------------------------------- #
class FeatureStatus(Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"


class MissingValuePolicy(Enum):
    """SECTION 7: UNKNOWN critical dependency blocks. A declared domain rule
    (e.g. "volume missing pre-2015 means no trades") must be versioned inside
    the feature definition itself - never implicit."""
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"
    DECLARED_DEFAULT = "DECLARED_DEFAULT"


class UnknownPolicy(Enum):
    PROPAGATE_UNKNOWN = "PROPAGATE_UNKNOWN"
    BLOCK = "BLOCK"
    DECLARED_DOMAIN_RULE = "DECLARED_DOMAIN_RULE"


#: Leakage taxonomy (SECTION 7). Every value must be detectable by the
#: feature engine and the bias audit; none may be silently tolerated.
LEAKAGE_TYPES = (
    "FUTURE_DATA",
    "LOOK_AHEAD",
    "INVALID_AVAILABLE_TIME",
    "FUTURE_NORMALIZATION",
    "FUTURE_AGGREGATION",
    "FUTURE_LABEL_CONTAMINATION",
    "CROSS_SPLIT_LEAKAGE",
    "TARGET_LEAKAGE",
)


@dataclass(frozen=True)
class FeatureDefinition:
    """Immutable, versioned feature definition (SECTION 5).

    A changed calculation is a NEW VERSION - historical definitions are
    never mutated."""
    feature_id: str
    feature_version: str
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    calculation_definition: str
    event_time_semantics: str
    available_time_semantics: str
    lookback_window: int
    dependencies: Tuple[str, ...]
    normalization_definition: Mapping[str, Any]
    missing_value_policy: MissingValuePolicy
    unknown_policy: UnknownPolicy
    timezone: str
    source_provenance: Mapping[str, Any]
    content_hash: str
    implementation_hash: str
    status: FeatureStatus
    feature_type: str = "MARKET_PRICE"
    session_definition: Mapping[str, Any] | None = None
    declared_default: Any = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("feature_definition_id", self.feature_id,
                            location="feature.feature_id")
        SemVer.parse(self.feature_version, location="feature.feature_version")
        for name in ("name", "description", "calculation_definition"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"feature.{name} must be a non-empty string",
                    location=f"feature.{name}")
        for name in ("input_schema", "output_schema", "normalization_definition"):
            if not isinstance(getattr(self, name), Mapping):
                raise ContractValidationError(
                    f"feature.{name} must be a mapping",
                    location=f"feature.{name}")
        # SECTION 6: availability semantics are mandatory; event-time-only
        # visibility is the definition of look-ahead.
        if self.available_time_semantics not in ("AVAILABLE_AT", "EVENT_PLUS_LATENCY"):
            raise ContractValidationError(
                "feature.available_time_semantics must declare how availability "
                "is derived (AVAILABLE_AT or EVENT_PLUS_LATENCY); "
                "event-time-only visibility is LOOK_AHEAD",
                location="feature.available_time_semantics", rule_id="AI-PIT")
        if not isinstance(self.lookback_window, int) or self.lookback_window < 1:
            raise ContractValidationError(
                "feature.lookback_window must be a positive integer",
                location="feature.lookback_window")
        if not isinstance(self.timezone, str) or not self.timezone:
            raise ContractValidationError(
                "feature.timezone must be explicit",
                location="feature.timezone")
        if not isinstance(self.missing_value_policy, MissingValuePolicy):
            raise ContractValidationError(
                "feature.missing_value_policy must be a MissingValuePolicy",
                location="feature.missing_value_policy", rule_id="SCHEMA-ENUM")
        if not isinstance(self.unknown_policy, UnknownPolicy):
            raise ContractValidationError(
                "feature.unknown_policy must be an UnknownPolicy",
                location="feature.unknown_policy", rule_id="SCHEMA-ENUM")
        if not isinstance(self.status, FeatureStatus):
            raise ContractValidationError(
                "feature.status must be a FeatureStatus",
                location="feature.status", rule_id="SCHEMA-ENUM")
        if self.missing_value_policy is MissingValuePolicy.DECLARED_DEFAULT \
                and self.declared_default is None:
            raise ContractValidationError(
                "DECLARED_DEFAULT policy requires an explicit declared_default "
                "(versioned domain rule, never implicit)",
                location="feature.declared_default", rule_id="AI-IMPLICIT")
        for dep in self.dependencies:
            if not isinstance(dep, str) or not dep:
                raise ContractValidationError(
                    "feature.dependencies entries must be non-empty strings",
                    location="feature.dependencies")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "feature.content_hash mismatch (corrupted feature rejected)",
                location="feature.content_hash", rule_id="FEATURE-001",
                details={"expected": expected})

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "name": self.name, "description": self.description,
            "feature_type": self.feature_type,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "calculation_definition": self.calculation_definition,
            "event_time_semantics": self.event_time_semantics,
            "available_time_semantics": self.available_time_semantics,
            "lookback_window": self.lookback_window,
            "dependencies": list(self.dependencies),
            "normalization_definition": dict(self.normalization_definition),
            "missing_value_policy": self.missing_value_policy.value,
            "unknown_policy": self.unknown_policy.value,
            "timezone": self.timezone,
            "session_definition": dict(self.session_definition)
            if self.session_definition else None,
            "declared_default": self.declared_default,
        })


@dataclass(frozen=True)
class FeatureSnapshot:
    """Point-in-time feature vector: ONLY data available at as_of moment."""
    snapshot_id: str
    feature_keys: Tuple[str, ...]
    values: Mapping[str, Any]
    as_of: datetime
    available_at: datetime
    environment: str
    content_hash: str
    provenance: Mapping[str, Any]
    missing_features: Tuple[str, ...] = ()
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("feature_snapshot_id", self.snapshot_id,
                            location="snapshot.snapshot_id")
        if not self.feature_keys:
            raise ContractValidationError(
                "snapshot.feature_keys must be non-empty",
                location="snapshot.feature_keys")
        ensure_utc(self.as_of, location="snapshot.as_of")
        ensure_utc(self.available_at, location="snapshot.available_at")
        if self.available_at > ensure_utc(self.as_of):
            raise ContractValidationError(
                "snapshot.available_at cannot exceed as_of (a snapshot may "
                "only contain data already available at the as-of moment)",
                location="snapshot.available_at", rule_id="AI-PIT")
        _require_inference_environment(self.environment, "snapshot.environment")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "snapshot.content_hash mismatch",
                location="snapshot.content_hash", rule_id="FEATURE-002",
                details={"expected": expected})

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "feature_keys": list(self.feature_keys),
            "values": dict(self.values),
            "as_of": ensure_utc(self.as_of).isoformat(),
            "available_at": ensure_utc(self.available_at).isoformat(),
            "missing": list(self.missing_features),
        })

    @property
    def feature_hash(self) -> str:
        return self.content_hash


# --------------------------------------------------------------------- #
# Labels                                                                 #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class LabelDefinition:
    """Versioned supervised label (SECTION 11).

    future_return(t, t+N) is a LABEL, never a feature: label availability is
    event_time + horizon, and feature snapshots may never reference labels."""
    label_definition_id: str
    label_version: str
    target_definition: str
    horizon_bars: int
    availability_rule: str
    calculation: str
    leakage_rules: Tuple[str, ...]
    content_hash: str
    threshold: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("label_definition_id", self.label_definition_id,
                            location="label.label_definition_id")
        SemVer.parse(self.label_version, location="label.label_version")
        if not isinstance(self.target_definition, str) or not self.target_definition:
            raise ContractValidationError(
                "label.target_definition must be a non-empty string",
                location="label.target_definition")
        if not isinstance(self.horizon_bars, int) or self.horizon_bars < 1:
            raise ContractValidationError(
                "label.horizon_bars must be a positive integer",
                location="label.horizon_bars")
        if self.availability_rule != "AVAILABLE_AT_EVENT_PLUS_HORIZON":
            raise ContractValidationError(
                "label.availability_rule must be "
                "AVAILABLE_AT_EVENT_PLUS_HORIZON (labels only exist after "
                "their horizon passes - anything earlier is TARGET_LEAKAGE)",
                location="label.availability_rule", rule_id="LABEL-001")
        if not self.leakage_rules:
            raise ContractValidationError(
                "label.leakage_rules must be declared",
                location="label.leakage_rules")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "label.content_hash mismatch",
                location="label.content_hash", rule_id="LABEL-002",
                details={"expected": expected})

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "target_definition": self.target_definition,
            "horizon_bars": self.horizon_bars,
            "availability_rule": self.availability_rule,
            "calculation": self.calculation,
            "leakage_rules": list(self.leakage_rules),
            "threshold": self.threshold,
        })


# --------------------------------------------------------------------- #
# Intelligence dataset + splits                                          #
# --------------------------------------------------------------------- #
class IntelligenceDatasetType(Enum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    OOS = "OOS"
    INFERENCE = "INFERENCE"
    SHADOW = "SHADOW"
    REPLAY = "REPLAY"


@dataclass(frozen=True)
class SplitDefinition:
    """Temporal split contract: TRAIN < VALIDATION < OOS (SECTION 10)."""
    split_id: str
    semantics: str
    boundaries: Mapping[str, str]
    overlap_declared: bool
    overlap_justification: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.split_id, str) or not self.split_id:
            raise ContractValidationError(
                "split.split_id must be a non-empty string",
                location="split.split_id")
        if self.semantics != "TEMPORAL":
            raise ContractValidationError(
                "split.semantics must be TEMPORAL (declared time semantics)",
                location="split.semantics", rule_id="SPLIT-001")
        for key in ("train_end", "validation_end", "oos_end"):
            if key not in self.boundaries:
                raise ContractValidationError(
                    f"split.boundaries must contain {key}",
                    location="split.boundaries", rule_id="SPLIT-002")
        ordered = [datetime.fromisoformat(self.boundaries[k])
                   for k in ("train_end", "validation_end", "oos_end")]
        for moment in ordered:
            ensure_utc(moment, location="split.boundaries")
        if not (ordered[0] < ordered[1] < ordered[2]):
            raise ContractValidationError(
                "split boundaries must be strictly increasing "
                "(TRAIN < VALIDATION < OOS); temporal inversion is rejected",
                location="split.boundaries", rule_id="SPLIT-002")
        if self.overlap_declared and not self.overlap_justification:
            raise ContractValidationError(
                "declared overlap requires an explicit justification",
                location="split.overlap_justification", rule_id="SPLIT-003")


@dataclass(frozen=True)
class IntelligenceDataset:
    """Immutable intelligence dataset (SECTION 9).

    Derived from a Phase 6 point-in-time ResearchDataset via deterministic
    feature computation - never mutated; changed data = new version."""
    intelligence_dataset_id: str
    dataset_version: str
    dataset_type: IntelligenceDatasetType
    feature_schema_hash: str
    source_dataset_hash: str
    observation_count: int
    time_range: Mapping[str, str]
    symbol_scope: Tuple[str, ...]
    environment: str
    split_definition: Mapping[str, Any]
    normalization_version: str
    provenance: Mapping[str, Any]
    content_hash: str
    created_at: datetime
    label_definition_hash: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("intelligence_dataset_id", self.intelligence_dataset_id,
                            location="idataset.intelligence_dataset_id")
        SemVer.parse(self.dataset_version, location="idataset.dataset_version")
        if not isinstance(self.dataset_type, IntelligenceDatasetType):
            raise ContractValidationError(
                "idataset.dataset_type must be an IntelligenceDatasetType",
                location="idataset.dataset_type", rule_id="SCHEMA-ENUM")
        _require_sha256(self.feature_schema_hash, "idataset.feature_schema_hash")
        _require_sha256(self.source_dataset_hash, "idataset.source_dataset_hash")
        if not isinstance(self.observation_count, int) or self.observation_count < 0:
            raise ContractValidationError(
                "idataset.observation_count must be a non-negative integer",
                location="idataset.observation_count")
        if "start" not in self.time_range or "end" not in self.time_range:
            raise ContractValidationError(
                "idataset.time_range must contain start and end",
                location="idataset.time_range")
        if not self.symbol_scope:
            raise ContractValidationError(
                "idataset.symbol_scope must be non-empty",
                location="idataset.symbol_scope")
        if not isinstance(self.split_definition, Mapping) or not self.split_definition:
            raise ContractValidationError(
                "idataset.split_definition must be a mapping",
                location="idataset.split_definition")
        if not isinstance(self.normalization_version, str) or not self.normalization_version:
            raise ContractValidationError(
                "idataset.normalization_version must be explicit "
                "(normalization parameters are versioned artifacts)",
                location="idataset.normalization_version")
        if not self.provenance:
            raise ContractValidationError(
                "idataset.provenance must be provided (lineage to the source "
                "dataset and feature versions)",
                location="idataset.provenance")
        _require_research_environment(self.environment, "idataset.environment")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "idataset.content_hash mismatch",
                location="idataset.content_hash", rule_id="DATASETX-001",
                details={"expected": expected})
        ensure_utc(self.created_at, location="idataset.created_at")
        if self.label_definition_hash is not None:
            _require_sha256(self.label_definition_hash,
                            "idataset.label_definition_hash")

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "dataset_type": self.dataset_type.value,
            "feature_schema_hash": self.feature_schema_hash,
            "source_dataset_hash": self.source_dataset_hash,
            "observation_count": self.observation_count,
            "time_range": dict(self.time_range),
            "symbol_scope": list(self.symbol_scope),
            "split_definition": dict(self.split_definition),
            "normalization_version": self.normalization_version,
            "label_definition_hash": self.label_definition_hash,
            "rows": self.provenance.get("row_digest", ""),
        })


# --------------------------------------------------------------------- #
# Training                                                               #
# --------------------------------------------------------------------- #
class TrainingStatus(Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INVALID = "INVALID"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class TrainingConfig:
    """Fully explicit training configuration (SECTION 17).

    No implicit defaults: every hyperparameter, threshold, resource limit and
    the seed must be recorded. Missing critical parameter = UNKNOWN = reject."""
    config_id: str
    config_version: str
    model_family: str
    hyperparameters: Mapping[str, Any]
    seed: int
    feature_keys: Tuple[str, ...]
    thresholds: Mapping[str, Any]
    resource_limits: Mapping[str, Any]
    config_hash: str
    environment: str
    label_key: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("config_id", self.config_id, location="tconfig.config_id")
        SemVer.parse(self.config_version, location="tconfig.config_version")
        if not isinstance(self.model_family, str) or not self.model_family:
            raise ContractValidationError(
                "tconfig.model_family must be a non-empty string",
                location="tconfig.model_family")
        if not self.hyperparameters:
            raise ContractValidationError(
                "tconfig.hyperparameters must be explicit (no hidden defaults)",
                location="tconfig.hyperparameters", rule_id="AI-IMPLICIT")
        if not isinstance(self.seed, int):
            raise ContractValidationError(
                "tconfig.seed must be an explicit integer",
                location="tconfig.seed", rule_id="AI-IMPLICIT")
        if not self.feature_keys:
            raise ContractValidationError(
                "tconfig.feature_keys must be non-empty",
                location="tconfig.feature_keys")
        if not self.thresholds:
            raise ContractValidationError(
                "tconfig.thresholds must be explicit and versioned "
                "(decision thresholds are configuration)",
                location="tconfig.thresholds", rule_id="AI-IMPLICIT")
        for key in ("max_training_seconds", "max_memory_mb"):
            if key not in self.resource_limits:
                raise ContractValidationError(
                    f"tconfig.resource_limits must declare {key} "
                    "(resource exhaustion must fail safely)",
                    location="tconfig.resource_limits", rule_id="AI-IMPLICIT")
        expected = self.compute_config_hash()
        if self.config_hash != expected:
            raise ContractValidationError(
                "tconfig.config_hash mismatch",
                location="tconfig.config_hash", rule_id="TCONFIG-001",
                details={"expected": expected})
        _require_research_environment(self.environment, "tconfig.environment")

    def compute_config_hash(self) -> str:
        return canonical_hash({
            "model_family": self.model_family,
            "hyperparameters": dict(self.hyperparameters),
            "seed": self.seed,
            "feature_keys": list(self.feature_keys),
            "thresholds": dict(self.thresholds),
            "resource_limits": dict(self.resource_limits),
            "label_key": self.label_key,
        })


@dataclass(frozen=True)
class TrainingRun:
    """Immutable training run (SECTION 16/18).

    FAILED/INVALID runs can never become validated models; partial artifacts
    are quarantined (not deployable)."""
    training_run_id: str
    model_candidate_id: str
    dataset_hash: str
    feature_schema_hash: str
    label_hash: str | None
    config_hash: str
    code_hash: str
    dependency_hash: str
    seed: int
    environment: str
    started_at: datetime
    status: TrainingStatus
    completed_at: datetime | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    artifact_hash: str | None = None
    failure_reason: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("training_run_id", self.training_run_id,
                            location="trun.training_run_id")
        validate_identifier("model_id", self.model_candidate_id,
                            location="trun.model_candidate_id")
        for name in ("dataset_hash", "feature_schema_hash", "config_hash",
                     "code_hash", "dependency_hash"):
            _require_sha256(getattr(self, name), f"trun.{name}")
        if self.label_hash is not None:
            _require_sha256(self.label_hash, "trun.label_hash")
        if not isinstance(self.seed, int):
            raise ContractValidationError(
                "trun.seed must be an integer (training reproducibility)",
                location="trun.seed", rule_id="AI-IMPLICIT")
        _require_research_environment(self.environment, "trun.environment")
        ensure_utc(self.started_at, location="trun.started_at")
        if not isinstance(self.status, TrainingStatus):
            raise ContractValidationError(
                "trun.status must be a TrainingStatus",
                location="trun.status", rule_id="SCHEMA-ENUM")
        if self.completed_at is not None:
            ensure_utc(self.completed_at, location="trun.completed_at")
            if ensure_utc(self.completed_at) < ensure_utc(self.started_at):
                raise ContractValidationError(
                    "trun.completed_at cannot precede started_at",
                    location="trun.completed_at")
        if self.status is TrainingStatus.COMPLETED:
            if self.artifact_hash is None:
                raise ContractValidationError(
                    "a COMPLETED run must carry its artifact hash",
                    location="trun.artifact_hash", rule_id="TRAIN-001")
            if self.completed_at is None:
                raise ContractValidationError(
                    "a COMPLETED run must carry completed_at",
                    location="trun.completed_at", rule_id="TRAIN-001")
        if self.status in (TrainingStatus.FAILED, TrainingStatus.INVALID) \
                and not self.failure_reason:
            raise ContractValidationError(
                "FAILED/INVALID runs must record a failure reason",
                location="trun.failure_reason", rule_id="TRAIN-002")

    def dependency_lock(self) -> Mapping[str, str]:
        """SECTION 56: the hashes locked at training start."""
        return {
            "dataset_hash": self.dataset_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "label_hash": self.label_hash or "",
            "config_hash": self.config_hash,
            "code_hash": self.code_hash,
            "dependency_hash": self.dependency_hash,
        }


# --------------------------------------------------------------------- #
# Model registry                                                         #
# --------------------------------------------------------------------- #
#: Model families (SECTION 14). Core depends on the adapter contract, not
#: on any vendor/framework; the built-in families are deterministic
#: statistical estimators implemented in the standard library.
MODEL_FAMILIES = (
    "BASELINE",
    "LINEAR",
    "CLASSIFIER",
    "ANOMALY",
    "REGIME",
    "ENSEMBLE",
    "TIME_SERIES",
    "CUSTOM",
)


@dataclass(frozen=True)
class ModelDefinition:
    """Immutable, content-identified model (SECTION 12/13).

    Registry ids are handles only: model identity is model_hash over the
    immutable inputs (artifact, config, dataset, features, label, code,
    dependencies, seed, family)."""
    model_id: str
    model_version: str
    model_family: str
    artifact_hash: str
    code_hash: str
    feature_schema_hash: str
    dataset_hash: str
    label_definition_hash: str | None
    training_config_hash: str
    evaluation_hash: str | None
    dependency_hash: str
    training_environment: str
    inference_environments: Tuple[str, ...]
    seed: int
    framework_version: str
    runtime_version: str
    status: str
    model_hash: str
    created_at: datetime
    provenance: Mapping[str, Any]
    valid_until: datetime | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("model_id", self.model_id, location="model.model_id")
        SemVer.parse(self.model_version, location="model.model_version")
        if self.model_family not in MODEL_FAMILIES:
            raise ContractValidationError(
                f"model.model_family must be one of {MODEL_FAMILIES}",
                location="model.model_family", rule_id="SCHEMA-ENUM")
        for name in ("artifact_hash", "code_hash", "feature_schema_hash",
                     "dataset_hash", "training_config_hash", "dependency_hash",
                     "model_hash"):
            _require_sha256(getattr(self, name), f"model.{name}")
        if self.label_definition_hash is not None:
            _require_sha256(self.label_definition_hash,
                            "model.label_definition_hash")
        if self.evaluation_hash is not None:
            _require_sha256(self.evaluation_hash, "model.evaluation_hash")
        _require_research_environment(self.training_environment,
                                      "model.training_environment")
        if not self.inference_environments:
            raise ContractValidationError(
                "model.inference_environments must be explicit (silently "
                "allowing every environment is forbidden)",
                location="model.inference_environments", rule_id="AI-ENV")
        for env in self.inference_environments:
            _require_inference_environment(env, "model.inference_environments")
        if not isinstance(self.framework_version, str) or not self.framework_version:
            raise ContractValidationError(
                "model.framework_version must be explicit",
                location="model.framework_version")
        if not isinstance(self.runtime_version, str) or not self.runtime_version:
            raise ContractValidationError(
                "model.runtime_version must be explicit",
                location="model.runtime_version")
        expected = self.compute_model_hash()
        if self.model_hash != expected:
            raise ContractValidationError(
                "model.model_hash mismatch (content identity broken)",
                location="model.model_hash", rule_id="MODELX-001",
                details={"expected": expected})
        ensure_utc(self.created_at, location="model.created_at")
        if self.valid_until is not None:
            ensure_utc(self.valid_until, location="model.valid_until")

    def compute_model_hash(self) -> str:
        """Content identity (SECTION 13): hashes of every immutable input.
        Registry ids and timestamps are excluded by design."""
        return canonical_hash({
            "model_version": self.model_version,
            "model_family": self.model_family,
            "artifact_hash": self.artifact_hash,
            "code_hash": self.code_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "dataset_hash": self.dataset_hash,
            "label_definition_hash": self.label_definition_hash,
            "training_config_hash": self.training_config_hash,
            "evaluation_hash": self.evaluation_hash,
            "dependency_hash": self.dependency_hash,
            "inference_environments": list(self.inference_environments),
            "seed": self.seed,
            "framework_version": self.framework_version,
            "runtime_version": self.runtime_version,
            "status": self.status,
        })


# --------------------------------------------------------------------- #
# Evaluation / validation                                                #
# --------------------------------------------------------------------- #
class TaskType(Enum):
    CLASSIFICATION = "CLASSIFICATION"
    REGRESSION = "REGRESSION"
    ANOMALY = "ANOMALY"
    TIME_SERIES = "TIME_SERIES"


class UncertaintyStatus(Enum):
    CONFIDENT = "CONFIDENT"
    UNCERTAIN = "UNCERTAIN"
    UNKNOWN = "UNKNOWN"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True)
class ModelEvaluation:
    """Deterministic, versioned evaluation evidence (SECTION 19/20).

    No single magic score decides production eligibility."""
    model_evaluation_id: str
    model_hash: str
    dataset_hash: str
    task_type: TaskType
    metrics: Mapping[str, Any]
    metrics_version: str
    calibration: Mapping[str, Any] | None
    confidence_distribution: Mapping[str, Any]
    uncertainty_summary: Mapping[str, Any]
    stability: Mapping[str, Any]
    coverage: Mapping[str, Any]
    created_at: datetime
    environment: str
    evidence_hash: str
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("model_evaluation_id", self.model_evaluation_id,
                            location="meval.model_evaluation_id")
        _require_sha256(self.model_hash, "meval.model_hash")
        _require_sha256(self.dataset_hash, "meval.dataset_hash")
        if not isinstance(self.task_type, TaskType):
            raise ContractValidationError(
                "meval.task_type must be a TaskType",
                location="meval.task_type", rule_id="SCHEMA-ENUM")
        if not self.metrics:
            raise ContractValidationError(
                "meval.metrics must be non-empty (never fabricate a metric "
                "when labels are insufficient - record INSUFFICIENT_LABELS)",
                location="meval.metrics", rule_id="MEVAL-001")
        SemVer.parse(self.metrics_version, location="meval.metrics_version")
        for name in ("confidence_distribution", "uncertainty_summary",
                     "stability", "coverage"):
            if not isinstance(getattr(self, name), Mapping):
                raise ContractValidationError(
                    f"meval.{name} must be a mapping",
                    location=f"meval.{name}")
        _require_research_environment(self.environment, "meval.environment")
        _require_sha256(self.evidence_hash, "meval.evidence_hash")
        ensure_utc(self.created_at, location="meval.created_at")

    def compute_evidence_hash(self) -> str:
        return canonical_hash({
            "model_hash": self.model_hash,
            "dataset_hash": self.dataset_hash,
            "task_type": self.task_type.value,
            "metrics": dict(self.metrics),
            "metrics_version": self.metrics_version,
            "calibration": dict(self.calibration) if self.calibration else None,
            "stability": dict(self.stability),
        })


class EvidenceStatus(Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


#: Evidence checklist for the model validation gate (SECTION 40). Critical
#: items flip the gate to UNKNOWN -> BLOCK when absent.
CRITICAL_EVIDENCE = (
    "dataset_integrity",
    "leakage_audit",
    "feature_integrity",
    "model_artifact_integrity",
    "evaluation",
    "oos",
    "robustness",
    "replay",
    "provenance",
)


@dataclass(frozen=True)
class ModelValidation:
    model_validation_id: str
    model_hash: str
    evidence: Mapping[str, str]
    overall: EvidenceStatus
    auditor: str
    created_at: datetime
    environment: str
    notes: str = ""
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("model_validation_id", self.model_validation_id,
                            location="mval.model_validation_id")
        _require_sha256(self.model_hash, "mval.model_hash")
        if not self.evidence:
            raise ContractValidationError(
                "mval.evidence must record every checklist item",
                location="mval.evidence", rule_id="MVAL-001")
        for item in CRITICAL_EVIDENCE:
            if item not in self.evidence:
                raise ContractValidationError(
                    f"mval.evidence missing critical item '{item}'",
                    location="mval.evidence", rule_id="MVAL-001",
                    details={"item": item})
        for item, status in self.evidence.items():
            if status not in ("PRESENT", "MISSING", "UNKNOWN"):
                raise ContractValidationError(
                    f"mval.evidence['{item}'] must be PRESENT/MISSING/UNKNOWN",
                    location=f"mval.evidence.{item}", rule_id="SCHEMA-ENUM")
        if not isinstance(self.overall, EvidenceStatus):
            raise ContractValidationError(
                "mval.overall must be an EvidenceStatus",
                location="mval.overall", rule_id="SCHEMA-ENUM")
        # SECTION 40: a missing/UNKNOWN critical item forces overall UNKNOWN.
        if self.overall is EvidenceStatus.PRESENT:
            for item in CRITICAL_EVIDENCE:
                if self.evidence.get(item) != "PRESENT":
                    raise ContractValidationError(
                        f"overall PRESENT is forbidden while critical "
                        f"evidence '{item}' is not PRESENT",
                        location="mval.overall", rule_id="MVAL-002",
                        details={"item": item})
        if not isinstance(self.auditor, str) or not self.auditor:
            raise ContractValidationError(
                "mval.auditor must be recorded",
                location="mval.auditor")
        _require_research_environment(self.environment, "mval.environment")
        ensure_utc(self.created_at, location="mval.created_at")


# --------------------------------------------------------------------- #
# Inference                                                              #
# --------------------------------------------------------------------- #
class InferenceStatus(Enum):
    VALID = "VALID"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class InferenceRequest:
    inference_id: str
    model_id: str
    model_version: str
    inference_time: datetime
    environment: str
    feature_snapshot_hash: str
    request_hash: str
    provenance: Mapping[str, Any]
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("inference_id", self.inference_id,
                            location="ireq.inference_id")
        validate_identifier("model_id", self.model_id, location="ireq.model_id")
        SemVer.parse(self.model_version, location="ireq.model_version")
        ensure_utc(self.inference_time, location="ireq.inference_time")
        _require_inference_environment(self.environment, "ireq.environment")
        _require_sha256(self.feature_snapshot_hash,
                        "ireq.feature_snapshot_hash")
        expected = self.compute_request_hash()
        if self.request_hash != expected:
            raise ContractValidationError(
                "ireq.request_hash mismatch",
                location="ireq.request_hash", rule_id="INFERENCE-001",
                details={"expected": expected})

    def compute_request_hash(self) -> str:
        """SECTION 26: idempotency identity - same model version, snapshot,
        time, environment and configuration = same semantic request."""
        return canonical_hash({
            "model_id": self.model_id,
            "model_version": self.model_version,
            "inference_time": ensure_utc(self.inference_time).isoformat(),
            "environment": self.environment,
            "feature_snapshot_hash": self.feature_snapshot_hash,
            "provenance": dict(self.provenance),
        })


@dataclass(frozen=True)
class InferenceResult:
    inference_id: str
    model_hash: str
    feature_hash: str
    output: Mapping[str, Any]
    status: InferenceStatus
    inference_time: datetime
    environment: str
    produced_at: datetime
    valid_from: datetime
    valid_until: datetime
    provenance: Mapping[str, Any]
    content_hash: str
    confidence: float | None = None
    probability: float | None = None
    score: float | None = None
    uncertainty: str = "UNKNOWN"
    uncertainty_measures: Mapping[str, Any] = field(default_factory=dict)
    explanation_ref: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("inference_id", self.inference_id,
                            location="ires.inference_id")
        _require_sha256(self.model_hash, "ires.model_hash")
        _require_sha256(self.feature_hash, "ires.feature_hash")
        if not isinstance(self.output, Mapping):
            raise ContractValidationError(
                "ires.output must be a mapping",
                location="ires.output")
        if not isinstance(self.status, InferenceStatus):
            raise ContractValidationError(
                "ires.status must be an InferenceStatus",
                location="ires.status", rule_id="SCHEMA-ENUM")
        if self.uncertainty not in ("CONFIDENT", "UNCERTAIN", "UNKNOWN",
                                    "NOT_AVAILABLE"):
            raise ContractValidationError(
                "ires.uncertainty must be CONFIDENT/UNCERTAIN/UNKNOWN/"
                "NOT_AVAILABLE",
                location="ires.uncertainty", rule_id="SCHEMA-ENUM")
        for name in ("confidence", "probability", "score"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                    raise ContractValidationError(
                        f"ires.{name} must be within [0, 1] when present "
                        "(probability/confidence/score semantics are "
                        "declared, never interchanged)",
                        location=f"ires.{name}", rule_id="AI-SEMANTICS")
        ensure_utc(self.inference_time, location="ires.inference_time")
        _require_inference_environment(self.environment, "ires.environment")
        for name in ("produced_at", "valid_from", "valid_until"):
            ensure_utc(getattr(self, name), location=f"ires.{name}")
        if ensure_utc(self.valid_until) < ensure_utc(self.valid_from):
            raise ContractValidationError(
                "ires.valid_until cannot precede valid_from",
                location="ires.valid_until", rule_id="INFERENCE-002")
        if not self.provenance:
            raise ContractValidationError(
                "ires.provenance must be complete (model/dataset/feature/"
                "code hashes - SECTION 54 lineage)",
                location="ires.provenance", rule_id="AI-PROVENANCE")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "ires.content_hash mismatch",
                location="ires.content_hash", rule_id="INFERENCE-003",
                details={"expected": expected})

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "inference_id_semantic": self.provenance.get("request_hash", ""),
            "model_hash": self.model_hash,
            "feature_hash": self.feature_hash,
            "output": dict(self.output),
            "status": self.status.value,
            "confidence": self.confidence,
            "probability": self.probability,
            "score": self.score,
            "uncertainty": self.uncertainty,
            # provenance is semantic content: a forged lineage changes the
            # content identity (tamper-evident, SECTION 54/55)
            "provenance": dict(self.provenance),
        })

    @property
    def expired(self) -> bool:
        from architecture.contracts.time import utc_now
        return ensure_utc(self.valid_until) < utc_now()
