"""Phase 1 SQLite store tests (SECTION 20/29) - stores, queries, immutability."""
from __future__ import annotations

import pytest

from architecture.contracts.errors import StorageError
from architecture.contracts.identifiers import new_identifier
from core.data.contracts import (
    LineageRecord,
    LineageStage,
    RawDataRecord,
    compute_payload_hash,
    build_lineage,
)
from core.events.contracts import EventType
from platform.database.sqlite_stores import StorageSet, open_database
from tests.factories import make_event
from tests.phase1_factories import at, market_tick_payload


@pytest.fixture()
def storage(tmp_path):
    storage = StorageSet(tmp_path / "phase1.db")
    yield storage
    storage.close()


def make_raw(**overrides):
    payload = market_tick_payload()
    defaults = dict(
        raw_id=new_identifier("raw_id"),
        ingestion_id=new_identifier("ingestion_id"),
        source="feed-xauusd",
        source_id="feed-xauusd",
        received_time=at(12, 0),
        source_timestamp=at(11, 59, 58),
        payload=payload,
        payload_hash=compute_payload_hash(payload),
        schema_id="market_tick",
        schema_version="1.0.0",
        environment="SIMULATION",
        correlation_id=new_identifier("correlation_id"),
    )
    defaults.update(overrides)
    return RawDataRecord(**defaults)


class TestRawStore:
    def test_append_and_get(self, storage):
        raw = make_raw()
        storage.raw.append(raw)
        assert storage.raw.count() == 1
        loaded = storage.raw.get_by_id(raw.raw_id)
        assert loaded.payload == raw.payload
        assert loaded.payload_hash == raw.payload_hash
        assert loaded.received_time == raw.received_time

    def test_append_twice_same_id_rejected(self, storage):
        raw = make_raw()
        storage.raw.append(raw)
        with pytest.raises(StorageError):
            storage.raw.append(raw)

    def test_hash_verified_on_append(self, storage):
        bad = make_raw(payload_hash="0" * 64)
        with pytest.raises(Exception):
            storage.raw.append(bad)

    def test_find_by_hash(self, storage):
        raw = make_raw()
        storage.raw.append(raw)
        found = storage.raw.find_by_hash("feed-xauusd", raw.payload_hash)
        assert found is not None and found.raw_id == raw.raw_id
        assert storage.raw.find_by_hash("feed-xauusd", "f" * 64) is None

    def test_get_missing_raises(self, storage):
        with pytest.raises(StorageError):
            storage.raw.get_by_id("raw_" + "0" * 32)


class TestEventStore:
    def test_append_returns_sequence(self, storage):
        first = make_event()
        second = make_event()
        seq1 = storage.events.append(first)
        seq2 = storage.events.append(second)
        assert seq2 > seq1 >= 1
        assert storage.events.count() == 2

    def test_get_sequence(self, storage):
        event = make_event()
        seq = storage.events.append(event)
        assert storage.events.get_sequence(event.event_id) == seq

    def test_invalid_event_rejected_on_write(self, storage):
        from dataclasses import replace

        invalid = replace(make_event(), environment="NOT_AN_ENV")
        with pytest.raises(Exception):
            storage.events.append(invalid)

    def test_duplicate_event_id_rejected(self, storage):
        event = make_event()
        storage.events.append(event)
        with pytest.raises(StorageError):
            storage.events.append(event)

    def test_query_by_time(self, storage):
        early = make_event(event_time=at(10, 0), received_time=at(10, 0))
        late = make_event(event_time=at(12, 0), received_time=at(12, 0))
        storage.events.append(early)
        storage.events.append(late)
        found = list(storage.events.query_by_time(at(9, 0), at(11, 0)))
        assert [e.event_id for e in found] == [early.event_id]

    def test_query_by_type_source_causation(self, storage):
        root = make_event()
        child = make_event(causation_id=root.event_id)
        storage.events.append(root)
        storage.events.append(child)
        assert len(list(storage.events.query_by_type("MARKET_DATA_RECEIVED"))) == 2
        assert len(list(storage.events.query_by_source(root.source))) == 2
        caused = list(storage.events.query_by_causation(root.event_id))
        assert [e.event_id for e in caused] == [child.event_id]

    def test_query_by_correlation(self, storage):
        from tests.factories import make_event as mk

        correlation = new_identifier("correlation_id")
        a = mk(correlation_id=correlation)
        b = mk(correlation_id=correlation, causation_id=a.event_id)
        other = mk()
        storage.events.append(a)
        storage.events.append(b)
        storage.events.append(other)
        found = list(storage.events.iter_by_correlation_id(correlation))
        assert {e.event_id for e in found} == {a.event_id, b.event_id}

    def test_dedup_key_lookup(self, storage):
        event = make_event(metadata={"dedup_key": "dk-1"})
        storage.events.append(event)
        found = storage.events.find_by_dedup_key("dk-1")
        assert found is not None and found.event_id == event.event_id
        assert storage.events.find_by_dedup_key("dk-2") is None

    def test_roundtrip_preserves_enums_and_times(self, storage):
        event = make_event(event_type=EventType.DATA_QUALITY_CHANGED)
        storage.events.append(event)
        loaded = storage.events.get_by_id(event.event_id)
        assert loaded.event_type is EventType.DATA_QUALITY_CHANGED
        assert loaded.event_time == event.event_time


class TestLineageStore:
    def test_append_and_query(self, storage):
        correlation = new_identifier("correlation_id")
        source = build_lineage(
            stage=LineageStage.SOURCE, entity_type="data_source", entity_id="feed-xauusd",
            correlation_id=correlation, parent_id=None,
            lineage_id=new_identifier("lineage_id"), recorded_at=at(12, 0),
        )
        raw = make_raw()
        raw_link = build_lineage(
            stage=LineageStage.RAW, entity_type="raw_data", entity_id=raw.raw_id,
            correlation_id=correlation, parent_id="feed-xauusd",
            lineage_id=new_identifier("lineage_id"), recorded_at=at(12, 0),
        )
        storage.lineage.append(source)
        storage.lineage.append(raw_link)
        assert len(list(storage.lineage.iter_by_correlation_id(correlation))) == 2
        assert len(list(storage.lineage.iter_by_entity("raw_data", raw.raw_id))) == 1

    def test_chain_for_walks_to_source(self, storage):
        correlation = new_identifier("correlation_id")
        source = build_lineage(
            stage=LineageStage.SOURCE, entity_type="data_source", entity_id="feed-xauusd",
            correlation_id=correlation, parent_id=None,
            lineage_id=new_identifier("lineage_id"), recorded_at=at(12, 0),
        )
        raw = make_raw()
        raw_link = build_lineage(
            stage=LineageStage.RAW, entity_type="raw_data", entity_id=raw.raw_id,
            correlation_id=correlation, parent_id="feed-xauusd",
            lineage_id=new_identifier("lineage_id"), recorded_at=at(12, 0),
        )
        event = make_event(correlation_id=correlation)
        event_link = build_lineage(
            stage=LineageStage.EVENT, entity_type="event", entity_id=event.event_id,
            correlation_id=correlation, parent_id=raw.raw_id,
            lineage_id=new_identifier("lineage_id"), recorded_at=at(12, 0),
        )
        for record in (source, raw_link, event_link):
            storage.lineage.append(record)
        chain = storage.lineage.chain_for("event", event.event_id)
        assert [r.stage for r in chain] == [
            LineageStage.SOURCE, LineageStage.RAW, LineageStage.EVENT
        ]


class TestStorageLifecycle:
    def test_restart_reopens_same_history(self, tmp_path):
        path = tmp_path / "restart.db"
        storage = StorageSet(path)
        raw = make_raw()
        event = make_event()
        storage.raw.append(raw)
        storage.events.append(event)
        storage.close()

        reopened = StorageSet(path)
        assert reopened.raw.count() == 1
        assert reopened.events.count() == 1
        assert reopened.raw.get_by_id(raw.raw_id).payload == raw.payload
        reopened.close()

    def test_in_memory_database(self):
        storage = StorageSet(":memory:")
        storage.events.append(make_event())
        assert storage.events.count() == 1
        storage.close()
