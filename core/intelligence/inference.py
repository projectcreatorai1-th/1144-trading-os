"""Inference engine + AI safety gate (owned by core.intelligence).

Deterministic inference (SECTION 23-26):
- point-in-time: only data available at inference_time
- environment isolation: model must be approved for the environment
- idempotency: same semantic request -> same immutable result
- freshness: expired results are EXPIRED, never silently current

The AISafetyValidator is the ONE centralized AI safety authority
(SECTION 47). It validates whether AI OUTPUT is usable - it does NOT
replace the Phase 3 risk engine and can never approve exposure.
Critical UNKNOWN -> BLOCK, never UNKNOWN -> SAFE.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Mapping, Sequence

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.adapter import ModelAdapter, ModelAdapterError
from core.intelligence.contracts import (
    InferenceRequest,
    InferenceResult,
    InferenceStatus,
    ModelDefinition,
    UncertaintyStatus,
    _require_inference_environment,
)
from core.intelligence.registry import ModelRegistry

CONTRACT_VERSION = "1.0.0"

#: Model lifecycle statuses allowed to produce usable (non-shadow-blocked)
#: inference. SHADOW/PAPER/DEMO outputs are advisory evidence by contract.
PRODUCTION_INFERENCE_STATUSES = ("PRODUCTION_ELIGIBLE",)


class InferenceError(ContractError):
    rule_id = "INFERENCE"


class SafetyDecision(Enum):
    ALLOW = "ALLOW"
    LIMITED = "LIMITED"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SafetyAssessment:
    decision: SafetyDecision
    reasons: tuple[str, ...]


#: Statuses permitted to run inference at all (a FAILED/RETIRED/SUSPENDED
#: model must never infer; SECTION 16/41/69).
INFERABLE_STATUSES = (
    "TRAINED", "VALIDATED", "OOS_VALIDATED", "RESEARCH_APPROVED",
    "SHADOW", "PAPER", "DEMO", "PRODUCTION_ELIGIBLE",
)


class AISafetyValidator:
    """The single AI safety gate (SECTION 47). Scattered safety decisions
    are forbidden - every consumer asks this validator."""

    def __init__(self, drift_status: str = "NORMAL",
                 ood_status: str = "IN_DISTRIBUTION",
                 freshness: str = "FRESH") -> None:
        self._drift_status = drift_status
        self._ood_status = ood_status
        self._freshness = freshness

    def with_context(self, *, drift_status: str | None = None,
                     ood_status: str | None = None,
                     freshness: str | None = None) -> "AISafetyValidator":
        return AISafetyValidator(
            drift_status=drift_status if drift_status is not None
            else self._drift_status,
            ood_status=ood_status if ood_status is not None
            else self._ood_status,
            freshness=freshness if freshness is not None
            else self._freshness,
        )

    def assess(self, *, model: ModelDefinition, environment: str,
               inference_time: datetime, snapshot_available_at: datetime,
               confidence: float | None = None) -> SafetyAssessment:
        reasons: list[str] = []
        _require_inference_environment(environment, "safety.environment")
        if model.status not in INFERABLE_STATUSES:
            return SafetyAssessment(
                SafetyDecision.BLOCK,
                (f"model status {model.status} is not inferable"
                 " (MODEL_NOT_VALIDATED)",))
        if environment not in model.inference_environments:
            reasons.append(
                f"ENVIRONMENT_MISMATCH: model approved for "
                f"{list(model.inference_environments)}, requested {environment}")
        if environment == "LIVE" and \
                model.status not in PRODUCTION_INFERENCE_STATUSES:
            reasons.append(
                "MODEL_NOT_VALIDATED: LIVE inference requires "
                "PRODUCTION_ELIGIBLE status")
        if model.valid_until is not None and \
                ensure_utc(model.valid_until) < ensure_utc(inference_time):
            reasons.append("MODEL_EXPIRED")
        if self._freshness == "STALE":
            reasons.append("FEATURES_STALE")
        # SECTION 109: critical UNKNOWN blocks.
        if self._drift_status == "UNKNOWN":
            reasons.append("DRIFT_UNKNOWN (critical UNKNOWN blocks)")
        if self._ood_status == "UNKNOWN":
            reasons.append("OOD_UNKNOWN (critical UNKNOWN blocks)")
        if self._drift_status == "CRITICAL":
            reasons.append("DRIFT_CRITICAL")
        if self._ood_status == "OOD_CRITICAL":
            reasons.append("OOD_CRITICAL")
        if reasons:
            return SafetyAssessment(SafetyDecision.BLOCK, tuple(reasons))
        if self._drift_status == "WARNING":
            return SafetyAssessment(
                SafetyDecision.LIMITED,
                ("DRIFT_WARNING: outputs usable as evidence only",))
        if confidence is not None and confidence < 0.5:
            return SafetyAssessment(
                SafetyDecision.LIMITED,
                ("LOW_CONFIDENCE: advisory use only",))
        return SafetyAssessment(SafetyDecision.ALLOW, ())


class InferenceEngine:
    """Deterministic, idempotent, environment-aware inference."""

    def __init__(self, registry: ModelRegistry, adapter: ModelAdapter,
                 safety: AISafetyValidator | None = None) -> None:
        self._registry = registry
        self._adapter = adapter
        self._safety = safety or AISafetyValidator()
        self._results: dict[str, InferenceResult] = {}
        self._latencies: list[float] = []
        self._failures = 0
        self._unknowns = 0
        self._confidences: list[float] = []

    @property
    def stats(self) -> Mapping[str, Any]:
        total = len(self._results) + self._failures
        return {
            "inference_count": len(self._results),
            "failure_count": self._failures,
            "unknown_rate": self._unknowns / total if total else 0.0,
            "latency": _percentiles(self._latencies),
        }

    def infer(self, *, model_id: str, model_version: str,
              inference_time: datetime, environment: str,
              snapshot, provenance: Mapping[str, Any] | None = None,
              validity_seconds: int = 300) -> InferenceResult:
        model = self._registry.get(model_id, model_version)
        moment = ensure_utc(inference_time, location="infer.time")
        request = InferenceRequest(
            inference_id=new_identifier("inference_id"),
            model_id=model_id,
            model_version=model_version,
            inference_time=moment,
            environment=environment,
            feature_snapshot_hash=snapshot.feature_hash,
            request_hash="",
            provenance=dict(provenance or {}),
        )
        object.__setattr__(request, "request_hash",
                           request.compute_request_hash())
        request.validate()
        # SECTION 26: duplicate request returns the original immutable result.
        existing = self._results.get(request.request_hash)
        if existing is not None:
            return existing
        assessment = self._safety.assess(
            model=model, environment=environment, inference_time=moment,
            snapshot_available_at=snapshot.available_at)
        if assessment.decision is SafetyDecision.BLOCK:
            self._failures += 1
            raise InferenceError(
                "AI safety gate BLOCKED inference",
                location="infer.safety", rule_id="AI-SAFETY",
                details={"reasons": list(assessment.reasons)})
        # PIT rule: a snapshot may never carry data from after inference_time.
        if ensure_utc(snapshot.available_at) > moment:
            self._failures += 1
            raise InferenceError(
                "POINT_IN_TIME_VIOLATION: snapshot contains data not "
                "available at inference_time",
                location="infer.pit", rule_id="AI-PIT",
                details={"snapshot_available_at":
                         ensure_utc(snapshot.available_at).isoformat(),
                         "inference_time": moment.isoformat()})
        try:
            output = self._adapter_predict(model, snapshot)
        except ModelAdapterError as error:
            self._failures += 1
            raise InferenceError(
                f"inference failed: {error}",
                location="infer.adapter", rule_id="INFERENCE-004") \
                from error
        confidence = output.get("confidence")
        probability = output.get("probability")
        score = output.get("score")
        uncertainty = output.get("uncertainty_status", "UNKNOWN")
        if confidence is not None:
            self._confidences.append(float(confidence))
        if uncertainty == "UNKNOWN":
            self._unknowns += 1
        if assessment.decision is SafetyDecision.LIMITED:
            output = dict(output)
            output["advisory_only_reason"] = "; ".join(assessment.reasons)
        produced_at = moment
        result = InferenceResult(
            inference_id=request.inference_id,
            model_hash=model.model_hash,
            feature_hash=snapshot.feature_hash,
            output=dict(output),
            status=InferenceStatus.VALID,
            inference_time=moment,
            environment=environment,
            produced_at=produced_at,
            valid_from=moment,
            valid_until=moment + timedelta(seconds=validity_seconds),
            provenance={
                "request_hash": request.request_hash,
                "model_id": model.model_id,
                "model_version": model.model_version,
                "model_hash": model.model_hash,
                "dataset_hash": model.dataset_hash,
                "feature_schema_hash": model.feature_schema_hash,
                "code_hash": model.code_hash,
                "artifact_hash": model.artifact_hash,
                "lineage": dict(snapshot.provenance),
                "safety_decision": assessment.decision.value,
            },
            content_hash="",
            confidence=float(confidence) if confidence is not None else None,
            probability=float(probability) if probability is not None
            else None,
            score=float(score) if score is not None else None,
            uncertainty=uncertainty,
        )
        object.__setattr__(result, "content_hash",
                           result.compute_content_hash())
        result.validate()
        self._results[request.request_hash] = result
        return result

    def _adapter_predict(self, model: ModelDefinition, snapshot) \
            -> Mapping[str, Any]:
        payload = model.provenance.get("artifact", {})
        artifact_json = json.dumps(payload, sort_keys=True)
        return self._adapter.predict(artifact_json, snapshot.values)

    def find(self, request_hash: str) -> InferenceResult | None:
        return self._results.get(request_hash)

    def replay_inference(self, result: InferenceResult, *,
                         adapter: ModelAdapter, model: ModelDefinition) \
            -> Mapping[str, Any]:
        """Read-only re-derivation of a stored result (SECTION 44)."""
        payload = model.provenance.get("artifact", {})
        artifact_json = json.dumps(payload, sort_keys=True)
        output = adapter.predict(artifact_json, result.provenance.get(
            "feature_values", {}))
        return output


def _percentiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)

    def pct(p: float) -> float:
        index = min(int(p * len(ordered)), len(ordered) - 1)
        return ordered[index]

    return {"p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99),
            "max": ordered[-1]}
