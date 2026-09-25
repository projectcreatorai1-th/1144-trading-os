"""Ledger contract (owned by core.ledger).

Raw ledger entries are immutable (RULE: never overwrite historical truth).
Corrections are new ADJUSTMENT entries linked via adjusts_entry_id, or
REVERSAL-style entries linked via reverses_entry_id. Every entry must trace
to at least one source object.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import (
    is_valid_identifier,
    validate_any_identifier,
    validate_identifier,
)
from architecture.contracts.time import ensure_utc
from architecture.contracts.versioning import SemVer
from core.execution.contracts import to_decimal
from core.ledger.money import parse_decimal

CONTRACT_VERSION = "1.1.0"

TRACE_REFERENCE_FIELDS = (
    "source_event_id",
    "order_id",
    "execution_id",
    "position_id",
    "strategy_id",
)

_ID_KIND_BY_FIELD = {
    "source_event_id": "event_id",
    "order_id": "order_id",
    "execution_id": "execution_id",
    "position_id": "position_id",
    "strategy_id": "strategy_id",
    "adjusts_entry_id": "ledger_entry_id",
    "reverses_entry_id": "ledger_entry_id",
}


class LedgerType(Enum):
    ORDER = "ORDER"
    EXECUTION = "EXECUTION"
    POSITION = "POSITION"
    CASH = "CASH"
    FEE = "FEE"
    COMMISSION = "COMMISSION"
    SWAP = "SWAP"
    FUNDING = "FUNDING"
    MARGIN = "MARGIN"
    ADJUSTMENT = "ADJUSTMENT"
    TRANSFER = "TRANSFER"


@dataclass(frozen=True)
class LedgerEntry:
    ledger_entry_id: str
    entry_type: LedgerType
    account_id: str
    amount: Decimal
    currency: str
    environment: str
    entry_time: datetime
    correlation_id: str
    source_event_id: str | None = None
    order_id: str | None = None
    execution_id: str | None = None
    position_id: str | None = None
    strategy_id: str | None = None
    causation_id: str | None = None
    adjusts_entry_id: str | None = None
    reverses_entry_id: str | None = None
    quantity: Decimal | None = None
    symbol: str | None = None
    status: str = "POSTED"
    entry_hash: str | None = None
    previous_entry_hash: str | None = None
    idempotency_key: str | None = None
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount, "ledger.amount"))
        if self.quantity is not None:
            object.__setattr__(
                self, "quantity", to_decimal(self.quantity, "ledger.quantity")
            )

    def validate(self) -> None:
        validate_identifier(
            "ledger_entry_id", self.ledger_entry_id, location="ledger.ledger_entry_id"
        )
        if not isinstance(self.entry_type, LedgerType):
            raise ContractValidationError(
                f"ledger.entry_type must be a LedgerType, got {self.entry_type!r}",
                location="ledger.entry_type",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.account_id, str) or not self.account_id:
            raise ContractValidationError(
                "ledger.account_id must be a non-empty string",
                location="ledger.account_id",
            )
        if not isinstance(self.currency, str) or not self.currency:
            raise ContractValidationError(
                "ledger.currency must be a non-empty string",
                location="ledger.currency",
            )
        parse_environment(self.environment, location="ledger.environment")
        ensure_utc(self.entry_time, location="ledger.entry_time")
        validate_any_identifier(self.correlation_id, location="ledger.correlation_id")

        for ref_field in TRACE_REFERENCE_FIELDS:
            value = getattr(self, ref_field)
            if value is not None:
                validate_identifier(
                    _ID_KIND_BY_FIELD[ref_field], value, location=f"ledger.{ref_field}"
                )
        if not any(getattr(self, f) is not None for f in TRACE_REFERENCE_FIELDS):
            raise ContractValidationError(
                "Ledger entries must trace to at least one of "
                f"{', '.join(TRACE_REFERENCE_FIELDS)}",
                location="ledger.traceability",
                rule_id="TRACE-001",
            )
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="ledger.causation_id")
        if self.adjusts_entry_id is not None and self.reverses_entry_id is not None:
            raise ContractValidationError(
                "adjusts_entry_id and reverses_entry_id are mutually exclusive",
                location="ledger.correction",
            )
        if self.entry_type is LedgerType.ADJUSTMENT and self.adjusts_entry_id is None:
            raise ContractValidationError(
                "ADJUSTMENT entries must reference the corrected entry via adjusts_entry_id",
                location="ledger.adjusts_entry_id",
            )
        if self.entry_type is LedgerType.TRANSFER and (
            self.adjusts_entry_id is not None or self.reverses_entry_id is not None
        ):
            raise ContractValidationError(
                "TRANSFER entries must not be correction entries",
                location="ledger.correction",
            )
        if self.status != "POSTED":
            raise ContractValidationError(
                f"ledger.status must be POSTED (got {self.status!r}); future workflow "
                "states arrive with a contract version",
                location="ledger.status",
                rule_id="SCHEMA-ENUM",
            )
        if self.symbol is not None and (not isinstance(self.symbol, str) or not self.symbol):
            raise ContractValidationError(
                "ledger.symbol must be a non-empty string or None",
                location="ledger.symbol",
            )
        if self.quantity is not None and self.quantity == 0:
            raise ContractValidationError(
                "ledger.quantity must be non-zero when present",
                location="ledger.quantity",
            )
        if self.entry_hash is not None and self.entry_hash != self.compute_hash():
            raise ContractValidationError(
                "Ledger entry hash mismatch (integrity violation)",
                location="ledger.entry_hash",
                rule_id="LEDGER-001",
                details={"expected": self.compute_hash(), "actual": self.entry_hash},
            )
        for name in ("entry_hash", "previous_entry_hash", "idempotency_key"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ContractValidationError(
                    f"ledger.{name} must be a non-empty string or None",
                    location=f"ledger.{name}",
                )
        SemVer.parse(self.contract_version, location="ledger.contract_version")

    def hash_material(self) -> dict[str, Any]:
        """Canonical content that the entry hash commits to (amount/quantity
        as canonical strings; never floats)."""
        return {
            "ledger_entry_id": self.ledger_entry_id,
            "entry_type": self.entry_type.value,
            "account_id": self.account_id,
            "amount": str(self.amount),
            "currency": self.currency,
            "environment": self.environment,
            "entry_time": ensure_utc(self.entry_time).isoformat(),
            "correlation_id": self.correlation_id,
            "source_event_id": self.source_event_id,
            "order_id": self.order_id,
            "execution_id": self.execution_id,
            "position_id": self.position_id,
            "strategy_id": self.strategy_id,
            "adjusts_entry_id": self.adjusts_entry_id,
            "reverses_entry_id": self.reverses_entry_id,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "symbol": self.symbol,
            "previous_entry_hash": self.previous_entry_hash,
            "idempotency_key": self.idempotency_key,
        }

    def compute_hash(self) -> str:
        import hashlib
        import json

        material = json.dumps(
            self.hash_material(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger_entry_id": self.ledger_entry_id,
            "entry_type": self.entry_type.value,
            "account_id": self.account_id,
            "amount": str(self.amount),
            "currency": self.currency,
            "environment": self.environment,
            "entry_time": ensure_utc(self.entry_time).isoformat(),
            "correlation_id": self.correlation_id,
            "source_event_id": self.source_event_id,
            "order_id": self.order_id,
            "execution_id": self.execution_id,
            "position_id": self.position_id,
            "strategy_id": self.strategy_id,
            "causation_id": self.causation_id,
            "adjusts_entry_id": self.adjusts_entry_id,
            "reverses_entry_id": self.reverses_entry_id,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "symbol": self.symbol,
            "status": self.status,
            "entry_hash": self.entry_hash,
            "previous_entry_hash": self.previous_entry_hash,
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_storage(cls, data: Mapping[str, Any]) -> "LedgerEntry":
        """Rebuild a posted entry from stored plain values."""
        from architecture.contracts.time import parse_canonical

        entry = cls(
            ledger_entry_id=data["ledger_entry_id"],
            entry_type=LedgerType(data["entry_type"]),
            account_id=data["account_id"],
            amount=parse_decimal(data["amount"], location="ledger.amount"),
            currency=data["currency"],
            environment=data["environment"],
            entry_time=parse_canonical(data["entry_time"]),
            correlation_id=data["correlation_id"],
            source_event_id=data.get("source_event_id"),
            order_id=data.get("order_id"),
            execution_id=data.get("execution_id"),
            position_id=data.get("position_id"),
            strategy_id=data.get("strategy_id"),
            causation_id=data.get("causation_id"),
            adjusts_entry_id=data.get("adjusts_entry_id"),
            reverses_entry_id=data.get("reverses_entry_id"),
            quantity=parse_decimal(data["quantity"], location="ledger.quantity")
            if data.get("quantity") is not None else None,
            symbol=data.get("symbol"),
            status=data.get("status", "POSTED"),
            entry_hash=data.get("entry_hash"),
            previous_entry_hash=data.get("previous_entry_hash"),
            idempotency_key=data.get("idempotency_key"),
        )
        entry.validate()
        return entry

    def adjustment_for(
        self,
        *,
        ledger_entry_id: str,
        amount: Decimal,
        reason: str,
        correlation_id: str | None = None,
    ) -> "LedgerEntry":
        """Build a correction entry that adjusts this raw entry (no overwrite).

        The correction inherits the raw entry's trace references so the
        adjustment stays traceable to order/execution/position/strategy."""
        if self.entry_type is LedgerType.TRANSFER:
            raise ContractValidationError(
                "TRANSFER entries cannot be adjusted via this helper",
                location="ledger.adjustment_for",
            )
        entry = LedgerEntry(
            ledger_entry_id=ledger_entry_id,
            entry_type=LedgerType.ADJUSTMENT,
            account_id=self.account_id,
            amount=amount,
            currency=self.currency,
            environment=self.environment,
            entry_time=self.entry_time,
            correlation_id=correlation_id or self.correlation_id,
            source_event_id=self.source_event_id,
            order_id=self.order_id,
            execution_id=self.execution_id,
            position_id=self.position_id,
            strategy_id=self.strategy_id,
            causation_id=self.causation_id,
            adjusts_entry_id=self.ledger_entry_id,
        )
        entry.validate()
        return entry

    def reversal_of(
        self,
        *,
        ledger_entry_id: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> "LedgerEntry":
        """Build a reversal entry negating this raw entry."""
        entry = LedgerEntry(
            ledger_entry_id=ledger_entry_id,
            entry_type=self.entry_type,
            account_id=self.account_id,
            amount=-self.amount,
            currency=self.currency,
            environment=self.environment,
            entry_time=self.entry_time,
            correlation_id=correlation_id or self.correlation_id,
            source_event_id=self.source_event_id,
            order_id=self.order_id,
            execution_id=self.execution_id,
            position_id=self.position_id,
            strategy_id=self.strategy_id,
            causation_id=self.causation_id,
            reverses_entry_id=self.ledger_entry_id,
        )
        entry.validate()
        return entry
