"""Balance reconstruction (owned by core.ledger).

opening balance + ledger entries = closing balance, deterministically, per
account and currency, with per-type buckets (cash/fees/commission/swap/
funding/adjustment). Decimal-only arithmetic (SECTION 20)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator, Mapping

from core.ledger.contracts import LedgerEntry, LedgerType
from core.ledger.money import parse_decimal, precision_for
from core.ledger.store import LedgerStore

CONTRACT_VERSION = "1.0.0"

BALANCE_ENTRY_TYPES = (
    LedgerType.CASH,
    LedgerType.EXECUTION,
    LedgerType.FEE,
    LedgerType.COMMISSION,
    LedgerType.SWAP,
    LedgerType.FUNDING,
    LedgerType.ADJUSTMENT,
    LedgerType.TRANSFER,
)


@dataclass(frozen=True)
class BalanceReport:
    account_id: str
    currency: str
    opening_balance: Decimal
    closing_balance: Decimal
    entry_count: int
    by_type: Mapping[str, Decimal]

    def to_dict(self) -> dict[str, str | int | dict[str, str]]:
        return {
            "account_id": self.account_id,
            "currency": self.currency,
            "opening_balance": str(self.opening_balance),
            "closing_balance": str(self.closing_balance),
            "entry_count": self.entry_count,
            "by_type": {key: str(value) for key, value in self.by_type.items()},
        }


class BalanceReconstructor:
    def __init__(self, store: LedgerStore) -> None:
        self._store = store

    def reconstruct(
        self,
        account_id: str,
        *,
        currency: str,
        opening_balance: Decimal | int | str = Decimal("0"),
    ) -> BalanceReport:
        precision_for(currency)  # registered currency only
        opening = parse_decimal(opening_balance, location="balance.opening")
        by_type: dict[str, Decimal] = {}
        closing = opening
        count = 0
        for entry in self._entries(account_id, currency):
            if entry.entry_type not in BALANCE_ENTRY_TYPES:
                continue
            closing += entry.amount
            by_type[entry.entry_type.value] = by_type.get(entry.entry_type.value, Decimal("0")) + entry.amount
            count += 1
        return BalanceReport(
            account_id=account_id,
            currency=currency,
            opening_balance=opening,
            closing_balance=closing,
            entry_count=count,
            by_type=dict(by_type),
        )

    def _entries(self, account_id: str, currency: str) -> Iterator[LedgerEntry]:
        yield from self._store.iter_by_account(account_id, currency)
