"""Data source registry (owned by core.data).

Registration and validation of data sources. The registry IS the data-source
configuration boundary, separated from system/risk/strategy config (SECTION
6/31). Phase 1 does not connect real providers; registration, validation
and lookup work for real. In-memory by design; persistence is a documented
Phase 2+ extension.
"""
from __future__ import annotations

from typing import Iterator, Mapping

from architecture.contracts.errors import ContractError, IngestionError
from architecture.contracts.schema import build_schema_registry
from core.data.contracts import DataSourceRecord, SourceStatus

CONTRACT_VERSION = "1.0.0"


class SourceRegistry:
    """Validated in-memory registry of data sources."""

    def __init__(self) -> None:
        self._sources: dict[str, DataSourceRecord] = {}

    def register(self, record: DataSourceRecord) -> None:
        record.validate()
        if record.source_id in self._sources:
            raise ContractError(
                f"Duplicate data source registration '{record.source_id}'",
                location="source_registry",
                rule_id="ARCH-003",
            )
        schemas = build_schema_registry()
        if record.schema_id not in schemas.schema_ids:
            raise ContractError(
                f"Data source '{record.source_id}' declares unknown schema '{record.schema_id}'",
                location="source_registry",
                rule_id="DQ-013",
                details={"schema_id": record.schema_id, "known_schemas": list(schemas.schema_ids)},
            )
        self._sources[record.source_id] = record

    def get(self, source_id: str) -> DataSourceRecord | None:
        return self._sources.get(source_id)

    def require(self, source_id: str) -> DataSourceRecord:
        record = self._sources.get(source_id)
        if record is None:
            raise IngestionError(
                f"Unknown data source '{source_id}' (rejecting, DQ-012)",
                location="source_registry",
                rule_id="DQ-012",
                details={"source_id": source_id, "registered": sorted(self._sources)},
            )
        if not record.enabled:
            raise IngestionError(
                f"Data source '{source_id}' is disabled (rejecting, DQ-012)",
                location="source_registry",
                rule_id="DQ-012",
                details={"source_id": source_id, "enabled": False},
            )
        if record.status is SourceStatus.RETIRED:
            raise IngestionError(
                f"Data source '{source_id}' is retired (rejecting, DQ-012)",
                location="source_registry",
                rule_id="DQ-012",
                details={"source_id": source_id, "status": record.status.value},
            )
        return record

    def iter_all(self) -> Iterator[DataSourceRecord]:
        for source_id in sorted(self._sources):
            yield self._sources[source_id]

    def count(self) -> int:
        return len(self._sources)
