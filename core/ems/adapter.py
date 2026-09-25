"""ExecutionAdapter port + capability (owned by core.ems).

Core never depends on MT5; adapters implement this port (SECTION 17)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.time import ensure_utc
from architecture.contracts.versioning import SemVer


class ConnectionState(Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


class PositionSemantics(Enum):
    HEDGING = "HEDGING"
    NETTING = "NETTING"
    # BACKWARD COMPATIBLE extension (Phase 10 finding 2): the real MT5
    # ACCOUNT_MARGIN_MODE enum is 0=RETAIL_NETTING, 1=EXCHANGE,
    # 2=RETAIL_HEDGING (verified live against the DEMO terminal; the old
    # adapter mapped 1->HEDGING and everything else to UNKNOWN, which
    # wrongly blocked real hedging accounts - EXEC-003).
    EXCHANGE = "EXCHANGE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AdapterCapability:
    adapter_id: str
    adapter_version: str
    environment: str
    supported_symbols: tuple[str, ...]
    order_types: tuple[str, ...]
    time_in_force: tuple[str, ...]
    min_quantity: str
    max_quantity: str
    quantity_step: str
    price_precision: int
    position_semantics: PositionSemantics
    partial_fill_support: bool
    cancel_support: bool
    replace_support: bool
    provenance: Mapping[str, Any]

    def validate(self) -> None:
        if not isinstance(self.adapter_id, str) or not self.adapter_id:
            raise ContractValidationError(
                "capability.adapter_id must be a non-empty string",
                location="capability.adapter_id",
            )
        SemVer.parse(self.adapter_version, location="capability.adapter_version")
        parse_environment(self.environment, location="capability.environment")
        for name in ("supported_symbols", "order_types", "time_in_force"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or not value:
                raise ContractValidationError(
                    f"capability.{name} must be a non-empty tuple", location=f"capability.{name}",
                )
        from core.ledger.money import parse_decimal

        for name in ("min_quantity", "max_quantity", "quantity_step"):
            parse_decimal(getattr(self, name), location=f"capability.{name}")
        if not isinstance(self.price_precision, int) or isinstance(self.price_precision, bool) \
                or self.price_precision < 0:
            raise ContractValidationError(
                "capability.price_precision must be a non-negative integer",
                location="capability.price_precision",
            )
        if not isinstance(self.position_semantics, PositionSemantics):
            raise ContractValidationError(
                "capability.position_semantics must be PositionSemantics",
                location="capability.position_semantics", rule_id="SCHEMA-ENUM",
            )
        for name in ("partial_fill_support", "cancel_support", "replace_support"):
            if not isinstance(getattr(self, name), bool):
                raise ContractValidationError(
                    f"capability.{name} must be a boolean", location=f"capability.{name}",
                )
        if not isinstance(self.provenance, Mapping) or not self.provenance:
            raise ContractValidationError(
                "capability.provenance is required",
                location="capability.provenance", rule_id="PROV-001",
            )

    def supports(self, *, symbol: str, order_type: str, time_in_force: str) -> bool:
        return (
            ("*" in self.supported_symbols or symbol in self.supported_symbols)
            and order_type in self.order_types
            and time_in_force in self.time_in_force
        )

    @property
    def execution_safe(self) -> bool:
        """UNKNOWN semantics or empty critical fields block execution."""
        return self.position_semantics is not PositionSemantics.UNKNOWN


@dataclass(frozen=True)
class AdapterResponse:
    """Normalized adapter result. Raw evidence is carried in `raw` and MUST be
    persisted by the caller; MT5/broker objects never leak into the domain."""

    ok: bool
    broker_order_id: str | None
    normalized_error: str | None  # BrokerError value or None
    raw: Mapping[str, Any]
    raw_reference: str
    broker_timestamp: datetime | None = None

    def validate(self) -> None:
        if not isinstance(self.raw_reference, str) or not self.raw_reference:
            raise ContractValidationError(
                "adapter.raw_reference is required (raw evidence identity)",
                location="adapter.raw_reference",
            )
        if not isinstance(self.raw, Mapping):
            raise ContractValidationError(
                "adapter.raw must carry the preserved broker response",
                location="adapter.raw",
            )
        if self.broker_timestamp is not None:
            ensure_utc(self.broker_timestamp, location="adapter.broker_timestamp")


class ExecutionAdapter(ABC):
    """Generic execution boundary implemented by MT5/Simulation adapters."""

    @abstractmethod
    def adapter_id(self) -> str:  # pragma: no cover - port
        ...

    @abstractmethod
    def environment(self) -> str:  # pragma: no cover
        ...

    @abstractmethod
    def connect(self) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def disconnect(self) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def health(self) -> ConnectionState:  # pragma: no cover
        ...

    @abstractmethod
    def capabilities(self) -> AdapterCapability:  # pragma: no cover
        ...

    @abstractmethod
    def submit_order(self, canonical_request: Mapping[str, Any]) -> AdapterResponse:  # pragma: no cover
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> AdapterResponse:  # pragma: no cover
        ...

    @abstractmethod
    def replace_order(self, broker_order_id: str,
                      replacement: Mapping[str, Any]) -> AdapterResponse:  # pragma: no cover
        ...

    @abstractmethod
    def poll_order(self, broker_order_id: str) -> AdapterResponse:  # pragma: no cover
        ...
