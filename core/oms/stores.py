"""Execution report store port (owned by core.oms)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.oms.contracts import ExecutionReport


class ExecutionReportStore(ABC):
    @abstractmethod
    def append(self, report: ExecutionReport) -> bool:  # pragma: no cover - port
        """Append a report. Returns False when the idempotency key already
        exists (duplicate broker report - no duplicate canonical effect)."""
        ...

    @abstractmethod
    def get_by_id(self, execution_id: str) -> ExecutionReport:  # pragma: no cover
        ...

    @abstractmethod
    def iter_by_order(self, order_id: str) -> Iterator[ExecutionReport]:  # pragma: no cover
        ...

    @abstractmethod
    def find_by_idempotency_key(self, idempotency_key: str) -> ExecutionReport | None:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...
