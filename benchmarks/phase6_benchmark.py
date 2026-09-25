"""Phase 6 benchmark (SECTION 103). LOCAL DEVELOPMENT BENCHMARK ONLY."""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from architecture.contracts.identifiers import new_identifier  # noqa: E402
from architecture.contracts.time import UTC  # noqa: E402
from core.backtest.engine import StrategyLogic  # noqa: E402
from core.research.contracts import Observation  # noqa: E402
from core.research.pipeline import (  # noqa: E402
    ResearchPipeline,
    replay_compare,
    stress_test,
    walk_forward,
)
from tests.phase1_factories import at  # noqa: E402
from tests.test_phase6_core import make_config, make_dataset, make_model

ITERATIONS = 20
SERIES = 200


def series_dataset(count=SERIES):
    from tests.test_phase6_core import make_dataset

    base = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    observations = [
        Observation(
            symbol="XAUUSD",
            event_time=base + timedelta(hours=i),
            available_time=base + timedelta(hours=i),
            payload={"price": str(2650 + (i % 20))},
        )
        for i in range(count)
    ]
    return make_dataset(observations)


def benchmark_logic(observation, state):
    seen = len(state.decisions)
    return "OPEN" if seen % 5 == 0 and seen > 0 else "CLOSE" if seen % 5 == 3 else None


LOGIC = StrategyLogic(
    strategy_id=new_identifier("strategy_id"), strategy_version="1.0.0",
    logic_hash="a" * 64, on_observation=benchmark_logic,
)


def run(output: Path | None = None) -> dict:
    tmp = tempfile.mkdtemp()
    try:
        return _run(Path(tmp), output)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(_tmp: Path, output: Path | None) -> dict:
    dataset = series_dataset()

    durations = []
    for _ in range(ITERATIONS):
        pipeline = ResearchPipeline(config=make_config(), model=make_model())
        t0 = time.monotonic()
        pipeline.execute(dataset=dataset, logic=LOGIC,
                        policy_reference="pol", policy_hash="p" * 64,
                        portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(14, 0))
        durations.append((time.monotonic() - t0) * 1000.0)

    per_event = statistics.fmean(durations) / SERIES

    t0 = time.monotonic()
    validation = walk_forward(
        pipeline=ResearchPipeline(config=make_config(), model=make_model()),
        datasets=[series_dataset(), series_dataset(), series_dataset()],
        logic=LOGIC, policy_reference="pol", policy_hash="p" * 64,
        portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(14, 0))
    wf_ms = (time.monotonic() - t0) * 1000.0

    t0 = time.monotonic()
    stress = stress_test(base_config=make_config(), model=make_model(),
                         dataset=dataset, logic=LOGIC, at=at(14, 0))
    stress_ms = (time.monotonic() - t0) * 1000.0

    t0 = time.monotonic()
    pipeline = ResearchPipeline(config=make_config(), model=make_model())
    _, first, _, _ = pipeline.execute(
        dataset=dataset, logic=LOGIC, policy_reference="pol", policy_hash="p" * 64,
        portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(14, 0))
    _, second, _, _ = pipeline.execute(
        dataset=dataset, logic=LOGIC, policy_reference="pol", policy_hash="p" * 64,
        portfolio_version="1.0.0", portfolio_hash="q" * 64, at=at(15, 0))
    comparison = replay_compare(recorded_metrics=first.metrics,
                                replayed_metrics=second.metrics,
                                recorded_equity=first.state.equity_curve,
                                replayed_equity=second.state.equity_curve)
    replay_ms = (time.monotonic() - t0) * 1000.0

    results = {
        "iterations": ITERATIONS,
        "series_length": SERIES,
        "backtest_run_ms_mean": round(statistics.fmean(durations), 3),
        "event_processing_ms_per_event": round(per_event, 4),
        "walk_forward_3_windows_ms": round(wf_ms, 3),
        "stress_3_scenarios_ms": round(stress_ms, 3),
        "replay_compare_pair_ms": round(replay_ms, 3),
        "replay_deterministic": comparison.matched,
        "stress_overall": stress.overall,
        "walk_forward_status": validation.status,
    }
    if output is not None:
        lines = [
            "# Phase 6 Benchmark (LOCAL DEVELOPMENT BENCHMARK)",
            "",
            "Deterministic synthetic series (labeled SYNTHETIC, not historical evidence).",
            "NOT production capacity claims.",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for key, value in results.items():
            lines.append(f"| {key} | {value} |")
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


if __name__ == "__main__":
    report = Path(__file__).resolve().parents[1] / "docs" / "phase-6" / "phase-6-benchmark.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(run(report), indent=2))
    print(f"written: {report}")
