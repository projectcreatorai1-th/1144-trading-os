"""Phase 5 benchmark (SECTION 70). LOCAL DEVELOPMENT BENCHMARK ONLY.

Measures: order validation, OMS submission, EMS routing, simulation
execution, fill ingestion, position projection, ledger integration,
recovery scan. NOT production throughput claims.
Run: python benchmarks/phase5_benchmark.py
"""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.simulation.execution import SimulationExecutionAdapter  # noqa: E402
from adapters.simulation.sequencer import SimulationSequencer  # noqa: E402
from architecture.contracts.identifiers import new_identifier  # noqa: E402
from core.ems.engine import ExecutionManagementSystem  # noqa: E402
from core.execution.boundary import ExecutionContext  # noqa: E402
from core.execution.contracts import OrderStatus  # noqa: E402
from core.ledger.posting import LedgerPostingService  # noqa: E402
from core.oms.contracts import (  # noqa: E402
    ExecutionStatus,
    ExecutionType,
    execution_idempotency_key,
)
from core.oms.engine import OrderManagementSystem  # noqa: E402
from core.oms.projection import ExecutionProjector  # noqa: E402
from platform.database.sqlite_stores import StorageSet  # noqa: E402
from platform.security.contracts import Role  # noqa: E402
from tests.factories import make_risk_decision  # noqa: E402
from tests.test_phase5_boundary_oms import aligned, ctx, make_order  # noqa: E402

ITERATIONS = 100


def run(output: Path | None = None) -> dict:
    tmp = tempfile.mkdtemp()
    try:
        return _run(Path(tmp), output)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp_dir: Path, output: Path | None) -> dict:
    from tests.phase1_factories import at
    from core.risk.contracts import RiskResult

    storage = StorageSet(tmp_dir / "bench.db")
    posting = LedgerPostingService(storage.ledger, storage.audit)
    adapter = SimulationExecutionAdapter(SimulationSequencer())
    adapter.connect()
    ems = ExecutionManagementSystem(adapters={"SIMULATION": adapter})
    oms = OrderManagementSystem(orders=storage.orders,
                                reports=storage.execution_reports,
                                audit=storage.audit)
    projector = ExecutionProjector(states=storage.states, posting=posting,
                                   events=storage.events, audit=storage.audit)
    risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                              decision=RiskResult.ALLOW,
                              decision_time=at(11, 59), expires_at=at(13, 0))

    submit_durations = []
    orders = []
    for _ in range(ITERATIONS):
        order = aligned(make_order(), risk)
        t0 = time.monotonic()
        result = oms.submit(order, risk_decision=risk, execution_context=ctx())
        submit_durations.append((time.monotonic() - t0) * 1000.0)
        orders.append(result.order)

    ems_durations = []
    from dataclasses import replace as dc_replace
    for order in orders:
        accepted = dc_replace(order, status=OrderStatus.ACCEPTED)
        t0 = time.monotonic()
        ems.submit(accepted, risk_decision=risk, execution_context=ctx())
        ems_durations.append((time.monotonic() - t0) * 1000.0)

    fill_durations = []
    projection_durations = []
    for index, order in enumerate(orders):
        from tests.test_phase5_pipeline import _report

        report = _report(order, ref=f"BENCH-{index}")
        t0 = time.monotonic()
        oms.ingest_execution_report(report)
        fill_durations.append((time.monotonic() - t0) * 1000.0)
        t0 = time.monotonic()
        projector.project(report, order, at=at(12, 2))
        projection_durations.append((time.monotonic() - t0) * 1000.0)

    t0 = time.monotonic()
    active = list(oms.recover())
    recovery_ms = (time.monotonic() - t0) * 1000.0

    results = {
        "iterations": ITERATIONS,
        "oms_submit_ms_mean": round(statistics.fmean(submit_durations), 3),
        "ems_route_ms_mean": round(statistics.fmean(ems_durations), 3),
        "fill_ingest_ms_mean": round(statistics.fmean(fill_durations), 3),
        "projection_ms_mean": round(statistics.fmean(projection_durations), 3),
        "full_pipeline_ops_per_second": round(
            1000.0 / (statistics.fmean(submit_durations)
                      + statistics.fmean(ems_durations)
                      + statistics.fmean(fill_durations)
                      + statistics.fmean(projection_durations)), 1),
        "recovery_scan_ms": round(recovery_ms, 3),
        "active_orders_after_run": len(active),
        "ledger_entries": storage.ledger.count(),
    }
    storage.close()

    if output is not None:
        lines = [
            "# Phase 5 Benchmark (LOCAL DEVELOPMENT BENCHMARK)",
            "",
            "Deterministic data, local SQLite + simulation adapter.",
            "NOT production throughput claims.",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for key, value in results.items():
            lines.append(f"| {key} | {value} |")
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


if __name__ == "__main__":
    report = Path(__file__).resolve().parents[1] / "docs" / "phase-5" / "phase-5-benchmark.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(run(report), indent=2))
    print(f"written: {report}")
