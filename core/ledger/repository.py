"""Ledger repository port (owned by core.ledger).

Implementations arrive with platform.database in later phases. Append-only:
updates of raw entries are impossible by contract.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.ledger.contracts import LedgerEntry


class LedgerRepository(ABC):
    @abstractmethod
    def append(self, entry: LedgerEntry) -> None:  # pragma: no cover - port definition
        ...

    @abstractmethod
    def get_by_id(self, ledger_entry_id: str) -> LedgerEntry:  # pragma: no cover
        ...

    @abstractmethod
    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[LedgerEntry]:  # pragma: no cover
        ...
