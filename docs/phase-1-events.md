# Phase 1 — Event Foundation

- **WHAT**: Ingestion pipeline to event store to timeline/replay, on the Phase 0 Event contract (v1.1.0) - no duplicate event model.
- **WHY**: Events are the system's memory: immutable, causally linked, replayable (SECTIONS 17-27).
- **BOUNDARY**: Foundation events only. STRATEGY_SIGNAL_CREATED/RISK_EVALUATED/ORDER_*/POSITION_* stay unimplemented (later phases; the enum members exist from Phase 0 for compatibility only).

## Event types used in Phase 1

Data: `MARKET_DATA_RECEIVED`, `NEWS_RECEIVED`, `ECONOMIC_EVENT` (all Phase 0 types, reused).
Foundation (added in contract 1.1.0): `DATA_QUALITY_CHANGED`, `DATA_SOURCE_CHANGED`, `SYSTEM_TIME_ANOMALY`, `EVENT_REJECTED`, `EVENT_ACCEPTED`, `REPLAY_STARTED`, `REPLAY_COMPLETED`.

The pipeline accepts only data event types for ingestion; `ORDER_CREATED` and friends are rejected (scope guard, tested).

## Causality

Root data events may have `causation_id = None`; every derived event references its cause: data event -> `EVENT_ACCEPTED` / `DATA_QUALITY_CHANGED` / `SYSTEM_TIME_ANOMALY`; rejected input -> `EVENT_REJECTED`. All share the ingestion correlation_id, so `validate_causal_chain` (Phase 0) can walk them (tested end-to-end).

## Event bus (platform/event_bus/in_process.py)

`core.events.bus.EventBus` port + synchronous in-process implementation: publish (validates the event, environment-gated), subscribe (optionally by type), unsubscribe. Subscriber exceptions are collected and raised as a structured `BusError` after all subscribers were attempted - never swallowed. Single-threaded by design (no thread/process-safety claimed). Redis/Kafka/NATS/RabbitMQ implementations can replace it without touching core.

## Timeline (core/events/timeline.py)

`EventTimelineService.build(correlation_id)` -> entries with event_id, event_type, event_time, received_time, source, causation_id, sequence, quality, out-of-order flag. Two separate orders: `in_event_time_order` and `in_ingestion_order`. Out-of-order entries are flagged, never re-timestamped.

- **TEST**: `tests/test_phase1_bus_timeline_replay.py`, `tests/test_phase1_pipeline.py`.
