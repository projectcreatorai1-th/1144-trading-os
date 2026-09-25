"""Phase 1 tests: event bus, timeline, replay foundation (SECTIONS 25-27)."""
from __future__ import annotations

import pytest

from architecture.contracts.errors import BusError, EnvironmentMismatchError
from architecture.contracts.identifiers import new_identifier
from core.events.contracts import EventType
from core.events.replay import ReplayFilter, ReplayRunner
from core.events.timeline import EventTimelineService
from platform.database.sqlite_stores import SqliteEventStore, StorageSet
from platform.event_bus.in_process import InProcessEventBus
from tests.factories import make_event
from tests.phase1_factories import at


@pytest.fixture()
def storage(tmp_path):
    storage = StorageSet(tmp_path / "bus.db")
    yield storage
    storage.close()


class TestEventBus:
    def test_publish_subscribe_roundtrip(self):
        bus = InProcessEventBus()
        received = []
        bus.subscribe(received.append)
        event = make_event()
        bus.publish(event)
        assert received == [event]

    def test_type_filtered_subscription(self):
        bus = InProcessEventBus()
        received = []
        bus.subscribe(received.append, event_type="NEWS_RECEIVED")
        bus.publish(make_event(event_type=EventType.NEWS_RECEIVED))
        bus.publish(make_event(event_type=EventType.MARKET_DATA_RECEIVED))
        assert len(received) == 1

    def test_unsubscribe_stops_delivery(self):
        bus = InProcessEventBus()
        received = []
        subscription = bus.subscribe(received.append)
        bus.unsubscribe(subscription)
        bus.publish(make_event())
        assert received == []
        with pytest.raises(BusError):
            bus.unsubscribe(subscription)

    def test_invalid_event_rejected(self):
        from dataclasses import replace

        bus = InProcessEventBus()
        with pytest.raises(Exception):
            bus.publish(replace(make_event(), event_type="NOT_A_TYPE"))

    def test_environment_gate_blocks_replay_to_live_bus(self):
        live_bus = InProcessEventBus(declared_environment="LIVE")
        replay_event = make_event(environment="REPLAY")
        with pytest.raises(EnvironmentMismatchError):
            live_bus.publish(replay_event)

    def test_environment_gate_allows_matching(self):
        bus = InProcessEventBus(declared_environment="SIMULATION")
        received = []
        bus.subscribe(received.append)
        bus.publish(make_event(environment="SIMULATION"))
        assert len(received) == 1

    def test_subscriber_failure_raises_structured_error(self):
        bus = InProcessEventBus()
        good = []

        def broken(_event):
            raise RuntimeError("subscriber exploded")

        bus.subscribe(broken)
        bus.subscribe(good.append)
        with pytest.raises(BusError) as excinfo:
            bus.publish(make_event())
        assert excinfo.value.rule_id == "BUS-001"
        assert len(good) == 1  # all subscribers attempted despite failure

    def test_non_callable_handler_rejected(self):
        bus = InProcessEventBus()
        with pytest.raises(BusError):
            bus.subscribe("not-callable")


class TestTimeline:
    def test_timeline_two_orders(self, storage):
        correlation = new_identifier("correlation_id")
        early = make_event(
            event_time=at(11, 0), received_time=at(12, 0, 5),
            correlation_id=correlation,
            metadata={"sequence_number": 2, "data_quality": "VALIDATED"},
        )
        late_ingested_first = make_event(
            event_time=at(12, 0), received_time=at(12, 0, 1),
            correlation_id=correlation, causation_id=early.event_id,
            metadata={"sequence_number": 1, "data_quality": "VALIDATED"},
        )
        storage.events.append(late_ingested_first)
        storage.events.append(early)

        timeline = EventTimelineService(storage.events).build(correlation)
        ingestion = [e.event_id for e in timeline.in_ingestion_order]
        event_order = [e.event_id for e in timeline.in_event_time_order]
        assert ingestion[0] == late_ingested_first.event_id
        assert event_order[0] == early.event_id
        assert early.event_id in timeline.out_of_order_event_ids
        assert late_ingested_first.event_id not in timeline.out_of_order_event_ids

    def test_timeline_does_not_mutate_timestamps(self, storage):
        correlation = new_identifier("correlation_id")
        event = make_event(event_time=at(10, 30), correlation_id=correlation)
        storage.events.append(event)
        timeline = EventTimelineService(storage.events).build(correlation)
        assert timeline.entries[0].event_time == at(10, 30)
        assert storage.events.get_by_id(event.event_id).event_time == at(10, 30)

    def test_timeline_entry_fields(self, storage):
        correlation = new_identifier("correlation_id")
        event = make_event(
            correlation_id=correlation, metadata={"sequence_number": 7, "data_quality": "DEGRADED"}
        )
        storage.events.append(event)
        entry = EventTimelineService(storage.events).build(correlation).entries[0]
        data = entry.to_dict()
        for key in ("event_id", "event_type", "event_time", "received_time", "source",
                    "causation_id", "sequence", "quality", "out_of_order"):
            assert key in data
        assert data["sequence"] == 7
        assert data["quality"] == "DEGRADED"


class TestReplay:
    def test_replay_by_time_range(self, storage):
        old = make_event(event_time=at(10, 0), received_time=at(10, 0))
        new = make_event(event_time=at(12, 0), received_time=at(12, 0))
        storage.events.append(old)
        storage.events.append(new)

        runner = ReplayRunner(storage.events)
        result = runner.run(ReplayFilter(time_start=at(9, 0), time_end=at(11, 0)))
        assert [e.event_id for e in result.events] == [old.event_id]
        assert result.environment == "REPLAY"

    def test_replay_by_type(self, storage):
        tick = make_event(event_type=EventType.MARKET_DATA_RECEIVED)
        news = make_event(event_type=EventType.NEWS_RECEIVED)
        storage.events.append(tick)
        storage.events.append(news)
        result = ReplayRunner(storage.events).run(ReplayFilter(event_type="NEWS_RECEIVED"))
        assert [e.event_id for e in result.events] == [news.event_id]

    def test_replay_by_correlation(self, storage):
        correlation = new_identifier("correlation_id")
        a = make_event(correlation_id=correlation)
        b = make_event(correlation_id=correlation, causation_id=a.event_id)
        other = make_event()
        for event in (a, b, other):
            storage.events.append(event)
        result = ReplayRunner(storage.events).run(ReplayFilter(correlation_id=correlation))
        assert {e.event_id for e in result.events} == {a.event_id, b.event_id}

    def test_replay_writes_marker_events(self, storage):
        storage.events.append(make_event())
        result = ReplayRunner(storage.events).run()
        stored_types = [e.event_type for e in storage.events.iter_all()]
        assert EventType.REPLAY_STARTED in stored_types
        assert EventType.REPLAY_COMPLETED in stored_types

    def test_replay_does_not_mutate_source_events(self, storage):
        event = make_event(event_time=at(10, 0), received_time=at(10, 0))
        storage.events.append(event)
        ReplayRunner(storage.events).run()
        reloaded = storage.events.get_by_id(event.event_id)
        assert reloaded.payload == event.payload
        assert reloaded.event_time == event.event_time

    def test_replay_refuses_non_replay_environment(self, storage):
        runner = ReplayRunner(storage.events)
        with pytest.raises(EnvironmentMismatchError):
            runner.run(environment="LIVE")
        with pytest.raises(EnvironmentMismatchError):
            runner.run(environment="PAPER")

    def test_replay_marker_events_cannot_reach_live_bus(self, storage):
        from dataclasses import replace

        storage.events.append(make_event())
        result = ReplayRunner(storage.events).run()
        started = storage.events.get_by_id(result.replay_started_event_id)
        live_bus = InProcessEventBus(declared_environment="LIVE")
        with pytest.raises(EnvironmentMismatchError):
            live_bus.publish(started)

    def test_replay_reversed_time_range_rejected(self, storage):
        runner = ReplayRunner(storage.events)
        with pytest.raises(EnvironmentMismatchError):
            runner.run(ReplayFilter(time_start=at(12, 0), time_end=at(10, 0)))
