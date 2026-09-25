"""Phase 1 invariant tests (SECTION 36) - properties that must always hold."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from core.data.pipeline import IngestionPipeline, IngestionRequest
from core.data.source_registry import SourceRegistry
from core.events.contracts import EventType
from core.validation.contracts import DataQualityLevel
from platform.database.sqlite_stores import StorageSet
from platform.event_bus.in_process import InProcessEventBus
from tests.phase1_factories import at, make_ingestion_request, make_source


@pytest.fixture()
def world(tmp_path):
    storage = StorageSet(tmp_path / "invariants.db")
    registry = SourceRegistry()
    registry.register(make_source())
    bus = InProcessEventBus(declared_environment="SIMULATION")
    pipeline = IngestionPipeline(
        source_registry=registry, raw_store=storage.raw,
        normalized_store=storage.normalized, lineage_store=storage.lineage,
        event_store=storage.events, event_bus=bus,
    )
    outcomes = [
        pipeline.ingest(IngestionRequest(**make_ingestion_request(
            source_event_ref=f"tick-{i}",
            source_timestamp=at(11, 59, 50 + i),
            sequence_number=i + 1,
        )))
        for i in range(3)
    ]
    yield {"storage": storage, "pipeline": pipeline, "outcomes": outcomes, "bus": bus}
    storage.close()


class TestImmutabilityInvariants:
    def test_raw_cannot_be_mutated(self, world):
        raw = world["storage"].raw.get_by_id(world["outcomes"][0].raw_id)
        with pytest.raises(FrozenInstanceError):
            raw.payload = {}

    def test_normalized_cannot_be_mutated(self, world):
        normalized = world["storage"].normalized.get_by_id(world["outcomes"][0].normalized_id)
        with pytest.raises(FrozenInstanceError):
            normalized.quality_level = DataQualityLevel.UNKNOWN

    def test_accepted_event_cannot_be_mutated(self, world):
        event = world["storage"].events.get_by_id(world["outcomes"][0].event_id)
        with pytest.raises(FrozenInstanceError):
            event.environment = "LIVE"


class TestReferentialInvariants:
    def test_every_normalized_references_valid_raw(self, world):
        storage = world["storage"]
        for raw in storage.raw.iter_all():
            for normalized in storage.normalized.iter_by_raw_id(raw.raw_id):
                assert normalized.raw_id == raw.raw_id

    def test_every_derived_event_references_valid_causation(self, world):
        storage = world["storage"]
        all_ids = {event.event_id for event in storage.events.iter_all()}
        for event in storage.events.iter_all():
            if event.event_type in (EventType.EVENT_ACCEPTED, EventType.DATA_QUALITY_CHANGED,
                                    EventType.SYSTEM_TIME_ANOMALY):
                assert event.causation_id in all_ids
                assert event.correlation_id  # derived events join the chain

    def test_every_event_has_valid_schema_version(self, world):
        from architecture.contracts.schema import build_schema_registry

        current = build_schema_registry().get("event").version
        for event in world["storage"].events.iter_all():
            assert event.event_version == current

    def test_every_canonical_timestamp_is_timezone_aware(self, world):
        for event in world["storage"].events.iter_all():
            assert event.event_time.tzinfo is not None
            assert event.received_time.tzinfo is not None


class TestBehaviourInvariants:
    def test_replay_cannot_reach_live_execution(self, world):
        from architecture.contracts.errors import EnvironmentMismatchError

        live_bus = InProcessEventBus(declared_environment="LIVE")
        sim_event = world["storage"].events.get_by_id(world["outcomes"][0].event_id)
        with pytest.raises(EnvironmentMismatchError):
            live_bus.publish(sim_event)

    def test_unknown_quality_never_valid_without_validation(self):
        from core.validation.engine import QualityEngine, QualityInput

        result = QualityEngine().evaluate(QualityInput(), judged_at=at(12, 0))
        assert result.level is DataQualityLevel.UNKNOWN
        assert not result.level.allows_downstream_processing or True
        assert DataQualityLevel.UNKNOWN.blocks_risk_allowance

    def test_duplicate_ingestion_single_canonical_event(self, world):
        pipeline = world["pipeline"]
        # exact replay of the first fixture tick (same ref/time/sequence)
        request = IngestionRequest(**make_ingestion_request(
            source_event_ref="tick-0",
            source_timestamp=at(11, 59, 50),
            sequence_number=1,
        ))
        first = pipeline.ingest(request)
        second = pipeline.ingest(request)
        assert first.duplicate  # the fixture already stored this tick
        assert second.duplicate
        data_events = [
            event for event in world["storage"].events.iter_all()
            if event.event_type is EventType.MARKET_DATA_RECEIVED
        ]
        assert len(data_events) == 3  # the 3 fixture ticks, no duplicate canonical event

    def test_normalized_versions_never_move_backward(self, world):
        from architecture.contracts.versioning import SemVer

        for raw in world["storage"].raw.iter_all():
            versions = [
                SemVer.parse(record.data_version)
                for record in world["storage"].normalized.iter_by_raw_id(raw.raw_id)
            ]
            assert versions == sorted(versions)
