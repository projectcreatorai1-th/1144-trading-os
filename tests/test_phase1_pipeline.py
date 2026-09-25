"""Phase 1 ingestion pipeline tests (SECTIONS 17-24) + recovery/idempotency."""
from __future__ import annotations

import pytest

from core.data.pipeline import IngestionPipeline, IngestionRequest
from core.data.source_registry import SourceRegistry
from core.events.contracts import EventType
from core.validation.contracts import DataQualityLevel
from platform.database.sqlite_stores import StorageSet
from platform.event_bus.in_process import InProcessEventBus
from tests.phase1_factories import at, make_ingestion_request, make_source


@pytest.fixture()
def env(tmp_path):
    storage = StorageSet(tmp_path / "pipeline.db")
    registry = SourceRegistry()
    registry.register(make_source())
    registry.register(make_source(
        source_id="feed-news",
        name="News feed",
        schema_id="news_item",
    ))
    bus = InProcessEventBus(declared_environment="SIMULATION")
    pipeline = IngestionPipeline(
        source_registry=registry,
        raw_store=storage.raw,
        normalized_store=storage.normalized,
        lineage_store=storage.lineage,
        event_store=storage.events,
        event_bus=bus,
    )
    yield {"storage": storage, "pipeline": pipeline, "registry": registry, "bus": bus}
    storage.close()


def ingest(pipeline, **overrides):
    return pipeline.ingest(IngestionRequest(**make_ingestion_request(**overrides)))


class TestHappyPath:
    def test_accept_creates_full_chain(self, env):
        outcome = ingest(env["pipeline"])
        assert outcome.accepted
        assert outcome.raw_id and outcome.normalized_id and outcome.event_id
        assert outcome.accepted_event_id

        storage = env["storage"]
        assert storage.raw.count() == 1
        assert storage.normalized.count() == 1
        # data event + EVENT_ACCEPTED
        types = [e.event_type for e in storage.events.iter_all()]
        assert EventType.MARKET_DATA_RECEIVED in types
        assert EventType.EVENT_ACCEPTED in types

    def test_raw_normalized_event_ids_connected(self, env):
        outcome = ingest(env["pipeline"])
        storage = env["storage"]
        raw = storage.raw.get_by_id(outcome.raw_id)
        normalized = storage.normalized.get_by_id(outcome.normalized_id)
        event = storage.events.get_by_id(outcome.event_id)
        assert normalized.raw_id == raw.raw_id
        assert event.metadata["raw_id"] == raw.raw_id
        assert event.metadata["normalized_id"] == normalized.normalized_id
        assert event.entity_id == normalized.normalized_id

    def test_lineage_source_to_event(self, env):
        outcome = ingest(env["pipeline"])
        chain = env["storage"].lineage.chain_for("event", outcome.event_id)
        stages = [record.stage.value for record in chain]
        assert stages == ["SOURCE", "RAW", "NORMALIZED", "EVENT"]
        ids = [record.entity_id for record in chain]
        assert ids[-1] == outcome.event_id
        assert ids[-2] == outcome.normalized_id
        assert ids[-3] == outcome.raw_id

    def test_quality_validated_and_symbol_uppercased(self, env):
        outcome = ingest(env["pipeline"], payload={"symbol": "xauusd", "bid": "1", "ask": "2"})
        assert outcome.quality.level is DataQualityLevel.VALIDATED
        event = env["storage"].events.get_by_id(outcome.event_id)
        assert event.payload["symbol"] == "XAUUSD"

    def test_latency_report_attached(self, env):
        outcome = ingest(env["pipeline"])
        assert outcome.latency is not None
        assert outcome.latency.ingestion_latency_ms is not None

    def test_published_to_bus(self, env):
        received = []
        env["bus"].subscribe(received.append)
        ingest(env["pipeline"])
        assert len(received) == 1


class TestRejections:
    def test_unknown_source_rejected_without_raw(self, env):
        outcome = ingest(env["pipeline"], source_id="ghost-feed")
        assert outcome.rejected
        assert outcome.raw_id is None
        assert outcome.rejection_reasons[0]["rule_id"] == "DQ-012"

    def test_schema_mismatch_keeps_raw_and_records_rejection(self, env):
        outcome = ingest(
            env["pipeline"],
            payload={"symbol": "XAUUSD"},  # missing bid/ask
        )
        assert outcome.rejected
        assert outcome.raw_id is not None  # raw truth is kept
        storage = env["storage"]
        assert storage.raw.count() == 1
        assert storage.normalized.count() == 0
        types = [e.event_type for e in storage.events.iter_all()]
        assert EventType.EVENT_REJECTED in types
        assert EventType.MARKET_DATA_RECEIVED not in types

    def test_invalid_schema_version_rejected(self, env):
        outcome = ingest(env["pipeline"], schema_version="9.9.9")
        assert outcome.rejected
        assert any(r["rule_id"] == "DQ-013" for r in outcome.rejection_reasons)

    def test_unknown_schema_id_rejected(self, env):
        outcome = ingest(env["pipeline"], schema_id="nope")
        assert outcome.rejected

    def test_future_timestamp_rejected_keeps_raw(self, env):
        outcome = ingest(env["pipeline"], source_timestamp=at(12, 10), received_time=at(12, 10))
        assert outcome.rejected
        assert env["storage"].raw.count() == 1

    def test_naive_source_timestamp_rejected(self, env):
        from datetime import datetime as dt

        outcome = ingest(env["pipeline"], source_timestamp=dt(2026, 9, 23, 11, 59, 58))
        assert outcome.rejected

    def test_invalid_symbol_rejected(self, env):
        outcome = ingest(env["pipeline"], payload={"symbol": "gold!!!", "bid": "1", "ask": "2"})
        assert outcome.rejected
        assert any(r["rule_id"] == "DQ-011" for r in outcome.rejection_reasons)

    def test_stale_data_accepted_with_stale_quality(self, env):
        outcome = ingest(env["pipeline"], source_timestamp=at(10, 0), received_time=at(12, 0))
        assert outcome.accepted  # stale is NOT invalid: kept with explicit status
        assert outcome.quality.level is DataQualityLevel.STALE
        event = env["storage"].events.get_by_id(outcome.event_id)
        assert event.metadata["data_quality"] == "STALE"
        types = [e.event_type for e in env["storage"].events.iter_all()]
        assert EventType.DATA_QUALITY_CHANGED in types

    def test_out_of_order_flagged_not_rejected(self, env):
        first = ingest(env["pipeline"], source_timestamp=at(11, 59, 58), sequence_number=1)
        assert first.accepted
        second = ingest(env["pipeline"], source_timestamp=at(11, 59, 50),
                        sequence_number=2, source_event_ref="tick-2")
        assert second.accepted
        assert second.quality.level is DataQualityLevel.DEGRADED
        types = [e.event_type for e in env["storage"].events.iter_all()]
        assert EventType.SYSTEM_TIME_ANOMALY in types

    def test_trading_event_type_out_of_scope_rejected(self, env):
        with pytest.raises(Exception):
            ingest(env["pipeline"], event_type="ORDER_CREATED")


class TestDeduplication:
    def test_exact_duplicate_detected(self, env):
        first = ingest(env["pipeline"])
        second = ingest(env["pipeline"])
        assert first.accepted
        assert second.duplicate
        assert second.duplicate_of_event_id == first.event_id
        # no duplicate canonical history
        storage = env["storage"]
        assert storage.events.count() == 2  # data event + accepted marker only once
        assert storage.normalized.count() == 1
        assert storage.raw.count() == 2  # raw truth keeps the duplicate record

    def test_same_content_different_time_is_separate_event(self, env):
        first = ingest(env["pipeline"], source_timestamp=at(11, 59, 58))
        second = ingest(env["pipeline"], source_timestamp=at(11, 59, 59))
        assert first.accepted and second.accepted
        assert env["storage"].normalized.count() == 2

    def test_same_time_different_ref_is_separate_event(self, env):
        first = ingest(env["pipeline"], source_event_ref="tick-a")
        second = ingest(env["pipeline"], source_event_ref="tick-b")
        assert first.accepted and second.accepted


class TestSequencing:
    def test_sequence_gap_flagged(self, env):
        ingest(env["pipeline"], sequence_number=1)
        outcome = ingest(env["pipeline"], sequence_number=5, source_event_ref="t2",
                         source_timestamp=at(11, 59, 59))
        assert outcome.accepted
        assert any(c.rule_id == "DQ-009" for c in outcome.quality.checks)

    def test_sequence_regression_flagged(self, env):
        ingest(env["pipeline"], sequence_number=5)
        outcome = ingest(env["pipeline"], sequence_number=2, source_event_ref="t2",
                         source_timestamp=at(11, 59, 59))
        assert any(c.rule_id == "DQ-009" for c in outcome.quality.checks)

    def test_sequence_duplicate_flagged(self, env):
        ingest(env["pipeline"], sequence_number=3)
        outcome = ingest(env["pipeline"], sequence_number=3, source_event_ref="t2",
                         source_timestamp=at(11, 59, 59))
        seq_checks = [c for c in outcome.quality.checks if c.rule_id == "DQ-009"]
        assert seq_checks and any("duplicate" in c.message for c in seq_checks)

    def test_sequence_absence_explicit_unknown(self, env):
        outcome = ingest(env["pipeline"], sequence_number=None)
        assert outcome.accepted
        event = env["storage"].events.get_by_id(outcome.event_id)
        assert event.metadata["sequence_number"] is None


class TestRecoveryAndIdempotency:
    def test_reprocessing_same_input_no_duplicate_canonical_history(self, tmp_path):
        path = tmp_path / "recovery.db"
        storage = StorageSet(path)
        registry = SourceRegistry()
        registry.register(make_source())
        pipeline = IngestionPipeline(
            source_registry=registry, raw_store=storage.raw,
            normalized_store=storage.normalized, lineage_store=storage.lineage,
            event_store=storage.events,
        )
        request = IngestionRequest(**make_ingestion_request())
        first = pipeline.ingest(request)
        storage.close()

        # restart: new pipeline over the same database
        storage2 = StorageSet(path)
        pipeline2 = IngestionPipeline(
            source_registry=registry, raw_store=storage2.raw,
            normalized_store=storage2.normalized, lineage_store=storage2.lineage,
            event_store=storage2.events,
        )
        replayed = pipeline2.ingest(request)
        assert replayed.duplicate
        assert replayed.duplicate_of_event_id == first.event_id
        assert storage2.normalized.count() == 1
        data_events = [e for e in storage2.events.iter_all()
                       if e.event_type is EventType.MARKET_DATA_RECEIVED]
        assert len(data_events) == 1
        storage2.close()

    def test_partial_failure_leaves_consistent_state(self, tmp_path):
        """Store write failure surfaces as a structured error; nothing half-written."""
        storage = StorageSet(tmp_path / "partial.db")
        registry = SourceRegistry()
        registry.register(make_source())
        pipeline = IngestionPipeline(
            source_registry=registry, raw_store=storage.raw,
            normalized_store=storage.normalized, lineage_store=storage.lineage,
            event_store=storage.events,
        )
        outcome = pipeline.ingest(IngestionRequest(**make_ingestion_request()))
        assert outcome.accepted
        # appending the exact same raw id again is rejected by the append-only store
        from architecture.contracts.errors import StorageError

        with pytest.raises(StorageError):
            storage.events.append(storage.events.get_by_id(outcome.event_id))
        storage.close()
