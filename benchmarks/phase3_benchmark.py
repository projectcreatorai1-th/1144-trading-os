"""Phase 3 benchmark (SECTION 37).

Local, developer-machine measurements with deterministic data: policy
resolution, context creation, rule evaluation, engine evaluation, decision
validation and replay. NOT production-scale claims.
Run: python benchmarks/phase3_benchmark.py
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
from core.policy.evaluation import PolicyEvaluator  # noqa: E402
from core.policy.registry import ActorContext, PolicyRegistry  # noqa: E402
from core.risk.context import build_context  # noqa: E402
from core.risk.engine import RiskEngine, RiskEvaluationRequest  # noqa: E402
from core.risk.replay import RiskReplayService  # noqa: E402
from core.risk.validator import RiskDecisionValidator  # noqa: E402
from platform.database.sqlite_stores import StorageSet  # noqa: E402
from platform.security.contracts import Role  # noqa: E402
from tests.factories import make_policy  # noqa: E402
from tests.phase1_factories import at  # noqa: E402

ITERATIONS = 200


def setup(tmp_dir: Path):
    storage = StorageSet(tmp_dir / "bench.db")
    registry = PolicyRegistry(storage.policies, storage.audit)
    author = ActorContext(user_id=new_identifier("user_id"), role=Role.ADMIN)
    approver = ActorContext(user_id=new_identifier("user_id"), role=Role.APPROVER)

    rules = tuple(
        {"rule_id": f"R-{index:02d}", "dimension": "EXPOSURE",
         "field": "positions.gross", "op": "<=", "limit": f"max_gross_{index}",
         "on_trigger": "BLOCK", "critical": index == 0}
        for index in range(5)
    )
    policy = make_policy(
        policy_type=PolicyType.EXPOSURE_POLICY, status=PolicyStatus.DRAFT,
        environment="SIMULATION", conditions=rules,
        actions=({"constrain": "risk"},),
        limits={f"max_gross_{i}": str(1000 + i) for i in range(5)},
        priority=10,
    )
    registry.create(policy, author, at=at(9, 0))
    versions = list(storage.policies.iter_versions(policy.policy_id))
    registry.submit_review(policy.policy_id, versions[-1].policy_version, author, at=at(9, 1))
    versions = list(storage.policies.iter_versions(policy.policy_id))
    registry.approve(policy.policy_id, versions[-1].policy_version, approver, at=at(9, 2))
    versions = list(storage.policies.iter_versions(policy.policy_id))
    registry.activate(policy.policy_id, versions[-1].policy_version, author, at=at(9, 3))
    return storage, registry


def run(output: Path | None = None) -> dict:
    tmp = tempfile.mkdtemp()
    try:
        return _run(Path(tmp), output)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp_dir: Path, output: Path | None) -> dict:
    storage, registry = setup(tmp_dir)
    engine = RiskEngine(policies=registry, audit=storage.audit)
    evaluator = PolicyEvaluator()

    context_values = dict(
        account_equity="10000", account_margin_level="500", gross_exposure="1000",
        drawdown_pct="1", market_state="NORMAL", volatility_state="NORMAL",
        spread_state="NORMAL", liquidity_state="NORMAL", data_quality="VALIDATED",
        event_risk="NORMAL", system_state="RUNNING", execution_state="READY",
    )

    context_durations = []
    for index in range(ITERATIONS):
        values = dict(context_values)
        values["gross_exposure"] = str(900 + (index % 200))
        t0 = time.monotonic()
        build_context(as_of=at(12, 0), environment="SIMULATION", **values)
        context_durations.append((time.monotonic() - t0) * 1000.0)

    resolve_durations = []
    for _ in range(ITERATIONS):
        t0 = time.monotonic()
        registry.resolve_active(PolicyType.EXPOSURE_POLICY, environment="SIMULATION", at=at(12, 0))
        resolve_durations.append((time.monotonic() - t0) * 1000.0)

    policy = registry.resolve_active(PolicyType.EXPOSURE_POLICY, environment="SIMULATION", at=at(12, 0))
    context = build_context(as_of=at(12, 0), environment="SIMULATION", **context_values)
    flattened = context.flattened()
    rule_durations = []
    for _ in range(ITERATIONS):
        t0 = time.monotonic()
        evaluator.evaluate(policy=policy, context=flattened, environment="SIMULATION",
                           timestamp=at(12, 0), correlation_id=new_identifier("correlation_id"))
        rule_durations.append((time.monotonic() - t0) * 1000.0)

    engine_durations = []
    first_decision = None
    for _ in range(ITERATIONS):
        request = RiskEvaluationRequest(
            action_type="NEW_EXPOSURE", subject="XAUUSD", environment="SIMULATION",
            requested_exposure="100", context=context,
            correlation_id=new_identifier("correlation_id"), at=at(12, 0))
        t0 = time.monotonic()
        decision = engine.evaluate(request)
        engine_durations.append((time.monotonic() - t0) * 1000.0)
        if first_decision is None:
            first_decision = decision
    storage.risk_decisions.append(first_decision, context)

    validator = RiskDecisionValidator()
    validation_durations = []
    for _ in range(ITERATIONS):
        t0 = time.monotonic()
        validator.validate(first_decision, environment="SIMULATION", now=at(12, 0))
        validation_durations.append((time.monotonic() - t0) * 1000.0)

    replay = RiskReplayService(storage.risk_decisions, registry)
    t0 = time.monotonic()
    comparison = replay.replay(first_decision.risk_decision_id, environment="REPLAY")
    replay_ms = (time.monotonic() - t0) * 1000.0

    results = {
        "iterations": ITERATIONS,
        "policy_rules_per_policy": len(policy.conditions),
        "context_creation_ms_mean": round(statistics.fmean(context_durations), 3),
        "policy_resolution_ms_mean": round(statistics.fmean(resolve_durations), 3),
        "rule_evaluation_ms_mean": round(statistics.fmean(rule_durations), 3),
        "engine_evaluation_ms_mean": round(statistics.fmean(engine_durations), 3),
        "engine_evaluations_per_second": round(1000.0 / statistics.fmean(engine_durations), 1),
        "decision_validation_ms_mean": round(statistics.fmean(validation_durations), 3),
        "replay_single_ms": round(replay_ms, 3),
        "replay_status": comparison.status,
    }
    storage.close()

    if output is not None:
        lines = [
            "# Phase 3 Benchmark (local, developer machine)",
            "",
            "Deterministic data. NOT a production-scale claim.",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for key, value in results.items():
            lines.append(f"| {key} | {value} |")
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


if __name__ == "__main__":
    report = Path(__file__).resolve().parents[1] / "docs" / "phase-3-benchmark.md"
    print(json.dumps(run(report), indent=2))
    print(f"written: {report}")
