"""Database boundary contract (owned by platform.database).

Layering (SECTION 33):  Core -> Repository/Service interface (domain ports)
-> Database adapter. Core never touches SQL; GUI never touches the database.
Future database technologies implement this boundary without breaking core
contracts. Phase 0 defines the boundary only - no implementation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DatabaseAdapter(ABC):
    """Technology boundary every database implementation must satisfy."""

    @abstractmethod
    def begin_transaction(self) -> Any:  # pragma: no cover - boundary definition
        ...

    @abstractmethod
    def commit_transaction(self, transaction: Any) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def rollback_transaction(self, transaction: Any) -> None:  # pragma: no cover
        ...
