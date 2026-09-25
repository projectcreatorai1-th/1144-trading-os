"""Execution report contract + broker error normalization (owned by core.oms).

ExecutionReports are immutable broker evidence. Corrections are linked
CORRECTION reports (Phase 2 ledger pattern). Broker errors normalize to a
canonical taxonomy while raw evidence is preserved (SECTION 12/24)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.time import ensure_utc
from core.execution.contracts import OrderSide
from core.ledger.money import parse_decimal

CONTRACT_VERSION = "1.0.0"


class ExecutionType(Enum):
    FILL = "FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    CANCEL = "CANCEL"
    REJECT = "REJECT"
    CORRECTION = "CORRECTION"
    STATUS = "STATUS"


class ExecutionStatus(Enum):
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class BrokerError(Enum):
    """Canonical broker error taxonomy (SECTION 24) - raw evidence preserved."""

    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"
    INVALID_VOLUME = "INVALID_VOLUME"
    INVALID_PRICE = "INVALID_PRICE"
    MARKET_CLOSED = "MARKET_CLOSED"
    TRADE_DISABLED = "TRADE_DISABLED"
    REQUOTE = "REQUOTE"
    OFF_QUOTES = "OFF_QUOTES"
    TIMEOUT = "TIMEOUT"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    BROKER_REJECTED = "BROKER_REJECTED"
    UNKNOWN = "UNKNOWN"


class RetryClassification(Enum):
    RETRYABLE = "RETRYABLE"
    NON_RETRYABLE = "NON_RETRYABLE"
    UNKNOWN = "UNKNOWN"


#: Deterministic retry classification (SECTION 25) - single source.
RETRYABLE_ERRORS = frozenset({BrokerError.TIMEOUT, BrokerError.CONNECTION_ERROR})
NON_RETRYABLE_ERRORS = frozenset({
    BrokerError.INSUFFICIENT_MARGIN, BrokerError.INVALID_VOLUME,
    BrokerError.INVALID_PRICE, BrokerError.MARKET_CLOSED, BrokerError.TRADE_DISABLED,
    BrokerError.BROKER_REJECTED,
})


def classify_retry(error: BrokerError) -> RetryClassification:
    if error in RETRYABLE_ERRORS:
        return RetryClassification.RETRYABLE
    if error in NON_RETRYABLE_ERRORS:
        return RetryClassification.NON_RETRYABLE
    return RetryClassification.UNKNOWN


def execution_idempotency_key(*, order_id: str, broker_order_id: str | None,
                              broker_execution_reference: str) -> str:
    """Deterministic per-fill identity: duplicate broker reports deduplicate."""
    material = f"{order_id}|{broker_order_id or ''}|{broker_execution_reference}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExecutionReport:
    execution_id: str
    order_id: str
    environment: str
    execution_type: ExecutionType
    status: ExecutionStatus
    symbol: str
    side: OrderSide
    executed_quantity: str
    execution_price: str
    currency: str
    broker_timestamp: datetime
    received_at: datetime
    provenance: Mapping[str, Any]
    raw_reference: str
    idempotency_key: str
    broker_order_id: str | None = None
    fees: str | None = None
    commission: str | None = None
    swap: str | None = None
    correction_of: str | None = None
    normalized_error: BrokerError | None = None

    def validate(self) -> None:
        validate_identifier("execution_id", self.execution_id, location="execution.execution_id")
        validate_identifier("order_id", self.order_id, location="execution.order_id")
        parse_environment(self.environment, location="execution.environment")
        for name, enum_type in (("execution_type", ExecutionType),
                                ("status", ExecutionStatus)):
            if not isinstance(getattr(self, name), enum_type):
                raise ContractValidationError(
                    f"execution.{name} must be a {enum_type.__name__}",
                    location=f"execution.{name}", rule_id="SCHEMA-ENUM",
                )
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ContractValidationError(
                "execution.symbol must be a non-empty string", location="execution.symbol",
            )
        if not isinstance(self.side, OrderSide):
            raise ContractValidationError(
                "execution.side must be an OrderSide", location="execution.side",
                rule_id="SCHEMA-ENUM",
            )
        quantity = parse_decimal(self.executed_quantity, location="execution.executed_quantity")
        if quantity < 0:
            raise ContractValidationError(
                "execution.executed_quantity must be >= 0",
                location="execution.executed_quantity",
            )
        parse_decimal(self.execution_price, location="execution.execution_price")
        if not isinstance(self.currency, str) or not self.currency:
            raise ContractValidationError(
                "execution.currency must be a non-empty string", location="execution.currency",
            )
        ensure_utc(self.broker_timestamp, location="execution.broker_timestamp")
        ensure_utc(self.received_at, location="execution.received_at")
        if not isinstance(self.provenance, Mapping) or not self.provenance:
            raise ContractValidationError(
                "execution.provenance is required (raw evidence preserved)",
                location="execution.provenance", rule_id="PROV-001",
            )
        for key in ("source", "adapter", "raw"):
            if key not in self.provenance:
                raise ContractValidationError(
                    f"execution.provenance.{key} is required (SECTION 53)",
                    location=f"execution.provenance.{key}",
                )
        if not isinstance(self.raw_reference, str) or not self.raw_reference:
            raise ContractValidationError(
                "execution.raw_reference is required (raw broker evidence)",
                location="execution.raw_reference",
            )
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key:
            raise ContractValidationError(
                "execution.idempotency_key is required (duplicate fill dedup)",
                location="execution.idempotency_key",
            )
        for name in ("fees", "commission", "swap"):
            value = getattr(self, name)
            if value is not None:
                parse_decimal(value, location=f"execution.{name}")
        if self.correction_of is not None:
            validate_identifier("execution_id", self.correction_of,
                                location="execution.correction_of")

    def quantity_decimal(self) -> Decimal:
        return parse_decimal(self.executed_quantity, location="execution.executed_quantity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id, "order_id": self.order_id,
            "environment": self.environment,
            "execution_type": self.execution_type.value,
            "status": self.status.value, "symbol": self.symbol,
            "side": self.side.value,
            "executed_quantity": self.executed_quantity,
            "execution_price": self.execution_price, "currency": self.currency,
            "broker_order_id": self.broker_order_id, "fees": self.fees,
            "commission": self.commission, "swap": self.swap,
            "broker_timestamp": ensure_utc(self.broker_timestamp).isoformat(),
            "received_at": ensure_utc(self.received_at).isoformat(),
            "provenance": dict(self.provenance),
            "raw_reference": self.raw_reference,
            "idempotency_key": self.idempotency_key,
            "correction_of": self.correction_of,
            "normalized_error": self.normalized_error.value if self.normalized_error else None,
        }


#: MT5 retcode -> canonical error (numeric codes from the MT5 API)
MT5_RETCODE_MAP = {
    10014: BrokerError.INVALID_VOLUME,
    10015: BrokerError.INVALID_PRICE,
    10016: BrokerError.INVALID_PRICE,
    10017: BrokerError.BROKER_REJECTED,
    10018: BrokerError.MARKET_CLOSED,
    10019: BrokerError.BROKER_REJECTED,
    10021: BrokerError.INSUFFICIENT_MARGIN,
    10026: BrokerError.TRADE_DISABLED,
    10027: BrokerError.BROKER_REJECTED,
    10030: BrokerError.BROKER_REJECTED,
    10004: BrokerError.REQUOTE,
    10044: BrokerError.BROKER_REJECTED,
    -1: BrokerError.CONNECTION_ERROR,
}


def normalize_error(raw_code: Any, *, adapter: str, adapter_version: str) -> BrokerError:
    """Normalize a broker-specific error code to the canonical taxonomy,
    preserving raw evidence at the call site (raw never dropped)."""
    if isinstance(raw_code, (int, float)):
        code = int(raw_code)
        if code == 10008 or code == 10009:
            return BrokerError.UNKNOWN  # success-ish codes are not errors
        if code == 10010 or code == 10012 or code == 10013:
            return BrokerError.TIMEOUT
        return MT5_RETCODE_MAP.get(code, BrokerError.UNKNOWN)
    text = str(raw_code or "").upper()
    mapping = (
        ("INSUFFICIENT_MARGIN", BrokerError.INSUFFICIENT_MARGIN),
        ("MARGIN", BrokerError.INSUFFICIENT_MARGIN),
        ("INVALID_VOLUME", BrokerError.INVALID_VOLUME),
        ("VOLUME", BrokerError.INVALID_VOLUME),
        ("INVALID_PRICE", BrokerError.INVALID_PRICE),
        ("PRICE", BrokerError.INVALID_PRICE),
        ("MARKET_CLOSED", BrokerError.MARKET_CLOSED),
        ("CLOSED", BrokerError.MARKET_CLOSED),
        ("TRADE_DISABLED", BrokerError.TRADE_DISABLED),
        ("DISABLED", BrokerError.TRADE_DISABLED),
        ("REQUOTE", BrokerError.REQUOTE),
        ("OFF_QUOTES", BrokerError.OFF_QUOTES),
        ("TIMEOUT", BrokerError.TIMEOUT),
        ("TIMED OUT", BrokerError.TIMEOUT),
        ("CONNECTION", BrokerError.CONNECTION_ERROR),
        ("NETWORK", BrokerError.CONNECTION_ERROR),
    )
    for needle, canonical in mapping:
        if needle in text:
            return canonical
    _ = adapter, adapter_version  # provenance carried by the caller's report
    return BrokerError.UNKNOWN if text else BrokerError.BROKER_REJECTED
