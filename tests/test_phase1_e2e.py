"""Phase 1 end-to-end test (SECTION 37):

SOURCE -> RAW -> VALIDATE -> NORMALIZE -> QUALITY -> EVENT -> EVENT STORE
-> TIMELINE -> REPLAY, with raw_id -> normalized_id -> event_id connected and
source -> provenance -> lineage -> correlation -> causation unbroken.
"""
from __future__ import annotations

from architecture.contracts.causality import validate_causal_chain
from core.data.pipeline import IngestionPipeline, IngestionRequest
from core.data.source_registry import SourceRegistry
from core.events.contracts import EventType
from core.events.replay import ReplayFilter, ReplayRunner
from core.events.timeline import EventTimelineService
from platform.database.sqlite_stores import StorageSet
from tests.phase1_factories import at, make_ingestion_request, make_source


def build_pipeline(tmp_path):
    storage = StorageSet(tmp_path / "e2e.db")
    registry = SourceRegistry()
    registry.register(make_source())
    pipeline = IngestionPipeline(
        source_registry=registry, raw_store=storage.raw,
        normalized_store=storage.normalized, lineage_store=storage.lineage,
        event_store=storage.events,
    )
    return storage, pipeline


def test_end_to_end_data_to_event_to_replay(tmp_path):
    storage, pipeline = build_pipeline(tmp_path)

    accepted = []
    for index in range(3):
        outcome = pipeline.ingest(IngestionRequest(**make_ingestion_request(
            source_event_ref=f"tick-{index}",
            source_timestamp=at(11, 59, 50 + index),
            sequence_number=index + 1,
        )))
        assert outcome.accepted
        accepted.append(outcome)

    # one out-of-order tick (accepted, flagged, timestamps untouched)
    out_of_order = pipeline.ingest(IngestionRequest(**make_ingestion_request(
        source_event_ref="tick-late",
        source_timestamp=at(11, 59, 48),
        sequence_number=4,
    )))
    assert out_of_order.accepted

    # one invalid input (kept raw, rejected, EVENT_REJECTED recorded)
    rejected = pipeline.ingest(IngestionRequest(**make_ingestion_request(
        source_event_ref="tick-bad",
        payload={"symbol": "XAU###$", "bid": "1", "ask": "2"},
    )))
    assert rejected.rejected

    # --- connections -------------------------------------------------------
    for outcome in accepted:
        raw = storage.raw.get_by_id(outcome.raw_id)
        normalized = storage.normalized.get_by_id(outcome.normalized_id)
        event = storage.events.get_by_id(outcome.event_id)
        assert normalized.raw_id == raw.raw_id
        assert event.metadata["raw_id"] == raw.raw_id
        assert event.metadata["normalized_id"] == normalized.normalized_id
        assert event.entity_id == normalized.normalized_id
        assert normalized.provenance.source == raw.source
        chain = storage.lineage.chain_for("event", event.event_id)
        assert [record.stage.value for record in chain] == [
            "SOURCE", "RAW", "NORMALIZED", "EVENT"
        ]

    # --- causal chain ------------------------------------------------------
    first_event = storage.events.get_by_id(accepted[0].event_id)
    accepted_marker = storage.events.get_by_id(accepted[0].accepted_event_id)
    validate_causal_chain([first_event, accepted_marker])

    # --- rejection evidence ------------------------------------------------
    rejection_events = list(storage.events.query_by_type(EventType.EVENT_REJECTED.value))
    assert len(rejection_events) == 1
    assert rejection_events[0].payload["raw_id"] == rejected.raw_id
    assert rejection_events[0].payload["reasons"]

    # --- timeline ------------------------------------------------------------
    timeline = EventTimelineService(storage.events).build(first_event.correlation_id)
    assert len(timeline.entries) == 2  # data event + EVENT_ACCEPTED
    assert timeline.in_event_time_order[0].event_id == first_event.event_id

    out_of_order_event = storage.events.get_by_id(out_of_order.event_id)
    late_timeline = EventTimelineService(storage.events).build(out_of_order_event.correlation_id)
    # data event + accepted + quality-changed + time anomaly
    assert len(late_timeline.entries) == 4
    chain_types = [entry.event_type for entry in late_timeline.entries]
    assert set(chain_types) == {
        "MARKET_DATA_RECEIVED", "EVENT_ACCEPTED", "DATA_QUALITY_CHANGED", "SYSTEM_TIME_ANOMALY"
    }
    anomaly_events = [
        event for event in storage.events.iter_by_correlation_id(out_of_order_event.correlation_id)
        if event.event_type is EventType.SYSTEM_TIME_ANOMALY
    ]
    assert anomaly_events and anomaly_events[0].causation_id == out_of_order_event.event_id
    assert out_of_order.quality.level.value == "DEGRADED"

    # --- replay (read-only, REPLAY environment) ---------------------------
    runner = ReplayRunner(storage.events)
    replay = runner.run(ReplayFilter(event_type=EventType.MARKET_DATA_RECEIVED.value))
    assert len(replay.events) == 4  # 3 ordered + 1 late (not the rejected one)
    for original, replayed in zip(storage.events.query_by_type(EventType.MARKET_DATA_RECEIVED.value), replay.events):
        assert original.payload == replayed.payload
        assert original.event_time == replayed.event_time

    storage.close()
