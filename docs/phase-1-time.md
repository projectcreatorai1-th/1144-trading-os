# Phase 1 — Time Foundation

- **WHAT**: Source-semantics-aware time validation, ordering, latency and anomaly detection on top of the Phase 0 kernel time primitives (single source of truth for time).
- **WHY**: Received order != event order; delayed data is not invalid data; timestamps must never be silently fixed (SECTIONS 12-16).
- **BOUNDARY**: No wall-clock assumptions beyond each source's declared semantics; naive datetimes are structurally invalid.

## Validation (core/time/validation.py)

`TimeValidator.validate_timestamps(...)` classifies each observation as `CLEAN | OUT_OF_ORDER | LATE | STALE | INVALID` and records typed anomalies (`CLOCK_SKEW`, `FUTURE_EVENT`, `TIMESTAMP_REGRESSION`, `STALE_DATA`, `NEGATIVE_LATENCY`, `LARGE_LATENCY`, `RECEIVED_AFTER_PROCESSED`, `SOURCE_INCONSISTENCY`):

| Condition | Classification |
|---|---|
| naive datetime / non-datetime | INVALID |
| outside 2000..2100 window | INVALID |
| event_time in future beyond tolerance (source-dependent) | INVALID |
| event_time after received_time beyond tolerance | INVALID (impossible) |
| processed_time before received_time | INVALID |
| small skew within future tolerance | CLEAN + CLOCK_SKEW anomaly |
| delay beyond max_expected_delay | LATE (delayed, not invalid) |
| age beyond stale_threshold (with judged_at + threshold evidence) | STALE |
| event_time behind the previous stream event | OUT_OF_ORDER |

Sources declare semantics via the data source registry: `REALTIME/DELAYED/BATCH/HISTORICAL`, `allows_future_events`, `future_tolerance_seconds`, `stale_threshold_seconds`, `max_expected_delay_seconds`. Historical sources (e.g. economic calendars) never fail future checks - their events legitimately live in the future. Original timestamps are never modified; anomalies are marked.

## Ordering (core/time/ordering.py)

`OrderingMonitor.check(stream_key, event_time, received_time)` tracks per-stream state and reports OUT_OF_ORDER with `expected`, `actual`, `difference_seconds` for both event-time and ingestion-order regressions. In-memory per pipeline instance (documented limitation - dedup is persisted, ordering is per session).

## Latency (core/time/latency.py)

`LatencyCalculator` produces `LatencyReport` (schema latency_report): ingestion (received - event), processing (processed - received), end-to-end (processed - event). Missing timestamps -> `null` (UNKNOWN), never guessed; impossible negative values -> null + `NEGATIVE_LATENCY` anomaly; large values -> `LARGE_LATENCY` anomaly.

- **TEST**: `tests/test_phase1_time.py`.
- **VERSION**: latency_report schema 1.0.0; time module contract 1.1.0.
