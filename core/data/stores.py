"""Data store ports (owned by core.data).

Append-only by contract: raw records are never updated or deleted; duplicate
ingestion is recorded as a reference, not an overwrite. Implementations live
in platform.database (SECTION 20/28 of Phase 1).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.data.contracts import LineageRecord, NormalizedDataRecord, RawDataRecord


class RawDataStore(ABC):
    """Immutable raw payload storage."""

    @abstractmethod
    def append(self, record: RawDataRecord) -> None:  # pragma: no cover - port definition
        ...

    @abstractmethod
    def get_by_id(self, raw_id: str) -> RawDataRecord:  # pragma: no cover
        ...

    @abstractmethod
    def find_by_hash(self, source: str, payload_hash: str) -> RawDataRecord | None:  # pragma: no cover
        ...

    @abstractmethod
    def iter_all(self) -> Iterator[RawDataRecord]:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...


class NormalizedDataStore(ABC):
    """Versioned normalized records; corrections append, never overwrite."""

    @abstractmethod
    def append(self, record: NormalizedDataRecord) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def get_by_id(self, normalized_id: str) -> NormalizedDataRecord:  # pragma: no cover
        ...

    @abstractmethod
    def iter_by_raw_id(self, raw_id: str) -> Iterator[NormalizedDataRecord]:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...


class LineageStore(ABC):
    """Lineage links: SOURCE -> RAW -> NORMALIZED -> EVENT (extensible)."""

    @abstractmethod
    def append(self, record: LineageRecord) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def iter_by_entity(self, entity_type: str, entity_id: str) -> Iterator[LineageRecord]:  # pragma: no cover
        ...

    @abstractmethod
    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[LineageRecord]:  # pragma: no cover
        ...

    @abstractmethod
    def chain_for(self, entity_type: str, entity_id: str) -> list[LineageRecord]:  # pragma: no cover
        ...
