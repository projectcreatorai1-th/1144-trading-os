"""Phase 7 unit tests: intelligence plane contracts and engines.

All data is SYNTHETIC (controlled fixtures); no real market data, no
broker, no execution. AI is advisory only.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.phase7_factories import (
    BAR_SECONDS, KEY_MOM, KEY_VOL, T0, build_rows, feature_engine,
    label_definition, pit_labels, research_dataset, split_definition,
    synthetic_observations, trained_classifier,
)

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier

from core.intelligence.contracts import (
    FeatureDefinition,
    FeatureSnapshot,
    FeatureStatus,
    IntelligenceDataset,
    IntelligenceDatasetType,
    InferenceRequest,
    InferenceResult,
    InferenceStatus,
    LabelDefinition,
    MissingValuePolicy,
    ModelDefinition,
    ModelEvaluation,
    ModelValidation,
    SplitDefinition,
    TaskType,
    TrainingConfig,
    TrainingRun,
    TrainingStatus,
    UnknownPolicy,
    canonical_hash,
)
from core.intelligence.dataset import (
    IntelligenceDatasetBuilder,
    label_available_at,
    validate_split_separation,
)
from core.intelligence.drift import (
    DriftEngine,
    DriftStatus,
    DriftType,
    drift_worst,
)
from core.intelligence.evaluation import (
    calibration_curve,
    classification_metrics,
    regression_metrics,
)
from core.intelligence.events import PHASE7_EVENT_TYPES, emit
from core.intelligence.explain import (
    ExplainabilityEngine,
    ModelHealthService,
    verify_artifact_integrity,
)
from core.intelligence.feature import (
    FeatureEngine,
    audit_label_independence,
    audit_normalization,
    implementation_hash,
    verify_snapshot_pit,
)
from core.intelligence.inference import (
    AISafetyValidator,
    InferenceEngine,
    SafetyDecision,
)
from core.intelligence.lifecycle import ModelActor, ModelLifecycleService
from core.intelligence.outputs import (
    AIIncident,
    AIProposal,
    AIPrediction,
    AISignal,
    ExplanationStatus,
    ExplanationType,
    FreshnessStatus,
    HealthStatus,
    IncidentStatus,
    IncidentType,
    ModelCard,
    ModelSelection,
    OODStatus,
    ProposedDirection,
    SignalKind,
)
from core.intelligence.proposal import (
    build_observation,
    build_prediction,
    build_proposal,
    build_recommendation,
    build_signal,
    proposal_to_candidate,
)
from core.intelligence.registry import FeatureRegistry, ModelRegistry
from core.intelligence.replay import (
    IntelligenceReplayAdapter,
    compare_models,
    diff_inferences,
)
from core.intelligence.validation import gate_blocks, validation_gate
from core.events.contracts import EventType
from core.research.contracts import CandidateStatus
from platform.audit.repository import AuditRepository
from platform.security.contracts import Permission, Role

UTC = timezone.utc


def moment(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


# --------------------------------------------------------------------- #
# Feature engine                                                         #
# --------------------------------------------------------------------- #
class TestFeatureEngine:
    def test_snapshot_is_point_in_time(self):
        dataset = research_dataset()
        engine = feature_engine()
        from tests.phase7_factories import FTR_MOM, FTR_VOL, make_feature
        mom = make_feature(FTR_MOM, "momentum_1", momentum_fn, 2)
        vol = make_feature(FTR_VOL, "volatility_2", volatility_fn, 3)
        # observation at index 10 becomes available at 10:30s + 10 min
        as_of = dataset.observations[10].available_time
        snap = engine.snapshot(dataset=dataset, definitions=(mom, vol),
                               symbol="EURUSD", as_of=as_of,
                               environment="RESEARCH")
        assert snap.available_at <= as_of
        verify_snapshot_pit(snap, dataset)

    def test_snapshot_excludes_not_yet_available(self):
        dataset = research_dataset()
        engine = feature_engine()
        from tests.phase7_factories import FTR_MOM, make_feature
        mom = make_feature(FTR_MOM, "momentum_1", momentum_fn, 2)
        # as_of between availability of obs 9 and obs 10
        as_of = dataset.observations[10].available_time - timedelta(seconds=1)
        snap = engine.snapshot(dataset=dataset, definitions=(mom,),
                               symbol="EURUSD", as_of=as_of,
                               environment="RESEARCH")
        # the last visible observation must be obs 9
        visible = dataset.visible_at(as_of)
        assert all(o.available_time <= as_of for o in visible)
        assert snap.available_at <= as_of

    def test_feature_definition_content_hash_locks_definition(self):
        from tests.phase7_factories import FTR_MOM, make_feature
        base = make_feature(FTR_MOM, "momentum_1", momentum_fn, 2)
        tampered = FeatureDefinition(
            **{**base.__dict__, "content_hash": "0" * 64})
        with pytest.raises(ContractError):
            tampered.validate()

    def test_implementation_hash_mismatch_rejected(self):
        from tests.phase7_factories import FTR_MOM, make_feature
        engine = FeatureEngine()
        definition = make_feature(FTR_MOM, "momentum_1", momentum_fn, 2)
        engine.register(definition, momentum_fn)
        # re-register the same definition with a DIFFERENT function
        with pytest.raises(ContractError):
            engine.register(definition, volatility_fn)

    def test_future_normalization_rejected(self):
        from tests.phase7_factories import FTR_MOM, make_feature
        definition = make_feature(FTR_MOM, "momentum_1", momentum_fn, 2)
        from dataclasses import replace
        leaked = replace(
            definition,
            normalization_definition={"fitted_on": "FULL_DATASET"},
            content_hash="")
        object.__setattr__(leaked, "content_hash",
                           leaked.compute_content_hash())
        with pytest.raises(ContractError) as err:
            audit_normalization((leaked,))
        assert "FUTURE_NORMALIZATION" in str(err.value)

    def test_target_leakage_rejected(self):
        from tests.phase7_factories import FTR_MOM, make_feature
        from dataclasses import replace
        definition = make_feature(FTR_MOM, "momentum_1", momentum_fn, 2)
        leaked = replace(definition,
                         dependencies=("label:future_return",),
                         content_hash="")
        object.__setattr__(leaked, "content_hash",
                           leaked.compute_content_hash())
        with pytest.raises(ContractError) as err:
            audit_label_independence((leaked,))
        assert "TARGET_LEAKAGE" in str(err.value)

    def test_missing_block_policy_blocks(self):
        from tests.phase7_factories import FTR_VOL, make_feature
        from dataclasses import replace
        engine = FeatureEngine()
        definition = make_feature(FTR_VOL, "volatility_2", volatility_fn, 3)
        blocking = replace(definition,
                           missing_value_policy=MissingValuePolicy.BLOCK,
                           content_hash="")
        object.__setattr__(blocking, "content_hash",
                           blocking.compute_content_hash())
        engine.register(blocking, volatility_fn)
        dataset = research_dataset()
        # as_of before ANY observation is visible -> missing -> BLOCK
        with pytest.raises(ContractError) as err:
            engine.snapshot(dataset=dataset, definitions=(blocking,),
                            symbol="EURUSD", as_of=T0 - timedelta(hours=1),
                            environment="RESEARCH")
        assert "BLOCK" in str(err.value)

    def test_unknown_policy_propagates_unknown_never_zero(self):
        from tests.phase7_factories import FTR_VOL, make_feature
        engine = FeatureEngine()
        definition = make_feature(FTR_VOL, "volatility_2", volatility_fn, 3)
        engine.register(definition, volatility_fn)
        dataset = research_dataset()
        snap = engine.snapshot(
            dataset=dataset, definitions=(definition,), symbol="EURUSD",
            as_of=dataset.observations[0].available_time,
            environment="RESEARCH")
        # volatility needs >=3 observations; with 1 visible it is UNKNOWN
        values = snap.values
        assert all(v != 0 for v in values.values()) or \
            all(v == "UNKNOWN" for v in values.values())


def momentum_fn(window):
    closes = [float(o.payload["close"]) for o in window]
    return closes[-1] - closes[-2] if len(closes) >= 2 else None


def volatility_fn(window):
    closes = [float(o.payload["close"]) for o in window]
    if len(closes) < 3:
        return None
    rets = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    mean = sum(rets) / len(rets)
    return (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5


# --------------------------------------------------------------------- #
# Labels + datasets + splits                                             #
# --------------------------------------------------------------------- #
class TestLabelsAndDatasets:
    def test_label_availability_respects_horizon(self):
        event = moment(10)
        available = label_available_at(event, 2, BAR_SECONDS)
        assert available == event + timedelta(seconds=2 * BAR_SECONDS)

    def test_label_definition_rejects_instant_availability(self):
        from dataclasses import replace
        label = label_definition()
        with pytest.raises(ContractError):
            replace(label,
                    availability_rule="AVAILABLE_AT_EVENT",
                    content_hash="0" * 64).validate()

    def test_split_rejects_temporal_inversion(self):
        with pytest.raises(ContractError) as err:
            SplitDefinition(
                split_id="s", semantics="TEMPORAL", overlap_declared=False,
                boundaries={"train_end": moment(50).isoformat(),
                            "validation_end": moment(40).isoformat(),
                            "oos_end": moment(30).isoformat()}).validate()
        assert "SPLIT-002" in str(err.value) or "increasing" in str(err.value)

    def test_declared_overlap_requires_justification(self):
        with pytest.raises(ContractError):
            SplitDefinition(
                split_id="s", semantics="TEMPORAL", overlap_declared=True,
                boundaries={"train_end": moment(10).isoformat(),
                            "validation_end": moment(20).isoformat(),
                            "oos_end": moment(30).isoformat()}).validate()

    def test_cross_split_overlap_rejected(self):
        env = trained_classifier()
        engine, dataset = env["engine"], env["dataset"]
        mom, vol = env["mom"], env["vol"]
        builder = IntelligenceDatasetBuilder(engine)
        split = split_definition()
        labels = pit_labels(dataset)
        common = dict(source=dataset, definitions=(mom, vol), split=split,
                      symbol="EURUSD", normalization_version="norm-1",
                      label=label_definition(), label_values=labels,
                      environment="RESEARCH")
        train = builder.build(dataset_type=IntelligenceDatasetType.TRAIN,
                              start=dataset.observations[3].available_time,
                              end=moment(30), **common)
        # overlapping validation window -> contamination
        validation = builder.build(
            dataset_type=IntelligenceDatasetType.VALIDATION,
            start=dataset.observations[3].available_time, end=moment(20),
            **common)
        oos = builder.build(dataset_type=IntelligenceDatasetType.OOS,
                            start=moment(45), end=moment(55), **common)
        with pytest.raises(ContractError) as err:
            validate_split_separation((train.dataset, validation.dataset,
                                       oos.dataset))
        assert "CROSS_SPLIT_LEAKAGE" in str(err.value)

    def test_clean_splits_pass(self):
        env = trained_classifier()
        engine, dataset = env["engine"], env["dataset"]
        mom, vol = env["mom"], env["vol"]
        builder = IntelligenceDatasetBuilder(engine)
        split = split_definition()
        labels = pit_labels(dataset)
        common = dict(source=dataset, definitions=(mom, vol), split=split,
                      symbol="EURUSD", normalization_version="norm-1",
                      label=label_definition(), label_values=labels,
                      environment="RESEARCH")
        train = builder.build(dataset_type=IntelligenceDatasetType.TRAIN,
                              start=dataset.observations[3].available_time,
                              end=moment(30), **common)
        validation = builder.build(
            dataset_type=IntelligenceDatasetType.VALIDATION,
            start=moment(31), end=moment(40), **common)
        oos = builder.build(dataset_type=IntelligenceDatasetType.OOS,
                            start=moment(41), end=moment(55), **common)
        validate_split_separation((train.dataset, validation.dataset,
                                   oos.dataset), split)

    def test_dataset_is_immutable_content_hash(self):
        env = trained_classifier()
        idataset = env["idataset"]
        tampered = IntelligenceDataset(
            **{**idataset.__dict__, "observation_count":
               idataset.observation_count + 1})
        with pytest.raises(ContractError):
            tampered.validate()


# --------------------------------------------------------------------- #
# Registry / training / evaluation                                       #
# --------------------------------------------------------------------- #
class TestRegistryAndTraining:
    def test_feature_registry_rejects_mutation(self):
        from tests.phase7_factories import FTR_MOM, make_feature
        from dataclasses import replace
        registry = FeatureRegistry()
        definition = make_feature(FTR_MOM, "momentum_1", momentum_fn, 2)
        registry.register(definition)
        changed = replace(definition, name="momentum_1_renamed",
                          content_hash="")
        object.__setattr__(changed, "content_hash",
                           changed.compute_content_hash())
        with pytest.raises(ContractError) as err:
            registry.register(changed)
        assert "NEW VERSION" in str(err.value)

    def test_model_registry_rejects_same_content(self):
        env = trained_classifier()
        registry = env["registry"]
        model = env["outcome"].model
        with pytest.raises(ContractError):
            registry.register(model)

    def test_model_registry_get_requires_explicit_version(self):
        env = trained_classifier()
        with pytest.raises(ContractError) as err:
            env["registry"].get(env["outcome"].model.model_id, "999.0.0")
        assert "never 'latest'" in str(err.value)

    def test_model_hash_is_content_identity(self):
        env = trained_classifier()
        model = env["outcome"].model
        assert model.model_hash == model.compute_model_hash()
        # same inputs trained again -> same model hash
        env2 = trained_classifier()
        assert env2["outcome"].model.model_hash == model.model_hash

    def test_failed_training_has_reason_and_no_model(self):
        env = trained_classifier()
        registry = ModelRegistry()
        from core.intelligence.training import TrainingService
        trainer = TrainingService(registry, env["adapter"])
        outcome = trainer.train(
            config=env["config"], dataset=env["idataset"], rows=[],
            model_id="mdl_" + "6" * 32, model_version="1.0.0",
            started_at=T0, trainer_source="failure fixture",
            fail=lambda: None)
        assert outcome.run.status is TrainingStatus.FAILED
        assert outcome.run.failure_reason
        assert outcome.model is None

    def test_completed_requires_artifact(self):
        run = TrainingRun(
            training_run_id=new_identifier("training_run_id"),
            model_candidate_id="mdl_" + "7" * 32,
            dataset_hash="a" * 64, feature_schema_hash="b" * 64,
            label_hash=None, config_hash="c" * 64, code_hash="d" * 64,
            dependency_hash="e" * 64, seed=1, environment="RESEARCH",
            started_at=T0, status=TrainingStatus.COMPLETED,
            completed_at=T0, artifact_hash=None)
        with pytest.raises(ContractError):
            run.validate()


class TestEvaluation:
    def test_classification_metrics_confusion(self):
        metrics = classification_metrics(
            ["UP", "UP", "DOWN", "DOWN"],
            ["UP", "DOWN", "DOWN", "DOWN"])
        assert metrics["accuracy"] == 0.75
        assert metrics["per_class"]["UP"]["precision"] == 1.0
        assert "NOT_AVAILABLE" in str(metrics["roc_auc"])

    def test_regression_metrics(self):
        metrics = regression_metrics([1.0, 2.0, 3.0], [1.0, 2.0, 4.0])
        assert metrics["mae"] == pytest.approx(1 / 3)
        assert metrics["rmse"] >= metrics["mae"]
        assert metrics["r2"] is not None

    def test_mape_not_fabricated_on_zero_labels(self):
        metrics = regression_metrics([0.0, 1.0], [0.5, 1.0])
        assert "NOT_AVAILABLE" in str(metrics["mape"])

    def test_calibration_curve_buckets(self):
        curve = calibration_curve([0.1, 0.85, 0.9], [0.0, 1.0, 1.0])
        assert curve["bucket_count"] == 5
        assert len(curve["buckets"]) == 5

    def test_evaluation_insufficient_labels_is_explicit(self):
        env = trained_classifier()
        evaluation = env["evaluator"].evaluate(
            model_hash=env["outcome"].model.model_hash,
            dataset_hash=env["idataset"].content_hash,
            artifact_json=env["outcome"].artifact_json,
            rows=[{"features": {KEY_MOM: 1.0, KEY_VOL: 1.0},
                   "label": "UNKNOWN"}],
            environment="RESEARCH", created_at=T0)
        assert evaluation.metrics["INSUFFICIENT_LABELS"] is True

    def test_evaluation_real_metrics(self):
        env = trained_classifier()
        evaluation = env["evaluator"].evaluate(
            model_hash=env["outcome"].model.model_hash,
            dataset_hash=env["idataset"].content_hash,
            artifact_json=env["outcome"].artifact_json,
            rows=env["labelled"], environment="RESEARCH", created_at=T0)
        assert evaluation.metrics["accuracy"] >= 0.0
        assert evaluation.metrics_version == "1.0.0"


# --------------------------------------------------------------------- #
# Validation gate                                                        #
# --------------------------------------------------------------------- #
class TestValidationGate:
    def test_missing_evidence_is_unknown_and_blocks(self):
        v = validation_gate(model_hash="a" * 64, evidence={},
                            auditor="system", created_at=T0)
        from core.intelligence.contracts import EvidenceStatus
        assert v.overall is EvidenceStatus.UNKNOWN
        assert gate_blocks(v)

    def test_partial_evidence_blocks(self):
        v = validation_gate(model_hash="a" * 64,
                            evidence={"dataset_integrity": "PRESENT"},
                            auditor="system", created_at=T0)
        assert gate_blocks(v)

    def test_full_evidence_passes(self):
        v = validation_gate(
            model_hash="a" * 64,
            evidence={item: "PRESENT" for item in (
                "dataset_integrity", "leakage_audit", "feature_integrity",
                "model_artifact_integrity", "evaluation", "oos",
                "robustness", "replay", "provenance")},
            auditor="researcher", created_at=T0)
        assert not gate_blocks(v)

    def test_present_overall_forbidden_with_unknown_item(self):
        with pytest.raises(ContractError):
            ModelValidation(
                model_validation_id=new_identifier("model_validation_id"),
                model_hash="a" * 64,
                evidence={"dataset_integrity": "UNKNOWN",
                          **{k: "PRESENT" for k in (
                              "leakage_audit", "feature_integrity",
                              "model_artifact_integrity", "evaluation",
                              "oos", "robustness", "replay", "provenance")}},
                overall=None, auditor="x", created_at=T0,
                environment="RESEARCH").validate()


# --------------------------------------------------------------------- #
# Inference + safety                                                     #
# --------------------------------------------------------------------- #
class TestInference:
    def _snap(self, env, index=50):
        return env["engine"].snapshot(
            dataset=env["dataset"], definitions=(env["mom"], env["vol"]),
            symbol="EURUSD",
            as_of=env["dataset"].observations[index].available_time,
            environment="RESEARCH")

    def test_inference_produces_valid_result_with_provenance(self):
        env = trained_classifier()
        model = env["outcome"].model
        result = env["inferencer"].infer(
            model_id=model.model_id, model_version=model.model_version,
            inference_time=env["dataset"].observations[50].available_time,
            environment="RESEARCH", snapshot=self._snap(env))
        assert result.status is InferenceStatus.VALID
        for key in ("model_hash", "dataset_hash", "feature_schema_hash",
                    "code_hash", "artifact_hash"):
            assert result.provenance[key]

    def test_idempotency_same_request_same_result(self):
        env = trained_classifier()
        model = env["outcome"].model
        t = env["dataset"].observations[50].available_time
        r1 = env["inferencer"].infer(model_id=model.model_id,
                                     model_version=model.model_version,
                                     inference_time=t,
                                     environment="RESEARCH",
                                     snapshot=self._snap(env))
        r2 = env["inferencer"].infer(model_id=model.model_id,
                                     model_version=model.model_version,
                                     inference_time=t,
                                     environment="RESEARCH",
                                     snapshot=self._snap(env))
        assert r1.content_hash == r2.content_hash
        assert r1.inference_id == r2.inference_id

    def test_environment_mismatch_blocks(self):
        env = trained_classifier()
        model = env["outcome"].model
        with pytest.raises(ContractError) as err:
            env["inferencer"].infer(
                model_id=model.model_id, model_version=model.model_version,
                inference_time=env["dataset"].observations[50].available_time,
                environment="PAPER", snapshot=self._snap(env))
        assert "ENVIRONMENT_MISMATCH" in str(err.value)

    def test_pit_violation_blocks(self):
        env = trained_classifier()
        model = env["outcome"].model
        snap = self._snap(env)
        from dataclasses import replace
        future = replace(
            snap,
            available_at=snap.available_at + timedelta(hours=1),
            content_hash="")
        object.__setattr__(future, "content_hash",
                           future.compute_content_hash())
        with pytest.raises(ContractError) as err:
            env["inferencer"].infer(
                model_id=model.model_id, model_version=model.model_version,
                inference_time=snap.available_at, environment="RESEARCH",
                snapshot=future)
        assert "POINT_IN_TIME_VIOLATION" in str(err.value)

    def test_deterministic_same_inputs_same_output(self):
        env = trained_classifier()
        model = env["outcome"].model
        t = env["dataset"].observations[50].available_time
        r1 = env["inferencer"].infer(model_id=model.model_id,
                                     model_version=model.model_version,
                                     inference_time=t,
                                     environment="RESEARCH",
                                     snapshot=self._snap(env))
        engine2 = trained_classifier()
        r2 = engine2["inferencer"].infer(
            model_id=model.model_id, model_version=model.model_version,
            inference_time=t, environment="RESEARCH",
            snapshot=engine2["inferencer"]._registry.get(
                model.model_id, model.model_version) and self._snap(engine2))
        assert r1.output == r2.output

    def test_safety_drift_critical_blocks(self):
        env = trained_classifier()
        model = env["outcome"].model
        safety = AISafetyValidator(drift_status="CRITICAL")
        engine = InferenceEngine(env["registry"], env["adapter"], safety)
        with pytest.raises(ContractError) as err:
            engine.infer(
                model_id=model.model_id, model_version=model.model_version,
                inference_time=env["dataset"].observations[50].available_time,
                environment="RESEARCH", snapshot=self._snap(env))
        assert "DRIFT_CRITICAL" in str(err.value)

    def test_safety_drift_warning_limited(self):
        env = trained_classifier()
        model = env["outcome"].model
        safety = AISafetyValidator(drift_status="WARNING")
        engine = InferenceEngine(env["registry"], env["adapter"], safety)
        result = engine.infer(
            model_id=model.model_id, model_version=model.model_version,
            inference_time=env["dataset"].observations[50].available_time,
            environment="RESEARCH", snapshot=self._snap(env))
        assert result.provenance["safety_decision"] == "LIMITED"
        assert "advisory_only_reason" in result.output

    def test_safety_critical_unknown_blocks(self):
        env = trained_classifier()
        model = env["outcome"].model
        safety = AISafetyValidator(drift_status="UNKNOWN", ood_status="UNKNOWN")
        engine = InferenceEngine(env["registry"], env["adapter"], safety)
        with pytest.raises(ContractError) as err:
            engine.infer(
                model_id=model.model_id, model_version=model.model_version,
                inference_time=env["dataset"].observations[50].available_time,
                environment="RESEARCH", snapshot=self._snap(env))
        assert "UNKNOWN" in str(err.value)

    def test_live_requires_production_eligible(self):
        env = trained_classifier()
        model = env["outcome"].model  # status TRAINED
        # a model approved for LIVE inference cannot exist in Phase 7:
        # AI-011 forbids it; the gate must therefore always block LIVE
        safety = AISafetyValidator()
        assessment = safety.assess(
            model=model, environment="LIVE",
            inference_time=T0,
            snapshot_available_at=T0)
        assert assessment.decision is SafetyDecision.BLOCK


# --------------------------------------------------------------------- #
# Drift / OOD / health / explainability                                  #
# --------------------------------------------------------------------- #
class TestDriftOodHealth:
    def test_feature_drift_status_progression(self):
        engine = DriftEngine()
        report = engine.feature_drift(
            model_hash="a" * 64, feature_key=KEY_MOM,
            reference=[1.0] * 50, current=[5.0] * 50, created_at=T0)
        assert report.status is DriftStatus.CRITICAL
        stable = engine.feature_drift(
            model_hash="a" * 64, feature_key=KEY_MOM,
            reference=[1.0] * 50, current=[1.0] * 50, created_at=T0)
        assert stable.status is DriftStatus.NORMAL

    def test_drift_unknown_statistic(self):
        engine = DriftEngine()
        report = engine.feature_drift(
            model_hash="a" * 64, feature_key=KEY_MOM,
            reference=[], current=[], created_at=T0)
        assert report.status is DriftStatus.UNKNOWN

    def test_drift_thresholds_versioned(self):
        report = DriftEngine().feature_drift(
            model_hash="a" * 64, feature_key=KEY_MOM,
            reference=[1.0], current=[1.0], created_at=T0)
        assert report.threshold_version

    def test_ood_progression(self):
        engine = DriftEngine()
        training = [float(i) for i in range(20)]
        assert engine.ood(model_hash="a" * 64, training=training,
                          value=9.5).status is OODStatus.IN_DISTRIBUTION
        assert engine.ood(model_hash="a" * 64, training=training,
                          value=40.0).status is OODStatus.OOD_CRITICAL
        assert engine.ood(model_hash="a" * 64, training=[1.0],
                          value=1.0).status is OODStatus.UNKNOWN

    def test_drift_worst_fail_closed(self):
        from core.intelligence.outputs import DriftReport
        from core.intelligence.drift import DEFAULT_DRIFT_THRESHOLDS
        from core.intelligence.drift import DriftThresholds, THRESHOLD_VERSION
        normal = DriftReport(
            drift_report_id=new_identifier("drift_report_id"),
            model_hash="a" * 64, drift_type=DriftType.FEATURE_DRIFT,
            status=DriftStatus.NORMAL, threshold_version=THRESHOLD_VERSION,
            created_at=T0, environment="RESEARCH")
        unknown = DriftReport(
            drift_report_id=new_identifier("drift_report_id"),
            model_hash="a" * 64, drift_type=DriftType.LABEL_DRIFT,
            status=DriftStatus.UNKNOWN, threshold_version=THRESHOLD_VERSION,
            created_at=T0, environment="RESEARCH")
        assert drift_worst((normal, unknown)) is DriftStatus.UNKNOWN

    def test_health_is_operational_not_profitability(self):
        env = trained_classifier()
        service = ModelHealthService()
        health = service.record(
            model=env["outcome"].model, artifact_integrity="VERIFIED",
            dependency_integrity="VERIFIED",
            feature_compatibility="VERIFIED",
            inference_success_rate=0.99, error_rate=0.01,
            missing_features_rate=0.0,
            data_freshness=FreshnessStatus.FRESH, drift_status="NORMAL",
            unknown_rate=0.0, latency={"p50": 1.0},
            confidence_distribution={"n": 10}, created_at=T0,
            environment="RESEARCH")
        assert health.overall is HealthStatus.HEALTHY
        assert not any("pnl" in k or "profit" in k or "return" in k
                       for k in health.__dict__)

    def test_health_unknown_freshness_is_unknown(self):
        env = trained_classifier()
        service = ModelHealthService()
        health = service.record(
            model=env["outcome"].model, artifact_integrity="VERIFIED",
            dependency_integrity="VERIFIED",
            feature_compatibility="VERIFIED",
            inference_success_rate=0.99, error_rate=0.01,
            missing_features_rate=0.0,
            data_freshness=FreshnessStatus.UNKNOWN, drift_status="NORMAL",
            unknown_rate=0.0, latency={}, confidence_distribution={},
            created_at=T0, environment="RESEARCH")
        assert health.overall is HealthStatus.UNKNOWN

    def test_artifact_integrity_verified(self):
        env = trained_classifier()
        assert verify_artifact_integrity(
            env["outcome"].model) == "VERIFIED"

    def test_explain_with_limitations(self):
        env = trained_classifier()
        explainer = ExplainabilityEngine(env["adapter"])
        explanation = explainer.explain(
            model=env["outcome"].model,
            feature_values={KEY_MOM: 0.5, KEY_VOL: 0.2},
            feature_hash="a" * 64, inference_ref="inf_1",
            environment="RESEARCH", generated_at=T0)
        assert explanation.status is ExplanationStatus.AVAILABLE
        assert "causal" in explanation.limitations.lower()

    def test_explanation_unavailable_is_honest(self):
        from core.intelligence.adapter import BaselineRegressor
        env = trained_classifier()
        explainer = ExplainabilityEngine(BaselineRegressor())
        explanation = explainer.explain(
            model=env["outcome"].model, feature_values={},
            feature_hash="a" * 64, inference_ref=None,
            environment="RESEARCH", generated_at=T0)
        assert explanation.status is ExplanationStatus.UNAVAILABLE
        assert explanation.evidence == {}


# --------------------------------------------------------------------- #
# Outputs / proposals / candidates                                       #
# --------------------------------------------------------------------- #
class TestAIOutputs:
    def _result(self, env):
        model = env["outcome"].model
        snap = env["engine"].snapshot(
            dataset=env["dataset"], definitions=(env["mom"], env["vol"]),
            symbol="EURUSD",
            as_of=env["dataset"].observations[50].available_time,
            environment="RESEARCH")
        return env["inferencer"].infer(
            model_id=model.model_id, model_version=model.model_version,
            inference_time=snap.available_at, environment="RESEARCH",
            snapshot=snap), model

    def test_observation_builder(self):
        env = trained_classifier()
        result, model = self._result(env)
        observation = build_observation(
            result=result, model=model, environment="RESEARCH")
        observation.validate()
        assert observation.model_hash == model.model_hash

    def test_prediction_requires_horizon(self):
        env = trained_classifier()
        result, model = self._result(env)
        prediction = build_prediction(
            result=result, model=model,
            label_definition_ref="lbl_" + "3" * 32, horizon_bars=2,
            value="UP", environment="RESEARCH")
        prediction.validate()

    def test_recommendation_rejects_execution_verb(self):
        env = trained_classifier()
        result, model = self._result(env)
        with pytest.raises(ContractError) as err:
            build_recommendation(
                result=result, model=model, recommended_action="BUY",
                rationale="x", environment="RESEARCH")
        assert "AI-AUTHORITY" in str(err.value)

    def test_signal_builder(self):
        env = trained_classifier()
        result, model = self._result(env)
        signal = build_signal(
            result=result, model=model, signal_kind=SignalKind.REGIME,
            semantics="volatility regime classifier", timeframe="M1",
            environment="RESEARCH")
        signal.validate()

    def test_proposal_carries_evidence(self):
        env = trained_classifier()
        result, model = self._result(env)
        proposal = build_proposal(
            result=result, model=model,
            direction=ProposedDirection.LONG,
            rationale="momentum positive (SYNTHETIC)",
            environment="RESEARCH",
            evidence={"model_hash": model.model_hash})
        proposal.validate()
        with pytest.raises(ContractError):
            build_proposal(
                result=result, model=model,
                direction=ProposedDirection.LONG, rationale="no evidence",
                environment="RESEARCH", evidence={})

    def test_proposal_to_candidate_is_phase6_candidate(self):
        env = trained_classifier()
        result, model = self._result(env)
        proposal = build_proposal(
            result=result, model=model,
            direction=ProposedDirection.LONG,
            rationale="candidate fixture", environment="RESEARCH",
            evidence={"model_hash": model.model_hash})
        candidate = proposal_to_candidate(
            proposal=proposal, strategy_family="AI_MOMENTUM",
            logic_version="1.0.0", logic_hash="a" * 64,
            parameter_set={"threshold": "0.5"},
            dataset_reference=env["idataset"].intelligence_dataset_id,
            research_run_reference="rsr_" + "9" * 32, created_at=T0)
        assert candidate.status is CandidateStatus.DRAFT
        assert candidate.parameter_set["model_hash"] == model.model_hash


# --------------------------------------------------------------------- #
# Lifecycle / replay / selection / incidents / cards                     #
# --------------------------------------------------------------------- #
class TestLifecycle:
    def _service(self, env):
        return ModelLifecycleService(env["registry"], FakeAuditRepo())

    def test_promotion_requires_full_chain(self):
        env = trained_classifier()
        model = env["outcome"].model  # TRAINED
        service = self._service(env)
        actor = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        with pytest.raises(ContractError):
            service.transition(model.model_id, model.model_version,
                               "PRODUCTION_ELIGIBLE", actor, T0,
                               "skip", human_approval=True)

    def test_promotion_requires_human_approval(self):
        env = trained_classifier()
        model = env["outcome"].model
        service = self._service(env)
        actor = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        v1 = service.transition(model.model_id, model.model_version,
                                "VALIDATED", actor, T0, "validated").model_version
        v2 = service.transition(model.model_id, v1, "OOS_VALIDATED",
                                actor, T0, "oos").model_version
        with pytest.raises(ContractError) as err:
            service.transition(model.model_id, v2, "RESEARCH_APPROVED",
                               actor, T0, "promotion without approval",
                               human_approval=False)
        assert "HUMAN_APPROVAL" in str(err.value)

    def test_unauthorized_actor_cannot_promote(self):
        env = trained_classifier()
        model = env["outcome"].model
        service = self._service(env)
        viewer = ModelActor("usr_" + "2" * 32, Role.VIEWER)
        approver = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        version = service.transition(model.model_id, model.model_version,
                                     "VALIDATED", approver, T0,
                                     "validated").model_version
        version = service.transition(model.model_id, version,
                                     "OOS_VALIDATED", approver, T0,
                                     "oos").model_version
        with pytest.raises(ContractError) as err:
            service.transition(model.model_id, version, "RESEARCH_APPROVED",
                               viewer, T0, "viewer tries to approve",
                               human_approval=True)
        assert "PERM" in str(err.value)

    def test_full_chain_with_human_approval(self):
        env = trained_classifier()
        model = env["outcome"].model
        service = self._service(env)
        approver = ModelActor("usr_" + "1" * 32, Role.APPROVER)

        def step(version, target, reason, approval=False):
            return service.transition(model.model_id, version, target,
                                      approver, T0, reason,
                                      human_approval=approval).model_version
        version = step(model.model_version, "VALIDATED", "validated")
        version = step(version, "OOS_VALIDATED", "oos")
        version = step(version, "RESEARCH_APPROVED", "research signoff",
                       approval=True)
        version = step(version, "SHADOW", "shadow", approval=True)
        version = step(version, "PAPER", "paper", approval=True)
        version = step(version, "DEMO", "demo", approval=True)
        final = service.transition(model.model_id, version,
                                   "PRODUCTION_ELIGIBLE", approver, T0,
                                   "production", human_approval=True)
        assert final.status == "PRODUCTION_ELIGIBLE"
        # every historical version remains queryable (immutable chain)
        assert env["registry"].get(model.model_id,
                                   model.model_version) is not None

    def test_suspend_keeps_model_queryable(self):
        env = trained_classifier()
        model = env["outcome"].model
        service = self._service(env)
        operator = ModelActor("usr_" + "3" * 32, Role.OPERATOR)
        approver = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        version = service.transition(model.model_id, model.model_version,
                                     "VALIDATED", approver, T0,
                                     "validated").model_version
        version = service.transition(model.model_id, version, "OOS_VALIDATED",
                                     approver, T0, "oos").model_version
        version = service.transition(model.model_id, version,
                                     "RESEARCH_APPROVED", approver, T0,
                                     "research", human_approval=True).model_version
        version = service.transition(model.model_id, version, "SHADOW",
                                     approver, T0, "shadow",
                                     human_approval=True).model_version
        suspended = service.suspend(model.model_id, version, operator, T0,
                                    "drift evidence")
        assert suspended.status == "SUSPENDED"
        assert env["registry"].get(model.model_id,
                                   model.model_version) is not None


class FakeAuditRepo(AuditRepository):
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)

    def get_by_id(self, audit_id):
        for record in self.records:
            if record.audit_id == audit_id:
                return record
        raise KeyError(audit_id)

    def iter_by_correlation_id(self, correlation_id):
        return iter([r for r in self.records
                     if r.correlation_id == correlation_id])


class TestReplay:
    def test_replay_reproduces_outputs(self):
        env = trained_classifier()
        model = env["outcome"].model
        adapter = IntelligenceReplayAdapter(env["engine"], env["adapter"])
        moments = [env["dataset"].observations[i].available_time
                   for i in range(10, 15)]
        outputs = [env["adapter"].predict(
            env["outcome"].artifact_json,
            env["engine"].snapshot(
                dataset=env["dataset"],
                definitions=(env["mom"], env["vol"]), symbol="EURUSD",
                as_of=m, environment="REPLAY").values)
            for m in moments]
        replay = adapter.replay(
            model=model, source=env["dataset"],
            definitions=(env["mom"], env["vol"]), symbol="EURUSD",
            moments=moments, original_outputs=outputs,
            config_hash=env["config"].config_hash, created_at=T0)
        from core.intelligence.outputs import ReplayMatch
        assert replay.match is ReplayMatch.MATCH

    def test_replay_mismatch_detected(self):
        env = trained_classifier()
        model = env["outcome"].model
        adapter = IntelligenceReplayAdapter(env["engine"], env["adapter"])
        moments = [env["dataset"].observations[i].available_time
                   for i in range(10, 12)]
        outputs = [{"label": "UP", "probability": 0.99}] * len(moments)
        replay = adapter.replay(
            model=model, source=env["dataset"],
            definitions=(env["mom"], env["vol"]), symbol="EURUSD",
            moments=moments, original_outputs=outputs,
            config_hash=env["config"].config_hash, created_at=T0)
        from core.intelligence.outputs import ReplayMatch
        assert replay.match is ReplayMatch.MISMATCH
        assert replay.differences

    def test_diff_classifies_expected_version_difference(self):
        left = [{"label": "UP", "probability": 0.6}]
        right = [{"label": "DOWN", "probability": 0.4}]
        diffs = diff_inferences(left, right, left_model_hash="a" * 64,
                                right_model_hash="b" * 64)
        from core.intelligence.outputs import ReplayDiffKind
        assert diffs and diffs[0].kind is \
            ReplayDiffKind.EXPECTED_VERSION_DIFFERENCE

    def test_diff_flags_nondeterminism_for_same_model(self):
        left = [{"label": "UP", "probability": 0.6}]
        right = [{"label": "UP", "probability": 0.7}]
        from core.intelligence.outputs import ReplayDiffKind
        diffs = diff_inferences(left, right, left_model_hash="a" * 64,
                                right_model_hash="a" * 64)
        assert diffs and diffs[0].kind is \
            ReplayDiffKind.NONDETERMINISM_SUSPECTED

    def test_model_comparison_never_picks_winner(self):
        evidence = compare_models(
            left_outputs=[{"label": "UP"}], right_outputs=[{"label": "DOWN"}],
            left_model_hash="a" * 64, right_model_hash="b" * 64)
        assert evidence["decision"] == "HUMAN_REVIEW_REQUIRED (no automatic winner)"


class TestSelectionCardsIncidents:
    def test_selection_requires_policy_and_actor(self):
        selection = ModelSelection(
            model_selection_id=new_identifier("model_selection_id"),
            selection_policy="selection-policy-1.0.0 (stability-first)",
            candidate_models=("a" * 64, "b" * 64),
            selected_model_hash="a" * 64,
            decision_actor="usr_" + "1" * 32, decided_at=T0,
            environment="RESEARCH")
        selection.validate()

    def test_selection_selected_must_be_candidate(self):
        with pytest.raises(ContractError):
            ModelSelection(
                model_selection_id=new_identifier("model_selection_id"),
                selection_policy="p", candidate_models=("a" * 64,),
                selected_model_hash="c" * 64,
                decision_actor="usr_" + "1" * 32, decided_at=T0,
                environment="RESEARCH").validate()

    def test_model_card_immutable_hash(self):
        card = ModelCard(
            model_hash="a" * 64, purpose="SYNTHETIC research model",
            intended_use="research evidence only",
            prohibited_use="live trading, execution, risk override",
            training_data_ref="ids_" + "1" * 32,
            feature_set=(KEY_MOM, KEY_VOL),
            limitations="deterministic statistical estimator on SYNTHETIC "
                        "data; not predictive of real markets",
            metrics={"accuracy": 0.63}, oos_summary={"status": "PASS"},
            robustness_summary={"status": "STABLE"},
            known_failure_cases=("regime change", "flat markets"),
            uncertainty_summary={"status": "NOT_AVAILABLE"},
            drift_baseline={"status": "NORMAL"}, environment="RESEARCH",
            model_version="1.0.0", provenance={"fixture": True},
            card_hash="", created_at=T0)
        object.__setattr__(card, "card_hash", card.compute_card_hash())
        card.validate()
        assert "prohibited_use" in card.to_dict()

    def test_incident_preserves_evidence(self):
        incident = AIIncident(
            ai_incident_id=new_identifier("ai_incident_id"),
            incident_type=IncidentType.FEATURE_LEAKAGE,
            model_hash="a" * 64, detected_at=T0, environment="RESEARCH",
            evidence={"feature": KEY_MOM, "detail": "future window"},
            status=IncidentStatus.OPEN,
            description="future feature injected in fixture")
        incident.validate()
        with pytest.raises(ContractError):
            AIIncident(
                ai_incident_id=new_identifier("ai_incident_id"),
                incident_type=IncidentType.OOD, model_hash=None,
                detected_at=T0, environment="RESEARCH", evidence={},
                status=IncidentStatus.OPEN,
                description="no evidence").validate()


class TestEvents:
    def test_phase7_event_types_exist(self):
        assert len(PHASE7_EVENT_TYPES) == 13
        assert EventType.FEATURE_CREATED.value == "FEATURE_CREATED"
        assert EventType.MODEL_REPLAYED.value == "MODEL_REPLAYED"

    def test_emit_produces_valid_event(self):
        event = emit(EventType.TRAINING_COMPLETED,
                     payload={"model_hash": "a" * 64},
                     environment="RESEARCH", entity_id="trg_" + "1" * 32)
        event.validate()
        assert event.source == "core.intelligence"

    def test_emit_rejects_wrong_environment(self):
        with pytest.raises(ContractError):
            emit(EventType.INFERENCE_COMPLETED, payload={},
                 environment="NOT_AN_ENV", entity_id="inf_" + "1" * 32)
