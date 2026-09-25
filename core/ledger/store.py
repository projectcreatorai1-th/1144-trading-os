"""Ledger store port (owned by core.ledger).

Append-only with hash-chain and idempotency enforcement at the storage
boundary: posted entries can never be updated or deleted (SECTIONS 16-19).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.ledger.contracts import LedgerEntry
from core.ledger.repository import LedgerRepository


class LedgerStore(LedgerRepository, ABC):
    @abstractmethod
    def append(self, entry: LedgerEntry) -> None:  # pragma: no cover
        """Validate integrity + idempotency, then append (no overwrite ever)."""
        ...

    @abstractmethod
    def iter_by_account(self, account_id: str, currency: str | None = None) -> Iterator[LedgerEntry]:  # pragma: no cover
        ...

    @abstractmethod
    def find_by_idempotency_key(self, idempotency_key: str) -> LedgerEntry | None:  # pragma: no cover
        ...

    @abstractmethod
    def last_entry_hash(self, account_id: str) -> str | None:  # pragma: no cover
        ...

    @abstractmethod
    def verify_account_chain(self, account_id: str) -> list[dict[str, str]]:  # pragma: no cover
        """Return chain-verification issues (empty list = intact)."""
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...
