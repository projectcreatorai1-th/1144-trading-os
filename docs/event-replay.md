# Event Replay Foundation (Phase 1)

- **WHAT**: Replay-safe, read-only access to historical events - by time range, event type or correlation_id.
- **WHY**: The system must be able to re-examine what happened without mutating history (SECTION 26).
- **BOUNDARY**: Foundation only - no full replay engine, no strategy re-execution (later phases).

## Runner (core/events/replay.py)

`ReplayRunner(store).run(ReplayFilter, environment=...)`:

1. Enforces the REPLAY environment - any other environment (LIVE/PAPER/...) fails closed (`EnvironmentMismatchError`); validator rule EVENT-004 keeps the gate present.
2. Emits `REPLAY_STARTED` / `REPLAY_COMPLETED` marker events (in the REPLAY environment) with the filter description.
3. Iterates the store read-only; source events are never modified (tested).

## Environment isolation (tested)

- Replay marker/data events in the REPLAY environment can never be published to a bus declared LIVE (bus environment gate).
- `ReplayFilter` time ranges are validated (end before start fails).

## Timeline vs replay

`EventTimelineService` reconstructs one correlation chain (both event-time and ingestion order, out-of-order flagged); `ReplayRunner` iterates filtered history. Both are read-only views over the same immutable store.

- **TEST**: `tests/test_phase1_bus_timeline_replay.py`, replay section of `tests/test_phase1_e2e.py`.
