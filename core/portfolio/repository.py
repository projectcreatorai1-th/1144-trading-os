"""Position repository port (owned by core.portfolio)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.portfolio.contracts import Position


class PositionRepository(ABC):
    @abstractmethod
    def save(self, position: Position) -> None:  # pragma: no cover - port definition
        ...

    @abstractmethod
    def get_by_id(self, position_id: str) -> Position:  # pragma: no cover - port definition
        ...

    @abstractmethod
    def iter_by_account(self, account_id: str) -> Iterator[Position]:  # pragma: no cover
        ...
