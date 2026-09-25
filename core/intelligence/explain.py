"""Explainability + model health (owned by core.intelligence).

Explanations (SECTION 32) distinguish MODEL_EXPLANATION /
FEATURE_CONTRIBUTION / RULE_EXPLANATION / DATA_EVIDENCE and never claim
causality from correlation. Unavailable explanations are
EXPLANATION_UNAVAILABLE - never fabricated text.

Model health (SECTION 38) is operational health, NOT profitability.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.adapter import ModelAdapter, ModelAdapterError
from core.intelligence.contracts import ModelDefinition, canonical_hash
from core.intelligence.outputs import (
    Explanation,
    ExplanationStatus,
    ExplanationType,
    FreshnessStatus,
    HealthStatus,
    ModelHealth,
)

CONTRACT_VERSION = "1.0.0"

NOT_CAUSAL_LIMITATION = (
    "Feature contributions are associations under the fitted linear form; "
    "they are NOT causal effects and carry no counterfactual claim."
)


class ExplainabilityError(ContractError):
    rule_id = "EXPLAIN"


class ExplainabilityEngine:
    """Derives explanations from models that support them."""

    def __init__(self, adapter: ModelAdapter) -> None:
        from core.intelligence.adapter import AdapterCapability
        self._adapter = adapter
        self._capability = AdapterCapability.FEATURE_CONTRIBUTION

    def explain(self, *, model: ModelDefinition, feature_values: Mapping[str, Any],
                feature_hash: str, inference_ref: str | None,
                environment: str, generated_at: datetime) -> Explanation:
        import json as _json
        if not self._adapter.supports(self._capability):
            explanation = Explanation(
                explanation_id=new_identifier("explanation_id"),
                explanation_type=ExplanationType.MODEL_EXPLANATION,
                model_ref=f"{model.model_id}@{model.model_version}",
                model_version=model.model_version,
                input_feature_hash=feature_hash,
                method="UNAVAILABLE",
                generated_at=ensure_utc(generated_at,
                                        location="explain.generated_at"),
                environment=environment,
                status=ExplanationStatus.UNAVAILABLE,
                limitations=(
                    f"The {model.model_family} adapter exposes no "
                    "explanation method; EXPLANATION_UNAVAILABLE is honest, "
                    "fabricated text is forbidden."),
                evidence={},
                inference_ref=inference_ref,
            )
            explanation.validate()
            return explanation
        artifact_json = _json.dumps(model.provenance.get("artifact", {}),
                                    sort_keys=True)
        try:
            evidence = self._adapter.explain(artifact_json, feature_values)
        except ModelAdapterError as error:
            raise ExplainabilityError(
                f"explanation failed: {error}",
                location="explain.adapter", rule_id="EXPLAIN-001") from error
        explanation = Explanation(
            explanation_id=new_identifier("explanation_id"),
            explanation_type=ExplanationType.FEATURE_CONTRIBUTION,
            model_ref=f"{model.model_id}@{model.model_version}",
            model_version=model.model_version,
            input_feature_hash=feature_hash,
            method=f"{type(self._adapter).__name__}.explain "
                   "(signed feature contribution)",
            generated_at=ensure_utc(generated_at,
                                    location="explain.generated_at"),
            environment=environment,
            status=ExplanationStatus.AVAILABLE,
            limitations=NOT_CAUSAL_LIMITATION,
            evidence=dict(evidence),
            inference_ref=inference_ref,
        )
        explanation.validate()
        return explanation


class ModelHealthService:
    """Operational health; health != profitability (no performance fields)."""

    def __init__(self) -> None:
        self._records: dict[str, ModelHealth] = {}

    def record(self, *, model: ModelDefinition,
               artifact_integrity: str,
               dependency_integrity: str,
               feature_compatibility: str,
               inference_success_rate: float,
               error_rate: float,
               missing_features_rate: float,
               data_freshness: FreshnessStatus,
               drift_status: str,
               unknown_rate: float,
               latency: Mapping[str, Any],
               confidence_distribution: Mapping[str, Any],
               created_at: datetime,
               environment: str) -> ModelHealth:
        components = [
            artifact_integrity == "VERIFIED",
            dependency_integrity == "VERIFIED",
            feature_compatibility == "VERIFIED",
        ]
        operational = inference_success_rate >= 0.95 and error_rate <= 0.05
        if data_freshness is FreshnessStatus.UNKNOWN or \
                drift_status == "UNKNOWN":
            overall = HealthStatus.UNKNOWN
        elif all(components) and operational:
            overall = HealthStatus.HEALTHY
        elif any(components) or operational:
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.UNHEALTHY
        health = ModelHealth(
            model_health_id=new_identifier("model_health_id"),
            model_hash=model.model_hash,
            artifact_integrity=artifact_integrity,
            dependency_integrity=dependency_integrity,
            feature_compatibility=feature_compatibility,
            inference_success_rate=inference_success_rate,
            error_rate=error_rate,
            missing_features_rate=missing_features_rate,
            data_freshness=data_freshness,
            overall=overall,
            created_at=ensure_utc(created_at, location="health.created_at"),
            environment=environment,
            latency=dict(latency),
            confidence_distribution=dict(confidence_distribution),
            drift_status=drift_status,
            unknown_rate=unknown_rate,
        )
        health.validate()
        self._records[model.model_hash] = health
        return health

    def latest(self, model_hash: str) -> ModelHealth | None:
        return self._records.get(model_hash)


def verify_artifact_integrity(model: ModelDefinition) -> str:
    """SECTION 66: artifact hash must commit to the stored artifact. The
    canonicalization must match the adapter byte-for-byte."""
    import hashlib
    import json as _json

    def canonical(payload: Mapping[str, Any]) -> str:
        material = _json.dumps(payload, sort_keys=True,
                               separators=(",", ":"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    artifact = model.provenance.get("artifact")
    if artifact is None:
        return "UNKNOWN"
    inner = {k: v for k, v in artifact.items() if k != "artifact_hash"}
    if canonical(inner) != artifact.get("artifact_hash"):
        return "UNKNOWN"
    if model.artifact_hash != hashlib.sha256(
            _json.dumps(artifact, sort_keys=True).encode("utf-8")).hexdigest():
        return "UNKNOWN"
    return "VERIFIED"
