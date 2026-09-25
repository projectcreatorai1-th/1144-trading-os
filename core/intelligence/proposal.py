"""AI outputs + candidate integration (owned by core.intelligence).

Builders for AIObservation / AIPrediction / AIRecommendation / AISignal /
AIProposal from inference results, and the AI candidate factory that
reuses the Phase 6 StrategyCandidate contract (SECTION 30) - there is no
second candidate registry and candidates never skip lifecycle states.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.contracts import (
    InferenceResult,
    InferenceStatus,
    ModelDefinition,
    UncertaintyStatus,
)
from core.intelligence.outputs import (
    AIProposal,
    AIPrediction,
    AIRecommendation,
    AISignal,
    AIObservation,
    OutputType,
    ProposedDirection,
    SignalKind,
)
from core.research.contracts import CandidateStatus, StrategyCandidate

CONTRACT_VERSION = "1.0.0"
DEFAULT_VALIDITY = timedelta(seconds=300)


class ProposalError(ContractError):
    rule_id = "APROPOSAL"


def _window(result: InferenceResult, validity: timedelta) \
        -> tuple[datetime, datetime, datetime]:
    moment = ensure_utc(result.inference_time)
    return moment, moment, moment + validity


def build_observation(*, result: InferenceResult, model: ModelDefinition,
                      environment: str,
                      validity: timedelta = DEFAULT_VALIDITY) -> AIObservation:
    produced, valid_from, valid_until = _window(result, validity)
    observation = AIObservation(
        output_id=new_identifier("ai_output_id"),
        model_ref=f"{model.model_id}@{model.model_version}",
        model_hash=model.model_hash,
        feature_snapshot_ref=result.provenance.get("request_hash", ""),
        feature_hash=result.feature_hash,
        output=dict(result.output),
        event_time=produced,
        inference_time=produced,
        environment=environment,
        provenance=dict(result.provenance),
        content_hash="",
        produced_at=produced,
        valid_from=valid_from,
        valid_until=valid_until,
        confidence=result.confidence,
        uncertainty=result.uncertainty,
    )
    object.__setattr__(observation, "content_hash",
                       observation.compute_content_hash())
    observation.validate()
    return observation


def build_prediction(*, result: InferenceResult, model: ModelDefinition,
                     label_definition_ref: str, horizon_bars: int,
                     value: Any, environment: str,
                     validity: timedelta = DEFAULT_VALIDITY) -> AIPrediction:
    produced, valid_from, valid_until = _window(result, validity)
    prediction = AIPrediction(
        output_id=new_identifier("ai_output_id"),
        model_ref=f"{model.model_id}@{model.model_version}",
        model_hash=model.model_hash,
        feature_snapshot_ref=result.provenance.get("request_hash", ""),
        feature_hash=result.feature_hash,
        label_definition_ref=label_definition_ref,
        horizon_bars=horizon_bars,
        value=value,
        output=dict(result.output),
        event_time=produced,
        inference_time=produced,
        environment=environment,
        provenance=dict(result.provenance),
        content_hash="",
        produced_at=produced,
        valid_from=valid_from,
        valid_until=valid_until,
        probability=result.probability,
        confidence=result.confidence,
        uncertainty=result.uncertainty,
    )
    object.__setattr__(prediction, "content_hash",
                       prediction.compute_content_hash())
    prediction.validate()
    return prediction


def build_recommendation(*, result: InferenceResult, model: ModelDefinition,
                         recommended_action: str, rationale: str,
                         environment: str,
                         evidence: Mapping[str, Any] | None = None,
                         validity: timedelta = DEFAULT_VALIDITY) \
        -> AIRecommendation:
    produced, valid_from, valid_until = _window(result, validity)
    recommendation = AIRecommendation(
        output_id=new_identifier("ai_output_id"),
        model_ref=f"{model.model_id}@{model.model_version}",
        model_hash=model.model_hash,
        feature_snapshot_ref=result.provenance.get("request_hash", ""),
        feature_hash=result.feature_hash,
        recommended_action=recommended_action,
        rationale=rationale,
        output=dict(result.output),
        event_time=produced,
        inference_time=produced,
        environment=environment,
        provenance=dict(result.provenance),
        content_hash="",
        produced_at=produced,
        valid_from=valid_from,
        valid_until=valid_until,
        evidence=dict(evidence or result.provenance),
        confidence=result.confidence,
        uncertainty=result.uncertainty,
    )
    object.__setattr__(recommendation, "content_hash",
                       recommendation.compute_content_hash())
    recommendation.validate()
    return recommendation


def build_signal(*, result: InferenceResult, model: ModelDefinition,
                 signal_kind: SignalKind, semantics: str, timeframe: str,
                 environment: str,
                 validity: timedelta = DEFAULT_VALIDITY) -> AISignal:
    produced, valid_from, valid_until = _window(result, validity)
    signal = AISignal(
        output_id=new_identifier("ai_output_id"),
        signal_kind=signal_kind,
        semantics=semantics,
        timeframe=timeframe,
        model_ref=f"{model.model_id}@{model.model_version}",
        model_hash=model.model_hash,
        feature_snapshot_ref=result.provenance.get("request_hash", ""),
        feature_hash=result.feature_hash,
        output=dict(result.output),
        timestamp=produced,
        environment=environment,
        provenance=dict(result.provenance),
        content_hash="",
        valid_from=valid_from,
        valid_until=valid_until,
        confidence=result.confidence,
        uncertainty=result.uncertainty,
        produced_at=produced,
    )
    object.__setattr__(signal, "content_hash", signal.compute_content_hash())
    signal.validate()
    return signal


def build_proposal(*, result: InferenceResult, model: ModelDefinition,
                   direction: ProposedDirection, rationale: str,
                   environment: str,
                   evidence: Mapping[str, Any],
                   proposed_strategy: str | None = None,
                   proposed_conditions: Mapping[str, Any] | None = None,
                   expected_horizon_bars: int | None = None,
                   validity: timedelta = DEFAULT_VALIDITY) -> AIProposal:
    if result.status is not InferenceStatus.VALID:
        raise ProposalError(
            "proposals require a VALID inference result",
            location="proposal.build", rule_id="APROPOSAL-001")
    produced, valid_from, valid_until = _window(result, validity)
    proposal = AIProposal(
        ai_proposal_id=new_identifier("ai_proposal_id"),
        proposed_direction=direction,
        model_ref=f"{model.model_id}@{model.model_version}",
        model_hash=model.model_hash,
        feature_snapshot_ref=result.provenance.get("request_hash", ""),
        feature_hash=result.feature_hash,
        rationale=rationale,
        output=dict(result.output),
        event_time=produced,
        inference_time=produced,
        environment=environment,
        provenance=dict(result.provenance),
        content_hash="",
        produced_at=produced,
        valid_from=valid_from,
        valid_until=valid_until,
        proposed_strategy=proposed_strategy,
        proposed_conditions=dict(proposed_conditions or {}),
        evidence=dict(evidence),
        expected_horizon_bars=expected_horizon_bars,
        confidence=result.confidence,
        uncertainty=result.uncertainty,
    )
    object.__setattr__(proposal, "content_hash",
                       proposal.compute_content_hash())
    proposal.validate()
    return proposal


# --------------------------------------------------------------------- #
# Candidate integration (SECTION 30/105)                                 #
# --------------------------------------------------------------------- #
def proposal_to_candidate(*, proposal: AIProposal,
                          strategy_family: str,
                          logic_version: str, logic_hash: str,
                          parameter_set: Mapping[str, Any],
                          dataset_reference: str,
                          research_run_reference: str,
                          created_at: datetime) -> StrategyCandidate:
    """AI proposals become Phase 6 StrategyCandidates - the SAME contract,
    the SAME lifecycle. Candidates are research objects: they are not live
    strategies and cannot skip states."""
    candidate = StrategyCandidate(
        candidate_id=new_identifier("strategy_candidate_id"),
        strategy_family=strategy_family,
        logic_version=logic_version,
        logic_hash=logic_hash,
        parameter_set={
            **dict(parameter_set),
            "ai_proposal_id": proposal.ai_proposal_id,
            "model_hash": proposal.model_hash,
            "feature_hash": proposal.feature_hash,
        },
        dataset_reference=dataset_reference,
        research_run_reference=research_run_reference,
        status=CandidateStatus.DRAFT,
        created_at=ensure_utc(created_at, location="candidate.created_at"),
        environment="RESEARCH",
    )
    candidate.validate()
    return candidate
