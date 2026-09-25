"""Phase 1 benchmark (SECTION 41).

Measures, on the developer machine, with deterministic data:
- ingestion throughput (events/second)
- validation + normalization + event creation durations (from latency reports)
- event store write and query durations

Local measurements only - no production-scale claims.
Run: python benchmarks/phase1_benchmark.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.data.pipeline import IngestionPipeline, IngestionRequest  # noqa: E402
from core.data.source_registry import SourceRegistry  # noqa: E402
from platform.database.sqlite_stores import StorageSet  # noqa: E402
from platform.event_bus.in_process import InProcessEventBus  # noqa: E402

TICK_COUNT = 300


def build(tmp_dir: Path):
    storage = StorageSet(tmp_dir / "bench.db")
    registry = SourceRegistry()

    from tests.phase1_factories import at, make_source

    registry.register(make_source())
    pipeline = IngestionPipeline(
        source_registry=registry, raw_store=storage.raw,
        normalized_store=storage.normalized, lineage_store=storage.lineage,
        event_store=storage.events, event_bus=InProcessEventBus(),
    )
    return storage, pipeline, at


def run(output: Path | None = None) -> dict:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        storage, pipeline, at = build(tmp_dir)

        durations = []
        ingestion_ms = []
        processing_ms = []
        end_to_end_ms = []
        from tests.phase1_factories import make_ingestion_request

        started = time.monotonic()
        for index in range(TICK_COUNT):
            request = IngestionRequest(**make_ingestion_request(
                source_event_ref=f"tick-{index}",
                source_timestamp=at(11, 0) + timedelta(seconds=index),
                sequence_number=index + 1,
            ))
            t0 = time.monotonic()
            outcome = pipeline.ingest(request)
            durations.append((time.monotonic() - t0) * 1000.0)
            assert outcome.accepted
            ingestion_ms.append(outcome.latency.ingestion_latency_ms)
            processing_ms.append(outcome.latency.processing_latency_ms)
            end_to_end_ms.append(outcome.latency.end_to_end_latency_ms)
        total = time.monotonic() - started

        t0 = time.monotonic()
        count = storage.events.count()
        store_op_ms = (time.monotonic() - t0) * 1000.0

        t0 = time.monotonic()
        queried = len(list(storage.events.query_by_type("MARKET_DATA_RECEIVED")))
        query_ms = (time.monotonic() - t0) * 1000.0

        results = {
            "ticks": TICK_COUNT,
            "throughput_events_per_second": round(TICK_COUNT / total, 1),
            "ingest_wall_ms_mean": round(statistics.fmean(durations), 3),
            "ingest_wall_ms_max": round(max(durations), 3),
            "measured_ingestion_latency_ms_mean": round(statistics.fmean(ingestion_ms), 3),
            "measured_processing_latency_ms_mean": round(statistics.fmean(processing_ms), 3),
            "measured_end_to_end_latency_ms_mean": round(statistics.fmean(end_to_end_ms), 3),
            "store_count_op_ms": round(store_op_ms, 3),
            "query_by_type_ms": round(query_ms, 3),
            "store_event_count": count,
            "query_result_count": queried,
        }
        storage.close()

    if output is not None:
        lines = [
            "# Phase 1 Benchmark (local, developer machine)",
            "",
            "Deterministic data, in-memory-scale local run. NOT a production-scale claim.",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for key, value in results.items():
            lines.append(f"| {key} | {value} |")
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


if __name__ == "__main__":
    report = Path(__file__).resolve().parents[1] / "docs" / "phase-1-benchmark.md"
    print(json.dumps(run(report), indent=2))
    print(f"written: {report}")
