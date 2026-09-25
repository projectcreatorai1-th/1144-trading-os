"""Provenance contract (SECTION 23).

Decision-critical objects carry provenance so that RAW -> NORMALIZED ->
ANALYZED -> DECISION -> EXECUTION -> RESULT stays traceable end to end.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from architecture.contracts.errors import ProvenanceError
from architecture.contracts.time import ensure_not_before, ensure_utc


@dataclass(frozen=True)
class Provenance:
    source: str
    source_id: str
    ingestion_time: datetime
    event_time: datetime
    source_version: str | None = None
    processing_time: datetime | None = None
    model_version: str | None = None
    policy_version: str | None = None
    data_version: str | None = None

    def validate(self) -> None:
        if not isinstance(self.source, str) or not self.source:
            raise ProvenanceError(
                "provenance.source must be a non-empty string",
                location="provenance.source",
            )
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ProvenanceError(
                "provenance.source_id must be a non-empty string",
                location="provenance.source_id",
            )
        event_time = ensure_utc(self.event_time, location="provenance.event_time")
        ingestion_time = ensure_utc(
            self.ingestion_time, location="provenance.ingestion_time"
        )
        ensure_not_before(
            ingestion_time, not_before=event_time, location="provenance.ingestion_time"
        )
        if self.processing_time is not None:
            ensure_not_before(
                ensure_utc(self.processing_time, location="provenance.processing_time"),
                not_before=event_time,
                location="provenance.processing_time",
            )
