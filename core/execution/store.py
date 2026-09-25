"""Order store port (owned by core.execution; extended for Phase 5).

Orders are stored as immutable versions: every lifecycle transition writes a
new row (same order_id, bumped order_version). Append-only; duplicate
idempotency keys rejected - deterministic semantic identity."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.execution.contracts import Order
from core.execution.repository import OrderRepository


class OrderStore(OrderRepository, ABC):
    @abstractmethod
    def save(self, order: Order) -> None:  # pragma: no cover - port
        """Persist one immutable order version (id + version unique)."""
        ...

    @abstractmethod
    def get_by_id(self, order_id: str) -> Order:  # pragma: no cover
        """Latest version of the order."""
        ...

    @abstractmethod
    def get_version(self, order_id: str, order_version: int) -> Order:  # pragma: no cover
        ...

    @abstractmethod
    def iter_versions(self, order_id: str) -> Iterator[Order]:  # pragma: no cover
        ...

    @abstractmethod
    def find_by_idempotency_key(self, idempotency_key: str) -> Order | None:  # pragma: no cover
        ...

    @abstractmethod
    def iter_active(self) -> Iterator[Order]:  # pragma: no cover
        """Latest versions not in terminal states (recovery scan)."""
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...
