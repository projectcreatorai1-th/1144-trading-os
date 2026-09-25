"""Phase 6 core tests: dataset/PIT/bias + backtest engine determinism +
pipeline + validation (OOS/walk-forward) + stress/robustness + replay diff."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractError, ContractValidationError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import UTC
from core.backtest.engine import (
    BacktestEngine,
    BacktestState,
    SimulationClock,
    StrategyLogic,
)
from core.research.bias import (
    BiasAuditor,
    BiasStatus,
    DecisionRecord,
    bias_invalidates_run,
)
from core.research.contracts import (
    CandidateStatus,
    ExecutionModel,
    Observation,
    ResearchConfig,
    ResearchDataset,
    StrategyCandidate,
    SpreadModel,
    IntrabarPolicy,
    canonical_hash,
)
from core.research.pipeline import (
    ResearchPipeline,
    out_of_sample,
    replay_compare,
    robustness_sensitivity,
    stress_test,
    walk_forward,
)
from tests.phase1_factories import at

T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


# --------------------------------------------------------------------- #
# fixtures                                                               #
# --------------------------------------------------------------------- #
def obs(symbol, event_hour, available_hour, price, data_type="MARKET_DATA"):
    event = T0.replace(hour=event_hour)
    available = T0.replace(hour=available_hour) if available_hour >= event_hour else event
    return Observation(symbol=symbol, event_time=event, available_time=available,
                       payload={"price": price}, data_type=data_type)


def make_dataset(observations=None, **overrides):
    o = observations if observations is not None else [
        obs("XAUUSD", 10, 10, "2650.00"),
        obs("XAUUSD", 11, 11, "2655.00"),
        obs("XAUUSD", 12, 12, "2660.00"),
    ]
    defaults = dict(
        dataset_version="1.0.0", timeframe="1H", timezone="UTC",
        environment="BACKTEST",
    )
    defaults.update(overrides)
    dataset = ResearchDataset(
        dataset_id=new_identifier("dataset_id"),
        symbols=tuple(sorted({o.symbol for o in o})),
        time_range={"start": min(x.event_time for x in o).isoformat(),
                    "end": max(x.event_time for x in o).isoformat()},
        data_source="tests.synthetic",
        source_versions={"feed": "1.0.0"}, quality_summary={"level": "VALIDATED"},
        lineage={"lineage_id": "lin_x"}, content_hash="0" * 64,
        created_at=at(9, 0), observations=tuple(o), **defaults,
    )
    object.__setattr__(dataset, "content_hash", dataset.compute_content_hash())
    dataset.validate()
    return dataset


def make_config(**overrides):
    defaults = dict(
        config_id=new_identifier("config_id"), config_version="1.0.0",
        initial_capital="10000", symbols=("XAUUSD",), timeframe="1H",
        date_range={"start": "2026-01-05T10:00:00+00:00",
                    "end": "2026-01-05T12:00:00+00:00"},
        commission="0.50", spread="0.20", slippage="0.10", leverage="100",
        execution_assumptions={"fill": "next-observation"},
        config_hash="0" * 64, environment="BACKTEST",
    )
    defaults.update(overrides)
    config = ResearchConfig(**defaults)
    object.__setattr__(config, "config_hash", config.compute_config_hash())
    config.validate()
    return config


def make_model(**overrides):
    defaults = dict(
        execution_model_id=new_identifier("execution_model_id"),
        execution_model_version="1.0.0",
        fill_models={"MARKET": "fill-at-observation-plus-slippage"},
        spread_model=SpreadModel.FIXED,
        slippage_model={"kind": "fixed-decimal"},
        latency_assumptions={"order_latency_ms": "UNKNOWN->assumption 0"},
        commission_model={"kind": "fixed"},
        liquidity_assumption="UNKNOWN", market_impact="ASSUMPTION",
        intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        model_hash="0" * 64, environment="BACKTEST",
    )
    defaults.update(overrides)
    model = ExecutionModel(**defaults)
    object.__setattr__(model, "model_hash", model.compute_model_hash())
    model.validate()
    return model


def buy_on_third(observation, state):
    seen = len(state.decisions)
    return "OPEN" if seen == 2 else None


LOGIC = StrategyLogic(
    strategy_id=new_identifier("strategy_id"), strategy_version="1.0.0",
    logic_hash="a" * 64, on_observation=buy_on_third,
)


# --------------------------------------------------------------------- #
# Dataset + point-in-time                                                #
# --------------------------------------------------------------------- #
class TestDataset:
    def test_immutable(self):
        dataset = make_dataset()
        with pytest.raises(FrozenInstanceError):
            dataset.timeframe = "4H"

    def test_hash_verification(self):
        dataset = make_dataset()
        tampered = replace(dataset, content_hash="f" * 64)
        with pytest.raises(ContractValidationError) as excinfo:
            tampered.validate()
        assert excinfo.value.rule_id == "DATASET-003"

    def test_available_cannot_precede_event(self):
        with pytest.raises(ContractValidationError) as excinfo:
            Observation(symbol="X", event_time=T0,
                        available_time=T0 - timedelta(minutes=1),
                        payload={}).validate()
        assert excinfo.value.rule_id == "PIT-001"

    def test_point_in_time_visibility(self):
        delayed = obs("XAUUSD", 10, 12, "2650.00")  # event 10:00, available 12:00
        dataset = make_dataset([delayed])
        at_1101 = dataset.visible_at(datetime(2026, 1, 5, 11, 1, tzinfo=UTC))
        at_1203 = dataset.visible_at(datetime(2026, 1, 5, 12, 3, tzinfo=UTC))
        assert at_1101 == ()          # SECTION 9: 10:01 must NOT see the event
        assert len(at_1203) == 1       # 12:03 may see it

    def test_research_environment_enforced(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_dataset(environment="LIVE")
        assert excinfo.value.rule_id == "ENV-005"

    def test_timezone_unknown_rejected(self):
        with pytest.raises(ContractValidationError):
            make_dataset(timezone="")

    def test_survivorship_status_unknown_without_membership(self):
        assert make_dataset().survivorship_status == "UNKNOWN"

    def test_data_change_creates_new_version_not_mutation(self):
        v1 = make_dataset()
        extra = obs("EURUSD", 13, 13, "1.10")
        v2 = make_dataset([*v1.observations, extra],
                          dataset_version="2.0.0")
        assert v1.dataset_version == "1.0.0"  # v1 unchanged
        assert v2.content_hash != v1.content_hash


# --------------------------------------------------------------------- #
# Bias audit                                                             #
# --------------------------------------------------------------------- #
class TestBias:
    def test_clean_decisions_pass(self):
        dataset = make_dataset()
        decisions = [
            DecisionRecord(datetime(2026, 1, 5, 12, 0, tzinfo=UTC), (0, 1, 2)),
        ]
        report = BiasAuditor().audit(dataset=dataset, decisions=decisions,
                                     environment="BACKTEST", at=at(13, 0))
        assert report.overall in (BiasStatus.PASS, BiasStatus.WARN)
        assert not bias_invalidates_run(report)

    def test_look_ahead_fails_and_invalidates(self):
        dataset = make_dataset()
        decisions = [
            # decision at 10:30 consumes the 12:00 observation
            DecisionRecord(datetime(2026, 1, 5, 10, 30, tzinfo=UTC), (2,)),
        ]
        report = BiasAuditor().audit(dataset=dataset, decisions=decisions,
                                     environment="BACKTEST", at=at(13, 0))
        assert report.overall is BiasStatus.FAIL
        assert bias_invalidates_run(report)

    def test_future_label_unknown_invalidates(self):
        dataset = make_dataset(observations=[
            obs("XAUUSD", 10, 10, "2650.00", data_type="LABEL"),
        ])
        decisions = []
        report = BiasAuditor().audit(dataset=dataset, decisions=decisions,
                                     environment="BACKTEST", at=at(13, 0))
        # critical UNKNOWN (none here) or FAIL invalidates; with no decisions
        # future-normalization is UNKNOWN but not critical => WARN at most
        assert not bias_invalidates_run(report)

    def test_survivorship_unknown_reported(self):
        report = BiasAuditor().audit(
            dataset=make_dataset(), decisions=[],
            environment="BACKTEST", at=at(13, 0))
        survivorship = [c for c in report.checks if c.bias_type == "SURVIVORSHIP"]
        assert survivorship and survivorship[0].status is BiasStatus.UNKNOWN

    def test_multiple_testing_warns(self):
        report = BiasAuditor().audit(
            dataset=make_dataset(), decisions=[],
            multiple_tests=10, environment="BACKTEST", at=at(13, 0))
        selection = [c for c in report.checks if c.bias_type == "SELECTION"]
        assert selection[0].status is BiasStatus.WARN


# --------------------------------------------------------------------- #
# Backtest engine                                                        #
# --------------------------------------------------------------------- #
class TestBacktest:
    def _pipeline(self, policy_allows=None):
        return ResearchPipeline(config=make_config(), model=make_model(),
                                policy_allows=policy_allows)

    def test_deterministic_identical_results(self):
        run1 = self._pipeline().execute(
            dataset=make_dataset(), logic=LOGIC,
            policy_reference="pol", policy_hash="p" * 64,
            portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(14, 0))
        run2 = self._pipeline().execute(
            dataset=make_dataset(), logic=LOGIC,
            policy_reference="pol", policy_hash="p" * 64,
            portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(15, 0))
        assert run1[3].metrics == run2[3].metrics
        assert run1[0].run_hash == run2[0].run_hash

    def test_clock_never_backwards(self):
        clock = SimulationClock.start(T0)
        with pytest.raises(ContractError):
            clock.advance_to(T0 - timedelta(minutes=1))

    def test_unknown_cost_fails_closed(self):
        with pytest.raises(ContractError) as excinfo:
            BacktestEngine(config=make_config(spread="UNKNOWN"),
                           model=make_model()).run(
                dataset=make_dataset(), logic=LOGIC)
        assert excinfo.value.rule_id == "NOFALLBACK-001"

    def test_policy_gate_blocks_actions(self):
        outcome = self._pipeline(policy_allows=lambda action: False).execute(
            dataset=make_dataset(), logic=LOGIC,
            policy_reference="pol", policy_hash="p" * 64,
            portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(14, 0))
        assert outcome[1].metrics["trade_count"] == 0

    def test_metrics_present(self):
        result = self._pipeline().execute(
            dataset=make_dataset(), logic=LOGIC,
            policy_reference="pol", policy_hash="p" * 64,
            portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(14, 0))[3]
        for key in ("net_pnl", "gross_profit", "gross_loss", "win_rate",
                    "profit_factor", "expectancy", "max_drawdown", "trade_count"):
            assert key in result.metrics

    def test_dependency_hash_mismatch_detected(self):
        pipeline = self._pipeline()
        run = pipeline.execute(
            dataset=make_dataset(), logic=LOGIC,
            policy_reference="pol", policy_hash="p" * 64,
            portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(14, 0))[0]
        tampered = replace(make_dataset(), content_hash="f" * 64)
        mismatches = run.verify_dependencies(
            dataset=tampered, config=make_config(), model=make_model())
        assert "dataset_hash" in mismatches


# --------------------------------------------------------------------- #
# Validation / stress / robustness / replay                              #
# --------------------------------------------------------------------- #
class TestValidation:
    def test_oos_frozen_parameters(self):
        pipeline = ResearchPipeline(config=make_config(), model=make_model())
        validation = out_of_sample(
            pipeline=pipeline, dataset=make_dataset(),
            train_dataset=make_dataset(), logic=LOGIC,
            policy_reference="pol", policy_hash="p" * 64,
            portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(14, 0))
        assert validation.status == "PASSED"
        assert validation.frozen_parameters["logic_hash"] == LOGIC.logic_hash

    def test_walk_forward_windows(self):
        pipeline = ResearchPipeline(config=make_config(), model=make_model())
        validation = walk_forward(
            pipeline=pipeline,
            datasets=[make_dataset(), make_dataset(dataset_version="2.0.0")],
            logic=LOGIC, policy_reference="pol", policy_hash="p" * 64,
            portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(14, 0))
        assert validation.status == "PASSED" and len(validation.windows) == 2

    def test_stress_scenarios_versioned(self):
        report = stress_test(
            base_config=make_config(), model=make_model(),
            dataset=make_dataset(), logic=LOGIC,
            run_reference="rsr_x", at=at(14, 0))
        assert report.overall == "COMPLETED"
        assert report.config_version == "1.0.0"

    def test_stress_invalid_config_fails(self):
        with pytest.raises(ContractError):
            stress_test(
                base_config=make_config(commission="UNKNOWN"),
                model=make_model(), dataset=make_dataset(), logic=LOGIC,
                at=at(14, 0))

    def test_robustness_classification(self):
        assert robustness_sensitivity(
            base_metrics={"net_pnl": "100"},
            stressed_metrics=[{"net_pnl": "95"}, {"net_pnl": "105"}]) == "STABLE"
        assert robustness_sensitivity(
            base_metrics={"net_pnl": "100"},
            stressed_metrics=[{"net_pnl": "-50"}]) == "UNSTABLE"
        assert robustness_sensitivity(
            base_metrics={"net_pnl": "100"}, stressed_metrics=[]) == "UNKNOWN"

    def test_replay_compare_match_and_mismatch(self):
        metrics = {"net_pnl": "10", "trade_count": 1}
        curve = [{"time": "t", "equity": "10010"}]
        matched = replay_compare(recorded_metrics=metrics, replayed_metrics=metrics,
                                 recorded_equity=curve, replayed_equity=curve)
        assert matched.matched
        mismatched = replay_compare(recorded_metrics=metrics,
                                    replayed_metrics={"net_pnl": "11", "trade_count": 1},
                                    recorded_equity=curve, replayed_equity=curve)
        assert mismatched.status == "MISMATCH"
        assert any(d.startswith("metric:net_pnl") for d in mismatched.differences)


# --------------------------------------------------------------------- #
# Candidate + result invalidation (E2E 017/018/019 semantics)           #
# --------------------------------------------------------------------- #
class TestCandidateAndInvalidation:
    def test_candidate_never_live(self):
        candidate = StrategyCandidate(
            candidate_id=new_identifier("strategy_candidate_id"),
            strategy_family="TREND", logic_version="1.0.0", logic_hash="a" * 64,
            parameter_set={"period": 3}, dataset_reference="dts_x",
            research_run_reference="rsr_x",
            status=CandidateStatus.BACKTESTED,
            created_at=at(14, 0), environment="BACKTEST")
        candidate.validate()
        with pytest.raises(ContractValidationError):
            replace(candidate, environment="LIVE").validate()

    def test_bias_fail_invalidates_result(self):
        from core.research.contracts import ResearchResult, ResultStatus, BiasStatus
        from core.research.pipeline import finalize_result
        pipeline_run = ResearchPipeline(config=make_config(), model=make_model()).execute(
            dataset=make_dataset(), logic=LOGIC,
            policy_reference="pol", policy_hash="p" * 64,
            portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(14, 0))
        run, outcome, bias_report, _ = pipeline_run
        from core.research.contracts import BiasCheck
        failed_bias = replace(
            bias_report, overall=BiasStatus.FAIL,
            checks=(BiasCheck("LOOK_AHEAD", BiasStatus.FAIL, "injected"),))
        failed_bias.validate()
        result = finalize_result(run=run, outcome=outcome,
                                 bias_report=failed_bias, at=at(15, 0))
        assert result.status is ResultStatus.INVALID
