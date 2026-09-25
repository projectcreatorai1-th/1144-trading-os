"""Normalization service (owned by core.data).

Raw -> Normalized. Never loses source, timestamps, versions or the raw
reference; provenance is preserved and extended. Raw data is never edited:
the normalized representation is a separate record (SECTION 7/11).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from architecture.contracts.identifiers import new_identifier
from architecture.contracts.provenance import Provenance
from architecture.contracts.time import ensure_utc
from core.data.contracts import NormalizedDataRecord, RawDataRecord
from core.validation.engine import QualityEvaluation

CONTRACT_VERSION = "1.0.0"
INITIAL_DATA_VERSION = "1.0.0"


class NormalizationService:
    """Deterministic, schema-preserving normalization."""

    def normalize(
        self,
        raw: RawDataRecord,
        quality: QualityEvaluation,
        *,
        processed_time: datetime,
    ) -> NormalizedDataRecord:
        raw.validate()
        processed = ensure_utc(processed_time, location="normalize.processed_time")
        provenance = Provenance(
            source=raw.source,
            source_id=raw.source_id,
            source_version=raw.source_version,
            event_time=ensure_utc(raw.source_timestamp, location="normalize.event_time"),
            ingestion_time=ensure_utc(raw.received_time, location="normalize.ingestion_time"),
            processing_time=processed,
            data_version=INITIAL_DATA_VERSION,
        )
        record = NormalizedDataRecord(
            normalized_id=new_identifier("normalized_id"),
            raw_id=raw.raw_id,
            source=raw.source,
            event_time=ensure_utc(raw.source_timestamp, location="normalize.event_time"),
            received_time=ensure_utc(raw.received_time, location="normalize.received_time"),
            processed_time=processed,
            schema_id=raw.schema_id,
            schema_version=raw.schema_version,
            payload=self._normalized_payload(raw.payload),
            provenance=provenance,
            lineage_id=new_identifier("lineage_id"),
            data_version=INITIAL_DATA_VERSION,
            quality_level=quality.level,
            quality_reasons=quality.reasons,
        )
        record.validate()
        return record

    @staticmethod
    def _normalized_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Structural normalization only (no trading semantics): symbols are
        upper-cased; nothing is dropped, rounded or guessed."""
        normalized = dict(payload)
        symbol = normalized.get("symbol")
        if isinstance(symbol, str) and symbol:
            normalized["symbol"] = symbol.upper()
        return normalized
