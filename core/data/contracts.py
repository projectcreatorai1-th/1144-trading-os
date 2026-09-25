"""Data contracts (owned by core.data): raw records, normalized records,
data source registration and lineage links.

Raw data is immutable truth: wrong data stays raw and is flagged by quality,
never edited. Normalized records preserve provenance and the raw reference.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError, IdentifierValidationError
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.provenance import Provenance
from architecture.contracts.time import ensure_utc, utc_now
from architecture.contracts.versioning import SemVer
from core.validation.contracts import DataQualityLevel

CONTRACT_VERSION = "1.0.0"


class SourceType(Enum):
    MARKET_DATA = "MARKET_DATA"
    NEWS = "NEWS"
    CALENDAR = "CALENDAR"
    BROKER = "BROKER"
    MT5 = "MT5"
    MANUAL = "MANUAL"
    FILE = "FILE"
    API = "API"
    REPLAY = "REPLAY"


class SourceStatus(Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    RETIRED = "RETIRED"


class TimestampSemantics(Enum):
    REALTIME = "REALTIME"
    DELAYED = "DELAYED"
    BATCH = "BATCH"
    HISTORICAL = "HISTORICAL"


class LineageStage(Enum):
    SOURCE = "SOURCE"
    RAW = "RAW"
    NORMALIZED = "NORMALIZED"
    EVENT = "EVENT"
    ANALYZED = "ANALYZED"
    DECISION = "DECISION"
    ORDER = "ORDER"
    EXECUTION = "EXECUTION"
    RESULT = "RESULT"


#: Stages created by the Phase 1 pipeline; later stages are reserved.
PHASE_1_LINEAGE_STAGES = (
    LineageStage.SOURCE,
    LineageStage.RAW,
    LineageStage.NORMALIZED,
    LineageStage.EVENT,
)

SYMBOL_ALLOWED_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789./_-")


def compute_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, Decimal)):
        return str(value)
    raise TypeError(f"Payload values must be JSON-native; got {type(value).__name__}")


@dataclass(frozen=True)
class RawDataRecord:
    raw_id: str
    ingestion_id: str
    source: str
    source_id: str
    received_time: datetime
    source_timestamp: datetime
    payload: Mapping[str, Any]
    payload_hash: str
    schema_id: str
    schema_version: str
    environment: str
    correlation_id: str
    source_version: str | None = None
    metadata: Mapping[str, Any] | None = None
    duplicate_of: str | None = None

    def validate(self) -> None:
        validate_identifier("raw_id", self.raw_id, location="raw.raw_id")
        validate_identifier("ingestion_id", self.ingestion_id, location="raw.ingestion_id")
        for name in ("source", "source_id", "schema_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"raw.{name} must be a non-empty string",
                    location=f"raw.{name}",
                )
        SemVer.parse(self.schema_version, location="raw.schema_version")
        if self.source_version is not None:
            if not isinstance(self.source_version, str) or not self.source_version:
                raise ContractValidationError(
                    "raw.source_version must be a non-empty string or None",
                    location="raw.source_version",
                )
        ensure_utc(self.received_time, location="raw.received_time")
        ensure_utc(self.source_timestamp, location="raw.source_timestamp")
        if not isinstance(self.payload, Mapping):
            raise ContractValidationError(
                "raw.payload must be a mapping",
                location="raw.payload",
            )
        expected_hash = compute_payload_hash(self.payload)
        if self.payload_hash != expected_hash:
            raise ContractValidationError(
                "raw.payload_hash does not match the canonical payload hash",
                location="raw.payload_hash",
                rule_id="DQ-014",
                details={"expected": expected_hash, "actual": self.payload_hash},
            )
        parse_environment(self.environment, location="raw.environment")
        from architecture.contracts.identifiers import validate_any_identifier

        validate_any_identifier(self.correlation_id, location="raw.correlation_id")
        if self.metadata is not None and not isinstance(self.metadata, Mapping):
            raise ContractValidationError(
                "raw.metadata must be a mapping or None",
                location="raw.metadata",
            )
        if self.duplicate_of is not None:
            validate_identifier("raw_id", self.duplicate_of, location="raw.duplicate_of")

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_id": self.raw_id,
            "ingestion_id": self.ingestion_id,
            "source": self.source,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "received_time": ensure_utc(self.received_time).isoformat(),
            "source_timestamp": ensure_utc(self.source_timestamp).isoformat(),
            "payload": dict(self.payload),
            "payload_hash": self.payload_hash,
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "environment": self.environment,
            "correlation_id": self.correlation_id,
            "metadata": dict(self.metadata) if self.metadata is not None else None,
            "duplicate_of": self.duplicate_of,
        }

    @classmethod
    def from_storage(cls, data: Mapping[str, Any]) -> "RawDataRecord":
        """Rebuild a raw record from stored plain values (ISO strings)."""
        from architecture.contracts.time import parse_canonical

        record = cls(
            raw_id=data["raw_id"],
            ingestion_id=data["ingestion_id"],
            source=data["source"],
            source_id=data["source_id"],
            source_version=data.get("source_version"),
            received_time=parse_canonical(data["received_time"]),
            source_timestamp=parse_canonical(data["source_timestamp"]),
            payload=data["payload"],
            payload_hash=data["payload_hash"],
            schema_id=data["schema_id"],
            schema_version=data["schema_version"],
            environment=data["environment"],
            correlation_id=data["correlation_id"],
            metadata=data.get("metadata"),
            duplicate_of=data.get("duplicate_of"),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class NormalizedDataRecord:
    normalized_id: str
    raw_id: str
    source: str
    event_time: datetime
    received_time: datetime
    processed_time: datetime
    schema_id: str
    schema_version: str
    payload: Mapping[str, Any]
    provenance: Provenance
    lineage_id: str
    data_version: str
    quality_level: DataQualityLevel
    quality_reasons: tuple[str, ...] = ()
    supersedes: str | None = None

    def validate(self) -> None:
        validate_identifier("normalized_id", self.normalized_id, location="normalized.normalized_id")
        validate_identifier("raw_id", self.raw_id, location="normalized.raw_id")
        if not isinstance(self.source, str) or not self.source:
            raise ContractValidationError(
                "normalized.source must be a non-empty string",
                location="normalized.source",
            )
        event_time = ensure_utc(self.event_time, location="normalized.event_time")
        received_time = ensure_utc(self.received_time, location="normalized.received_time")
        processed_time = ensure_utc(self.processed_time, location="normalized.processed_time")
        if processed_time < received_time:
            raise ContractValidationError(
                "normalized.processed_time before received_time",
                location="normalized.processed_time",
            )
        if received_time < event_time:
            raise ContractValidationError(
                "normalized.received_time before event_time",
                location="normalized.received_time",
            )
        for name in ("schema_id",):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ContractValidationError(
                    f"normalized.{name} must be a non-empty string",
                    location=f"normalized.{name}",
                )
        SemVer.parse(self.schema_version, location="normalized.schema_version")
        if not isinstance(self.payload, Mapping):
            raise ContractValidationError(
                "normalized.payload must be a mapping",
                location="normalized.payload",
            )
        if not isinstance(self.provenance, Provenance):
            raise ContractValidationError(
                "normalized.provenance is required (normalization must never lose provenance)",
                location="normalized.provenance",
                rule_id="PROV-001",
            )
        self.provenance.validate()
        validate_identifier("lineage_id", self.lineage_id, location="normalized.lineage_id")
        SemVer.parse(self.data_version, location="normalized.data_version")
        if not isinstance(self.quality_level, DataQualityLevel):
            raise ContractValidationError(
                f"normalized.quality_level must be a DataQualityLevel, got {self.quality_level!r}",
                location="normalized.quality_level",
                rule_id="SCHEMA-ENUM",
            )
        if self.supersedes is not None:
            validate_identifier("normalized_id", self.supersedes, location="normalized.supersedes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalized_id": self.normalized_id,
            "raw_id": self.raw_id,
            "source": self.source,
            "event_time": ensure_utc(self.event_time).isoformat(),
            "received_time": ensure_utc(self.received_time).isoformat(),
            "processed_time": ensure_utc(self.processed_time).isoformat(),
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "payload": dict(self.payload),
            "provenance": {
                "source": self.provenance.source,
                "source_id": self.provenance.source_id,
            },
            "lineage_id": self.lineage_id,
            "data_version": self.data_version,
            "quality_level": self.quality_level.value,
            "quality_reasons": list(self.quality_reasons),
            "supersedes": self.supersedes,
        }

    @classmethod
    def from_storage(cls, data: Mapping[str, Any]) -> "NormalizedDataRecord":
        """Rebuild a record from stored plain values (ISO strings, provenance dict)."""
        from architecture.contracts.time import parse_canonical

        provenance_data = data["provenance"]
        processing = provenance_data.get("processing_time")
        provenance = Provenance(
            source=provenance_data["source"],
            source_id=provenance_data["source_id"],
            source_version=provenance_data.get("source_version"),
            event_time=parse_canonical(provenance_data["event_time"]),
            ingestion_time=parse_canonical(provenance_data["ingestion_time"]),
            processing_time=parse_canonical(processing) if processing else None,
            model_version=provenance_data.get("model_version"),
            policy_version=provenance_data.get("policy_version"),
            data_version=provenance_data.get("data_version"),
        )
        record = cls(
            normalized_id=data["normalized_id"],
            raw_id=data["raw_id"],
            source=data["source"],
            event_time=parse_canonical(data["event_time"]),
            received_time=parse_canonical(data["received_time"]),
            processed_time=parse_canonical(data["processed_time"]),
            schema_id=data["schema_id"],
            schema_version=data["schema_version"],
            payload=data["payload"],
            provenance=provenance,
            lineage_id=data["lineage_id"],
            data_version=data["data_version"],
            quality_level=DataQualityLevel(data["quality_level"]),
            quality_reasons=tuple(data.get("quality_reasons", ())),
            supersedes=data.get("supersedes"),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class DataSourceRecord:
    source_id: str
    name: str
    source_type: SourceType
    provider: str
    version: str
    status: SourceStatus
    timezone: str
    timestamp_semantics: TimestampSemantics
    schema_id: str
    enabled: bool
    reliability_metadata: Mapping[str, Any]
    allows_future_events: bool = False
    max_expected_delay_seconds: float | None = None
    sensitive_fields: tuple[str, ...] = ()

    def validate(self) -> None:
        from architecture.contracts.config import SECRET_KEY_MARKERS

        for name in ("source_id", "name", "provider", "timezone", "schema_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"source.{name} must be a non-empty string",
                    location=f"source.{name}",
                )
        if not isinstance(self.source_type, SourceType):
            raise ContractValidationError(
                f"source.source_type must be a SourceType, got {self.source_type!r}",
                location="source.source_type",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.status, SourceStatus):
            raise ContractValidationError(
                f"source.status must be a SourceStatus, got {self.status!r}",
                location="source.status",
                rule_id="SCHEMA-ENUM",
            )
        SemVer.parse(self.version, location="source.version")
        if not isinstance(self.timestamp_semantics, TimestampSemantics):
            raise ContractValidationError(
                f"source.timestamp_semantics must be a TimestampSemantics, got {self.timestamp_semantics!r}",
                location="source.timestamp_semantics",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.enabled, bool) or not isinstance(self.allows_future_events, bool):
            raise ContractValidationError(
                "source.enabled and source.allows_future_events must be booleans",
                location="source.enabled",
            )
        if not isinstance(self.reliability_metadata, Mapping):
            raise ContractValidationError(
                "source.reliability_metadata must be a mapping",
                location="source.reliability_metadata",
            )
        for key in ("stale_threshold_seconds", "future_tolerance_seconds"):
            value = self.reliability_metadata.get(key)
            if value is None or not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ContractValidationError(
                    f"source.reliability_metadata must declare {key} >= 0",
                    location=f"source.reliability_metadata.{key}",
                )
        if self.max_expected_delay_seconds is not None and self.max_expected_delay_seconds < 0:
            raise ContractValidationError(
                "source.max_expected_delay_seconds must be >= 0",
                location="source.max_expected_delay_seconds",
            )
        for key in list(self.reliability_metadata) + list(self.sensitive_fields):
            lowered = str(key).lower()
            if any(marker in lowered for marker in SECRET_KEY_MARKERS):
                raise ContractValidationError(
                    f"Source registration must not contain secret-like keys (found '{key}')",
                    location="source.reliability_metadata",
                    rule_id="SEC-001",
                )
        for field_name in self.sensitive_fields:
            if not isinstance(field_name, str) or not field_name:
                raise ContractValidationError(
                    "source.sensitive_fields entries must be non-empty strings",
                    location="source.sensitive_fields",
                )

    @property
    def stale_threshold_seconds(self) -> float:
        return float(self.reliability_metadata["stale_threshold_seconds"])

    @property
    def future_tolerance_seconds(self) -> float:
        return float(self.reliability_metadata["future_tolerance_seconds"])


@dataclass(frozen=True)
class LineageRecord:
    lineage_id: str
    stage: LineageStage
    entity_type: str
    entity_id: str
    correlation_id: str
    recorded_at: datetime
    parent_id: str | None = None
    metadata: Mapping[str, Any] | None = None

    def validate(self) -> None:
        validate_identifier("lineage_id", self.lineage_id, location="lineage.lineage_id")
        if not isinstance(self.stage, LineageStage):
            raise ContractValidationError(
                f"lineage.stage must be a LineageStage, got {self.stage!r}",
                location="lineage.stage",
                rule_id="SCHEMA-ENUM",
            )
        for name in ("entity_type", "entity_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"lineage.{name} must be a non-empty string",
                    location=f"lineage.{name}",
                )
        from architecture.contracts.identifiers import validate_any_identifier

        validate_any_identifier(self.correlation_id, location="lineage.correlation_id")
        ensure_utc(self.recorded_at, location="lineage.recorded_at")
        if self.stage is LineageStage.SOURCE:
            if self.parent_id is not None:
                raise ContractValidationError(
                    "SOURCE lineage links must not reference a parent",
                    location="lineage.parent_id",
                )
        elif self.parent_id is None:
            raise ContractValidationError(
                f"Non-SOURCE lineage links must reference a parent entity id (stage={self.stage.value})",
                location="lineage.parent_id",
                rule_id="TRACE-002",
            )
        elif not isinstance(self.parent_id, str) or not self.parent_id:
            raise ContractValidationError(
                "lineage.parent_id must be a non-empty string",
                location="lineage.parent_id",
            )
        if self.metadata is not None and not isinstance(self.metadata, Mapping):
            raise ContractValidationError(
                "lineage.metadata must be a mapping or None",
                location="lineage.metadata",
            )


def build_lineage(
    *,
    stage: LineageStage,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    parent_id: str | None,
    lineage_id: str,
    recorded_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> LineageRecord:
    record = LineageRecord(
        lineage_id=lineage_id,
        stage=stage,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        recorded_at=recorded_at or utc_now(),
        parent_id=parent_id,
        metadata=metadata,
    )
    record.validate()
    return record
