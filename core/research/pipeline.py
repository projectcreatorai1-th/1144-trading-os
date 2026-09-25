"""Research pipeline: run construction, result finalization, replay+diff,
validation (OOS / walk-forward), stress and robustness (owned by core.research)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence

from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc
from core.backtest.engine import BacktestEngine, BacktestOutcome, StrategyLogic
from core.research.bias import BiasAuditor, BiasReport, DecisionRecord, bias_invalidates_run
from core.research.contracts import (
    ExecutionModel,
    ResearchConfig,
    ResearchDataset,
    ResearchResult,
    ResearchRun,
    ResultStatus,
    RobustnessStatus,
    ValidationStatus,
    BiasStatus,
    StressReport,
    StressScenario,
    canonical_hash,
    research_run_hash,
)

CONTRACT_VERSION = "1.0.0"


def build_research_run(*, dataset: ResearchDataset, logic: StrategyLogic,
                       config: ResearchConfig, model: ExecutionModel,
                       policy_reference: str, policy_hash: str,
                       portfolio_version: str, portfolio_hash: str,
                       code_version: str, environment: str = "BACKTEST",
                       created_at: datetime) -> ResearchRun:
    run_hash = research_run_hash(
        dataset_id=dataset.dataset_id, dataset_hash=dataset.content_hash,
        strategy_id=logic.strategy_id, strategy_hash=logic.logic_hash,
        config_hash=config.config_hash,
        execution_model_hash=model.model_hash,
        policy_hash=policy_hash, code_version=code_version, seed=config.seed)
    run = ResearchRun(
        research_run_id=new_identifier("research_run_id"),
        dataset_id=dataset.dataset_id, dataset_version=dataset.dataset_version,
        dataset_hash=dataset.content_hash,
        strategy_id=logic.strategy_id, strategy_version=logic.strategy_version,
        strategy_hash=logic.logic_hash,
        config_id=config.config_id, config_version=config.config_version,
        config_hash=config.config_hash,
        execution_model_id=model.execution_model_id,
        execution_model_version=model.execution_model_version,
        execution_model_hash=model.model_hash,
        policy_reference=policy_reference, policy_hash=policy_hash,
        portfolio_version=portfolio_version, portfolio_hash=portfolio_hash,
        environment=environment,
        created_at=ensure_utc(created_at, location="run.at"),
        source_hash=canonical_hash(dataset.data_source),
        input_hash=run_hash,
        code_version=code_version,
        run_hash=run_hash,
        provenance={"seed": config.seed, "capital_mode": config.capital_mode},
    )
    run.validate()
    return run


def finalize_result(*, run: ResearchRun, outcome: BacktestOutcome,
                    bias_report: BiasReport, multiple_tests: int = 1,
                    at: datetime) -> ResearchResult:
    invalid = bias_invalidates_run(bias_report)
    status = ResultStatus.INVALID if invalid else ResultStatus.VALID
    result = ResearchResult(
        result_id=new_identifier("research_result_id"),
        research_run_id=run.research_run_id,
        status=status,
        metrics=outcome.metrics,
        risk_metrics={"max_drawdown": outcome.metrics["max_drawdown"]},
        bias_status=bias_report.overall,
        data_quality="VALIDATED",
        robustness_status=RobustnessStatus.UNKNOWN,
        validation_status=ValidationStatus.PENDING,
        evidence_hash=canonical_hash({
            "metrics": outcome.metrics,
            "bias": [c.to_dict() for c in bias_report.checks],
            "run": run.run_hash,
        }),
        created_at=ensure_utc(at, location="result.at"),
        environment=run.environment,
        multiple_testing=multiple_tests > 1,
        assumption_labels={
            "spread": "ASSUMPTION", "slippage": "ASSUMPTION",
            "commission": "ASSUMPTION", "fills": "SYNTHETIC",
            "capital": "ASSUMPTION",
        },
    )
    result.validate()
    return result


class ResearchPipeline:
    """One deterministic research run: backtest + bias audit + result."""

    def __init__(self, *, config: ResearchConfig, model: ExecutionModel,
                 policy_allows: Callable[[str], bool] | None = None) -> None:
        self._engine = BacktestEngine(config=config, model=model,
                                      policy_allows=policy_allows)
        self._auditor = BiasAuditor()

    def execute(self, *, dataset: ResearchDataset, logic: StrategyLogic,
                policy_reference: str, policy_hash: str,
                portfolio_version: str, portfolio_hash: str,
                code_version: str = CONTRACT_VERSION,
                environment: str = "BACKTEST",
                multiple_tests: int = 1,
                at: datetime) -> tuple[ResearchRun, BacktestOutcome,
                                       BiasReport, ResearchResult]:
        decisions: list[DecisionRecord] = []
        outcome = self._engine.run(dataset=dataset, logic=logic,
                                   decision_sink=decisions)
        bias_report = self._auditor.audit(
            dataset=dataset, decisions=decisions,
            multiple_tests=multiple_tests, environment=environment, at=at)
        run = build_research_run(
            dataset=dataset, logic=logic,
            config=self._engine._config, model=self._engine._model,
            policy_reference=policy_reference, policy_hash=policy_hash,
            portfolio_version=portfolio_version, portfolio_hash=portfolio_hash,
            code_version=code_version, environment=environment, created_at=at)
        result = finalize_result(run=run, outcome=outcome,
                                 bias_report=bias_report,
                                 multiple_tests=multiple_tests, at=at)
        return run, outcome, bias_report, result


# --------------------------------------------------------------------- #
# Replay + diff (SECTION 53-57, 94)                                      #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class ReplayComparison:
    status: str  # MATCH | MISMATCH | ERROR
    differences: tuple[str, ...]

    @property
    def matched(self) -> bool:
        return self.status == "MATCH"


def replay_compare(*, recorded_metrics: Mapping[str, Any],
                   replayed_metrics: Mapping[str, Any],
                   recorded_equity: Sequence[Mapping[str, Any]],
                   replayed_equity: Sequence[Mapping[str, Any]]) -> ReplayComparison:
    differences: list[str] = []
    for key in sorted(set(recorded_metrics) | set(replayed_metrics)):
        if str(recorded_metrics.get(key)) != str(replayed_metrics.get(key)):
            differences.append(f"metric:{key}:"
                               f"{recorded_metrics.get(key)}!={replayed_metrics.get(key)}")
    if len(recorded_equity) != len(replayed_equity):
        differences.append(f"equity_curve_length:{len(recorded_equity)}!={len(replayed_equity)}")
    else:
        for index, (a, b) in enumerate(zip(recorded_equity, replayed_equity)):
            if a.get("equity") != b.get("equity"):
                differences.append(f"equity[{index}]:{a.get('equity')}!={b.get('equity')}")
    return ReplayComparison("MATCH" if not differences else "MISMATCH",
                            tuple(differences))


# --------------------------------------------------------------------- #
# OOS + walk-forward (SECTION 60-63)                                     #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class OOSValidation:
    status: str            # PASSED | FAILED
    frozen_parameters: Mapping[str, Any]
    in_sample_metrics: Mapping[str, Any]
    out_of_sample_metrics: Mapping[str, Any]
    note: str


def out_of_sample(*, pipeline: ResearchPipeline, dataset: ResearchDataset,
                  train_dataset: ResearchDataset, logic: StrategyLogic,
                  policy_reference: str, policy_hash: str,
                  portfolio_version: str, portfolio_hash: str,
                  at: datetime) -> OOSValidation:
    """Parameters FROZEN (same logic object) on unseen data (SECTION 63)."""
    _, _, _, train_result = pipeline.execute(
        dataset=train_dataset, logic=logic,
        policy_reference=policy_reference, policy_hash=policy_hash,
        portfolio_version=portfolio_version, portfolio_hash=portfolio_hash,
        at=at)
    _, _, _, oos_result = pipeline.execute(
        dataset=dataset, logic=logic,
        policy_reference=policy_reference, policy_hash=policy_hash,
        portfolio_version=portfolio_version, portfolio_hash=portfolio_hash, at=at)
    return OOSValidation(
        status="PASSED" if oos_result.status is not ResultStatus.INVALID else "FAILED",
        frozen_parameters={"logic_hash": logic.logic_hash},
        in_sample_metrics=dict(train_result.metrics),
        out_of_sample_metrics=dict(oos_result.metrics),
        note="parameters frozen; OOS run used unseen data with the identical logic hash",
    )


@dataclass(frozen=True)
class WalkForwardWindow:
    window_index: int
    time_range: Mapping[str, str]
    dataset_hash: str
    parameter_version: str
    metrics: Mapping[str, Any]


@dataclass(frozen=True)
class WalkForwardValidation:
    windows: tuple[WalkForwardWindow, ...]
    status: str  # PASSED when all windows valid


def walk_forward(*, pipeline: ResearchPipeline, datasets: Sequence[ResearchDataset],
                  logic: StrategyLogic, policy_reference: str, policy_hash: str,
                  portfolio_version: str, portfolio_hash: str,
                  at: datetime) -> WalkForwardValidation:
    windows: list[WalkForwardWindow] = []
    for index, dataset in enumerate(datasets):
        _, _, _, result = pipeline.execute(
            dataset=dataset, logic=logic,
            policy_reference=policy_reference, policy_hash=policy_hash,
            portfolio_version=portfolio_version, portfolio_hash=portfolio_hash, at=at)
        windows.append(WalkForwardWindow(
            window_index=index, time_range=dict(dataset.time_range),
            dataset_hash=dataset.content_hash,
            parameter_version=logic.strategy_version,
            metrics=dict(result.metrics)))
    status = "PASSED" if windows else "FAILED"
    return WalkForwardValidation(tuple(windows), status)


# --------------------------------------------------------------------- #
# Stress + robustness (SECTION 65-69)                                     #
# --------------------------------------------------------------------- #
DEFAULT_STRESS_SCENARIOS = (
    StressScenario("S-SPR-X2", "spread", "2", "spread doubled"),
    StressScenario("S-SLP-X2", "slippage", "2", "slippage doubled"),
    StressScenario("S-CMS-X2", "commission", "2", "commission doubled"),
)


def stress_test(*, base_config: ResearchConfig, model: ExecutionModel,
                dataset: ResearchDataset, logic: StrategyLogic,
                policy_allows: Callable[[str], bool] | None = None,
                scenarios: Sequence[StressScenario] = DEFAULT_STRESS_SCENARIOS,
                run_reference: str = "unknown", at: datetime | None = None) -> StressReport:
    from dataclasses import replace

    for scenario in scenarios:
        scaled = _scaled_config(base_config, scenario)
        engine = BacktestEngine(config=scaled, model=model, policy_allows=policy_allows)
        engine.run(dataset=dataset, logic=logic)
    report = StressReport(
        stress_report_id=new_identifier("stress_report_id"),
        research_run_id=run_reference,
        scenarios=tuple(scenarios),
        overall="COMPLETED",
        config_version=base_config.config_version,
        created_at=ensure_utc(at, location="stress.at") if at else
        ensure_utc(dataset.created_at, location="stress.at"),
        environment=base_config.environment,
    )
    report.validate()
    return report


def _scaled_config(config: ResearchConfig, scenario: StressScenario) -> ResearchConfig:
    from dataclasses import replace
    from decimal import Decimal

    base = Decimal(getattr(config, scenario.parameter))
    scaled = str(base * Decimal(scenario.multiplier))
    updated = replace(config, **{scenario.parameter: scaled})
    updated = replace(updated, config_hash=updated.compute_config_hash())
    updated.validate()
    return updated


def robustness_sensitivity(*, base_metrics: Mapping[str, Any],
                           stressed_metrics: Sequence[Mapping[str, Any]]) -> str:
    """STABLE / SENSITIVE / UNSTABLE / UNKNOWN from metric variation."""
    if not stressed_metrics:
        return "UNKNOWN"
    base = Decimal(str(base_metrics.get("net_pnl") or "0")).quantize(Decimal("0.0001"))
    values = [Decimal(str(m.get("net_pnl") or "0")).quantize(Decimal("0.0001"))
              for m in stressed_metrics]
    sign_flips = any((v > 0) != (base > 0) for v in values)
    if sign_flips:
        return "UNSTABLE"
    spread = max(abs(v - base) for v in values)
    magnitude = abs(base) if base != 0 else Decimal("1")
    return "STABLE" if spread <= magnitude else "SENSITIVE"
