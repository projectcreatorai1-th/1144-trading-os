"""Order repository port (owned by core.execution).

Implementations arrive with platform.database in later phases.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.execution.contracts import Order


class OrderRepository(ABC):
    @abstractmethod
    def save(self, order: Order) -> None:  # pragma: no cover - port definition
        ...

    @abstractmethod
    def get_by_id(self, order_id: str) -> Order:  # pragma: no cover - port definition
        ...

    @abstractmethod
    def iter_by_strategy(self, strategy_id: str) -> Iterator[Order]:  # pragma: no cover
        ...
