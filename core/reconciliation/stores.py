"""Reconciliation store ports (owned by core.reconciliation).

Observations and reconciliation results are immutable history (SECTIONS 28/31).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.reconciliation.contracts import ExternalObservation, ReconciliationResult


class ObservationStore(ABC):
    @abstractmethod
    def append(self, observation: ExternalObservation) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def get_by_id(self, observation_id: str) -> ExternalObservation:  # pragma: no cover
        ...

    @abstractmethod
    def iter_by_entity(self, entity_type: str, entity_id: str) -> Iterator[ExternalObservation]:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...


class ReconciliationStore(ABC):
    @abstractmethod
    def append(self, result: ReconciliationResult) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def get_by_id(self, reconciliation_id: str) -> ReconciliationResult:  # pragma: no cover
        ...

    @abstractmethod
    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[ReconciliationResult]:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...
