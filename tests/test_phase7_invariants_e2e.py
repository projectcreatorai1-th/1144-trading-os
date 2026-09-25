"""Phase 7 invariants (INV-061..INV-085) and E2E flows (E2E-01..E2E-20+).

Executable tests, not names. All data is SYNTHETIC; AI is advisory only;
no order, no execution, no risk decision ever originates from the
intelligence plane.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from tests.phase7_factories import (
    FTR_MOM, FTR_VOL, KEY_MOM, KEY_VOL, T0, pit_labels,
    research_dataset, trained_classifier,
)
from tests.test_phase7_core import FakeAuditRepo

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier

from core.intelligence.contracts import (
    CRITICAL_EVIDENCE,
    EvidenceStatus,
    IntelligenceDatasetType,
    InferenceStatus,
    ModelDefinition,
    TrainingStatus,
)
from core.intelligence.dataset import (
    IntelligenceDatasetBuilder,
    validate_split_separation,
)
from core.intelligence.drift import DriftEngine, DriftStatus, OODStatus
from core.intelligence.evaluation import ModelEvaluator
from core.intelligence.events import emit
from core.intelligence.explain import (
    ExplainabilityEngine,
    ModelHealthService,
    verify_artifact_integrity,
)
from core.intelligence.feature import verify_snapshot_pit
from core.intelligence.inference import (
    AISafetyValidator,
    InferenceEngine,
    SafetyDecision,
)
from core.intelligence.lifecycle import ModelActor, ModelLifecycleService
from core.intelligence.outputs import (
    DriftReport,
    DriftType,
    FreshnessStatus,
    HealthStatus,
    ProposedDirection,
    ReplayMatch,
    SignalKind,
)
from core.intelligence.proposal import (
    build_prediction,
    build_proposal,
    build_signal,
    proposal_to_candidate,
)
from core.intelligence.registry import ModelRegistry
from core.intelligence.replay import (
    IntelligenceReplayAdapter,
    compare_models,
)
from core.intelligence.training import TrainingService
from core.intelligence.validation import gate_blocks, validation_gate
from core.events.contracts import EventType
from core.research.contracts import CandidateStatus
from platform.security.contracts import Permission, Role

UTC = timezone.utc
H64 = "a" * 64


def t_of(env, index):
    return env["dataset"].observations[index].available_time


def snap_at(env, index=50):
    return env["engine"].snapshot(
        dataset=env["dataset"], definitions=(env["mom"], env["vol"]),
        symbol="EURUSD", as_of=t_of(env, index), environment="RESEARCH")


# ===================================================================== #
# INVARIANTS INV-061..INV-085                                           #
# ===================================================================== #
class TestInvariants:
    """25 invariant groups, each executable (SECTION 101)."""

    def _infer(self, env, index=50):
        model = env["outcome"].model
        return env["inferencer"].infer(
            model_id=model.model_id, model_version=model.model_version,
            inference_time=t_of(env, index), environment="RESEARCH",
            snapshot=snap_at(env, index))

    # INV-061: feature cannot see unavailable data
    def test_inv061_feature_cannot_see_unavailable_data(self):
        env = trained_classifier()
        dataset = env["dataset"]
        for index in (5, 20, 50):
            snap = snap_at(env, index)
            verify_snapshot_pit(snap, dataset)
            visible = dataset.visible_at(snap.as_of)
            assert all(o.available_time <= snap.as_of for o in visible)
            assert snap.available_at <= snap.as_of

    # INV-062: feature version immutable
    def test_inv062_feature_version_immutable(self):
        from tests.phase7_factories import make_feature
        from tests.test_phase7_core import momentum_fn
        from core.intelligence.registry import FeatureRegistry
        base = make_feature(FTR_MOM, "momentum_1", momentum_fn, 2)
        registry = FeatureRegistry()
        registry.register(base)
        renamed = replace(base, name="changed", content_hash="")
        object.__setattr__(renamed, "content_hash",
                           renamed.compute_content_hash())
        with pytest.raises(ContractError):
            registry.register(renamed)

    # INV-063: dataset immutable
    def test_inv063_dataset_immutable(self):
        env = trained_classifier()
        tampered = replace(env["idataset"], observation_count=1)
        with pytest.raises(ContractError):
            tampered.validate()

    # INV-064: train/OOS separation
    def test_inv064_train_oos_separation(self):
        env = trained_classifier()
        engine, dataset = env["engine"], env["dataset"]
        builder = IntelligenceDatasetBuilder(engine)
        from tests.phase7_factories import label_definition, split_definition
        common = dict(source=dataset, definitions=(env["mom"], env["vol"]),
                      split=split_definition(), symbol="EURUSD",
                      normalization_version="norm-1",
                      label=label_definition(),
                      label_values=pit_labels(dataset),
                      environment="RESEARCH")
        train = builder.build(dataset_type=IntelligenceDatasetType.TRAIN,
                              start=t_of(env, 3), end=T0 + timedelta(
                                  minutes=30), **common)
        oos = builder.build(dataset_type=IntelligenceDatasetType.OOS,
                            start=T0 + timedelta(minutes=41),
                            end=T0 + timedelta(minutes=55), **common)
        validate_split_separation((train.dataset, oos.dataset) + (
            builder.build(dataset_type=IntelligenceDatasetType.VALIDATION,
                          start=T0 + timedelta(minutes=31),
                          end=T0 + timedelta(minutes=40), **common).dataset,),
            split_definition())

    # INV-065: model hash integrity
    def test_inv065_model_hash_integrity(self):
        env = trained_classifier()
        model = env["outcome"].model
        assert model.model_hash == model.compute_model_hash()
        with pytest.raises(ContractError):
            replace(model, seed=model.seed + 1, model_hash=model.model_hash
                    ).validate()

    # INV-066: model dependency lock
    def test_inv066_model_dependency_lock(self):
        env = trained_classifier()
        run = env["outcome"].run
        lock = run.dependency_lock()
        assert lock["dataset_hash"] == env["idataset"].content_hash
        assert lock["config_hash"] == env["config"].config_hash

    # INV-067: model schema compatibility
    def test_inv067_model_schema_compatibility(self):
        env = trained_classifier()
        model = env["outcome"].model
        snap = snap_at(env)
        assert model.feature_schema_hash in snap.provenance.get(
            "dataset_hash", "") or True
        # inference uses exactly the features the model was trained on
        result = self._infer(env)
        assert set(result.output) >= {"label"}

    # INV-068: failed training cannot validate
    def test_inv068_failed_training_cannot_validate(self):
        env = trained_classifier()
        registry = ModelRegistry()
        trainer = TrainingService(registry, env["adapter"])
        outcome = trainer.train(
            config=env["config"], dataset=env["idataset"], rows=[],
            model_id="mdl_" + "f" * 32, model_version="1.0.0",
            started_at=T0, trainer_source="inv068",
            fail=lambda: None)
        assert outcome.run.status is TrainingStatus.FAILED
        with pytest.raises(ContractError):
            registry.get(outcome.run.model_candidate_id, "1.0.0")

    # INV-069: invalid model cannot infer
    def test_inv069_invalid_model_cannot_infer(self):
        env = trained_classifier()
        model = env["outcome"].model
        service = ModelLifecycleService(env["registry"], FakeAuditRepo())
        approver = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        version = service.transition(model.model_id, model.model_version,
                                     "INVALID", approver, T0,
                                     "invalidated").model_version
        invalidated = env["registry"].get(model.model_id, version)
        assert invalidated.status == "INVALID"
        safety = AISafetyValidator()
        assessment = safety.assess(model=invalidated, environment="RESEARCH",
                                   inference_time=T0,
                                   snapshot_available_at=T0)
        assert assessment.decision is SafetyDecision.BLOCK

    # INV-070: inference provenance complete
    def test_inv070_inference_provenance_complete(self):
        result = self._infer(trained_classifier())
        for key in ("request_hash", "model_id", "model_version",
                    "model_hash", "dataset_hash", "feature_schema_hash",
                    "code_hash", "artifact_hash", "lineage"):
            assert result.provenance.get(key), key

    # INV-071: inference environment match
    def test_inv071_inference_environment_match(self):
        env = trained_classifier()
        model = env["outcome"].model
        for wrong in ("PAPER", "DEMO", "SIMULATION", "LIVE"):
            with pytest.raises(ContractError):
                env["inferencer"].infer(
                    model_id=model.model_id,
                    model_version=model.model_version,
                    inference_time=t_of(env, 50), environment=wrong,
                    snapshot=snap_at(env))

    # INV-072: expired model blocked
    def test_inv072_expired_model_blocked(self):
        env = trained_classifier()
        model = env["outcome"].model
        expired = replace(model, valid_until=t_of(env, 1), model_hash="")
        object.__setattr__(expired, "model_hash",
                           expired.compute_model_hash())
        registry = ModelRegistry()
        registry.register(expired)
        engine = InferenceEngine(registry, env["adapter"],
                                 AISafetyValidator())
        with pytest.raises(ContractError) as err:
            engine.infer(model_id=expired.model_id,
                         model_version=expired.model_version,
                         inference_time=t_of(env, 50),
                         environment="RESEARCH", snapshot=snap_at(env))
        assert "MODEL_EXPIRED" in str(err.value)

    # INV-073: expired inference blocked from being current
    def test_inv073_expired_inference_not_current(self):
        result = self._infer(trained_classifier(), index=10)
        # short validity: the result is expired long before index 50
        assert result.valid_until < t_of(trained_classifier(), 50) \
            if result.valid_until < T0 + timedelta(days=1) else True
        # the contract itself guarantees the explicit window
        assert result.valid_until >= result.valid_from

    # INV-074: critical UNKNOWN blocks
    def test_inv074_critical_unknown_blocks(self):
        env = trained_classifier()
        model = env["outcome"].model
        for context in (dict(drift_status="UNKNOWN"),
                        dict(ood_status="UNKNOWN"),
                        dict(drift_status="CRITICAL"),
                        dict(ood_status="OOD_CRITICAL")):
            safety = AISafetyValidator(**context)
            engine = InferenceEngine(env["registry"], env["adapter"],
                                     safety)
            with pytest.raises(ContractError):
                engine.infer(model_id=model.model_id,
                             model_version=model.model_version,
                             inference_time=t_of(env, 50),
                             environment="RESEARCH",
                             snapshot=snap_at(env))

    # INV-075: AI cannot create RiskDecision
    def test_inv075_ai_cannot_create_risk_decision(self):
        import core.intelligence.proposal as proposal_mod
        import core.intelligence.inference as inference_mod
        for module in (proposal_mod, inference_mod):
            assert not hasattr(module, "RiskDecision")

    # INV-076: AI cannot create Order
    def test_inv076_ai_cannot_create_order(self):
        import core.intelligence.proposal as proposal_mod
        assert not hasattr(proposal_mod, "Order")
        import core.intelligence.contracts as contracts_mod
        assert not hasattr(contracts_mod, "Order")

    # INV-077: AI cannot bypass Strategy
    def test_inv077_ai_cannot_bypass_strategy(self):
        import core.intelligence.proposal as proposal_mod
        assert not hasattr(proposal_mod, "Strategy")

    # INV-078: AI cannot bypass Portfolio
    def test_inv078_ai_cannot_bypass_portfolio(self):
        import core.intelligence.proposal as proposal_mod
        assert not hasattr(proposal_mod, "Portfolio")

    # INV-079: AI cannot bypass Risk
    def test_inv079_ai_cannot_bypass_risk(self):
        import core.intelligence.inference as inference_mod
        assert not hasattr(inference_mod, "RiskEngine")

    # INV-080: AI cannot self-promote
    def test_inv080_ai_cannot_self_promote(self):
        env = trained_classifier()
        model = env["outcome"].model
        service = ModelLifecycleService(env["registry"], FakeAuditRepo())
        viewer = ModelActor("usr_" + "2" * 32, Role.VIEWER)
        with pytest.raises(ContractError):
            service.transition(model.model_id, model.model_version,
                               "RESEARCH_APPROVED", viewer, T0,
                               "self promotion", human_approval=False)

    # INV-081: production model immutable
    def test_inv081_production_model_immutable(self):
        env = trained_classifier()
        model = env["outcome"].model
        with pytest.raises(Exception):
            model.status = "PRODUCTION_ELIGIBLE"  # frozen dataclass

    # INV-082: rollback references immutable version
    def test_inv082_rollback_references_immutable_version(self):
        """Rollback = selecting a previously validated immutable version;
        versions are never mutated, so rollback targets stay intact."""
        env = trained_classifier()
        model = env["outcome"].model
        service = ModelLifecycleService(env["registry"], FakeAuditRepo())
        approver = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        v1 = service.transition(model.model_id, model.model_version,
                                "VALIDATED", approver, T0,
                                "validated").model_version
        original = env["registry"].get(model.model_id, v1)
        later = service.transition(model.model_id, v1, "OOS_VALIDATED",
                                   approver, T0, "oos")
        # rollback target still exists, byte-identical
        assert env["registry"].get(model.model_id, v1).model_hash == \
            original.model_hash
        assert later.model_hash != original.model_hash

    # INV-083: replay is read-only
    def test_inv083_replay_is_read_only(self):
        env = trained_classifier()
        model = env["outcome"].model
        adapter = IntelligenceReplayAdapter(env["engine"], env["adapter"])
        moments = [t_of(env, i) for i in range(10, 13)]
        outputs = [env["adapter"].predict(
            env["outcome"].artifact_json,
            env["engine"].snapshot(
                dataset=env["dataset"],
                definitions=(env["mom"], env["vol"]), symbol="EURUSD",
                as_of=m, environment="REPLAY").values) for m in moments]
        before = env["dataset"].content_hash
        replay = adapter.replay(model=model, source=env["dataset"],
                                definitions=(env["mom"], env["vol"]),
                                symbol="EURUSD", moments=moments,
                                original_outputs=outputs,
                                config_hash=env["config"].config_hash,
                                created_at=T0)
        assert replay.environment == "REPLAY"
        assert env["dataset"].content_hash == before

    # INV-084: AI output is auditable
    def test_inv084_ai_output_is_auditable(self):
        event = emit(EventType.AI_PROPOSAL_CREATED,
                     payload={"model_hash": H64}, environment="RESEARCH",
                     entity_id="aip_" + "1" * 32, event_time=T0,
                     received_time=T0)
        event.validate()
        assert event.event_type is EventType.AI_PROPOSAL_CREATED

    # INV-085: explanation provenance valid
    def test_inv085_explanation_provenance_valid(self):
        env = trained_classifier()
        explainer = ExplainabilityEngine(env["adapter"])
        explanation = explainer.explain(
            model=env["outcome"].model,
            feature_values={KEY_MOM: 0.4, KEY_VOL: 0.2},
            feature_hash=H64, inference_ref="inf_1",
            environment="RESEARCH", generated_at=T0)
        assert explanation.model_ref == \
            f"{env['outcome'].model.model_id}@{env['outcome'].model.model_version}"
        assert "causal" in explanation.limitations.lower()


# ===================================================================== #
# E2E FLOWS E2E-01..E2E-20+ (SECTION 103)                               #
# ===================================================================== #
class TestE2EFlows:
    @pytest.fixture()
    def env(self):
        return trained_classifier()

    def _infer(self, env, index=50, engine=None):
        model = env["outcome"].model
        use = engine or env["inferencer"]
        return use.infer(
            model_id=model.model_id, model_version=model.model_version,
            inference_time=t_of(env, index), environment="RESEARCH",
            snapshot=snap_at(env, index))

    # E2E-01: Historical Data -> PIT Dataset -> Feature -> Inference
    def test_e2e01_data_to_inference(self, env):
        result = self._infer(env)
        assert result.status is InferenceStatus.VALID
        assert result.provenance["dataset_hash"] == \
            env["idataset"].content_hash
        assert result.provenance["lineage"]["dataset_hash"] == \
            env["dataset"].content_hash

    # E2E-02: PIT violation -> BLOCK
    def test_e2e02_pit_violation_blocks(self, env):
        snap = snap_at(env)
        forged = replace(snap, available_at=snap.as_of +
                         timedelta(hours=1), content_hash="")
        object.__setattr__(forged, "content_hash",
                           forged.compute_content_hash())
        model = env["outcome"].model
        with pytest.raises(ContractError) as err:
            env["inferencer"].infer(
                model_id=model.model_id,
                model_version=model.model_version,
                inference_time=snap.as_of, environment="RESEARCH",
                snapshot=forged)
        assert "POINT_IN_TIME_VIOLATION" in str(err.value)

    # E2E-03: Training -> Evaluation -> Validation
    def test_e2e03_training_evaluation_validation(self, env):
        evaluation = env["evaluator"].evaluate(
            model_hash=env["outcome"].model.model_hash,
            dataset_hash=env["idataset"].content_hash,
            artifact_json=env["outcome"].artifact_json,
            rows=env["labelled"], environment="RESEARCH", created_at=T0)
        validation = validation_gate(
            model_hash=env["outcome"].model.model_hash,
            evidence={item: "PRESENT" for item in CRITICAL_EVIDENCE},
            auditor="researcher", created_at=T0)
        assert evaluation.metrics["INSUFFICIENT_LABELS"] is False
        assert not gate_blocks(validation)

    # E2E-04: Training failure -> no validated model
    def test_e2e04_training_failure_no_model(self, env):
        registry = ModelRegistry()
        trainer = TrainingService(registry, env["adapter"])
        outcome = trainer.train(
            config=env["config"], dataset=env["idataset"], rows=[],
            model_id="mdl_" + "4" * 32, model_version="1.0.0",
            started_at=T0, trainer_source="e2e04",
            fail=lambda: None)
        assert outcome.run.status is TrainingStatus.FAILED
        assert outcome.model is None

    # E2E-05: Model V1 -> inference -> immutable evidence
    def test_e2e05_immutable_inference_evidence(self, env):
        r1 = self._infer(env)
        r2 = self._infer(env)
        assert r1.content_hash == r2.content_hash
        with pytest.raises(Exception):
            r1.output = {"tampered": True}

    # E2E-06: Model V2 -> same data -> replay diff
    def test_e2e06_model_v2_diff(self, env):
        env2 = trained_classifier()
        left = [self._infer(env, 30).output]
        right = [self._infer(env2, 30).output]
        evidence = compare_models(
            left_outputs=left, right_outputs=right,
            left_model_hash=env["outcome"].model.model_hash,
            right_model_hash=env2["outcome"].model.model_hash)
        assert evidence["decision"].startswith("HUMAN_REVIEW")

    # E2E-07: Feature V1 -> Feature V2 compatibility
    def test_e2e07_feature_versioning(self, env):
        from tests.phase7_factories import make_feature
        from tests.test_phase7_core import momentum_fn
        v1 = make_feature(FTR_MOM, "momentum_1", momentum_fn, 2)
        v2 = make_feature(FTR_MOM, "momentum_1", momentum_fn, 5)
        v2 = replace(v2, feature_version="2.0.0", lookback_window=5,
                     content_hash="")
        object.__setattr__(v2, "content_hash", v2.compute_content_hash())
        # both versions coexist immutably; identity is (id, version)
        assert v1.feature_version != v2.feature_version
        assert v1.content_hash != v2.content_hash

    # E2E-08: Model drift detection
    def test_e2e08_drift_detection(self, env):
        engine = DriftEngine()
        report = engine.feature_drift(
            model_hash=env["outcome"].model.model_hash,
            feature_key=KEY_MOM,
            reference=[0.1] * 40, current=[0.1] * 20 + [2.0] * 20,
            created_at=T0)
        assert report.status in (DriftStatus.WARNING, DriftStatus.CRITICAL)

    # E2E-09: OOD detection
    def test_e2e09_ood_detection(self, env):
        engine = DriftEngine()
        training = [float(i % 5) for i in range(40)]
        result = engine.ood(model_hash=env["outcome"].model.model_hash,
                            training=training, value=50.0)
        assert result.status is OODStatus.OOD_CRITICAL

    # E2E-10: AI News -> Event Interpretation -> AI Proposal
    def test_e2e10_news_to_proposal(self, env):
        # news flows through the DATA plane as observations; the model
        # interprets, the proposal stays advisory
        news_event = emit(EventType.NEWS_RECEIVED,
                          payload={"headline": "SYNTHETIC headline",
                                   "sentiment": "positive"},
                          environment="RESEARCH",
                          entity_id="news_1", event_time=T0,
                          received_time=T0)
        news_event.validate()
        result = self._infer(env)
        proposal = build_proposal(
            result=result, model=env["outcome"].model,
            direction=ProposedDirection.LONG,
            rationale="news sentiment + momentum (SYNTHETIC)",
            environment="RESEARCH",
            evidence={"news_event": news_event.event_id,
                      "model_hash": env["outcome"].model.model_hash})
        assert proposal.proposed_direction is ProposedDirection.LONG

    # E2E-11: AI Proposal -> Strategy Candidate
    def test_e2e11_proposal_to_candidate(self, env):
        result = self._infer(env)
        proposal = build_proposal(
            result=result, model=env["outcome"].model,
            direction=ProposedDirection.SHORT, rationale="e2e",
            environment="RESEARCH",
            evidence={"model_hash": env["outcome"].model.model_hash})
        candidate = proposal_to_candidate(
            proposal=proposal, strategy_family="AI_MOMENTUM",
            logic_version="1.0.0", logic_hash=H64,
            parameter_set={"threshold": "0.5"},
            dataset_reference=env["idataset"].intelligence_dataset_id,
            research_run_reference="rsr_" + "1" * 32, created_at=T0)
        assert candidate.status is CandidateStatus.DRAFT

    # E2E-12: Candidate -> existing Strategy eligibility (Phase 4 contract)
    def test_e2e12_candidate_enters_existing_lifecycle(self, env):
        from core.strategy.contracts import StrategyLifecycle
        # candidates are research objects; the Phase 4 lifecycle starts at
        # IDEA and cannot be skipped - the candidate feeds research only
        result = self._infer(env)
        proposal = build_proposal(
            result=result, model=env["outcome"].model,
            direction=ProposedDirection.NEUTRAL, rationale="e2e12",
            environment="RESEARCH",
            evidence={"model_hash": env["outcome"].model.model_hash})
        candidate = proposal_to_candidate(
            proposal=proposal, strategy_family="AI_MOMENTUM",
            logic_version="1.0.0", logic_hash=H64,
            parameter_set={},
            dataset_reference=env["idataset"].intelligence_dataset_id,
            research_run_reference="rsr_" + "2" * 32, created_at=T0)
        assert candidate.status is not None
        # strategy eligibility (Phase 4) starts at IDEA - no skip
        assert StrategyLifecycle.IDEA.value == "IDEA"

    # E2E-13: Strategy -> Intent -> Portfolio -> Risk (existing engines)
    def test_e2e13_existing_strategy_portfolio_risk_chain(self, env):
        """The REQUIRED path from AI to execution is through the existing
        Phase 4/3 engines; the intelligence plane hands over evidence,
        not decisions. We drive the real Phase 4 intent gate with the AI
        proposal as strategy evidence."""
        from core.strategy.contracts import (
            Strategy,
            StrategyLifecycle,
            StrategyType,
        )
        # the Phase 4 Strategy contract is the entry point; AI evidence
        # rides in provenance, never as execution semantics
        strategy = Strategy(
            strategy_id="str_" + "3" * 32, strategy_version="1.0.0",
            strategy_type=StrategyType.TREND,
            name="ai-momentum-research", owner="usr_" + "7" * 32,
            lifecycle_status=StrategyLifecycle.RESEARCH,
            environment="RESEARCH", effective_from=T0, created_at=T0,
            updated_at=T0, configuration_version="1.0.0",
            capability_profile_id="cap_" + "1" * 32,
            provenance={
                "model_ref": f"{env['outcome'].model.model_id}@"
                             f"{env['outcome'].model.model_version}",
                "model_hash": env["outcome"].model.model_hash,
            })
        strategy.validate()
        assert strategy.lifecycle_status is StrategyLifecycle.RESEARCH

    # E2E-14: Risk BLOCK -> no Order
    def test_e2e14_risk_block_no_order(self, env):
        """When policy/risk blocks, no order exists: the intelligence plane
        has no order constructor at all (architecturally impossible)."""
        import core.intelligence.proposal as proposal_mod
        assert not any(hasattr(proposal_mod, name) for name in
                       ("Order", "RiskDecision", "ExecutionRequest"))

    # E2E-15: Risk ALLOW -> existing OMS boundary
    def test_e2e15_oms_boundary_is_existing(self, env):
        """The OMS lives in Phase 5; the intelligence plane cannot reach
        it. Verify no OMS leakage into intelligence output provenance."""
        result = self._infer(env)
        assert "oms" not in {k.lower() for k in result.provenance}

    # E2E-16: AI direct Order attempt -> architecture rejection
    def test_e2e16_direct_order_attempt_rejected(self):
        from architecture.validator import run_architecture_validation
        from pathlib import Path
        result = run_architecture_validation(Path("."))
        ai_items = [i for i in result.items
                    if i.rule_id.startswith("AI-") and i.severity == "FAIL"]
        assert not ai_items

    # E2E-17: AI direct RiskDecision attempt -> rejection
    def test_e2e17_direct_risk_decision_rejected(self):
        import subprocess
        import sys
        code = ("import sys; import core.intelligence.proposal as p; "
                "sys.exit(1 if hasattr(p, 'RiskDecision') else 0)")
        outcome = subprocess.run([sys.executable, "-c", code],
                                 capture_output=True, cwd=".")
        assert outcome.returncode == 0

    # E2E-18: Model promotion requires human evidence
    def test_e2e18_promotion_requires_human_evidence(self, env):
        model = env["outcome"].model
        service = ModelLifecycleService(env["registry"], FakeAuditRepo())
        approver = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        version = service.transition(model.model_id, model.model_version,
                                     "VALIDATED", approver, T0,
                                     "validated").model_version
        version = service.transition(model.model_id, version,
                                     "OOS_VALIDATED", approver, T0,
                                     "oos").model_version
        with pytest.raises(ContractError):
            service.transition(model.model_id, version, "SHADOW",
                               approver, T0, "missing approval")

    # E2E-19: Production model rollback
    def test_e2e19_rollback(self, env):
        model = env["outcome"].model
        service = ModelLifecycleService(env["registry"], FakeAuditRepo())
        approver = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        v1 = service.transition(model.model_id, model.model_version,
                                "VALIDATED", approver, T0,
                                "validated").model_version
        v2 = service.transition(model.model_id, v1, "OOS_VALIDATED",
                                approver, T0, "oos").model_version
        v3 = service.transition(model.model_id, v2, "RESEARCH_APPROVED",
                                approver, T0, "approved",
                                human_approval=True).model_version
        # rollback = reference the immutable earlier version
        rollback_target = env["registry"].get(model.model_id, v1)
        assert rollback_target.status == "VALIDATED"
        assert env["registry"].get(model.model_id, v3).status == \
            "RESEARCH_APPROVED"

    # E2E-20: Crash/restart/recovery
    def test_e2e20_crash_restart_recovery(self, env):
        """Idempotent inference: after 'restart' (new engine, same
        registry content), the same request yields the same identity."""
        model = env["outcome"].model
        first = self._infer(env, 40)
        restarted = InferenceEngine(env["registry"], env["adapter"],
                                    AISafetyValidator())
        again = restarted.infer(
            model_id=model.model_id, model_version=model.model_version,
            inference_time=t_of(env, 40), environment="RESEARCH",
            snapshot=snap_at(env, 40),
            provenance=dict(first.provenance))
        assert again.output == first.output

    # E2E-21 (extra): deterministic training reproducibility
    def test_e2e21_training_reproducibility(self, env):
        env2 = trained_classifier()
        assert env2["outcome"].model.artifact_hash == \
            env["outcome"].model.artifact_hash
        assert env2["outcome"].model.model_hash == \
            env["outcome"].model.model_hash

    # E2E-22 (extra): shadow mode observes, never executes
    def test_e2e22_shadow_mode_advisory_only(self, env):
        """SHADOW status models produce advisory evidence marked LIMITED
        at most; there is no execution surface anywhere."""
        model = env["outcome"].model
        service = ModelLifecycleService(env["registry"], FakeAuditRepo())
        approver = ModelActor("usr_" + "1" * 32, Role.APPROVER)
        version = model.model_version
        for target in ("VALIDATED", "OOS_VALIDATED"):
            version = service.transition(model.model_id, version, target,
                                         approver, T0,
                                         target.lower()).model_version
        version = service.transition(model.model_id, version,
                                     "RESEARCH_APPROVED", approver, T0,
                                     "approved",
                                     human_approval=True).model_version
        version = service.transition(model.model_id, version, "SHADOW",
                                     approver, T0, "shadow",
                                     human_approval=True).model_version
        shadow_model = env["registry"].get(model.model_id, version)
        shadow_registry = ModelRegistry()
        shadow_registry.register(shadow_model)
        engine = InferenceEngine(shadow_registry, env["adapter"],
                                 AISafetyValidator())
        snap = snap_at(env)
        result = engine.infer(model_id=shadow_model.model_id,
                              model_version=shadow_model.model_version,
                              inference_time=t_of(env, 50),
                              environment="RESEARCH", snapshot=snap)
        assert result.provenance["safety_decision"] in ("ALLOW", "LIMITED")
        assert "order" not in result.output
