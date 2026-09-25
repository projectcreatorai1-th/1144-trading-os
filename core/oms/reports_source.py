"""Execution report source port (owned by core.oms).

Adapters produce execution evidence through this port - normalized
ExecutionReports with preserved raw provenance (SECTION 24/53)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.oms.contracts import ExecutionReport


class ExecutionReportSource(ABC):
    @abstractmethod
    def poll_execution_reports(self) -> Iterator[ExecutionReport]:  # pragma: no cover - port
        ...
