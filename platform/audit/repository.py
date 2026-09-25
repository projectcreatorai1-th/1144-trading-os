"""Audit repository port (owned by platform.audit). Append-only by contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from platform.audit.contracts import AuditRecord


class AuditRepository(ABC):
    @abstractmethod
    def append(self, record: AuditRecord) -> None:  # pragma: no cover - port definition
        ...

    @abstractmethod
    def get_by_id(self, audit_id: str) -> AuditRecord:  # pragma: no cover - port definition
        ...

    @abstractmethod
    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[AuditRecord]:  # pragma: no cover
        ...
