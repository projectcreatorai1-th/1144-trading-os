"""Phase 4 benchmark (SECTION 53).

Local, developer-machine measurements with deterministic data: strategy
resolution, eligibility, evaluation, portfolio allocation, exposure
aggregation, portfolio decision, intent gate (risk boundary), replay.
NOT production-capacity claims. Run: python benchmarks/phase4_benchmark.py
"""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from architecture.contracts.identifiers import new_identifier  # noqa: E402
from core.policy.contracts import PolicyStatus, PolicyType  # noqa: E402
from core.policy.registry import ActorContext, PolicyRegistry  # noqa: E402
from core.portfolio.capacity import LiquidityBudget  # noqa: E402
from core.portfolio.decision import PortfolioDecisionEngine  # noqa: E402
from core.portfolio.exposure import ExposureAggregator, ExposureLeg  # noqa: E402
from core.portfolio.portfolio_contract import (  # noqa: E402
    Portfolio,
    PortfolioMembership,
    PortfolioStatus,
)
from core.risk.engine import RiskEngine, RiskEvaluationRequest  # noqa: E402
from core.strategy.contracts import (  # noqa: E402
    Strategy,
    StrategyLifecycle,
    StrategyType,
)
from core.strategy.evaluation import StrategyEligibilityEvaluator  # noqa: E402
from core.strategy.gate import IntentGate  # noqa: E402
from core.strategy.intent import IntentDirection, IntentType, StrategyIntent, Urgency  # noqa: E402
from platform.database.sqlite_stores import StorageSet  # noqa: E402
from platform.security.contracts import Role  # noqa: E402
from tests.factories import make_policy  # noqa: E402
from tests.phase1_factories import at  # noqa: E402
from tests.test_phase3_risk_engine import activate as activate_policy, full_context  # noqa: E402
from tests.test_phase4_strategy import make_capability, make_config  # noqa: E402

ITERATIONS = 100


def _strategy(lifecycle=StrategyLifecycle.DEMO):
    strategy = Strategy(
        strategy_id=new_identifier("strategy_id"), strategy_version="1.0.0",
        strategy_type=StrategyType.TREND, name="bench", owner="desk",
        lifecycle_status=lifecycle, environment="SIMULATION",
        effective_from=at(0, 0), created_at=at(9, 0), updated_at=at(9, 0),
        configuration_version="1.0.0", capability_profile_id="cap-bench",
    )
    strategy.validate()
    return strategy


def _membership(portfolio_id, sid, priority):
    return PortfolioMembership(
        portfolio_id=portfolio_id, strategy_id=sid, strategy_version="1.0.0",
        allocation="400", risk_budget="10", priority=priority, enabled=True,
        effective_from=at(0, 0), environment="SIMULATION",
    )


def run(output: Path | None = None) -> dict:
    tmp = tempfile.mkdtemp()
    try:
        return _run(Path(tmp), output)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp_dir: Path, output: Path | None) -> dict:
    storage = StorageSet(tmp_dir / "bench.db")
    policies = PolicyRegistry(storage.policies, storage.audit)
    activate_policy({"registry": policies, "storage": storage}, PolicyType.EXPOSURE_POLICY,
                    rules=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                            "field": "positions.gross", "op": "<=",
                            "limit": "max_gross", "on_trigger": "BLOCK", "critical": True},),
                    limits={"max_gross": "2000"})
    risk_engine = RiskEngine(policies=policies, audit=storage.audit)
    gate = IntentGate(risk_engine, storage.audit)

    strategy = _strategy()
    capability = make_capability(capability_profile_id="cap-bench")
    config = make_config(strategy)
    eligibility_evaluator = StrategyEligibilityEvaluator()
    context = full_context(gross_exposure="1000")

    # strategy store resolution
    storage.strategies.save(strategy)
    storage.strategies.save_capability(capability)
    storage.strategies.save_config(config)

    portfolio = Portfolio(
        portfolio_id=new_identifier("portfolio_id"), portfolio_version="1.0.0",
        name="bench", account_scope="ACC-1", environment="SIMULATION",
        status=PortfolioStatus.ACTIVE, base_currency="USD",
        strategy_members=(strategy.strategy_id,), allocation_policy_id="fixed",
        effective_from=at(0, 0), created_at=at(9, 0),
    )
    portfolio.validate()
    storage.portfolios.save(portfolio)
    members = [_membership(portfolio.portfolio_id, strategy.strategy_id, 1)]

    eligibility_durations = []
    for _ in range(ITERATIONS):
        t0 = time.monotonic()
        eligibility_evaluator.evaluate(
            strategy=strategy, capability=capability, config=config,
            context=context, symbol="XAUUSD",
            correlation_id=new_identifier("correlation_id"), at=at(12, 0))
        eligibility_durations.append((time.monotonic() - t0) * 1000.0)

    decision_engine = PortfolioDecisionEngine()
    legs = [ExposureLeg(strategy.strategy_id, "XAUUSD", "METALS", "LONG", "1000")]
    decision_durations = []
    decision = None
    for _ in range(ITERATIONS):
        t0 = time.monotonic()
        decision = decision_engine.evaluate(
            portfolio=portfolio, memberships=members,
            eligible_strategy_ids=[strategy.strategy_id],
            current_legs=legs, intent_legs=[],
            total_capital="1000", reserved_capital="100", requested_capital={},
            capacities=[], liquidity={}, constraint_limits={"max_total_exposure": "2000"},
            at=at(12, 0), correlation_id=new_identifier("correlation_id"))
        decision_durations.append((time.monotonic() - t0) * 1000.0)

    aggregator = ExposureAggregator()
    exposure_durations = []
    for _ in range(ITERATIONS):
        t0 = time.monotonic()
        aggregator.aggregate(portfolio_id=portfolio.portfolio_id,
                             legs=legs + [ExposureLeg("s2", "EURUSD", "FX", "SHORT", "300")],
                             environment="SIMULATION", computed_at=at(12, 0))
        exposure_durations.append((time.monotonic() - t0) * 1000.0)

    gate_durations = []
    for _ in range(ITERATIONS):
        intent = StrategyIntent(
            intent_id=new_identifier("intent_id"), strategy_id=strategy.strategy_id,
            strategy_version="1.0.0", intent_type=IntentType.OPEN, symbol="XAUUSD",
            direction=IntentDirection.LONG, requested_quantity="0.5",
            entry_conditions=({"condition_id": "C"},), exit_conditions=(),
            urgency=Urgency.NORMAL, rationale="bench",
            risk_context_hash="a" * 64, source_event_id=new_identifier("event_id"),
            correlation_id=new_identifier("correlation_id"), environment="SIMULATION",
            created_at=at(12, 0), expires_at=at(12, 30))
        t0 = time.monotonic()
        gate.authorize(intent=intent, portfolio_decision=decision,
                       base_context=full_context(gross_exposure="1000"), at=at(12, 0))
        gate_durations.append((time.monotonic() - t0) * 1000.0)

    # replay determinism: same inputs twice
    t0 = time.monotonic()
    first = gate.authorize(
        intent=StrategyIntent(
            intent_id=new_identifier("intent_id"), strategy_id=strategy.strategy_id,
            strategy_version="1.0.0", intent_type=IntentType.OPEN, symbol="XAUUSD",
            direction=IntentDirection.LONG, requested_quantity="0.5",
            entry_conditions=({"condition_id": "C"},), exit_conditions=(),
            urgency=Urgency.NORMAL, rationale="bench",
            risk_context_hash="a" * 64, source_event_id=new_identifier("event_id"),
            correlation_id=new_identifier("correlation_id"), environment="SIMULATION",
            created_at=at(12, 0), expires_at=at(12, 30)),
        portfolio_decision=decision, base_context=full_context(gross_exposure="1000"),
        at=at(12, 0))
    second = gate.authorize(
        intent=StrategyIntent(
            intent_id=new_identifier("intent_id"), strategy_id=strategy.strategy_id,
            strategy_version="1.0.0", intent_type=IntentType.OPEN, symbol="XAUUSD",
            direction=IntentDirection.LONG, requested_quantity="0.5",
            entry_conditions=({"condition_id": "C"},), exit_conditions=(),
            urgency=Urgency.NORMAL, rationale="bench",
            risk_context_hash="a" * 64, source_event_id=new_identifier("event_id"),
            correlation_id=new_identifier("correlation_id"), environment="SIMULATION",
            created_at=at(12, 0), expires_at=at(12, 30)),
        portfolio_decision=decision, base_context=full_context(gross_exposure="1000"),
        at=at(12, 0))
    replay_ms = (time.monotonic() - t0) * 1000.0

    results = {
        "iterations": ITERATIONS,
        "strategy_eligibility_ms_mean": round(statistics.fmean(eligibility_durations), 3),
        "portfolio_decision_ms_mean": round(statistics.fmean(decision_durations), 3),
        "exposure_aggregation_ms_mean": round(statistics.fmean(exposure_durations), 3),
        "intent_gate_ms_mean": round(statistics.fmean(gate_durations), 3),
        "intent_gates_per_second": round(1000.0 / statistics.fmean(gate_durations), 1),
        "replay_pair_ms": round(replay_ms, 3),
        "replay_deterministic": first.permission == second.permission,
    }
    storage.close()

    if output is not None:
        lines = [
            "# Phase 4 Benchmark (local, developer machine)",
            "",
            "Deterministic data. NOT production-capacity claims.",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for key, value in results.items():
            lines.append(f"| {key} | {value} |")
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


if __name__ == "__main__":
    report = Path(__file__).resolve().parents[1] / "docs" / "phase-4-benchmark.md"
    print(json.dumps(run(report), indent=2))
    print(f"written: {report}")
