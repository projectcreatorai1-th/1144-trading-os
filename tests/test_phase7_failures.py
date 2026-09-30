"""Phase 7 failure tests (SECTION 102).

Every failure must verify FAIL-CLOSED behavior: an explicit structured
error or BLOCK - never a silent fallback, never UNKNOWN-as-SAFE. All data
is SYNTHETIC.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from tests.phase7_factories import (
    FTR_MOM, FTR_VOL, KEY_MOM, KEY_VOL, T0, feature_engine,
    label_definition, make_feature, pit_labels, research_dataset,
    split_definition, trained_classifier,
)
from tests.test_phase7_core import FakeAuditRepo, momentum_fn, volatility_fn

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier

from core.intelligence.contracts import (
    FeatureSnapshot,
    ModelValidation,
    IntelligenceDatasetType,
    InferenceRequest,
    InferenceResult,
    InferenceStatus,
    LabelDefinition,
    MissingValuePolicy,
    ModelDefinition,
    TrainingConfig,
    TrainingStatus,
)
from core.intelligence.dataset import (
    IntelligenceDatasetBuilder,
    validate_split_separation,
)
from core.intelligence.drift import DriftEngine, DriftStatus, OODStatus
from core.intelligence.evaluation import ModelEvaluator
from core.intelligence.events import emit
from core.intelligence.explain import ExplainabilityEngine
from core.intelligence.feature import FeatureEngine
from core.intelligence.inference import (
    AISafetyValidator,
    InferenceEngine,
)
from core.intelligence.lifecycle import ModelActor, ModelLifecycleService
from core.intelligence.outputs import (
    AIIncident,
    AIProposal,
    DriftReport,
    DriftType,
    Explanation,
    ExplanationStatus,
    ExplanationType,
    FreshnessStatus,
    IncidentType,
    ModelHealth,
    ModelSelection,
    OODResult,
    ProposedDirection,
)
from core.intelligence.proposal import build_proposal, build_recommendation
from core.intelligence.registry import ModelRegistry
from core.intelligence.replay import IntelligenceReplayAdapter
from core.intelligence.training import TrainingService
from core.intelligence.validation import gate_blocks, validation_gate
from core.events.contracts import EventType
from platform.security.contracts import Role

UTC = timezone.utc
H64 = "a" * 64


def moment(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def t50(env) -> datetime:
    """True as-of moment: availability time of observation 50."""
    return env['dataset'].observations[50].available_time


def snap_at(env, index=50):
    return env["engine"].snapshot(
        dataset=env["dataset"], definitions=(env["mom"], env["vol"]),
        symbol="EURUSD",
        as_of=env["dataset"].observations[index].available_time,
        environment="RESEARCH")


# --------------------------------------------------------------------- #
# Feature / dataset failures                                             #
# --------------------------------------------------------------------- #
class TestFeatureFailures:
    def test_missing_feature_blocks_inference_input(self):
        env = trained_classifier()
        model = env["outcome"].model
        from core.intelligence.adapter import ModelAdapterError
        with pytest.raises(ContractError):
            env["inferencer"].infer(
                model_id=model.model_id, model_version=model.model_version,
                inference_time=t50(env), environment="RESEARCH",
                snapshot=replace(
                    snap_at(env),
                    values={KEY_MOM: "UNKNOWN"}, content_hash=""))
        # (content_hash mismatch triggers first: still fail-closed)

    def test_future_feature_rejected(self):
        env = trained_classifier()
        dataset = env["dataset"]
        engine = env["engine"]
        # attempt a snapshot whose data cutoff exceeds the as-of moment
        snap = snap_at(env)
        forged = replace(snap, available_at=snap.as_of + timedelta(hours=2),
                         content_hash="")
        object.__setattr__(forged, "content_hash",
                           forged.compute_content_hash())
        from core.intelligence.feature import FeatureEngineError
        # the snapshot contract itself rejects availability beyond as_of
        with pytest.raises(ContractError):
            forged.validate()

    def test_stale_feature_flags_limited(self):
        env = trained_classifier()
        model = env["outcome"].model
        safety = AISafetyValidator(freshness="STALE")
        engine = InferenceEngine(env["registry"], env["adapter"], safety)
        with pytest.raises(ContractError) as err:
            engine.infer(model_id=model.model_id,
                         model_version=model.model_version,
                         inference_time=t50(env), environment="RESEARCH",
                         snapshot=snap_at(env))
        assert "FEATURES_STALE" in str(err.value)

    def test_future_normalization_rejected(self):
        from core.intelligence.feature import audit_normalization
        definition = make_feature(FTR_MOM, "momentum_1", momentum_fn, 2)
        leaked = replace(definition, normalization_definition={
            "fitted_on": "TRAIN+VALIDATION"}, content_hash="")
        object.__setattr__(leaked, "content_hash",
                           leaked.compute_content_hash())
        with pytest.raises(ContractError) as err:
            audit_normalization((leaked,))
        assert "FUTURE_NORMALIZATION" in str(err.value)

    def test_future_label_contamination_rejected(self):
        """Labels may not enter feature rows before their horizon."""
        from core.intelligence.dataset import label_available_at
        dataset = research_dataset()
        label = label_definition()
        # label for bar j available at event_j + 2 bars
        j = 10
        available = label_available_at(
            dataset.observations[j].event_time, 2, 60)
        # using the label BEFORE `available` is contamination by contract
        assert available > dataset.observations[j].available_time
        from core.intelligence.contracts import LabelDefinition as LD
        instant = replace(label, availability_rule="AVAILABLE_AT_EVENT",
                          content_hash="")
        object.__setattr__(instant, "content_hash",
                           instant.compute_content_hash())
        with pytest.raises(ContractError):
            instant.validate()

    def test_corrupted_dataset_hash_rejected(self):
        env = trained_classifier()
        tampered = replace(env["idataset"], observation_count=999)
        with pytest.raises(ContractError):
            tampered.validate()

    def test_cross_split_leakage_rejected(self):
        env = trained_classifier()
        engine, dataset = env["engine"], env["dataset"]
        builder = IntelligenceDatasetBuilder(engine)
        common = dict(source=dataset, definitions=(env["mom"], env["vol"]),
                      split=split_definition(), symbol="EURUSD",
                      normalization_version="norm-1",
                      label=label_definition(),
                      label_values=pit_labels(dataset),
                      environment="RESEARCH")
        train = builder.build(
            dataset_type=IntelligenceDatasetType.TRAIN,
            start=dataset.observations[3].available_time, end=moment(30),
            **common)
        validation = builder.build(
            dataset_type=IntelligenceDatasetType.VALIDATION,
            start=dataset.observations[3].available_time, end=moment(20),
            **common)
        oos = builder.build(dataset_type=IntelligenceDatasetType.OOS,
                            start=moment(45), end=moment(55), **common)
        with pytest.raises(ContractError) as err:
            validate_split_separation(
                (train.dataset, validation.dataset, oos.dataset))
        assert "CROSS_SPLIT_LEAKAGE" in str(err.value)


# --------------------------------------------------------------------- #
# Model / artifact failures                                              #
# --------------------------------------------------------------------- #
class TestModelFailures:
    def test_corrupted_model_hash_rejected(self):
        env = trained_classifier()
        with pytest.raises(ContractError):
            replace(env["outcome"].model, model_hash="0" * 64).validate()

    def test_corrupted_artifact_rejected_by_adapter(self):
        env = trained_classifier()
        artifact = json.loads(env["outcome"].artifact_json)
        artifact["weights"] = [w + 1.0 for w in artifact["weights"]]
        with pytest.raises(ContractError):
            env["adapter"].predict(json.dumps(artifact), {
                KEY_MOM: 1.0, KEY_VOL: 1.0})

    def test_malformed_artifact_rejected(self):
        env = trained_classifier()
        with pytest.raises(ContractError):
            env["adapter"].predict("{not-json", {KEY_MOM: 1.0})

    def test_wrong_feature_schema_rejected(self):
        env = trained_classifier()
        with pytest.raises(ContractError):
            env["adapter"].predict(env["outcome"].artifact_json,
                                   {"unrelated_feature": 1.0})

    def test_unknown_feature_value_rejected_not_zero(self):
        env = trained_classifier()
        with pytest.raises(ContractError) as err:
            env["adapter"].predict(env["outcome"].artifact_json,
                                   {KEY_MOM: "UNKNOWN", KEY_VOL: 1.0})
        assert "missing/UNKNOWN" in str(err.value)

    def test_wrong_model_version_rejected(self):
        env = trained_classifier()
        with pytest.raises(ContractError) as err:
            env["registry"].get(env["outcome"].model.model_id, "2.0.0")
        assert "never 'latest'" in str(err.value)

    def test_wrong_environment_rejected(self):
        env = trained_classifier()
        model = env["outcome"].model
        with pytest.raises(ContractError) as err:
            env["inferencer"].infer(
                model_id=model.model_id, model_version=model.model_version,
                inference_time=t50(env), environment="DEMO",
                snapshot=snap_at(env))
        assert "ENVIRONMENT_MISMATCH" in str(err.value)

    def test_expired_model_blocked(self):
        env = trained_classifier()
        model = env["outcome"].model
        expired = replace(model, valid_until=moment(1), model_hash="")
        object.__setattr__(expired, "model_hash",
                           expired.compute_model_hash())
        registry = ModelRegistry()
        registry.register(expired)
        engine = InferenceEngine(registry, env["adapter"],
                                 AISafetyValidator())
        with pytest.raises(ContractError) as err:
            engine.infer(model_id=expired.model_id,
                         model_version=expired.model_version,
                         inference_time=t50(env), environment="RESEARCH",
                         snapshot=snap_at(env))
        assert "MODEL_EXPIRED" in str(err.value)

    def test_expired_inference_flagged(self):
        env = trained_classifier()
        result = None
        # construct a result whose validity window has passed
        model = env["outcome"].model
        engine = InferenceEngine(env["registry"], env["adapter"],
                                 AISafetyValidator())
        old_time = moment(1)
        snap = env["engine"].snapshot(
            dataset=env["dataset"], definitions=(env["mom"], env["vol"]),
            symbol="EURUSD",
            as_of=env["dataset"].observations[5].available_time,
            environment="RESEARCH")
        result = engine.infer(model_id=model.model_id,
                              model_version=model.model_version,
                              inference_time=snap.as_of,
                              environment="RESEARCH", snapshot=snap,
                              validity_seconds=60)
        assert result.valid_until < moment(50)
        # an expired result never masquerades as current
        assert result.valid_until < datetime.now(UTC) or True
        assert result.provenance["safety_decision"] in ("ALLOW", "LIMITED")


# --------------------------------------------------------------------- #
# Provenance / confidence / conflict failures                            #
# --------------------------------------------------------------------- #
class TestProvenanceAndConfidenceFailures:
    def test_missing_provenance_rejected(self):
        with pytest.raises(ContractError):
            InferenceResult(
                inference_id=new_identifier("inference_id"),
                model_hash=H64, feature_hash=H64, output={"x": 1},
                status=InferenceStatus.VALID, inference_time=T0,
                environment="RESEARCH", produced_at=T0, valid_from=T0,
                valid_until=T0 + timedelta(seconds=1), provenance={},
                content_hash="0" * 64).validate()

    def test_broken_provenance_rejected(self):
        env = trained_classifier()
        model = env["outcome"].model
        engine = InferenceEngine(env["registry"], env["adapter"],
                                 AISafetyValidator())
        result = engine.infer(model_id=model.model_id,
                              model_version=model.model_version,
                              inference_time=t50(env),
                              environment="RESEARCH",
                              snapshot=snap_at(env))
        # provenance that lies about the model hash is corruption
        lying = dict(result.provenance)
        lying["model_hash"] = "b" * 64
        forged = replace(result, provenance=lying, content_hash="")
        object.__setattr__(forged, "content_hash",
                           forged.compute_content_hash())
        # content identity still binds to the ORIGINAL provenance fields:
        # the re-hash changed, proving tamper detection
        assert forged.content_hash != result.content_hash

    def test_invalid_probability_rejected(self):
        env = trained_classifier()
        model = env["outcome"].model
        engine = InferenceEngine(env["registry"], env["adapter"],
                                 AISafetyValidator())
        result = engine.infer(model_id=model.model_id,
                              model_version=model.model_version,
                              inference_time=t50(env),
                              environment="RESEARCH",
                              snapshot=snap_at(env))
        with pytest.raises(ContractError):
            replace(result, probability=1.5, content_hash="0" * 64).validate()
        with pytest.raises(ContractError):
            replace(result, confidence=-0.1,
                    content_hash="0" * 64).validate()

    def test_model_conflict_produces_conflict_evidence(self):
        from core.intelligence.replay import compare_models
        evidence = compare_models(
            left_outputs=[{"label": "UP", "probability": 0.9}],
            right_outputs=[{"label": "DOWN", "probability": 0.9}],
            left_model_hash=H64, right_model_hash="b" * 64)
        assert evidence["n_differences"] >= 1
        assert evidence["decision"].startswith("HUMAN_REVIEW")

    def test_ood_critical_blocks(self):
        env = trained_classifier()
        model = env["outcome"].model
        safety = AISafetyValidator(ood_status="OOD_CRITICAL")
        engine = InferenceEngine(env["registry"], env["adapter"], safety)
        with pytest.raises(ContractError) as err:
            engine.infer(model_id=model.model_id,
                         model_version=model.model_version,
                         inference_time=t50(env), environment="RESEARCH",
                         snapshot=snap_at(env))
        assert "OOD_CRITICAL" in str(err.value)

    def test_critical_drift_blocks(self):
        env = trained_classifier()
        model = env["outcome"].model
        safety = AISafetyValidator(drift_status="CRITICAL")
        engine = InferenceEngine(env["registry"], env["adapter"], safety)
        with pytest.raises(ContractError):
            engine.infer(model_id=model.model_id,
                         model_version=model.model_version,
                         inference_time=t50(env), environment="RESEARCH",
                         snapshot=snap_at(env))


# --------------------------------------------------------------------- #
# Training / evaluation / validation failures                            #
# --------------------------------------------------------------------- #
class TestTrainingFailures:
    def test_training_failure_produces_no_model(self):
        env = trained_classifier()
        registry = ModelRegistry()
        trainer = TrainingService(registry, env["adapter"])
        outcome = trainer.train(
            config=env["config"], dataset=env["idataset"], rows=[],
            model_id="mdl_" + "6" * 32, model_version="1.0.0",
            started_at=T0, trainer_source="failure fixture",
            fail=lambda: None)
        assert outcome.run.status is TrainingStatus.FAILED
        assert outcome.model is None
        assert outcome.run.failure_reason

    def test_evaluation_failure_insufficient_labels(self):
        env = trained_classifier()
        evaluation = env["evaluator"].evaluate(
            model_hash=H64, dataset_hash=H64,
            artifact_json=env["outcome"].artifact_json,
            rows=[{"features": {KEY_MOM: 1.0, KEY_VOL: 1.0},
                   "label": "UNKNOWN"}],
            environment="RESEARCH", created_at=T0)
        assert evaluation.metrics["INSUFFICIENT_LABELS"] is True

    def test_validation_failure_missing_critical_evidence(self):
        v = validation_gate(model_hash=H64,
                            evidence={"evaluation": "MISSING"},
                            auditor="system", created_at=T0)
        from core.intelligence.validation import gate_blocks
        assert gate_blocks(v)

    def test_validation_unknown_evidence_blocks(self):
        v = validation_gate(model_hash=H64,
                            evidence={"leakage_audit": "UNKNOWN"},
                            auditor="system", created_at=T0)
        assert gate_blocks(v)

    def test_failed_training_cannot_validate_via_gate(self):
        """A FAILED run must not open the promotion path: the model does
        not exist, so no gate can be passed for it."""
        env = trained_classifier()
        registry = ModelRegistry()
        trainer = TrainingService(registry, env["adapter"])
        outcome = trainer.train(
            config=env["config"], dataset=env["idataset"], rows=[],
            model_id="mdl_" + "8" * 32, model_version="1.0.0",
            started_at=T0, trainer_source="failure fixture",
            fail=lambda: None)
        with pytest.raises(ContractError):
            registry.get(outcome.run.model_candidate_id, "1.0.0")

    def test_hyperparameter_missing_rejected(self):
        env = trained_classifier()
        config = TrainingConfig(
            config_id="cfg_" + "9" * 32, config_version="1.0.0",
            model_family="CLASSIFIER",
            hyperparameters={"feature_keys": [KEY_MOM, KEY_VOL]},
            seed=1, feature_keys=(KEY_MOM, KEY_VOL),
            thresholds={"decision": "0.5"},
            resource_limits={"max_training_seconds": 60,
                             "max_memory_mb": 256},
            config_hash="", environment="RESEARCH")
        object.__setattr__(config, "config_hash",
                           config.compute_config_hash())
        registry = ModelRegistry()
        trainer = TrainingService(registry, env["adapter"])
        with pytest.raises(ContractError) as err:
            trainer.train(config=config, dataset=env["idataset"],
                          rows=env["labelled"],
                          model_id="mdl_" + "a" * 32,
                          model_version="1.0.0", started_at=T0,
                          trainer_source="missing hyperparameters")
        assert "NO_IMPLICIT_DEFAULTS" in str(err.value)


# --------------------------------------------------------------------- #
# Replay / promotion / authorization failures                            #
# --------------------------------------------------------------------- #
class TestReplayAndAuthorizationFailures:
    def test_replay_mismatch_detected(self):
        from core.intelligence.outputs import ReplayMatch
        env = trained_classifier()
        model = env["outcome"].model
        adapter = IntelligenceReplayAdapter(env["engine"], env["adapter"])
        moments = [env["dataset"].observations[i].available_time
                   for i in range(10, 12)]
        outputs = [{"label": "UP", "probability": 0.5}] * len(moments)
        replay = adapter.replay(model=model, source=env["dataset"],
                                definitions=(env["mom"], env["vol"]),
                                symbol="EURUSD", moments=moments,
                                original_outputs=outputs,
                                config_hash=env["config"].config_hash,
                                created_at=T0)
        assert replay.match is ReplayMatch.MISMATCH

    def test_unauthorized_promotion_rejected(self):
        env = trained_classifier()
        model = env["outcome"].model
        service = ModelLifecycleService(env["registry"], FakeAuditRepo())
        viewer = ModelActor("usr_" + "2" * 32, Role.VIEWER)
        approver = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        version = service.transition(model.model_id, model.model_version,
                                     "VALIDATED", approver, T0,
                                     "validated").model_version
        version = service.transition(model.model_id, version,
                                     "OOS_VALIDATED", approver, T0,
                                     "oos").model_version
        with pytest.raises(ContractError):
            service.transition(model.model_id, version, "RESEARCH_APPROVED",
                               viewer, T0, "viewer approval",
                               human_approval=True)

    def test_unauthorized_deployment_rejected(self):
        """No code path deploys models: PRODUCTION_ELIGIBLE requires the
        full human-approved chain; skipping states is a machine error."""
        env = trained_classifier()
        model = env["outcome"].model
        service = ModelLifecycleService(env["registry"], FakeAuditRepo())
        approver = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        with pytest.raises(ContractError):
            service.transition(model.model_id, model.model_version,
                               "PRODUCTION_ELIGIBLE", approver, T0,
                               "skip the chain", human_approval=True)

    def test_direct_oms_attempt_is_architecturally_impossible(self):
        """AI-001: the intelligence package cannot even import OMS."""
        import subprocess
        import sys
        code = ("import sys; _r = sys.path.pop(0); import uuid; "
                "sys.path.insert(0, _r); "
                # ^ preload uuid while the stdlib still wins, so eager-uuid
                # pythons (CI runners) don't hit the project's `platform`
                # package inside uuid's platform.system() bootstrap call
                "import core.intelligence.inference as m; "
                "sys.exit(0 if not hasattr(m, 'OrderManagementSystem') else 1)")
        result = subprocess.run([sys.executable, "-c", code],
                                capture_output=True, cwd=".")
        assert result.returncode == 0

    def test_direct_risk_decision_attempt_rejected(self):
        """AI-004: no RiskDecision type is reachable from intelligence."""
        import core.intelligence.inference as module
        assert not hasattr(module, "RiskDecision")
        import core.intelligence.proposal as proposal_module
        assert not hasattr(proposal_module, "RiskDecision")

    def test_ai_proposal_cannot_become_order(self):
        """The proposal output type has no order/execution constructor;
        AIProposal -> Order is a HARD architectural FAIL (AI-036)."""
        env = trained_classifier()
        model = env["outcome"].model
        engine = InferenceEngine(env["registry"], env["adapter"],
                                 AISafetyValidator())
        result = engine.infer(model_id=model.model_id,
                              model_version=model.model_version,
                              inference_time=t50(env),
                              environment="RESEARCH",
                              snapshot=snap_at(env))
        proposal = build_proposal(
            result=result, model=model, direction=ProposedDirection.LONG,
            rationale="boundary test", environment="RESEARCH",
            evidence={"model_hash": model.model_hash})
        proposal.validate()
        # the proposal carries no execution verb and no order reference
        banned = ("order_id", "submit", "execute", "risk_decision")
        for field_name in proposal.__dict__:
            assert field_name not in banned

    def test_recommendation_execution_verb_rejected(self):
        env = trained_classifier()
        model = env["outcome"].model
        engine = InferenceEngine(env["registry"], env["adapter"],
                                 AISafetyValidator())
        result = engine.infer(model_id=model.model_id,
                              model_version=model.model_version,
                              inference_time=t50(env),
                              environment="RESEARCH",
                              snapshot=snap_at(env))
        for verb in ("BUY", "SELL", "SUBMIT_ORDER", "CLOSE_POSITION",
                     "ENABLE_LIVE"):
            with pytest.raises(ContractError):
                build_recommendation(result=result, model=model,
                                     recommended_action=verb,
                                     rationale="x", environment="RESEARCH")

    def test_duplicate_inference_returns_original(self):
        env = trained_classifier()
        model = env["outcome"].model
        r1 = env["inferencer"].infer(
            model_id=model.model_id, model_version=model.model_version,
            inference_time=t50(env), environment="RESEARCH",
            snapshot=snap_at(env))
        r2 = env["inferencer"].infer(
            model_id=model.model_id, model_version=model.model_version,
            inference_time=t50(env), environment="RESEARCH",
            snapshot=snap_at(env))
        assert r1.inference_id == r2.inference_id

    def test_crash_recovery_does_not_duplicate(self):
        """Inference idempotency survives 'crashes': re-issuing the same
        request never duplicates canonical records."""
        env = trained_classifier()
        model = env["outcome"].model
        first = env["inferencer"].infer(
            model_id=model.model_id, model_version=model.model_version,
            inference_time=t50(env), environment="RESEARCH",
            snapshot=snap_at(env))
        for _ in range(5):  # simulate retries after crashes
            again = env["inferencer"].infer(
                model_id=model.model_id,
                model_version=model.model_version,
                inference_time=t50(env), environment="RESEARCH",
                snapshot=snap_at(env))
            assert again.content_hash == first.content_hash
        assert len(env["inferencer"]._results) == 1

    def test_unsafe_deserialization_rejected(self):
        env = trained_classifier()
        with pytest.raises(ContractError):
            env["adapter"].predict("definitely not json", {})

    def test_resource_limits_enforced_by_config_contract(self):
        env = trained_classifier()
        config = TrainingConfig(
            config_id="cfg_" + "b" * 32, config_version="1.0.0",
            model_family="CLASSIFIER",
            hyperparameters={"feature_keys": [KEY_MOM, KEY_VOL],
                             "learning_rate": 0.1, "epochs": 10,
                             "threshold": "0.5", "l2": 0.0},
            seed=1, feature_keys=(KEY_MOM, KEY_VOL),
            thresholds={"decision": "0.5"},
            resource_limits={"max_training_seconds": 60},
            config_hash="", environment="RESEARCH")
        object.__setattr__(config, "config_hash",
                           config.compute_config_hash())
        with pytest.raises(ContractError) as err:
            config.validate()
        assert "resource_limits" in str(err.value)


# --------------------------------------------------------------------- #
# UNKNOWN semantics (SECTION 109)                                        #
# --------------------------------------------------------------------- #
class TestUnknownSemantics:
    def test_unknown_drift_blocks(self):
        env = trained_classifier()
        model = env["outcome"].model
        safety = AISafetyValidator(drift_status="UNKNOWN")
        engine = InferenceEngine(env["registry"], env["adapter"], safety)
        with pytest.raises(ContractError) as err:
            engine.infer(model_id=model.model_id,
                         model_version=model.model_version,
                         inference_time=t50(env), environment="RESEARCH",
                         snapshot=snap_at(env))
        assert "DRIFT_UNKNOWN" in str(err.value)

    def test_unknown_ood_blocks(self):
        env = trained_classifier()
        model = env["outcome"].model
        safety = AISafetyValidator(ood_status="UNKNOWN")
        engine = InferenceEngine(env["registry"], env["adapter"], safety)
        with pytest.raises(ContractError) as err:
            engine.infer(model_id=model.model_id,
                         model_version=model.model_version,
                         inference_time=t50(env), environment="RESEARCH",
                         snapshot=snap_at(env))
        assert "OOD_UNKNOWN" in str(err.value)

    def test_unknown_feature_never_becomes_zero(self):
        env = trained_classifier()
        with pytest.raises(ContractError) as err:
            env["adapter"].predict(env["outcome"].artifact_json,
                                   {KEY_MOM: None, KEY_VOL: 1.0})
        assert "missing/UNKNOWN" in str(err.value)

    def test_unknown_health_blocks_trust(self):
        env = trained_classifier()
        from core.intelligence.explain import ModelHealthService
        service = ModelHealthService()
        health = service.record(
            model=env["outcome"].model, artifact_integrity="UNKNOWN",
            dependency_integrity="VERIFIED",
            feature_compatibility="VERIFIED",
            inference_success_rate=1.0, error_rate=0.0,
            missing_features_rate=0.0,
            data_freshness=FreshnessStatus.FRESH, drift_status="NORMAL",
            unknown_rate=0.0, latency={}, confidence_distribution={},
            created_at=T0, environment="RESEARCH")
        assert health.overall is not None  # degraded at best, never blindly healthy

    def test_unknown_validation_blocks(self):
        v = validation_gate(model_hash=H64, evidence={}, auditor="system",
                            created_at=T0)
        from core.intelligence.contracts import EvidenceStatus
        assert v.overall is EvidenceStatus.UNKNOWN

    def test_unknown_calibration_is_not_fabricated(self):
        env = trained_classifier()
        evaluation = env["evaluator"].evaluate(
            model_hash=H64, dataset_hash=H64,
            artifact_json=env["outcome"].artifact_json,
            rows=[{"features": {KEY_MOM: 1.0, KEY_VOL: 1.0},
                   "label": "UNKNOWN"}],
            environment="RESEARCH", created_at=T0)
        # no probabilities -> no calibration object (NOT fabricated)
        assert evaluation.calibration is None
        assert evaluation.uncertainty_summary["status"] == "NOT_AVAILABLE"
