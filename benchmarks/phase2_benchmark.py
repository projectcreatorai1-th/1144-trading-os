"""Phase 2 benchmark (SECTION 55).

Local, developer-machine measurements with deterministic data:
state transition throughput, snapshot creation, state rebuild, ledger
posting throughput, balance reconstruction and reconciliation time.
NOT production-scale claims. Run: python benchmarks/phase2_benchmark.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from architecture.contracts.identifiers import new_identifier  # noqa: E402
from core.events.contracts import EventType  # noqa: E402
from core.ledger.balance import BalanceReconstructor  # noqa: E402
from core.ledger.contracts import LedgerType  # noqa: E402
from core.ledger.posting import LedgerDraft, LedgerPostingService  # noqa: E402
from core.reconciliation.contracts import ReconciliationScope  # noqa: E402
from core.reconciliation.engine import ReconciliationEngine  # noqa: E402
from core.state.processor import EventStateLedgerProcessor  # noqa: E402
from core.state.projector import EventStateProjector  # noqa: E402
from core.state.rebuilder import StateRebuilder  # noqa: E402
from core.state.snapshot import SnapshotService  # noqa: E402
from core.state.contracts import StateCategory  # noqa: E402
from platform.database.sqlite_stores import StorageSet  # noqa: E402

EVENT_COUNT = 200


def system_event(state: str, hour: int, second: int):
    from tests.factories import make_event
    from tests.phase1_factories import at

    return make_event(
        event_type=EventType.SYSTEM_STATE_CHANGED,
        payload={"state": state, "system": "bench-os"},
        event_time=at(hour, 0, second), received_time=at(hour, 0, second),
        correlation_id=new_identifier("correlation_id"),
    )


def cycle(starting: str) -> str:
    return {"READY": "RUNNING", "RUNNING": "DEGRADED", "DEGRADED": "RUNNING"}[starting]


def run(output: Path | None = None) -> dict:
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp()
    try:
        return _run_benchmark(Path(tmp), output)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_benchmark(tmp_dir: Path, output: Path | None) -> dict:
    storage = StorageSet(tmp_dir / "bench.db")
    posting = LedgerPostingService(storage.ledger, storage.audit)
    processor = EventStateLedgerProcessor(
        states=storage.states, events=storage.events, posting=posting,
        audit=storage.audit,
    )
    processor.register_ledger_handler("ECONOMIC_EVENT", lambda event: LedgerDraft(
        entry_type=LedgerType.CASH, account_id=event.payload["account_id"],
        amount=event.payload["deposit_amount"], currency="USD", reason="bench",
    ))

    from tests.factories import make_event
    from tests.phase1_factories import at

    # state transition throughput (machine-backed category)
    state_durations = []
    state = "READY"  # first event: STARTING -> READY (valid machine transition)
    for index in range(EVENT_COUNT):
        event = system_event(state, 11, index % 60)
        storage.events.append(event)
        t0 = time.monotonic()
        processor.process(event)
        state_durations.append((time.monotonic() - t0) * 1000.0)
        state = cycle(state)

    # ledger posting throughput
    ledger_durations = []
    for index in range(EVENT_COUNT):
        event = make_event(
            event_type=EventType.ECONOMIC_EVENT,
            payload={"deposit_amount": "1.25", "account_id": "ACC-BENCH", "currency": "USD"},
            event_time=at(11, 0, index % 60), received_time=at(11, 0, index % 60),
            correlation_id=new_identifier("correlation_id"),
        )
        storage.events.append(event)
        t0 = time.monotonic()
        processor.process(event)
        ledger_durations.append((time.monotonic() - t0) * 1000.0)

    # snapshot creation
    current = storage.states.get_current_state("SYSTEM_STATE", "bench-os")
    snapshots = SnapshotService(storage.snapshots, storage.audit)
    t0 = time.monotonic()
    snapshots.create(
        state=current,
        event_sequence=storage.events.get_sequence(current.source_event_id),
        last_event_id=current.source_event_id, created_at=at(12, 0),
    )
    snapshot_ms = (time.monotonic() - t0) * 1000.0

    # state rebuild
    projector = EventStateProjector()
    rebuilder = StateRebuilder(storage.events, projector)
    t0 = time.monotonic()
    report = rebuilder.rebuild(entity_type=StateCategory.SYSTEM_STATE, entity_id="bench-os")
    rebuild_ms = (time.monotonic() - t0) * 1000.0

    # balance reconstruction
    t0 = time.monotonic()
    balance = BalanceReconstructor(storage.ledger).reconstruct("ACC-BENCH", currency="USD")
    balance_ms = (time.monotonic() - t0) * 1000.0

    # reconciliation
    engine = ReconciliationEngine(storage.reconciliations, storage.audit)
    t0 = time.monotonic()
    engine.compare_values_map(
        scope=ReconciliationScope.BALANCE, environment="SIMULATION",
        internal_values={"ACC-BENCH": balance.closing_balance},
        external_values={"ACC-BENCH": str(balance.closing_balance)},
        source="bench", comparison_time=at(12, 0),
    )
    recon_ms = (time.monotonic() - t0) * 1000.0

    results = {
        "events": EVENT_COUNT * 2,
        "state_transition_ms_mean": round(statistics.fmean(state_durations), 3),
        "state_transitions_per_second": round(1000.0 / statistics.fmean(state_durations), 1),
        "ledger_posting_ms_mean": round(statistics.fmean(ledger_durations), 3),
        "ledger_posts_per_second": round(1000.0 / statistics.fmean(ledger_durations), 1),
        "snapshot_creation_ms": round(snapshot_ms, 3),
        "state_rebuild_ms": round(rebuild_ms, 3),
        "state_rebuild_applied_events": report.applied_events,
        "balance_reconstruction_ms": round(balance_ms, 3),
        "balance_total": str(balance.closing_balance),
        "reconciliation_ms": round(recon_ms, 3),
    }
    storage.close()

    if output is not None:
        lines = [
            "# Phase 2 Benchmark (local, developer machine)",
            "",
            "Deterministic data, local SQLite. NOT a production-scale claim.",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for key, value in results.items():
            lines.append(f"| {key} | {value} |")
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


if __name__ == "__main__":
    report = Path(__file__).resolve().parents[1] / "docs" / "phase-2-benchmark.md"
    print(json.dumps(run(report), indent=2))
    print(f"written: {report}")
