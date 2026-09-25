"""Order contract (owned by core.execution).

Order lifecycle follows the order_state machine. The transition
RISK_CHECK -> SUBMITTED is impossible without a valid, unexpired risk
decision in the same environment (RULE 009) — enforced at contract level.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import (
    assert_same_environment,
    parse_environment,
)
from architecture.contracts.errors import ContractValidationError, RiskGateError
from architecture.contracts.identifiers import validate_any_identifier, validate_identifier
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    TransitionRecord,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_not_before, ensure_utc
from architecture.contracts.versioning import SemVer
from core.risk.contracts import RiskDecision

CONTRACT_VERSION = "1.1.0"
ORDER_STATE_MACHINE = "order_state"

STATES_REQUIRING_RISK_DECISION = frozenset(
    {"SUBMITTED", "ACCEPTED", "PARTIAL_FILL", "FILLED", "CLOSED"}
)
PRICED_ORDER_TYPES = frozenset({"LIMIT", "STOP", "STOP_LIMIT"})


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    DAY = "DAY"


class OrderStatus(Enum):
    SIGNAL = "SIGNAL"
    DECISION = "DECISION"
    ORDER_CREATED = "ORDER_CREATED"
    RISK_CHECK = "RISK_CHECK"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"
    # Phase 5 execution lifecycle states (order contract 1.1.0)
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    ROUTING = "ROUTING"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    EXPIRED = "EXPIRED"
    REPLACEMENT_REQUESTED = "REPLACEMENT_REQUESTED"
    REPLACED = "REPLACED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


def to_decimal(value: Any, location: str) -> Decimal:
    """Normalize a quantity/price to Decimal with contract validation (shared).

    Binary floats are REJECTED: canonical financial values are decimals,
    integers or decimal strings (LEDGER-004)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ContractValidationError(f"{location} must be a number", location=location)
    if isinstance(value, float):
        raise ContractValidationError(
            f"{location} is a binary float; use Decimal or a decimal string "
            "(canonical financial values, LEDGER-004)",
            location=location,
            rule_id="LEDGER-004",
        )
    if isinstance(value, (int, str)):
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise ContractValidationError(
                f"{location} is not a valid number: {value!r}",
                location=location,
            ) from exc
    raise ContractValidationError(
        f"{location} must be a number, got {type(value).__name__}",
        location=location,
    )


@dataclass(frozen=True)
class Order:
    order_id: str
    client_order_id: str
    strategy_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    time_in_force: TimeInForce
    environment: str
    policy_id: str
    status: OrderStatus
    source: str
    created_at: datetime
    updated_at: datetime
    correlation_id: str
    price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    risk_decision_id: str | None = None
    causation_id: str | None = None
    account_id: str | None = None
    intent_id: str | None = None
    portfolio_decision_id: str | None = None
    risk_decision_hash: str | None = None
    policy_version: str | None = None
    risk_context_hash: str | None = None
    idempotency_key: str | None = None
    expires_at: datetime | None = None
    order_version: int = 1
    provenance: Mapping[str, Any] | None = None
    causal_chain: Mapping[str, Any] | None = None
    replaces_order_id: str | None = None
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", to_decimal(self.quantity, "order.quantity"))
        for optional in ("price", "stop_loss", "take_profit"):
            value = getattr(self, optional)
            if value is not None:
                object.__setattr__(self, optional, to_decimal(value, f"order.{optional}"))

    def validate(self) -> None:
        validate_identifier("order_id", self.order_id, location="order.order_id")
        if not isinstance(self.client_order_id, str) or not self.client_order_id:
            raise ContractValidationError(
                "order.client_order_id must be a non-empty string",
                location="order.client_order_id",
            )
        validate_identifier("strategy_id", self.strategy_id, location="order.strategy_id")
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ContractValidationError(
                "order.symbol must be a non-empty string",
                location="order.symbol",
            )
        if not isinstance(self.side, OrderSide):
            raise ContractValidationError(
                f"order.side must be an OrderSide, got {self.side!r}",
                location="order.side",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.order_type, OrderType):
            raise ContractValidationError(
                f"order.order_type must be an OrderType, got {self.order_type!r}",
                location="order.order_type",
                rule_id="SCHEMA-ENUM",
            )
        if self.quantity <= 0:
            raise ContractValidationError(
                f"order.quantity must be > 0, got {self.quantity}",
                location="order.quantity",
            )
        if self.order_type.value in PRICED_ORDER_TYPES and self.price is None:
            raise ContractValidationError(
                f"order.price is required for order_type {self.order_type.value}",
                location="order.price",
            )
        if not isinstance(self.time_in_force, TimeInForce):
            raise ContractValidationError(
                f"order.time_in_force must be a TimeInForce, got {self.time_in_force!r}",
                location="order.time_in_force",
                rule_id="SCHEMA-ENUM",
            )
        parse_environment(self.environment, location="order.environment")
        validate_identifier("policy_id", self.policy_id, location="order.policy_id")
        if not isinstance(self.status, OrderStatus):
            raise ContractValidationError(
                f"order.status must be an OrderStatus, got {self.status!r}",
                location="order.status",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.source, str) or not self.source:
            raise ContractValidationError(
                "order.source must be a non-empty string",
                location="order.source",
            )
        created_at = ensure_utc(self.created_at, location="order.created_at")
        ensure_not_before(self.updated_at, not_before=created_at, location="order.updated_at")
        if self.status.value in STATES_REQUIRING_RISK_DECISION:
            if not self.risk_decision_id:
                raise ContractValidationError(
                    f"order.status {self.status.value} requires risk_decision_id "
                    "(no order enters execution without a risk decision, RULE 009)",
                    location="order.risk_decision_id",
                    rule_id="RISK-GATE",
                )
            validate_identifier(
                "risk_decision_id",
                self.risk_decision_id,
                location="order.risk_decision_id",
            )
        elif self.risk_decision_id is not None:
            validate_identifier(
                "risk_decision_id",
                self.risk_decision_id,
                location="order.risk_decision_id",
            )
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="order.causation_id")
        validate_any_identifier(self.correlation_id, location="order.correlation_id")
        for field_name, kind in (("intent_id", "intent_id"),
                                 ("portfolio_decision_id", "portfolio_decision_id"),
                                 ("replaces_order_id", "order_id")):
            value = getattr(self, field_name)
            if value is not None:
                validate_identifier(kind, value, location=f"order.{field_name}")
        for name in ("risk_decision_hash", "risk_context_hash"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or len(value) != 64):
                raise ContractValidationError(
                    f"order.{name} must be a sha-256 hex string or None",
                    location=f"order.{name}",
                )
        if self.policy_version is not None:
            SemVer.parse(self.policy_version, location="order.policy_version")
        if self.idempotency_key is not None and (
            not isinstance(self.idempotency_key, str) or not self.idempotency_key
        ):
            raise ContractValidationError(
                "order.idempotency_key must be a non-empty string (semantic request key)",
                location="order.idempotency_key",
            )
        if self.expires_at is not None:
            ensure_not_before(self.expires_at, not_before=self.created_at,
                              location="order.expires_at")
        if not isinstance(self.order_version, int) or isinstance(self.order_version, bool)                 or self.order_version < 1:
            raise ContractValidationError(
                "order.order_version must be a positive integer",
                location="order.order_version",
            )
        if self.provenance is not None and not isinstance(self.provenance, Mapping):
            raise ContractValidationError(
                "order.provenance must be a mapping or None",
                location="order.provenance",
            )
        if self.causal_chain is not None and not isinstance(self.causal_chain, Mapping):
            raise ContractValidationError(
                "order.causal_chain must be a mapping or None",
                location="order.causal_chain",
            )
        SemVer.parse(self.contract_version, location="order.contract_version")

    def transition_status(
        self,
        target: OrderStatus,
        *,
        reason: str,
        actor: str,
        risk_decision: RiskDecision | None = None,
        at: datetime | None = None,
        machines: StateMachineRegistry | None = None,
    ) -> tuple["Order", TransitionRecord]:
        """Apply a lifecycle transition. SUBMISSION requires a passing risk decision."""
        registry = machines or build_state_machine_registry()
        context: dict[str, Any] = {}
        if (
            self.status is OrderStatus.RISK_CHECK
            and target is OrderStatus.SUBMITTED
        ):
            if not isinstance(risk_decision, RiskDecision):
                raise RiskGateError(
                    "Order submission requires a RiskDecision object "
                    "(missing risk decision fails closed, RULE 009)",
                    location="order.transition",
                    details={"order_id": self.order_id, "from": self.status.value, "to": target.value},
                )
            risk_decision.validate()
            assert_same_environment(
                self.environment,
                risk_decision.environment,
                context=f"order[{self.order_id}].risk_gate",
            )
            risk_decision.assert_executable(self.environment, now=at)
            context["risk_decision_validated"] = True

        record = registry.apply(
            ORDER_STATE_MACHINE,
            self.status.value,
            target.value,
            reason=reason,
            actor=actor,
            context=context,
            timestamp=at,
            correlation_id=self.correlation_id or None,
        )
        risk_decision_id = self.risk_decision_id
        if context.get("risk_decision_validated"):
            risk_decision_id = risk_decision.risk_decision_id
        updated_at = ensure_utc(at, location="order.updated_at") if at else ensure_utc(self.updated_at)
        updated = replace(
            self,
            status=target,
            risk_decision_id=risk_decision_id,
            updated_at=updated_at,
        )
        updated.validate()
        return updated, record

    def assert_execution_environment(self, target_environment: str, *, context: str = "execution") -> None:
        """An order may only execute in its own environment (fail closed)."""
        assert_same_environment(
            self.environment, target_environment, context=f"{context}[{self.order_id}]"
        )
