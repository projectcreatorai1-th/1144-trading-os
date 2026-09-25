"""Data ingestion pipeline (owned by core.data).

Flow (SECTION 17): INPUT -> RAW (immutable truth) -> SCHEMA VALIDATION ->
TIME VALIDATION -> DATA QUALITY -> NORMALIZATION -> EVENT CREATION -> CAUSAL
VALIDATION -> EVENT STORE (+ event bus). Rejections are structured and fail
closed: nothing invalid flows downstream, and every rejection carries its
reasons. Derived events keep causality: data event -> EVENT_ACCEPTED /
DATA_QUALITY_CHANGED / SYSTEM_TIME_ANOMALY / EVENT_REJECTED.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from architecture.contracts.errors import IngestionError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.observability import LogRecord, LogLevel, Logger
from architecture.contracts.schema import build_schema_registry
from architecture.contracts.time import ensure_utc, utc_now
from core.data.contracts import (
    LineageStage,
    RawDataRecord,
    build_lineage,
    compute_payload_hash,
)
from core.data.dedup import DeduplicationService, compute_dedup_key
from core.data.normalizer import NormalizationService
from core.data.sequencing import SequenceMonitor
from core.data.source_registry import SourceRegistry
from core.data.stores import LineageStore, NormalizedDataStore, RawDataStore
from core.events.bus import EventBus
from core.events.contracts import Event, EventType, build_event
from core.events.store import EventStore
from core.events.timeline import EventTimelineService
from core.time.latency import LatencyCalculator, LatencyReport
from core.time.ordering import OrderingMonitor
from core.time.validation import SourceTimeSemantics, TimeValidator
from core.validation.engine import QualityEngine, QualityEvaluation, QualityInput

CONTRACT_VERSION = "1.0.0"

DATA_EVENT_TYPES = {
    "MARKET_DATA_RECEIVED",
    "NEWS_RECEIVED",
    "ECONOMIC_EVENT",
}


@dataclass(frozen=True)
class IngestionRequest:
    source_id: str
    schema_id: str
    schema_version: str
    payload: Mapping[str, Any]
    source_timestamp: datetime
    environment: str
    event_type: str
    correlation_id: str | None = None
    causation_id: str | None = None
    source_event_ref: str | None = None
    sequence_number: int | None = None
    received_time: datetime | None = None
    metadata: Mapping[str, Any] | None = None
    ingestion_id: str | None = None
    now: datetime | None = None


@dataclass(frozen=True)
class IngestionOutcome:
    status: str  # ACCEPTED | REJECTED | DUPLICATE
    raw_id: str | None = None
    normalized_id: str | None = None
    event_id: str | None = None
    accepted_event_id: str | None = None
    duplicate_of_event_id: str | None = None
    quality: QualityEvaluation | None = None
    rejection_reasons: tuple[Mapping[str, Any], ...] = ()
    latency: LatencyReport | None = None
    correlation_id: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "ACCEPTED"

    @property
    def rejected(self) -> bool:
        return self.status == "REJECTED"

    @property
    def duplicate(self) -> bool:
        return self.status == "DUPLICATE"


class IngestionPipeline:
    """Orchestrates RAW -> VALIDATE -> NORMALIZE -> QUALITY -> VERSION ->
    LINEAGE -> EVENT -> EVENT STORE for one input at a time.

    Single-threaded by design (documented limitation, SECTION 44). Depends on
    ports only - storage technology is injected by the composition root."""

    def __init__(
        self,
        *,
        source_registry: SourceRegistry,
        raw_store: RawDataStore,
        normalized_store: NormalizedDataStore,
        lineage_store: LineageStore,
        event_store: EventStore,
        event_bus: EventBus | None = None,
        time_validator: TimeValidator | None = None,
        quality_engine: QualityEngine | None = None,
        logger: Logger | None = None,
    ) -> None:
        self._sources = source_registry
        self._raw_store = raw_store
        self._normalized_store = normalized_store
        self._lineage_store = lineage_store
        self._event_store = event_store
        self._bus = event_bus
        self._logger = logger
        self._time_validator = time_validator or TimeValidator()
        self._quality_engine = quality_engine or QualityEngine()
        self._schemas = build_schema_registry()
        self._ordering = OrderingMonitor()
        self._sequences = SequenceMonitor()
        self._dedup = DeduplicationService(event_store)
        self._normalizer = NormalizationService()
        self._latency = LatencyCalculator()
        self._timeline = EventTimelineService(event_store)

    def ingest(self, request: IngestionRequest) -> IngestionOutcome:
        import time as _time

        started = _time.monotonic()
        outcome = self._ingest_inner(request)
        if self._logger is not None:
            source = self._sources.get(request.source_id)
            sensitive = source.sensitive_fields if source is not None else ()
            duration_ms = round((_time.monotonic() - started) * 1000.0, 3)
            self._logger.log(
                LogRecord(
                    timestamp=utc_now(),
                    level=LogLevel.INFO if outcome.accepted else LogLevel.WARNING,
                    component="core.data.pipeline",
                    operation="ingest",
                    status=outcome.status,
                    correlation_id=outcome.correlation_id,
                    event_id=outcome.event_id,
                    source_id=request.source_id,
                    duration_ms=duration_ms,
                    error_code=outcome.rejection_reasons[0]["rule_id"]
                    if outcome.rejection_reasons else None,
                    details={
                        "raw_id": outcome.raw_id,
                        "normalized_id": outcome.normalized_id,
                        "quality": outcome.quality.level.value if outcome.quality else None,
                    },
                ).redacted(sensitive)
            )
        return outcome

    def _ingest_inner(self, request: IngestionRequest) -> IngestionOutcome:
        now = ensure_utc(request.now, location="ingest.now") if request.now else utc_now()
        received_time = request.received_time or now

        # 1. source resolution (unknown/disabled sources are not accepted input)
        try:
            source = self._sources.require(request.source_id)
        except IngestionError as exc:
            return self._rejected_pre_raw(request, exc)

        # 2. raw record (immutable truth, hash-verified) - stored before judgement
        payload_hash = compute_payload_hash(request.payload)
        raw = RawDataRecord(
            raw_id=new_identifier("raw_id"),
            ingestion_id=request.ingestion_id or new_identifier("ingestion_id"),
            source=source.source_id,
            source_id=source.source_id,
            received_time=received_time,
            source_timestamp=request.source_timestamp,
            payload=request.payload,
            payload_hash=payload_hash,
            schema_id=request.schema_id,
            schema_version=request.schema_version,
            environment=request.environment,
            correlation_id=request.correlation_id or new_identifier("correlation_id"),
            metadata=request.metadata,
        )
        try:
            raw.validate()
        except Exception as exc:
            return self._rejected_pre_raw(request, exc)
        self._raw_store.append(raw)
        self._append_lineage(
            LineageStage.SOURCE, "data_source", source.source_id, None, raw.correlation_id
        )
        self._append_lineage(LineageStage.RAW, "raw_data", raw.raw_id, source.source_id, raw.correlation_id)

        # 3. schema validation (registry-driven)
        schema_resolved = True
        schema_issues: tuple[Mapping[str, Any], ...] = ()
        try:
            schema = self._schemas.get(request.schema_id)
            if schema.version != request.schema_version:
                schema_resolved = False
            else:
                schema_issues = tuple(
                    issue.to_dict()
                    for issue in self._schemas.validate_instance(request.schema_id, request.payload)
                )
        except Exception:
            schema_resolved = False

        # 4. time validation (source-semantics aware)
        semantics = SourceTimeSemantics(
            source_id=source.source_id,
            timestamp_semantics=source.timestamp_semantics.value,
            allows_future_events=source.allows_future_events,
            future_tolerance_seconds=source.future_tolerance_seconds,
            stale_threshold_seconds=source.stale_threshold_seconds,
            max_expected_delay_seconds=source.max_expected_delay_seconds,
        )
        last_event_time = self._ordering.last_event_time(source.source_id)
        time_result = self._time_validator.validate_timestamps(
            event_time=request.source_timestamp,
            received_time=received_time,
            processed_time=None,
            now=now,
            semantics=semantics,
            last_event_time=last_event_time,
        )
        order_result = self._ordering.check(
            stream_key=source.source_id,
            event_time=request.source_timestamp,
            received_time=received_time,
        )
        sequence_result = self._sequences.check(source.source_id, request.sequence_number)

        # 5. deduplication (deterministic, persisted)
        dedup_key = compute_dedup_key(
            source=source.source_id,
            source_id=source.source_id,
            payload_hash=payload_hash,
            event_time=request.source_timestamp,
            source_event_ref=request.source_event_ref,
        )
        existing = self._dedup.find_duplicate(dedup_key)

        # 6. quality evaluation (all structured evidence)
        quality = self._quality_engine.evaluate(
            QualityInput(
                schema_issues=schema_issues,
                schema_validation_executed=True,
                schema_resolved=schema_resolved,
                source_registered=True,
                source_enabled=True,
                source_id=source.source_id,
                time_anomalies=tuple(
                    {
                        "anomaly": a.anomaly.value,
                        "message": a.message,
                        "details": dict(a.details),
                    }
                    for a in time_result.anomalies
                ),
                time_classification=time_result.classification.value,
                naive_datetime_fields=tuple(
                    a.details.get("field", "")
                    for a in time_result.anomalies
                    if "naive" in a.message
                ),
                duplicate=existing is not None,
                duplicate_of=existing.metadata.get("raw_id") if existing else None,
                sequence=sequence_result,
                out_of_order=order_result.out_of_order,
                order_details=order_result.to_dict(),
                symbol=request.payload.get("symbol") if isinstance(request.payload, Mapping) else None,
                symbol_check_applicable="symbol" in request.payload,
                hash_matches=True,  # raw.validate() verified the hash above
                provenance_check_applicable=False,
                provenance_present=True,
            ),
            judged_at=now,
        )

        latency = self._latency.compute(
            event_time=request.source_timestamp,
            received_time=received_time,
            processed_time=now,
            correlation_id=raw.correlation_id,
        )

        if existing is not None:
            return IngestionOutcome(
                status="DUPLICATE",
                raw_id=raw.raw_id,
                duplicate_of_event_id=existing.event_id,
                quality=quality,
                latency=latency,
                correlation_id=raw.correlation_id,
            )

        if quality.level.value == "INVALID":
            rejection_event = self._record_rejection(raw, quality)
            return IngestionOutcome(
                status="REJECTED",
                raw_id=raw.raw_id,
                quality=quality,
                rejection_reasons=tuple(c.to_dict() for c in quality.failed),
                latency=latency,
                event_id=rejection_event,
                correlation_id=raw.correlation_id,
            )

        # 7. normalization (INVALID never reaches here)
        normalized = self._normalizer.normalize(raw, quality, processed_time=now)
        self._normalized_store.append(normalized)
        self._append_lineage(
            LineageStage.NORMALIZED, "normalized_data", normalized.normalized_id,
            raw.raw_id, raw.correlation_id,
        )

        # 8. event creation (Phase 0 contract, registry-validated)
        event = build_event(
            event_id=new_identifier("event_id"),
            event_type=self._resolve_event_type(request.event_type),
            source=source.source_id,
            source_id=raw.raw_id,
            environment=request.environment,
            correlation_id=raw.correlation_id,
            causation_id=request.causation_id,
            entity_id=normalized.normalized_id,
            event_time=ensure_utc(request.source_timestamp, location="ingest.event_time"),
            received_time=ensure_utc(received_time, location="ingest.received_time"),
            payload=dict(normalized.payload),
            metadata={
                "raw_id": raw.raw_id,
                "normalized_id": normalized.normalized_id,
                "dedup_key": dedup_key,
                "data_version": normalized.data_version,
                "data_quality": normalized.quality_level.value,
                "sequence_number": request.sequence_number,
                "processed_time": ensure_utc(now).isoformat(),
            },
        )
        event_issues = self._schemas.validate_instance("event", event.to_dict())
        if event_issues:
            rejection_event = self._record_rejection(
                raw, quality, extra_reasons=tuple(i.to_dict() for i in event_issues)
            )
            return IngestionOutcome(
                status="REJECTED",
                raw_id=raw.raw_id,
                quality=quality,
                rejection_reasons=tuple(i.to_dict() for i in event_issues),
                latency=latency,
                event_id=rejection_event,
                correlation_id=raw.correlation_id,
            )
        self._event_store.append(event)
        self._append_lineage(
            LineageStage.EVENT, "event", event.event_id,
            normalized.normalized_id, raw.correlation_id,
        )

        # 9. derived events (causality preserved) + publication
        accepted_event_id = self._derived_event(
            EventType.EVENT_ACCEPTED,
            event,
            {"event_id": event.event_id, "quality": normalized.quality_level.value},
        )
        if normalized.quality_level.value not in ("VALIDATED", "VERIFIED"):
            self._derived_event(
                EventType.DATA_QUALITY_CHANGED,
                event,
                {"event_id": event.event_id, "level": normalized.quality_level.value,
                 "reasons": list(normalized.quality_reasons)},
            )
        if time_result.anomalies:
            self._derived_event(
                EventType.SYSTEM_TIME_ANOMALY,
                event,
                {"event_id": event.event_id,
                 "anomalies": [
                     {"anomaly": a.anomaly.value, "message": a.message, "details": dict(a.details)}
                     for a in time_result.anomalies
                 ]},
            )
        if self._bus is not None:
            self._bus.publish(event)

        return IngestionOutcome(
            status="ACCEPTED",
            raw_id=raw.raw_id,
            normalized_id=normalized.normalized_id,
            event_id=event.event_id,
            accepted_event_id=accepted_event_id,
            quality=quality,
            latency=latency,
            correlation_id=raw.correlation_id,
        )

    def _resolve_event_type(self, event_type: str) -> EventType:
        if event_type not in DATA_EVENT_TYPES:
            raise IngestionError(
                f"Event type '{event_type}' is not a Phase 1 data event type "
                f"({sorted(DATA_EVENT_TYPES)})",
                location="ingest.event_type",
            )
        return EventType(event_type)

    def _record_rejection(
        self,
        raw: RawDataRecord,
        quality: QualityEvaluation,
        extra_reasons: tuple[Mapping[str, Any], ...] = (),
    ) -> str:
        event = build_event(
            event_id=new_identifier("event_id"),
            event_type=EventType.EVENT_REJECTED,
            source="core.data.pipeline",
            source_id=raw.raw_id,
            environment=raw.environment,
            correlation_id=raw.correlation_id,
            causation_id=None,
            entity_id=raw.raw_id,
            event_time=utc_now(),
            received_time=utc_now(),
            payload={
                "raw_id": raw.raw_id,
                "reasons": [c.to_dict() for c in quality.failed] + [dict(r) for r in extra_reasons],
            },
            metadata={"raw_id": raw.raw_id, "data_quality": quality.level.value},
        )
        self._event_store.append(event)
        return event.event_id

    def _derived_event(self, event_type: EventType, cause: Event, payload: Mapping[str, Any]) -> str:
        event = build_event(
            event_id=new_identifier("event_id"),
            event_type=event_type,
            source="core.data.pipeline",
            source_id=cause.event_id,
            environment=cause.environment,
            correlation_id=cause.correlation_id,
            causation_id=cause.event_id,
            entity_id=cause.entity_id,
            event_time=utc_now(),
            received_time=utc_now(),
            payload=dict(payload),
            metadata={"caused_by": cause.event_id},
        )
        self._validate_causality(cause, event)
        self._event_store.append(event)
        return event.event_id

    @staticmethod
    def _validate_causality(cause: Event, derived: Event) -> None:
        from architecture.contracts.causality import validate_causal_chain

        validate_causal_chain([cause, derived])

    def _append_lineage(
        self, stage: LineageStage, entity_type: str, entity_id: str,
        parent_id: str | None, correlation_id: str,
    ) -> None:
        record = build_lineage(
            stage=stage,
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            parent_id=parent_id,
            lineage_id=new_identifier("lineage_id"),
        )
        self._lineage_store.append(record)

    def _rejected_pre_raw(self, request: IngestionRequest, error: Exception) -> IngestionOutcome:
        details = getattr(error, "details", {}) or {}
        return IngestionOutcome(
            status="REJECTED",
            rejection_reasons=(
                {
                    "rule_id": getattr(error, "rule_id", "DATA-REJECT"),
                    "severity": "FAIL",
                    "message": str(error),
                    "details": details,
                },
            ),
        )
