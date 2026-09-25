"""Risk decision store port (owned by core.risk).

Decisions + the exact context they were made against, so replay can
re-evaluate and compare (SECTION 25). Append-only."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Mapping

from core.risk.contracts import RiskDecision
from core.risk.context import RiskContext


class RiskDecisionStore(ABC):
    @abstractmethod
    def append(self, decision: RiskDecision, context: RiskContext) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def get_by_id(self, risk_decision_id: str) -> RiskDecision:  # pragma: no cover
        ...

    @abstractmethod
    def get_context(self, risk_decision_id: str) -> RiskContext:  # pragma: no cover
        ...

    @abstractmethod
    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[RiskDecision]:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...
