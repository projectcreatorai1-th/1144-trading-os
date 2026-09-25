"""AI output contracts (owned by core.intelligence).

Semantic hierarchy (SECTION 4): Observation != Prediction != Recommendation
!= Proposal != StrategyIntent != Order. AI outputs are advisory evidence
only - they can never masquerade as RiskDecision, Order or ExecutionRequest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Tuple

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import (
    new_identifier,
    validate_identifier,
)
from architecture.contracts.time import ensure_utc

from core.intelligence.contracts import (
    CONTRACT_VERSION,
    INFERENCE_ENVIRONMENTS,
    _require_inference_environment,
    _require_research_environment,
    _require_sha256,
    canonical_hash,
)


class OutputType(Enum):
    AI_OBSERVATION = "AI_OBSERVATION"
    AI_PREDICTION = "AI_PREDICTION"
    AI_RECOMMENDATION = "AI_RECOMMENDATION"
    AI_PROPOSAL = "AI_PROPOSAL"
    AI_SIGNAL = "AI_SIGNAL"


class SignalKind(Enum):
    DIRECTIONAL_BIAS = "DIRECTIONAL_BIAS"
    REGIME = "REGIME"
    VOLATILITY_STATE = "VOLATILITY_STATE"
    ANOMALY = "ANOMALY"
    PROBABILITY = "PROBABILITY"
    EVENT_IMPACT = "EVENT_IMPACT"
    SENTIMENT = "SENTIMENT"
    LIQUIDITY_STATE = "LIQUIDITY_STATE"


class ProposedDirection(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


def _validate_validity_window(valid_from: datetime, valid_until: datetime) -> None:
    if ensure_utc(valid_until) < ensure_utc(valid_from):
        raise ContractValidationError(
            "validity window is inverted (valid_until < valid_from)",
            location="output.valid_until", rule_id="AI-FRESHNESS")


def _validate_confidence(confidence: Any, location: str) -> None:
    if confidence is not None and (
        not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0
    ):
        raise ContractValidationError(
            f"{location} must be within [0, 1] when present",
            location=location, rule_id="AI-SEMANTICS")


# --------------------------------------------------------------------- #
# AIObservation - what the model observed (no prediction semantics)      #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class AIObservation:
    output_id: str
    model_ref: str
    model_hash: str
    feature_snapshot_ref: str
    feature_hash: str
    output: Mapping[str, Any]
    event_time: datetime
    inference_time: datetime
    environment: str
    provenance: Mapping[str, Any]
    content_hash: str
    produced_at: datetime
    valid_from: datetime
    valid_until: datetime
    confidence: float | None = None
    uncertainty: str = "UNKNOWN"
    explanation_ref: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("ai_output_id", self.output_id,
                            location="aobs.output_id")
        _require_sha256(self.model_hash, "aobs.model_hash")
        _require_sha256(self.feature_hash, "aobs.feature_hash")
        if not self.output:
            raise ContractValidationError(
                "aobs.output must be non-empty",
                location="aobs.output")
        if not self.provenance:
            raise ContractValidationError(
                "aobs.provenance must be complete",
                location="aobs.provenance", rule_id="AI-PROVENANCE")
        _validate_confidence(self.confidence, "aobs.confidence")
        _validate_validity_window(self.valid_from, self.valid_until)
        _require_inference_environment(self.environment, "aobs.environment")
        for name in ("event_time", "inference_time", "produced_at"):
            ensure_utc(getattr(self, name), location=f"aobs.{name}")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "aobs.content_hash mismatch",
                location="aobs.content_hash", rule_id="AI-OUTPUT-001",
                details={"expected": expected})

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "output_type": "AI_OBSERVATION",
            "model_hash": self.model_hash,
            "feature_hash": self.feature_hash,
            "output": dict(self.output),
            "inference_time": ensure_utc(self.inference_time).isoformat(),
            "environment": self.environment,
        })


# --------------------------------------------------------------------- #
# AIPrediction - a labelled claim about the future                       #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class AIPrediction:
    output_id: str
    model_ref: str
    model_hash: str
    feature_snapshot_ref: str
    feature_hash: str
    label_definition_ref: str
    horizon_bars: int
    value: Any
    output: Mapping[str, Any]
    event_time: datetime
    inference_time: datetime
    environment: str
    provenance: Mapping[str, Any]
    content_hash: str
    produced_at: datetime
    valid_from: datetime
    valid_until: datetime
    probability: float | None = None
    confidence: float | None = None
    uncertainty: str = "UNKNOWN"
    explanation_ref: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("ai_output_id", self.output_id,
                            location="apred.output_id")
        _require_sha256(self.model_hash, "apred.model_hash")
        _require_sha256(self.feature_hash, "apred.feature_hash")
        if not isinstance(self.horizon_bars, int) or self.horizon_bars < 1:
            raise ContractValidationError(
                "apred.horizon_bars must be a positive integer (a prediction "
                "without a horizon is not a prediction)",
                location="apred.horizon_bars", rule_id="AI-SEMANTICS")
        if not self.provenance:
            raise ContractValidationError(
                "apred.provenance must be complete",
                location="apred.provenance", rule_id="AI-PROVENANCE")
        _validate_confidence(self.probability, "apred.probability")
        _validate_confidence(self.confidence, "apred.confidence")
        _validate_validity_window(self.valid_from, self.valid_until)
        _require_inference_environment(self.environment, "apred.environment")
        for name in ("event_time", "inference_time", "produced_at"):
            ensure_utc(getattr(self, name), location=f"apred.{name}")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "apred.content_hash mismatch",
                location="apred.content_hash", rule_id="AI-OUTPUT-001",
                details={"expected": expected})

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "output_type": "AI_PREDICTION",
            "model_hash": self.model_hash,
            "feature_hash": self.feature_hash,
            "value": str(self.value),
            "horizon_bars": self.horizon_bars,
            "probability": self.probability,
            "inference_time": ensure_utc(self.inference_time).isoformat(),
            "environment": self.environment,
        })


# --------------------------------------------------------------------- #
# AIRecommendation - advisory action suggestion                           #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class AIRecommendation:
    output_id: str
    model_ref: str
    model_hash: str
    feature_snapshot_ref: str
    feature_hash: str
    recommended_action: str
    rationale: str
    output: Mapping[str, Any]
    event_time: datetime
    inference_time: datetime
    environment: str
    provenance: Mapping[str, Any]
    content_hash: str
    produced_at: datetime
    valid_from: datetime
    valid_until: datetime
    evidence: Mapping[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    uncertainty: str = "UNKNOWN"
    explanation_ref: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("ai_output_id", self.output_id,
                            location="arec.output_id")
        _require_sha256(self.model_hash, "arec.model_hash")
        _require_sha256(self.feature_hash, "arec.feature_hash")
        # SECTION 107: advisory verbs only - an order/risk verb is a hard fail.
        forbidden = ("BUY", "SELL", "SUBMIT_ORDER", "CLOSE_POSITION",
                     "OVERRIDE_RISK", "ENABLE_LIVE")
        if self.recommended_action.upper() in forbidden:
            raise ContractValidationError(
                f"recommended_action '{self.recommended_action}' is an "
                "execution/risk verb; AI recommendations are advisory only",
                location="arec.recommended_action", rule_id="AI-AUTHORITY")
        if not isinstance(self.rationale, str) or not self.rationale:
            raise ContractValidationError(
                "arec.rationale must be a non-empty string",
                location="arec.rationale")
        if not self.provenance:
            raise ContractValidationError(
                "arec.provenance must be complete",
                location="arec.provenance", rule_id="AI-PROVENANCE")
        _validate_confidence(self.confidence, "arec.confidence")
        _validate_validity_window(self.valid_from, self.valid_until)
        _require_inference_environment(self.environment, "arec.environment")
        for name in ("event_time", "inference_time", "produced_at"):
            ensure_utc(getattr(self, name), location=f"arec.{name}")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "arec.content_hash mismatch",
                location="arec.content_hash", rule_id="AI-OUTPUT-001",
                details={"expected": expected})

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "output_type": "AI_RECOMMENDATION",
            "model_hash": self.model_hash,
            "feature_hash": self.feature_hash,
            "recommended_action": self.recommended_action,
            "inference_time": ensure_utc(self.inference_time).isoformat(),
            "environment": self.environment,
        })


# --------------------------------------------------------------------- #
# AISignal - versioned market intelligence signal                         #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class AISignal:
    output_id: str
    signal_kind: SignalKind
    semantics: str
    timeframe: str
    model_ref: str
    model_hash: str
    feature_snapshot_ref: str
    feature_hash: str
    output: Mapping[str, Any]
    timestamp: datetime
    environment: str
    provenance: Mapping[str, Any]
    content_hash: str
    valid_from: datetime
    valid_until: datetime
    confidence: float | None = None
    uncertainty: str = "UNKNOWN"
    produced_at: datetime | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("ai_output_id", self.output_id,
                            location="asig.output_id")
        if not isinstance(self.signal_kind, SignalKind):
            raise ContractValidationError(
                "asig.signal_kind must be a SignalKind",
                location="asig.signal_kind", rule_id="SCHEMA-ENUM")
        for name in ("semantics", "timeframe"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"asig.{name} must be a non-empty string",
                    location=f"asig.{name}")
        _require_sha256(self.model_hash, "asig.model_hash")
        _require_sha256(self.feature_hash, "asig.feature_hash")
        if not self.provenance:
            raise ContractValidationError(
                "asig.provenance must be complete",
                location="asig.provenance", rule_id="AI-PROVENANCE")
        _validate_confidence(self.confidence, "asig.confidence")
        _validate_validity_window(self.valid_from, self.valid_until)
        _require_inference_environment(self.environment, "asig.environment")
        ensure_utc(self.timestamp, location="asig.timestamp")
        if self.produced_at is not None:
            ensure_utc(self.produced_at, location="asig.produced_at")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "asig.content_hash mismatch",
                location="asig.content_hash", rule_id="AI-OUTPUT-001",
                details={"expected": expected})

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "output_type": "AI_SIGNAL",
            "signal_kind": self.signal_kind.value,
            "semantics": self.semantics,
            "timeframe": self.timeframe,
            "model_hash": self.model_hash,
            "feature_hash": self.feature_hash,
            "output": dict(self.output),
            "timestamp": ensure_utc(self.timestamp).isoformat(),
            "environment": self.environment,
        })


# --------------------------------------------------------------------- #
# AIProposal - the strongest advisory output; never an order             #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class AIProposal:
    ai_proposal_id: str
    proposed_direction: ProposedDirection
    model_ref: str
    model_hash: str
    feature_snapshot_ref: str
    feature_hash: str
    rationale: str
    output: Mapping[str, Any]
    event_time: datetime
    inference_time: datetime
    environment: str
    provenance: Mapping[str, Any]
    content_hash: str
    produced_at: datetime
    valid_from: datetime
    valid_until: datetime
    proposed_strategy: str | None = None
    proposed_conditions: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    expected_horizon_bars: int | None = None
    confidence: float | None = None
    uncertainty: str = "UNKNOWN"
    explanation_ref: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("ai_proposal_id", self.ai_proposal_id,
                            location="aprop.ai_proposal_id")
        if not isinstance(self.proposed_direction, ProposedDirection):
            raise ContractValidationError(
                "aprop.proposed_direction must be a ProposedDirection",
                location="aprop.proposed_direction", rule_id="SCHEMA-ENUM")
        _require_sha256(self.model_hash, "aprop.model_hash")
        _require_sha256(self.feature_hash, "aprop.feature_hash")
        if not isinstance(self.rationale, str) or not self.rationale:
            raise ContractValidationError(
                "aprop.rationale must be a non-empty string",
                location="aprop.rationale")
        # SECTION 29/107: a proposal must reference its evidence; without
        # evidence it is not advisory intelligence, it is noise.
        if not self.evidence:
            raise ContractValidationError(
                "aprop.evidence must reference model/dataset/evaluation "
                "hashes (proposals carry their evidence)",
                location="aprop.evidence", rule_id="AI-EVIDENCE")
        if not self.provenance:
            raise ContractValidationError(
                "aprop.provenance must be complete",
                location="aprop.provenance", rule_id="AI-PROVENANCE")
        _validate_confidence(self.confidence, "aprop.confidence")
        if self.expected_horizon_bars is not None and (
            not isinstance(self.expected_horizon_bars, int)
            or self.expected_horizon_bars < 1
        ):
            raise ContractValidationError(
                "aprop.expected_horizon_bars must be a positive integer",
                location="aprop.expected_horizon_bars")
        _validate_validity_window(self.valid_from, self.valid_until)
        _require_inference_environment(self.environment, "aprop.environment")
        for name in ("event_time", "inference_time", "produced_at"):
            ensure_utc(getattr(self, name), location=f"aprop.{name}")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "aprop.content_hash mismatch",
                location="aprop.content_hash", rule_id="AI-OUTPUT-001",
                details={"expected": expected})

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "output_type": "AI_PROPOSAL",
            "proposed_direction": self.proposed_direction.value,
            "model_hash": self.model_hash,
            "feature_hash": self.feature_hash,
            "rationale": self.rationale,
            "inference_time": ensure_utc(self.inference_time).isoformat(),
            "environment": self.environment,
        })


# --------------------------------------------------------------------- #
# Drift / OOD / health                                                   #
# --------------------------------------------------------------------- #
class DriftType(Enum):
    DATA_DRIFT = "DATA_DRIFT"
    FEATURE_DRIFT = "FEATURE_DRIFT"
    LABEL_DRIFT = "LABEL_DRIFT"
    PREDICTION_DRIFT = "PREDICTION_DRIFT"
    PERFORMANCE_DRIFT = "PERFORMANCE_DRIFT"
    REGIME_DRIFT = "REGIME_DRIFT"
    MODEL_BEHAVIOR_DRIFT = "MODEL_BEHAVIOR_DRIFT"


class DriftStatus(Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class OODStatus(Enum):
    IN_DISTRIBUTION = "IN_DISTRIBUTION"
    OOD_WARNING = "OOD_WARNING"
    OOD_CRITICAL = "OOD_CRITICAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DriftReport:
    drift_report_id: str
    model_hash: str
    drift_type: DriftType
    status: DriftStatus
    threshold_version: str
    created_at: datetime
    environment: str
    statistic: float | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("drift_report_id", self.drift_report_id,
                            location="drift.drift_report_id")
        _require_sha256(self.model_hash, "drift.model_hash")
        if not isinstance(self.drift_type, DriftType):
            raise ContractValidationError(
                "drift.drift_type must be a DriftType",
                location="drift.drift_type", rule_id="SCHEMA-ENUM")
        if not isinstance(self.status, DriftStatus):
            raise ContractValidationError(
                "drift.status must be a DriftStatus",
                location="drift.status", rule_id="SCHEMA-ENUM")
        if not isinstance(self.threshold_version, str) or not self.threshold_version:
            raise ContractValidationError(
                "drift.threshold_version must be explicit (drift thresholds "
                "are versioned configuration)",
                location="drift.threshold_version", rule_id="AI-IMPLICIT")
        _require_research_environment(self.environment, "drift.environment")
        ensure_utc(self.created_at, location="drift.created_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "drift_report_id": self.drift_report_id,
            "model_hash": self.model_hash,
            "drift_type": self.drift_type.value,
            "status": self.status.value,
            "threshold_version": self.threshold_version,
            "statistic": self.statistic,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class OODResult:
    model_hash: str
    status: OODStatus
    threshold_version: str
    distance: float | None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        _require_sha256(self.model_hash, "ood.model_hash")
        if not isinstance(self.status, OODStatus):
            raise ContractValidationError(
                "ood.status must be an OODStatus",
                location="ood.status", rule_id="SCHEMA-ENUM")
        if not isinstance(self.threshold_version, str) or not self.threshold_version:
            raise ContractValidationError(
                "ood.threshold_version must be explicit",
                location="ood.threshold_version", rule_id="AI-IMPLICIT")


class HealthStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class FreshnessStatus(Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ModelHealth:
    model_health_id: str
    model_hash: str
    artifact_integrity: str
    dependency_integrity: str
    feature_compatibility: str
    inference_success_rate: float
    error_rate: float
    missing_features_rate: float
    data_freshness: FreshnessStatus
    overall: HealthStatus
    created_at: datetime
    environment: str
    latency: Mapping[str, Any] = field(default_factory=dict)
    confidence_distribution: Mapping[str, Any] = field(default_factory=dict)
    drift_status: str = "UNKNOWN"
    unknown_rate: float = 0.0
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("model_health_id", self.model_health_id,
                            location="health.model_health_id")
        _require_sha256(self.model_hash, "health.model_hash")
        for name in ("artifact_integrity", "dependency_integrity",
                     "feature_compatibility"):
            if getattr(self, name) not in ("VERIFIED", "UNKNOWN"):
                raise ContractValidationError(
                    f"health.{name} must be VERIFIED or UNKNOWN",
                    location=f"health.{name}", rule_id="SCHEMA-ENUM")
        for name in ("inference_success_rate", "error_rate",
                     "missing_features_rate", "unknown_rate"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ContractValidationError(
                    f"health.{name} must be a rate within [0, 1]",
                    location=f"health.{name}", rule_id="AI-SEMANTICS")
        if not isinstance(self.data_freshness, FreshnessStatus):
            raise ContractValidationError(
                "health.data_freshness must be a FreshnessStatus",
                location="health.data_freshness", rule_id="SCHEMA-ENUM")
        if not isinstance(self.overall, HealthStatus):
            raise ContractValidationError(
                "health.overall must be a HealthStatus",
                location="health.overall", rule_id="SCHEMA-ENUM")
        # Health != profitability: no performance/P&L field may exist here.
        ensure_utc(self.created_at, location="health.created_at")
        _require_inference_environment(self.environment, "health.environment")


# --------------------------------------------------------------------- #
# Explainability                                                         #
# --------------------------------------------------------------------- #
class ExplanationType(Enum):
    MODEL_EXPLANATION = "MODEL_EXPLANATION"
    FEATURE_CONTRIBUTION = "FEATURE_CONTRIBUTION"
    RULE_EXPLANATION = "RULE_EXPLANATION"
    DATA_EVIDENCE = "DATA_EVIDENCE"


class ExplanationStatus(Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Explanation:
    explanation_id: str
    explanation_type: ExplanationType
    model_ref: str
    model_version: str
    input_feature_hash: str
    method: str
    generated_at: datetime
    environment: str
    status: ExplanationStatus
    limitations: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    inference_ref: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("explanation_id", self.explanation_id,
                            location="expl.explanation_id")
        if not isinstance(self.explanation_type, ExplanationType):
            raise ContractValidationError(
                "expl.explanation_type must be an ExplanationType",
                location="expl.explanation_type", rule_id="SCHEMA-ENUM")
        _require_sha256(self.input_feature_hash, "expl.input_feature_hash")
        for name in ("method",):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ContractValidationError(
                    f"expl.{name} must be a non-empty string",
                    location=f"expl.{name}")
        if not isinstance(self.status, ExplanationStatus):
            raise ContractValidationError(
                "expl.status must be an ExplanationStatus",
                location="expl.status", rule_id="SCHEMA-ENUM")
        if not isinstance(self.limitations, str) or not self.limitations:
            raise ContractValidationError(
                "expl.limitations are mandatory; an explanation without "
                "stated limits overclaims",
                location="expl.limitations", rule_id="AI-EXPLAIN")
        if self.explanation_type is ExplanationType.FEATURE_CONTRIBUTION \
                and "causal" not in self.limitations.lower():
            raise ContractValidationError(
                "feature-contribution explanations must state they are not "
                "causal (correlation is never causation)",
                location="expl.limitations", rule_id="AI-EXPLAIN")
        if self.status is ExplanationStatus.UNAVAILABLE and self.evidence:
            raise ContractValidationError(
                "UNAVAILABLE explanations must not carry fabricated evidence",
                location="expl.evidence", rule_id="AI-EXPLAIN")
        ensure_utc(self.generated_at, location="expl.generated_at")
        _require_inference_environment(self.environment, "expl.environment")


# --------------------------------------------------------------------- #
# Replay / selection / model card / incidents                            #
# --------------------------------------------------------------------- #
class ReplayMatch(Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"


class ReplayDiffKind(Enum):
    EXPECTED_VERSION_DIFFERENCE = "EXPECTED_VERSION_DIFFERENCE"
    NONDETERMINISM_SUSPECTED = "NONDETERMINISM_SUSPECTED"


@dataclass(frozen=True)
class ModelReplay:
    model_replay_id: str
    model_hash: str
    dataset_hash: str
    feature_schema_hash: str
    config_hash: str
    seed: int
    original_hashes: Tuple[str, ...]
    reproduced_hashes: Tuple[str, ...]
    match: ReplayMatch
    created_at: datetime
    environment: str
    differences: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("model_replay_id", self.model_replay_id,
                            location="mrep.model_replay_id")
        _require_sha256(self.model_hash, "mrep.model_hash")
        _require_sha256(self.dataset_hash, "mrep.dataset_hash")
        _require_sha256(self.feature_schema_hash, "mrep.feature_schema_hash")
        _require_sha256(self.config_hash, "mrep.config_hash")
        if not isinstance(self.seed, int):
            raise ContractValidationError(
                "mrep.seed must be recorded",
                location="mrep.seed", rule_id="AI-IMPLICIT")
        if len(self.original_hashes) != len(self.reproduced_hashes):
            raise ContractValidationError(
                "replay must compare equal-length inference sequences",
                location="mrep.reproduced_hashes", rule_id="MREP-001")
        if not isinstance(self.match, ReplayMatch):
            raise ContractValidationError(
                "mrep.match must be a ReplayMatch",
                location="mrep.match", rule_id="SCHEMA-ENUM")
        from architecture.contracts.environment import parse_environment
        if self.environment != "REPLAY":
            parse_environment(self.environment, location="mrep.environment")
            raise ContractValidationError(
                "replay must run in the REPLAY environment (read-only)",
                location="mrep.environment", rule_id="AI-REPLAY")


@dataclass(frozen=True)
class ModelSelection:
    model_selection_id: str
    selection_policy: str
    candidate_models: Tuple[str, ...]
    selected_model_hash: str
    decision_actor: str
    decided_at: datetime
    environment: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("model_selection_id", self.model_selection_id,
                            location="msel.model_selection_id")
        if not isinstance(self.selection_policy, str) or not self.selection_policy:
            raise ContractValidationError(
                "msel.selection_policy must reference a versioned policy "
                "('latest model' is not a policy)",
                location="msel.selection_policy", rule_id="AI-SELECTION")
        if len(self.candidate_models) < 2:
            raise ContractValidationError(
                "model selection compares at least two candidates",
                location="msel.candidate_models", rule_id="AI-SELECTION")
        for candidate in self.candidate_models:
            _require_sha256(candidate, "msel.candidate_models")
        _require_sha256(self.selected_model_hash, "msel.selected_model_hash")
        if self.selected_model_hash not in self.candidate_models:
            raise ContractValidationError(
                "selected model must be one of the candidates",
                location="msel.selected_model_hash", rule_id="AI-SELECTION")
        if not isinstance(self.decision_actor, str) or not self.decision_actor:
            raise ContractValidationError(
                "msel.decision_actor must be recorded - AI never silently "
                "selects the production model",
                location="msel.decision_actor", rule_id="AI-SELECTION")
        ensure_utc(self.decided_at, location="msel.decided_at")
        _require_inference_environment(self.environment, "msel.environment")


@dataclass(frozen=True)
class ModelCard:
    model_hash: str
    purpose: str
    intended_use: str
    prohibited_use: str
    training_data_ref: str
    feature_set: Tuple[str, ...]
    limitations: str
    metrics: Mapping[str, Any]
    oos_summary: Mapping[str, Any]
    robustness_summary: Mapping[str, Any]
    known_failure_cases: Tuple[str, ...]
    uncertainty_summary: Mapping[str, Any]
    drift_baseline: Mapping[str, Any]
    environment: str
    model_version: str
    provenance: Mapping[str, Any]
    card_hash: str
    created_at: datetime
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        _require_sha256(self.model_hash, "card.model_hash")
        for name in ("purpose", "intended_use", "prohibited_use", "limitations"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"card.{name} must be a non-empty string",
                    location=f"card.{name}")
        expected = self.compute_card_hash()
        if self.card_hash != expected:
            raise ContractValidationError(
                "card.card_hash mismatch",
                location="card.card_hash", rule_id="CARD-001",
                details={"expected": expected})
        _require_research_environment(self.environment, "card.environment")
        ensure_utc(self.created_at, location="card.created_at")

    def compute_card_hash(self) -> str:
        return canonical_hash({
            "model_hash": self.model_hash,
            "purpose": self.purpose,
            "intended_use": self.intended_use,
            "prohibited_use": self.prohibited_use,
            "feature_set": list(self.feature_set),
            "limitations": self.limitations,
            "known_failure_cases": list(self.known_failure_cases),
            "model_version": self.model_version,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_hash": self.model_hash,
            "model_version": self.model_version,
            "purpose": self.purpose,
            "intended_use": self.intended_use,
            "prohibited_use": self.prohibited_use,
            "training_data_ref": self.training_data_ref,
            "feature_set": list(self.feature_set),
            "limitations": self.limitations,
            "metrics": dict(self.metrics),
            "oos_summary": dict(self.oos_summary),
            "robustness_summary": dict(self.robustness_summary),
            "known_failure_cases": list(self.known_failure_cases),
            "uncertainty_summary": dict(self.uncertainty_summary),
            "drift_baseline": dict(self.drift_baseline),
            "environment": self.environment,
            "provenance": dict(self.provenance),
            "card_hash": self.card_hash,
        }


class IncidentType(Enum):
    MODEL_CORRUPTION = "MODEL_CORRUPTION"
    FEATURE_LEAKAGE = "FEATURE_LEAKAGE"
    DATA_LEAKAGE = "DATA_LEAKAGE"
    MODEL_DRIFT = "MODEL_DRIFT"
    OOD = "OOD"
    INFERENCE_FAILURE = "INFERENCE_FAILURE"
    PROVENANCE_BREAK = "PROVENANCE_BREAK"
    UNAUTHORIZED_PROMOTION = "UNAUTHORIZED_PROMOTION"
    UNAUTHORIZED_EXECUTION_ATTEMPT = "UNAUTHORIZED_EXECUTION_ATTEMPT"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"


class IncidentStatus(Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class AIIncident:
    ai_incident_id: str
    incident_type: IncidentType
    model_hash: str | None
    detected_at: datetime
    environment: str
    evidence: Mapping[str, Any]
    status: IncidentStatus
    description: str
    resolved_at: datetime | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("ai_incident_id", self.ai_incident_id,
                            location="inc.ai_incident_id")
        if not isinstance(self.incident_type, IncidentType):
            raise ContractValidationError(
                "inc.incident_type must be an IncidentType",
                location="inc.incident_type", rule_id="SCHEMA-ENUM")
        if self.model_hash is not None:
            _require_sha256(self.model_hash, "inc.model_hash")
        if not self.evidence:
            raise ContractValidationError(
                "incidents preserve evidence (an evidence-less incident is "
                "not auditable)",
                location="inc.evidence", rule_id="AI-INCIDENT")
        if not isinstance(self.status, IncidentStatus):
            raise ContractValidationError(
                "inc.status must be an IncidentStatus",
                location="inc.status", rule_id="SCHEMA-ENUM")
        if not isinstance(self.description, str) or not self.description:
            raise ContractValidationError(
                "inc.description must be a non-empty string",
                location="inc.description")
        ensure_utc(self.detected_at, location="inc.detected_at")
        if self.resolved_at is not None:
            ensure_utc(self.resolved_at, location="inc.resolved_at")
            if ensure_utc(self.resolved_at) < ensure_utc(self.detected_at):
                raise ContractValidationError(
                    "inc.resolved_at cannot precede detected_at",
                    location="inc.resolved_at")
        _require_inference_environment(self.environment, "inc.environment")
