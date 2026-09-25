"""Durable outbox (owned by core.ems).

At-least-once delivery semantics with idempotent consumers - never assumed
exactly-once (SECTION 32)."""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterator, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.time import ensure_utc


class DeliveryStatus(Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class OutboxMessage:
    message_id: str
    aggregate_id: str
    event_type: str
    payload: Mapping[str, Any]
    payload_hash: str
    created_at: datetime
    delivery_status: DeliveryStatus
    attempt_count: int
    environment: str

    def validate(self) -> None:
        validate_identifier("outbox_message_id", self.message_id, location="outbox.message_id")
        for name in ("aggregate_id", "event_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"outbox.{name} must be a non-empty string", location=f"outbox.{name}",
                )
        if not isinstance(self.payload, Mapping):
            raise ContractValidationError(
                "outbox.payload must be a mapping", location="outbox.payload",
            )
        expected = hashlib.sha256(json.dumps(
            self.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
        ).encode("utf-8")).hexdigest()
        if self.payload_hash != expected:
            raise ContractValidationError(
                "outbox.payload_hash mismatch (integrity violation)",
                location="outbox.payload_hash",
            )
        ensure_utc(self.created_at, location="outbox.created_at")
        if not isinstance(self.delivery_status, DeliveryStatus):
            raise ContractValidationError(
                "outbox.delivery_status must be a DeliveryStatus",
                location="outbox.delivery_status", rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.attempt_count, int) or isinstance(self.attempt_count, bool) \
                or self.attempt_count < 0:
            raise ContractValidationError(
                "outbox.attempt_count must be a non-negative integer",
                location="outbox.attempt_count",
            )
        parse_environment(self.environment, location="outbox.environment")


class OutboxStore(ABC):
    @abstractmethod
    def append(self, message: OutboxMessage) -> None:  # pragma: no cover - port
        ...

    @abstractmethod
    def mark_delivered(self, message_id: str) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def mark_failed(self, message_id: str) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def increment_attempt(self, message_id: str) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def iter_pending(self) -> Iterator[OutboxMessage]:  # pragma: no cover
        ...

    @abstractmethod
    def get(self, message_id: str) -> OutboxMessage:  # pragma: no cover
        ...
